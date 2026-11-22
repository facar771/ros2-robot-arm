"""PR B: CAD meshes -> link-frame meshes, collision boxes and the zero-pose check.

Real data: the KUCUK_ROBOT STL exports (karma_robot). Failure modes: synthetic boxes built from the
profile's own link shapes and placed in the CAD frame with the inverse of the mesh map."""
import ast
import copy
import struct
from pathlib import Path

import numpy as np
import pytest
import yaml
from robot_core.mesh_import import (
    MeshError,
    generate,
    import_meshes,
    load_mesh_map,
    main,
    parse_mesh_map,
    read_stl,
    signed_volume,
    stl_bytes,
    zero_pose_link_origins,
)
from robot_core.profile import load_profile

from robot_core import mesh_import as mi
from tests.conftest import SRC

KARMA = SRC / 'robot_description' / 'profile' / 'karma_robot' / 'karma_robot.yaml'
MAP = KARMA.parent / 'meshes.yaml'
CAD = SRC / 'robot_description' / 'meshes' / 'karma_robot' / 'cad'
REL = 'src/robot_description/profile/karma_robot/karma_robot.yaml'


@pytest.fixture(scope='module')
def profile():
    return load_profile(KARMA)


@pytest.fixture(scope='module')
def raw_map():
    return yaml.safe_load(MAP.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def mesh_map(profile):
    return load_mesh_map(MAP, profile)


@pytest.fixture(scope='module')
def real(profile, mesh_map):
    return import_meshes(profile, mesh_map, CAD)


# ---- the real robot ---------------------------------------------------------------------------
def test_every_link_has_a_mesh_and_the_cad_zero_pose_matches_the_profile(profile, real):
    assert set(real.meshes) == {link.name for link in profile.links}
    assert real.warnings == ()


def test_generated_files_are_up_to_date(profile, mesh_map):
    """Fix: cd src/robot_core && python3 -m robot_core.mesh_import --profile
    ../robot_description/profile/karma_robot/karma_robot.yaml
    Meshes are compared by geometry (float32, 1 um), the report byte for byte."""
    files, _ = generate(profile, mesh_map, CAD, REL)
    for rel, data in files.items():
        current = (SRC / rel).read_bytes()
        if rel.endswith('.stl'):
            a, b = read_stl(SRC / rel), _tris(data)
            assert a.shape == b.shape and np.allclose(a, b, atol=1e-6), rel
        else:
            assert current == data, f'{rel} güncel değil'


def test_link_meshes_sit_in_their_link_frames(profile, real):
    """Every joint is inside the bounding box of the two link meshes it connects (link frames)."""
    origins = zero_pose_link_origins(profile)
    for joint in profile.joints:
        for link in (joint.parent, joint.child):
            local = origins[joint.child] - origins[link]
            pts = real.meshes[link].reshape(-1, 3)
            assert np.all(local >= pts.min(0) - 1e-5) and np.all(local <= pts.max(0) + 1e-5), \
                (joint.name, link)


def test_front_is_away_from_the_turret_protrusion(real):
    """Ferhat's check for the robot's front (+x): the protrusion on the turret (link_1, the J2
    motor side) points to the BACK."""
    pts = real.meshes['link_1'].reshape(-1, 3)
    assert -pts[:, 0].min() > pts[:, 0].max() + 0.02


def test_cad_was_exported_in_mm_and_the_arm_is_desktop_sized(real):
    pts = np.concatenate([m.reshape(-1, 3) for m in real.meshes.values()])
    assert 0.1 < np.ptp(pts[:, 2]) < 0.5                           # metres after the import


def _box_bounds(box):
    half = np.divide(box['size'], 2)
    return np.subtract(box['origin'], half), np.add(box['origin'], half)


def test_collision_boxes_cover_each_part_and_stay_tight(profile, mesh_map, real):
    """One box per CAD part: never smaller than the part (outward rounding), at most one grid
    step larger; together they cover the whole visual mesh."""
    origins = zero_pose_link_origins(profile)
    for link, boxes in real.collision.items():
        assert [b['part'] for b in boxes] == list(mesh_map.links[link])
        for box in boxes:
            tris = read_stl(CAD / box['part'])
            pts = ((tris - mesh_map.origin) * mesh_map.scale @ mesh_map.rotation.T
                   - origins[link]).reshape(-1, 3)
            lo, hi = _box_bounds(box)
            assert np.all(pts >= lo - 1e-9) and np.all(pts <= hi + 1e-9), (link, box['part'])
            assert np.all(hi - lo - np.ptp(pts, axis=0) < 2 * mi.GRID + 1e-9), (link, box['part'])


def _obb_overlap(a, b):
    """Separating-axis test of two oriented boxes (centre, rotation, half extents)."""
    (ca, ra, ea), (cb, rb, eb) = a, b
    axes = [ra[:, i] for i in range(3)] + [rb[:, i] for i in range(3)]
    axes += [np.cross(ra[:, i], rb[:, j]) for i in range(3) for j in range(3)]
    d = cb - ca
    for ax in axes:
        n = np.linalg.norm(ax)
        if n < 1e-9:
            continue
        ax = ax / n
        if (sum(ea[i] * abs(ra[:, i] @ ax) for i in range(3))
                + sum(eb[i] * abs(rb[:, i] @ ax) for i in range(3))) < abs(d @ ax):
            return False
    return True


def _link_transforms(profile, q):
    out = {profile.links[0].name: np.eye(4)}
    for j, qi in zip(profile.joints, q):
        k = np.array(j.axis, float)
        kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        a, b = np.eye(4), np.eye(4)
        a[:3, 3] = j.origin
        b[:3, :3] = np.eye(3) + np.sin(qi) * kx + (1 - np.cos(qi)) * kx @ kx
        out[j.child] = out[j.parent] @ a @ b
    return out


@pytest.mark.parametrize('pose', ['zero', 'home', 'ready'])
def test_no_self_collision_between_non_adjacent_links(profile, real, pose):
    """MoveIt checks every non-adjacent pair: the boxes must not overlap in the reference poses
    (the zero pose included: a single box around link_2 used to swallow link_4 / link_5)."""
    q = [0.0] * 6 if pose == 'zero' else profile.named_poses[pose]
    tf = _link_transforms(profile, q)
    adjacent = {frozenset(pair) for pair in profile.adjacent_pairs()}
    names = list(real.collision)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if frozenset((a, b)) in adjacent:
                continue
            for ba in real.collision[a]:
                for bb in real.collision[b]:
                    oa = (tf[a][:3, :3] @ ba['origin'] + tf[a][:3, 3], tf[a][:3, :3],
                          np.divide(ba['size'], 2))
                    ob = (tf[b][:3, :3] @ bb['origin'] + tf[b][:3, 3], tf[b][:3, :3],
                          np.divide(bb['size'], 2))
                    assert not _obb_overlap(oa, ob), (pose, a, ba['part'], b, bb['part'])


def test_cad_exports_are_closed_outward_meshes():
    for path in sorted(CAD.glob('*.stl')):
        assert signed_volume(read_stl(path)) > 0, path.name


def test_output_is_deterministic(profile, mesh_map):
    a, _ = generate(profile, mesh_map, CAD, REL)
    b, _ = generate(profile, mesh_map, CAD, REL)
    assert a == b


def test_meshes_never_touch_quality_or_gates():
    """A mesh is a picture, not a measurement: the importer does not even import the gate code."""
    tree = ast.parse(Path(mi.__file__).read_text(encoding='utf-8'))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
             for a in n.names} | {n.module for n in ast.walk(tree)
                                  if isinstance(n, ast.ImportFrom) and n.module}
    assert not any('maturity' in n or 'safezone' in n for n in names)


def test_check_mode(tmp_path, profile, mesh_map):
    assert main(['--check', '--profile', str(KARMA)]) == 0
    src = tmp_path / 'src'
    files, _ = generate(profile, mesh_map, CAD, REL)
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
    (src / 'robot_description' / 'meshes' / 'karma_robot' / 'cad').mkdir(parents=True)
    for p in CAD.glob('*.stl'):
        (src / 'robot_description' / 'meshes' / 'karma_robot' / 'cad' / p.name).write_bytes(
            p.read_bytes())
    args = ['--check', '--profile', str(KARMA), '--src', str(src)]
    assert main(args) == 0
    report = next(rel for rel in files if rel.endswith('meshes.yaml'))
    (src / report).write_text('eski', encoding='utf-8')
    assert main(args) == 1


def test_cli_reports_errors_with_exit_code_2(tmp_path):
    bad = tmp_path / 'meshes.yaml'
    bad.write_text('mesh_version: 99\n', encoding='utf-8')
    assert main(['--check', '--profile', str(KARMA), '--meshes', str(bad)]) == 2


# ---- synthetic CAD: failure modes -------------------------------------------------------------
def _tris(data: bytes) -> np.ndarray:
    n = struct.unpack('<I', data[80:84])[0]
    rec = np.dtype([('n', '<f4', 3), ('v', '<f4', 9), ('a', '<u2')])
    return np.frombuffer(data, rec, count=n, offset=84)['v'].reshape(-1, 3, 3).astype(float)


def box_tris(lo, hi):
    """12 outward triangles of an axis-aligned box."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]])
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (3, 0, 4), (3, 4, 7)]
    return v[np.array(f)]


def write_synthetic_cad(tmp_path, profile, mesh_map, shift=None, flip=None, rotate=None):
    """One box per link = the link's profile shape, written in CAD coordinates."""
    origins = zero_pose_link_origins(profile)
    cad = tmp_path / 'cad'
    cad.mkdir()
    files = {}
    for link in profile.links:
        half = np.array(link.dims if link.shape == 'box' else
                        (2 * link.dims[0], 2 * link.dims[0], link.dims[1])) / 2
        tris = box_tris(np.array(link.origin) - half, np.array(link.origin) + half)
        if rotate and link.name == rotate[0]:            # about the link frame's axis
            tris = tris @ rotate[1].T
        tris = tris + origins[link.name]
        if shift and link.name == shift[0]:
            tris = tris + np.asarray(shift[1])
        if flip == link.name:
            tris = tris[:, ::-1]
        cad_pts = (tris @ mesh_map.rotation) / mesh_map.scale + np.asarray(mesh_map.origin)
        (cad / f'{link.name}.stl').write_bytes(stl_bytes(cad_pts, 'synthetic'))
        files[link.name] = [f'{link.name}.stl']
    return cad, files


def synthetic_map(raw_map, files, **over):
    data = copy.deepcopy(raw_map)
    data['links'] = files
    data.update(over)
    return data


def test_synthetic_cad_from_the_profile_imports_cleanly(tmp_path, profile, mesh_map, raw_map):
    cad, files = write_synthetic_cad(tmp_path, profile, mesh_map)
    result = import_meshes(profile, parse_mesh_map(synthetic_map(raw_map, files), profile), cad)
    assert result.warnings == ()
    for link in profile.links:                             # the shape comes back in its frame
        (box,) = result.collision[link.name]
        assert box['origin'] == pytest.approx(link.origin, abs=2e-4)


def test_a_part_exported_at_the_wrong_place_is_reported(tmp_path, profile, mesh_map, raw_map):
    cad, files = write_synthetic_cad(tmp_path, profile, mesh_map, shift=('link_3', (0.04, 0, 0)))
    result = import_meshes(profile, parse_mesh_map(synthetic_map(raw_map, files), profile), cad)
    assert any(w.startswith('joint_3: link_3') and 'uyuşmuyor' in w for w in result.warnings)


def test_a_rotation_about_the_joint_axis_is_a_known_blind_spot(tmp_path, profile, mesh_map,
                                                               raw_map):
    """Documented limit: link_6 turned 90 deg about its roll axis keeps J6 inside its box."""
    rx = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float)
    cad, files = write_synthetic_cad(tmp_path, profile, mesh_map, rotate=('link_6', rx))
    result = import_meshes(profile, parse_mesh_map(synthetic_map(raw_map, files), profile), cad)
    assert result.warnings == ()


def test_inside_out_part_is_reported(tmp_path, profile, mesh_map, raw_map):
    cad, files = write_synthetic_cad(tmp_path, profile, mesh_map, flip='link_4')
    result = import_meshes(profile, parse_mesh_map(synthetic_map(raw_map, files), profile), cad)
    assert any(w.startswith('link_4:') and 'ters yüzlü' in w for w in result.warnings)


def test_wrong_units_are_an_error(tmp_path, profile, mesh_map, raw_map):
    cad, files = write_synthetic_cad(tmp_path, profile, mesh_map)
    data = synthetic_map(raw_map, files)
    data['cad']['units'] = 'm'                               # mm read as m: 1000x too large
    with pytest.raises(MeshError, match='birim ya da ölçek'):
        import_meshes(profile, parse_mesh_map(data, profile), cad)


def test_link_without_mesh_keeps_its_primitive(tmp_path, profile, mesh_map, raw_map):
    cad, files = write_synthetic_cad(tmp_path, profile, mesh_map)
    del files['link_5']
    result = import_meshes(profile, parse_mesh_map(synthetic_map(raw_map, files), profile), cad)
    assert result.warnings == ('link_5: mesh yok, primitif şekil kalır',)
    assert 'link_5' not in result.meshes


# ---- mesh map validation ----------------------------------------------------------------------
@pytest.mark.parametrize('mutate, match', [
    (lambda d: d.update(mesh_version=2), 'mesh_version'),
    (lambda d: d.update(robot='six_axis_robot'), 'robot'),
    (lambda d: d.update(colour='red'), 'bilinmeyen anahtar'),
    (lambda d: d['cad'].update(units='furlong'), 'cad.units'),
    (lambda d: d['cad'].update(origin=[0, 0]), 'cad.origin'),
    (lambda d: d['cad'].update(axes={'x': 'z', 'y': 'z', 'z': 'y'}), 'bir kez'),
    (lambda d: d['cad'].update(axes={'x': '-z', 'y': 'x', 'z': 'y'}), 'ayna'),
    (lambda d: d['cad'].update(axes={'x': 'w', 'y': 'x', 'z': 'y'}), 'cad.axes.x'),
    (lambda d: d['links'].update(link_9=['a.stl']), 'links.link_9'),
    (lambda d: d['links'].update(link_3=['../../secret.stl']), 'göreli yol'),
    (lambda d: d['links'].update(link_3=['/etc/passwd']), 'göreli yol'),
    (lambda d: d['links'].update(link_3=[]), 'en az bir'),
    (lambda d: d['links'].update(link_3=['j2-j3_kol.stl']), 'tek linke ait'),
    (lambda d: d.update(joint_tolerance=0.5), 'joint_tolerance'),
    (lambda d: d.update(joint_tolerance=True), 'joint_tolerance'),
])
def test_mesh_map_is_validated(profile, raw_map, mutate, match):
    data = copy.deepcopy(raw_map)
    mutate(data)
    with pytest.raises(MeshError, match=match):
        parse_mesh_map(data, profile)


# ---- STL reading ------------------------------------------------------------------------------
def test_ascii_and_binary_stl_read_the_same(tmp_path):
    tris = box_tris(np.zeros(3), np.array([1.0, 2.0, 3.0]))
    (tmp_path / 'b.stl').write_bytes(stl_bytes(tris, 'x'))
    lines = ['solid t']
    for t in tris:
        lines += ['facet normal 0 0 0', ' outer loop'] + [f'  vertex {a} {b} {c}' for a, b, c in t]
        lines += [' endloop', 'endfacet']
    (tmp_path / 'a.stl').write_text('\n'.join(lines + ['endsolid t']), encoding='ascii')
    assert np.allclose(read_stl(tmp_path / 'a.stl'), read_stl(tmp_path / 'b.stl'))


@pytest.mark.parametrize('payload, match', [
    (b'\xff' * 90, 'ne geçerli ikili ne ASCII'),
    (b'solid x\n vertex 1 2 3\n vertex 1 2 3\nendsolid', "3'ün katı"),
    (b'solid x\n vertex a b c\n vertex 1 2 3\n vertex 1 2 3\nendsolid', 'sayı olmayan'),
    (b'\x00' * 80 + struct.pack('<I', 0), 'hiç üçgen yok'),
])
def test_broken_stl_is_an_error(tmp_path, payload, match):
    (tmp_path / 'x.stl').write_bytes(payload)
    with pytest.raises(MeshError, match=match):
        read_stl(tmp_path / 'x.stl')


def test_missing_cad_file_is_an_error(tmp_path, profile, raw_map):
    data = synthetic_map(raw_map, {'link_3': ['yok.stl']})
    with pytest.raises(MeshError, match='okunamadı'):
        import_meshes(profile, parse_mesh_map(data, profile), tmp_path)
