"""Measure the image-processing core against the *known* geometry of the world file and write a
Markdown table (+ optional PNG). ``python -m robot_vision.validate --out DIR``.

Truth comes from ``workspace.sdf`` (box 6 cm, sphere r = 4 cm, cylinder r = 3 cm / h = 10 cm); the
images are rendered by :mod:`robot_vision.synth`, so this checks the *algorithms* (segmentation,
back-projection, fitting), not Gazebo's renderer. Tolerances are fixed in :data:`TOLERANCE`
before looking at results. Exit status 1 if a pass/fail row fails.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
from robot_core.perception import Intrinsics, optical_frame_in_world
from robot_core.workspace import Shape, Workspace, load_workspace

from robot_vision.detectors import ColorDetector
from robot_vision.frame import Frame
from robot_vision.geometry import ObjectMeasure, measure
from robot_vision.synth import render

TOLERANCE = {            # metres (yaw: degrees)
    'centre': 0.010, 'size': 0.010, 'height': 0.005, 'radius': 0.005,
    'nearest': 0.010, 'corners': 0.015, 'yaw': 4.0,
}
CLASS_OF = {'box': 'red', 'cylinder': 'green', 'sphere': 'blue'}
DEFAULT_WORLD = Path(__file__).resolve().parents[2] / 'robot_sim' / 'worlds' / 'workspace.sdf'
DEFAULT_FRAME = (Path(__file__).resolve().parents[1] / 'tests' / 'data'
                 / 'gazebo_workspace_frame.npz')
REFERENCE_POINT = (0.0, 0.0, 0.45)       # above the robot base; edge distances start here


@dataclass
class Row:
    scenario: str
    obj: str
    metric: str
    truth: str
    measured: str
    error: float            # in the unit of ``tol``
    tol: float
    unit: str
    informational: bool = False

    @property
    def ok(self) -> bool:
        return self.error <= self.tol


def _corners(shape: Shape, yaw: float = 0.0) -> np.ndarray:
    half = np.array(shape.dims) / 2
    c = np.array(shape.center)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rot = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return np.array([c + rot @ (half * np.array(s))
                     for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3)])


def _surface_distance(shape: Shape, p: np.ndarray, yaw: float = 0.0) -> float:
    """Exact distance from point ``p`` to the solid."""
    c = np.array(shape.center)
    if shape.kind == 'sphere':
        return float(np.linalg.norm(p - c) - shape.dims[0])
    if shape.kind == 'box':
        cy, sy = math.cos(-yaw), math.sin(-yaw)
        local = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]) @ (p - c)
        d = np.maximum(np.abs(local) - np.array(shape.dims) / 2, 0.0)
        return float(np.linalg.norm(d))
    dr = max(math.hypot(p[0] - c[0], p[1] - c[1]) - shape.dims[0], 0.0)
    dz = max(abs(p[2] - c[2]) - shape.dims[1] / 2, 0.0)
    return math.hypot(dr, dz)


def _rows(scenario: str, shape: Shape, m: Optional[ObjectMeasure], cam: np.ndarray,
          yaw: float, informational: bool) -> List[Row]:
    name = shape.name
    if m is None:
        return [Row(scenario, name, 'ölçüm alındı mı', 'evet', 'hayır (None)', 1.0, 0.0, '',
                    informational)]
    rows: List[Row] = []

    def add(metric, truth, measured, error, key, unit='cm', scale=100.0):
        rows.append(Row(scenario, name, metric, truth, measured, error * scale,
                        TOLERANCE[key] * scale, unit, informational))

    rows.append(Row(scenario, name, 'şekil sınıfı', shape.kind, m.shape,
                    0.0 if m.shape == shape.kind else 1.0, 0.0, '', informational))
    truth_c = np.array(shape.center)
    add('merkez konumu (3B)', _fmt(truth_c), _fmt(m.center),
        float(np.linalg.norm(np.array(m.center) - truth_c)), 'centre')
    if shape.kind == 'box':
        true_size = tuple(_aabb_size(shape, yaw))
    elif shape.kind == 'sphere':
        true_size = (2 * shape.dims[0],) * 3
    else:
        true_size = (2 * shape.dims[0], 2 * shape.dims[0], shape.dims[1])
    add('boyut x,y,z (en kötü eksen)', _fmt(true_size), _fmt(m.size),
        float(np.max(np.abs(np.array(m.size) - np.array(true_size)))), 'size')
    true_h = shape.height
    add('yükseklik', f'{true_h * 100:.1f} cm', f'{m.height * 100:.1f} cm',
        abs(m.height - true_h), 'height')
    if shape.kind in ('sphere', 'cylinder'):
        r = shape.dims[0]
        got = m.radius if m.radius is not None else float('nan')
        add('yarıçap', f'{r * 100:.1f} cm', f'{got * 100:.1f} cm',
            abs(got - r) if m.radius is not None else 1.0, 'radius')
    true_near = _surface_distance(shape, cam, yaw)
    add('kameraya en yakın nokta mesafesi', f'{true_near * 100:.1f} cm',
        f'{m.nearest_distance * 100:.1f} cm', abs(m.nearest_distance - true_near), 'nearest')
    if shape.kind == 'box':
        ref = np.array(REFERENCE_POINT)
        truth_d = sorted(float(np.linalg.norm(c - ref)) for c in _corners(shape, yaw))
        got_d = sorted(v for k, v in m.distances_from(REFERENCE_POINT).items() if 'corner' in k)
        worst = max(abs(a - b) for a, b in zip(truth_d, got_d)) if len(got_d) == 8 else 1.0
        add('8 köşe mesafesi, ref. (0,0,0.45) (en kötü)', f'{truth_d[0]:.3f}…{truth_d[-1]:.3f} m',
            f'{got_d[0]:.3f}…{got_d[-1]:.3f} m' if got_d else '-', worst, 'corners')
    if shape.kind == 'box' and abs(shape.dims[0] - shape.dims[1]) > 0.01:
        err = math.degrees(abs((m.obb_yaw - yaw + math.pi / 2) % math.pi - math.pi / 2))
        rows.append(Row(scenario, name, 'yön (yaw)', f'{math.degrees(yaw):.0f}°',
                        f'{math.degrees(m.obb_yaw):.1f}°', err, TOLERANCE['yaw'], '°',
                        informational))
    return rows


def _aabb_size(shape: Shape, yaw: float) -> np.ndarray:
    c = _corners(shape, yaw)
    return c.max(axis=0) - c.min(axis=0)


def _fmt(v) -> str:
    return '(' + ', '.join(f'{float(x) * 100:.1f}' for x in v) + ') cm'


def load_frame(path: Path, ws: Workspace) -> Frame:
    """A captured Gazebo sample (``tools/capture_frame.py`` layout; the first frame of the file).
    The camera pose comes from the world file, the intrinsics from the recorded CameraInfo."""
    data = np.load(path)
    rgb, depth = np.asarray(data['rgb']), np.asarray(data['depth'])
    if rgb.ndim == 4:
        rgb, depth = rgb[0], depth[0]
    intr = Intrinsics.from_camera_info_k(data['k'], int(data['width']), int(data['height']))
    return Frame(rgb, depth, intr, optical_frame_in_world(ws.camera))


def run(ws: Workspace, extra_box: Optional[Shape] = None,
        real_frame: Optional[Frame] = None) -> List[Row]:
    rows: List[Row] = []
    scenarios = [('A: gürültüsüz', 0.0, False), ('B: derinlik σ=2 mm', 0.002, False),
                 ('C: derinlik σ=5 mm (sınır, bilgi)', 0.005, True)]
    detector = ColorDetector()
    for title, sigma, info in scenarios:
        frame = render(ws.camera, ws.shapes, depth_noise=sigma, seed=21)
        segs = {s.label: s for s in detector.detect(frame)}
        cam = np.array(frame.camera_position)
        for shape in ws.shapes:
            seg = segs.get(CLASS_OF[shape.kind])
            rows += _rows(title, shape, measure(frame, seg) if seg else None, cam, 0.0, info)
    if real_frame is not None:
        segs = {s.label: s for s in detector.detect(real_frame)}
        cam = np.array(real_frame.camera_position)
        for shape in ws.shapes:
            seg = segs.get(CLASS_OF[shape.kind])
            rows += _rows('E: Gazebo\'nun GERÇEK karesi (yakalanmış)', shape,
                          measure(real_frame, seg) if seg else None, cam, 0.0, False)
    if extra_box is not None:
        yaw = math.radians(30)
        frame = render(ws.camera, [extra_box], depth_noise=0.001, seed=22,
                       yaws={extra_box.name: yaw})
        segs = detector.detect(frame)
        m = measure(frame, segs[0]) if segs else None
        rows += _rows('D: ek sentetik 8×4×5 cm kutu, yaw 30°, σ=1 mm', extra_box, m,
                      np.array(frame.camera_position), yaw, False)
    return rows


def to_markdown(rows: List[Row]) -> str:
    out = ['# Görüntü çekirdeği doğrulaması (PR-V1)', '',
           'Gerçek değerler `workspace.sdf`\'ten. **A-D**: görüntüler `robot_vision.synth` ile '
           'üretilir (320×240), yani yalnızca algoritmaları sınar (bölütleme, geri-izdüşüm, '
           'uydurma). **E**: Gazebo\'nun kendi görüntüleyicisinden yakalanmış gerçek kare.', '']
    for scen in dict.fromkeys(r.scenario for r in rows):
        part = [r for r in rows if r.scenario == scen]
        info = part[0].informational
        out += [f'## {scen}' + (' — bilgi amaçlı, geçti/kaldı sayılmaz' if info else ''), '',
                '| Nesne | Metrik | Gerçek | Ölçülen | Hata | Tolerans | Sonuç |',
                '|---|---|---|---|---|---|---|']
        for r in part:
            tol = f'≤ {r.tol:g} {r.unit}'.strip() if r.unit else 'eşleşmeli'
            err = f'{r.error:.2f} {r.unit}'.strip() if r.unit else ('0' if r.ok else 'yanlış')
            out.append(f'| {r.obj} | {r.metric} | {r.truth} | {r.measured} | {err} | {tol} | '
                       f'{"✅" if r.ok else ("⚠️" if info else "❌")} |')
        out.append('')
    graded = [r for r in rows if not r.informational]
    out.append(f'**Özet:** {sum(r.ok for r in graded)}/{len(graded)} satır geçti '
               f'(bilgi amaçlı {len(rows) - len(graded)} satır hariç).')
    return '\n'.join(out) + '\n'


def to_png(rows: List[Row], path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    num = [r for r in rows if r.unit]
    scen = list(dict.fromkeys(r.scenario for r in num))
    metrics = list(dict.fromkeys((r.obj, r.metric) for r in num))
    fig, ax = plt.subplots(figsize=(11, 0.34 * len(metrics) + 1.8))
    height = 0.8 / len(scen)
    colors = ['#4C78A8', '#F58518', '#9D9D9D', '#54A24B']
    for si, s in enumerate(scen):
        ratios = []
        for om in metrics:
            hit = [r for r in num if r.scenario == s and (r.obj, r.metric) == om]
            ratios.append(hit[0].error / hit[0].tol if hit and hit[0].tol else 0.0)
        ax.barh(np.arange(len(metrics)) + si * height, ratios, height, label=s,
                color=colors[si % 4])
    ax.axvline(1.0, color='#D62728', lw=1.2)
    ax.text(1.01, -0.9, 'tolerans', color='#D62728', fontsize=8)
    ax.set_yticks(np.arange(len(metrics)) + 0.4 - height / 2)
    ax.set_yticklabels([f'{o.replace("_", " ")}: {m}' for o, m in metrics], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel('hata / tolerans (1\'in altı = geçti)')
    ax.legend(fontsize=7, loc='lower right')
    ax.set_title('Görüntü çekirdeği: gerçek ölçülere karşı hata', fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path, default=Path('.'))
    ap.add_argument('--world', type=Path, default=DEFAULT_WORLD)
    ap.add_argument('--frame', type=Path, default=DEFAULT_FRAME,
                    help='yakalanmış Gazebo karesi (.npz, tools/capture_frame.py); yoksa E atlanır')
    args = ap.parse_args(argv)
    from robot_vision.synth import render as _  # noqa: F401  (fail early if broken)
    ws = load_workspace(args.world)
    extra = Shape('tall_box', 'box', (0.40, 0.05, 0.025), (0.08, 0.04, 0.05), (0.9, 0.1, 0.1))
    real = load_frame(args.frame, ws) if args.frame.exists() else None
    rows = run(ws, extra, real)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'vision_v1_dogrulama.md').write_text(to_markdown(rows), encoding='utf-8')
    png = to_png(rows, args.out / 'vision_v1_hata.png')
    graded = [r for r in rows if not r.informational]
    failed = [r for r in graded if not r.ok]
    print(f'{len(graded) - len(failed)}/{len(graded)} satır geçti; '
          f'PNG: {"evet" if png else "matplotlib yok"}')
    for r in failed:
        print(f'  KALDI: {r.scenario} / {r.obj} / {r.metric}: {r.error:.2f} {r.unit} > {r.tol:g}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
