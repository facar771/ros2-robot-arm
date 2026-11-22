"""CAD meshes -> link-frame meshes (PR B).

    cd src/robot_core && python3 -m robot_core.mesh_import --profile <robot>.yaml [--check]

Input: the CAD exports listed in ``<profile dir>/meshes.yaml`` (every part in the assembly frame,
all joints at the profile's ZERO pose) and the robot profile. For every link it

* moves the CAD points into the robot frame (``axes(p - origin) * units``) and then into the link's
  own frame at the zero pose (pure translation: the profile has no joint rotations),
* writes one binary STL per link (``robot_description/meshes/<robot>/link/<link>.stl``, metres),
* derives COLLISION boxes: one per CAD part, its link-frame bounding box rounded OUTWARD (never
  smaller than the part; together they cover the whole visual mesh), and
* checks that the CAD zero pose matches the profile's zero pose: every joint must lie inside (or
  within ``joint_tolerance`` of) the bounding boxes of the two meshes it connects. A miss is a
  WARNING in the report; nothing is corrected automatically.

Honest limits of the check: a rotation of a part about its own joint axis (e.g. exported at +90 deg)
keeps the joint inside the box and is NOT detected; only a picture (or CAD joint axes) shows it.

A mesh is a picture, not a measurement: this module reads no quality tag and changes no gate.
Only numpy and PyYAML (no mesh library: no new dependency, deterministic output).
"""
import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import yaml

from robot_core.profile import Profile, load_profile
from robot_core.profile_gen import SRC, profile_rel

MESH_VERSION = 1
UNITS = {'mm': 1e-3, 'cm': 1e-2, 'm': 1.0, 'in': 0.0254}
AXES = {'x': 0, 'y': 1, 'z': 2}
MAX_TOLERANCE = 0.05                 # m
MAX_TRIANGLES = 50_000               # per link; above it RViz / MoveIt get slow (warning)
GRID = 1e-4                          # m; collision boxes are rounded outward to this grid
UNIT_RATIO = (0.25, 4.0)             # mesh extent / chain extent outside this = wrong units


class MeshError(ValueError):
    """The mesh map or a CAD file is malformed or inconsistent (message: where and what)."""


def _fail(where: str, message: str) -> MeshError:
    return MeshError(f'{where}: {message}')


# ---- STL --------------------------------------------------------------------------------------
def read_stl(path: Union[str, Path]) -> np.ndarray:
    """Triangles (n, 3, 3) float64 of a binary or ASCII STL."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _fail(str(path), f'okunamadı ({exc.strerror or exc})') from exc
    if len(raw) >= 84:
        count = struct.unpack('<I', raw[80:84])[0]
        if len(raw) == 84 + 50 * count:
            rec = np.dtype([('n', '<f4', 3), ('v', '<f4', 9), ('a', '<u2')])
            tris = np.frombuffer(raw, dtype=rec, count=count, offset=84)['v']
            return _finite(path, tris.reshape(-1, 3, 3).astype(np.float64))
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError:
        raise _fail(str(path), 'ne geçerli ikili ne ASCII STL (boyut başlıkla uyuşmuyor)') from None
    verts = [line.split()[1:4] for line in text.splitlines() if line.strip().startswith('vertex')]
    if not verts or len(verts) % 3:
        raise _fail(str(path), 'ASCII STL: köşe sayısı 3\'ün katı değil ya da hiç üçgen yok')
    try:
        tris = np.array(verts, dtype=np.float64).reshape(-1, 3, 3)
    except ValueError:
        raise _fail(str(path), 'ASCII STL: sayı olmayan köşe') from None
    return _finite(path, tris)


def _finite(path: Path, tris: np.ndarray) -> np.ndarray:
    if not len(tris):
        raise _fail(str(path), 'hiç üçgen yok')
    if not np.all(np.isfinite(tris)):
        raise _fail(str(path), 'sonlu olmayan köşe (NaN/inf)')
    return tris


def stl_bytes(tris: np.ndarray, header: str) -> bytes:
    """Binary STL, float32, normals recomputed (deterministic for the same input)."""
    t = np.asarray(tris, dtype=np.float32)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]).astype(np.float64)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 0).astype(np.float32)
    rec = np.zeros(len(t), dtype=np.dtype([('n', '<f4', 3), ('v', '<f4', 9), ('a', '<u2')]))
    rec['n'], rec['v'] = n, t.reshape(-1, 9)
    head = header.encode('ascii', 'replace')[:80].ljust(80, b' ')
    return head + struct.pack('<I', len(t)) + rec.tobytes()


def signed_volume(tris: np.ndarray) -> float:
    """> 0 for a closed mesh with outward normals; < 0 = inside out (e.g. a mirrored export)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    return float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6.0)


# ---- mesh map ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class MeshMap:
    robot: str
    scale: float                      # CAD unit -> m
    origin: Tuple[float, float, float]   # robot origin in CAD units
    rotation: np.ndarray              # 3x3, robot = rotation @ cad
    links: Dict[str, Tuple[str, ...]]
    joint_tolerance: float


def _rotation(axes: Any) -> np.ndarray:
    if not isinstance(axes, Mapping) or set(axes) != set(AXES):
        raise _fail('cad.axes',
                    f"x, y, z anahtarları olmalı (ör. {{x: -z, y: -x, z: y}}): {axes!r}")
    rot = np.zeros((3, 3))
    for robot_axis, cad in axes.items():
        cad = str(cad).strip()
        sign = -1.0 if cad.startswith('-') else 1.0
        name = cad.lstrip('+-')
        if name not in AXES:
            raise _fail(f'cad.axes.{robot_axis}', f'x, y, z ya da önünde - olmalı: {cad!r}')
        rot[AXES[robot_axis], AXES[name]] = sign
    det = np.linalg.det(rot)
    if abs(abs(det) - 1.0) > 1e-9:
        raise _fail('cad.axes', 'her CAD ekseni bir kez kullanılmalı')
    if det < 0:
        raise _fail('cad.axes',
                    'sol el (ayna) dönüşümü: CAD aynalı olur; bir eksenin işaretini çevir')
    return rot


def parse_mesh_map(data: Any, profile: Profile) -> MeshMap:
    if not isinstance(data, Mapping):
        raise _fail('mesh haritası', f'eşleme olmalı, bulunan: {data!r}')
    allowed = {'mesh_version', 'robot', 'cad', 'links', 'joint_tolerance'}
    extra = set(data) - allowed
    if extra:
        raise _fail('mesh haritası', f'bilinmeyen anahtar(lar): {sorted(extra)}')
    if data.get('mesh_version') != MESH_VERSION:
        raise _fail('mesh_version', f"{MESH_VERSION} olmalı, bulunan: {data.get('mesh_version')!r}")
    if data.get('robot') != profile.robot:
        raise _fail('robot',
                    f"robot profili {profile.robot!r}, mesh haritası {data.get('robot')!r}")
    cad = data.get('cad')
    if not isinstance(cad, Mapping) or set(cad) != {'units', 'origin', 'axes'}:
        raise _fail('cad', 'units, origin, axes (yalnız bunlar) olmalı')
    if cad['units'] not in UNITS:
        raise _fail('cad.units', f'{sorted(UNITS)} değerlerinden biri olmalı: {cad["units"]!r}')
    origin = cad['origin']
    if (not isinstance(origin, (list, tuple)) or len(origin) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in origin)):
        raise _fail('cad.origin', f'3 sayı olmalı: {origin!r}')
    links_in = data.get('links')
    if not isinstance(links_in, Mapping) or not links_in:
        raise _fail('links', 'link -> [CAD dosyaları] eşlemesi olmalı')
    known = {link.name for link in profile.links}
    links: Dict[str, Tuple[str, ...]] = {}
    for name, files in links_in.items():
        if name not in known:
            raise _fail(f'links.{name}', f'robot profilinde böyle bir link yok ({sorted(known)})')
        if isinstance(files, str):
            files = [files]
        if not isinstance(files, (list, tuple)) or not files:
            raise _fail(f'links.{name}', 'en az bir dosya listesi olmalı')
        for f in files:
            p = PurePosixPath(str(f))
            if not isinstance(f, str) or p.is_absolute() or '..' in p.parts or '\\' in f:
                raise _fail(f'links.{name}', f'cad/ altında göreli yol olmalı: {f!r}')
        links[name] = tuple(files)
    seen: Dict[str, str] = {}
    for name, files in links.items():
        for f in files:
            if f in seen:
                raise _fail(f'links.{name}',
                            f'{f} zaten {seen[f]} linkinde: bir parça tek linke ait')
            seen[f] = name
    tol = data.get('joint_tolerance', 0.005)
    if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 < tol <= MAX_TOLERANCE:
        raise _fail('joint_tolerance', f'(0, {MAX_TOLERANCE}] m aralığında olmalı: {tol!r}')
    return MeshMap(profile.robot, UNITS[cad['units']], tuple(float(v) for v in origin),
                   _rotation(cad['axes']), links, float(tol))


def load_mesh_map(path: Union[str, Path], profile: Profile) -> MeshMap:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except OSError as exc:
        raise _fail(str(path), f'okunamadı ({exc.strerror or exc})') from exc
    except yaml.YAMLError as exc:
        raise _fail(str(path), f'YAML hatası: {exc}') from exc
    return parse_mesh_map(data, profile)


# ---- import -----------------------------------------------------------------------------------
def zero_pose_link_origins(profile: Profile) -> Dict[str, np.ndarray]:
    """World position of every link frame at q = 0 (joints carry no rotation in profile v3)."""
    out = {profile.links[0].name: np.zeros(3)}
    for joint in profile.joints:
        out[joint.child] = out[joint.parent] + np.asarray(joint.origin, dtype=float)
    return out


def _box_distance(point: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.linalg.norm(np.maximum(0.0, np.maximum(lo - point, point - hi))))


@dataclass(frozen=True)
class Imported:
    meshes: Dict[str, np.ndarray]          # link -> triangles in the LINK frame (m)
    # link -> one box per CAD part: {part, size, origin} (link frame, m). Per part, not per link:
    # one box around an L-shaped link (e.g. upper arm + elbow plates) would swallow its neighbours.
    collision: Dict[str, List[Dict[str, Any]]]
    warnings: Tuple[str, ...]


def _outward_box(points: np.ndarray) -> Tuple[List[float], List[float]]:
    lo = np.floor(points.min(0) / GRID) * GRID
    hi = np.ceil(points.max(0) / GRID) * GRID
    return ([round(float(v), 4) for v in hi - lo], [round(float(v), 5) for v in (hi + lo) / 2])


def import_meshes(profile: Profile, mesh_map: MeshMap, cad_dir: Union[str, Path]) -> Imported:
    cad_dir = Path(cad_dir)
    origins = zero_pose_link_origins(profile)
    origin = np.asarray(mesh_map.origin)
    warnings: List[str] = []
    world: Dict[str, np.ndarray] = {}
    part_world: Dict[str, List[Tuple[str, np.ndarray]]] = {}
    for link, files in mesh_map.links.items():
        parts = []
        for f in files:
            tris = read_stl(cad_dir / f)
            if signed_volume(tris) < 0:
                warnings.append(f'{link}: {f} ters yüzlü (işaretli hacim < 0; aynalanmış dışa '
                                f'aktarım?): görüntü içi dışa dönük olabilir')
            parts.append((f, ((tris - origin) * mesh_map.scale) @ mesh_map.rotation.T))
        part_world[link] = parts
        world[link] = np.concatenate([t for _, t in parts])
        tris = world[link]
        if len(tris) > MAX_TRIANGLES:
            warnings.append(f'{link}: {len(tris)} üçgen (> {MAX_TRIANGLES}): RViz/MoveIt yavaşlar')
    for link in profile.links:
        if link.name not in world:
            warnings.append(f'{link.name}: mesh yok, primitif şekil kalır')

    # units: the meshes must be about as large as the chain they dress
    pts = np.concatenate([w.reshape(-1, 3) for w in world.values()])
    mesh_extent = float(np.linalg.norm(pts.max(0) - pts.min(0)))
    joints = np.array([origins[j.child] for j in profile.joints] + [np.zeros(3)])
    chain_extent = float(np.linalg.norm(joints.max(0) - joints.min(0)))
    ratio = mesh_extent / max(chain_extent, 1e-6)
    if not UNIT_RATIO[0] <= ratio <= UNIT_RATIO[1]:
        raise _fail('cad.units', f'mesh boyu {mesh_extent:.4g} m, zincir {chain_extent:.4g} m '
                                 f'(oran {ratio:.3g}): birim ya da ölçek yanlış')

    # zero pose: every joint inside / near both meshes it connects
    boxes = {k: (v.reshape(-1, 3).min(0), v.reshape(-1, 3).max(0)) for k, v in world.items()}
    for joint in profile.joints:
        p = origins[joint.child]
        for link in (joint.parent, joint.child):
            if link not in boxes:
                continue
            d = _box_distance(p, *boxes[link])
            if d > mesh_map.joint_tolerance:
                warnings.append(f'{joint.name}: {link} mesh\'inin {d * 1000:.1f} mm dışında '
                                f'(> {mesh_map.joint_tolerance * 1000:g} mm): CAD sıfır pozu ile '
                                f'profil sıfır pozu uyuşmuyor olabilir')

    meshes, collision = {}, {}
    for link, tris in world.items():
        meshes[link] = tris - origins[link]
        collision[link] = []
        for part, ptris in part_world[link]:
            size, centre = _outward_box((ptris - origins[link]).reshape(-1, 3))
            collision[link].append({'part': part, 'size': size, 'origin': centre})
    return Imported(meshes, collision, tuple(warnings))


# ---- outputs ----------------------------------------------------------------------------------
def output_paths(robot: str, links: Sequence[str]) -> Dict[str, str]:
    """Generated file -> path relative to ``src``."""
    out = {f'mesh:{link}': f'robot_description/meshes/{robot}/link/{link}.stl' for link in links}
    out['report'] = f'robot_description/profile/generated/{robot}/meshes.yaml'
    return out


def _report(profile: Profile, mesh_map: MeshMap, result: Imported, rel: str) -> str:
    head = (f'# GENERATED by `python3 -m robot_core.mesh_import --profile {rel}`; '
            'do not edit.\n'
            '# Link-frame meshes of the CAD exports + collision boxes (one per CAD part: its\n'
            '# bounding box, rounded outward, so never smaller than the part) + the zero-pose\n'
            '# check warnings.\n'
            '# A mesh is a picture, not a measurement: it changes no quality tag and no gate.\n')
    body = {
        'robot': profile.robot,
        'links': {link: {'mesh': f'meshes/{profile.robot}/link/{link}.stl',
                         'triangles': int(len(result.meshes[link])),
                         'collision_boxes': result.collision[link]}
                  for link in [lk.name for lk in profile.links] if link in result.meshes},
        'warnings': list(result.warnings),
    }
    return head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100,
                                 default_flow_style=None)


def generate(profile: Profile, mesh_map: MeshMap, cad_dir: Union[str, Path],
             rel: str) -> Tuple[Dict[str, bytes], Imported]:
    result = import_meshes(profile, mesh_map, cad_dir)
    paths = output_paths(profile.robot, list(result.meshes))
    files = {paths[f'mesh:{link}']: stl_bytes(tris, f'robot_core.mesh_import {profile.robot} '
                                                    f'{link} (m, link frame; generated)')
             for link, tris in result.meshes.items()}
    files[paths['report']] = _report(profile, mesh_map, result, rel).encode('utf-8')
    return files, result


def default_paths(profile_path: Path, src: Path, robot: str) -> Tuple[Path, Path]:
    """(mesh map, CAD directory) of a profile."""
    return (profile_path.parent / 'meshes.yaml',
            src / 'robot_description' / 'meshes' / robot / 'cad')


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python3 -m robot_core.mesh_import')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--meshes', type=Path, help='mesh map (default: meshes.yaml beside the '
                                                    'profile)')
    parser.add_argument('--src', type=Path, default=SRC, help='the repository src/ directory')
    parser.add_argument('--check', action='store_true',
                        help='write nothing; exit 1 if a generated file is out of date')
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.profile)
        map_path, cad_dir = default_paths(args.profile, args.src, profile.robot)
        mesh_map = load_mesh_map(args.meshes or map_path, profile)
        files, result = generate(profile, mesh_map, cad_dir, profile_rel(profile.robot))
    except ValueError as exc:              # ProfileError / MeshError
        print(f'HATA: {exc}', file=sys.stderr)
        return 2
    for w in result.warnings:
        print(f'UYARI: {w}')
    stale = []
    for rel_path, data in files.items():
        path = args.src / rel_path
        if path.exists() and path.read_bytes() == data:
            print(f'güncel   {rel_path}')
            continue
        stale.append(rel_path)
        if args.check:
            print(f'ESKİ     {rel_path}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f'yazıldı  {rel_path}')
    if args.check and stale:
        print('\nMesh çıktıları güncel değil; yeniden üret: python3 -m robot_core.mesh_import '
              '--profile <profil>')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
