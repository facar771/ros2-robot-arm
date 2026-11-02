"""Validate the pan scan against the known geometry of the ring world (six objects over 160°,
no single view sees more than two). ``python -m robot_vision.scan_validate --out DIR``.

Tolerances are fixed before looking at results. Exit status 1 on a failed pass/fail row.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import List, Tuple

from robot_core.workspace import CameraSpec, Shape

from robot_vision.scan import ScanConfig, SyntheticPanner, ring_world, scan

TOL_CENTER, TOL_SIZE, TOL_HEIGHT = 0.010, 0.010, 0.005       # metres
COLOR = {'red': 'red', 'green': 'green', 'blue': 'blue'}
KIND = {'red': 'box', 'green': 'cylinder', 'blue': 'sphere'}
CONFIGS = (('A: gürültüsüz, 9 görüntü', 0.0, 9), ('B: derinlik σ=2 mm, 9 görüntü', 0.002, 9),
           ('C: derinlik σ=2 mm, 7 görüntü (daha seyrek)', 0.002, 7))


def _truth_size(s: Shape) -> Tuple[float, float, float]:
    if s.kind == 'sphere':
        return (2 * s.dims[0],) * 3
    if s.kind == 'cylinder':
        return (2 * s.dims[0], 2 * s.dims[0], s.dims[1])
    return tuple(s.dims)


def run_config(cam: CameraSpec, shapes: List[Shape], sigma: float, steps: int):
    result = scan(SyntheticPanner(cam, shapes, depth_noise=sigma),
                  ScanConfig(yaw_min=math.radians(-100), yaw_max=math.radians(100), steps=steps))
    rows, used = [], set()
    for s in shapes:
        color = s.name.split('_')[0]
        cands = [(math.dist(o.center[:2], s.center[:2]), i) for i, o in enumerate(result.objects)
                 if o.label == COLOR[color] and i not in used]
        if not cands or min(cands)[0] > 0.15:
            rows.append((s, None, None))
            continue
        d, i = min(cands)
        used.add(i)
        o = result.objects[i]
        err_c = math.dist(o.center, s.center)
        err_s = max(abs(a - b) for a, b in zip(o.measure.size, _truth_size(s)))
        err_h = abs(o.measure.height - s.height)
        rows.append((s, o, (err_c, err_s, err_h)))
    extras = [o for i, o in enumerate(result.objects) if i not in used]
    return result, rows, extras


def to_markdown(results) -> Tuple[str, bool]:
    out = ['# Pan taraması doğrulaması (PR-V4)', '',
           'Halka dünyası: 6 renkli nesne, kamera (0, 0, 0.6) konumunda kendi ekseni etrafında '
           'döner; tek görüntü 60° görür ve en çok üç nesne içerir. Gerçek değerler dünya '
           'tanımından; görüntüler `robot_vision.synth` ile (Gazebo YOK).', '']
    ok_all = True
    for title, _sigma, _steps, result, rows, extras in results:
        out += [f'## {title}', '',
                '| Nesne | Şekil | Merkez hatası | Boyut hatası | Yükseklik hatası '
                '| Görüldüğü görüntü | Sonuç |', '|---|---|---|---|---|---|---|']
        for s, o, e in rows:
            if o is None:
                out.append(f'| {s.name} | - | - | - | - | - | ❌ bulunamadı |')
                ok_all = False
                continue
            good = (o.measure.shape == KIND[s.name.split('_')[0]] and e[0] <= TOL_CENTER
                    and e[1] <= TOL_SIZE and e[2] <= TOL_HEIGHT and not o.partial)
            ok_all &= good
            out.append(f'| {s.name} | {o.measure.shape} | {e[0] * 100:.2f} cm | '
                       f'{e[1] * 100:.2f} cm | {e[2] * 100:.2f} cm | {o.views} | '
                       f'{"✅" if good else "❌"} |')
        dup = bool(extras)
        ok_all &= not dup
        out += ['', f'Bulunan nesne: **{len(result.objects)}** / gerçek {len(rows)}'
                f'{" — ❌ fazladan/tekrarlı: " + str(len(extras)) if dup else " — ✅ tekrar yok"}; '
                f'görüntü: {result.views_used}; açılar (°): '
                f'{", ".join(f"{math.degrees(a):.0f}" for a in result.angles)}', '']
    out.append(f'**Özet:** {"tüm satırlar geçti" if ok_all else "BAŞARISIZ satır var"} '
               f'(toleranslar: merkez ≤ {TOL_CENTER * 100:g} cm, boyut ≤ {TOL_SIZE * 100:g} cm, '
               f'yükseklik ≤ {TOL_HEIGHT * 100:g} cm, kesik nesne yok, tekrar yok).')
    return '\n'.join(out) + '\n', ok_all


def to_png(results, path: Path, cam: CameraSpec) -> bool:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.patches import Wedge
    except ImportError:
        return False
    title, sigma, steps, result, rows, extras = results[1]
    fig, ax = plt.subplots(figsize=(6.4, 5.4))
    fov = math.degrees(cam.horizontal_fov)
    for a in result.angles:
        ax.add_patch(Wedge((0, 0), 1.35, math.degrees(a) - fov / 2, math.degrees(a) + fov / 2,
                           color='#4C78A8', alpha=0.06))
    colors = {'red': '#D62728', 'green': '#2CA02C', 'blue': '#1F77B4'}
    for s, o, _ in rows:
        c = colors[s.name.split('_')[0]]
        ax.scatter([s.center[1]], [s.center[0]], marker='x', s=90, color='black', zorder=3)
        if o is not None:
            ax.scatter([o.center[1]], [o.center[0]], s=140, facecolors='none', edgecolors=c,
                       linewidths=2, zorder=4)
    ax.scatter([0], [0], marker='^', color='black')
    ax.set_xlim(-1.4, 1.4)
    ax.set_ylim(-0.2, 1.4)
    ax.invert_xaxis()
    ax.set_aspect('equal')
    ax.set_xlabel('y (m)  — sola doğru artar')
    ax.set_ylabel('x (m)')
    ax.set_title(f'Pan taraması, üstten görünüm ({title.split(":")[1].strip()})\n'
                 'x: gerçek konum, halka: ölçülen; mavi dilimler: görüntülerin görüş açısı',
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path, default=Path('.'))
    args = ap.parse_args(argv)
    cam, shapes = ring_world()
    results = []
    for title, sigma, steps in CONFIGS:
        result, rows, extras = run_config(cam, shapes, sigma, steps)
        results.append((title, sigma, steps, result, rows, extras))
    text, ok = to_markdown(results)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'vision_v4_tarama.md').write_text(text, encoding='utf-8')
    png = to_png(results, args.out / 'vision_v4_tarama.png', cam)
    print(('tüm satırlar geçti' if ok else 'BAŞARISIZ satır var')
          + f'; PNG: {"evet" if png else "yok"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
