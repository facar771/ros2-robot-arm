"""Three-point user frames: geometry, rejection rules, store (atomic write, schema, consistency)."""
import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest
import yaml
from robot_core.frames import (
    FrameError,
    FrameLimits,
    FrameStore,
    frame_from_three_points,
    resolve_rotation,
)
from robot_core.se3 import is_rotation

from tests.conftest import SRC

O = (0.40, 0.00, 0.30)            # noqa: E741  (the taught origin)
PX = (0.55, 0.00, 0.30)


def teach(pv, px=PX, name='table', **limits):
    return frame_from_three_points(name, O, px, pv, FrameLimits(**limits) if limits else None)


def pv_at(sin):
    """A point whose direction makes the given sine with +X, 0.1 m from the origin."""
    return (O[0] + 0.1 * math.sqrt(1 - sin * sin), 0.1 * sin, O[2])


def test_axes_from_three_points():
    f = teach((0.40, 0.12, 0.30))
    assert np.allclose(f.rotation_matrix, np.eye(3)) and f.origin == O
    tilted = frame_from_three_points('t', O, (0.4, 0.2, 0.3), (0.3, 0.0, 0.3))   # X along +Y
    assert tilted.rotation_matrix @ [1, 0, 0] == pytest.approx([0, 1, 0])
    assert tilted.rotation_matrix @ [0, 1, 0] == pytest.approx([-1, 0, 0], abs=1e-12)   # Pv side
    assert tilted.rotation_matrix @ [0, 0, 1] == pytest.approx([0, 0, 1])


def test_always_a_proper_rotation():
    rng = np.random.default_rng(3)
    made = 0
    for _ in range(500):
        o, px, pv = rng.uniform(-0.8, 0.8, (3, 3))
        try:
            f = frame_from_three_points('r', o, px, pv)
        except FrameError:
            continue
        made += 1
        assert is_rotation(f.rotation_matrix, 1e-9)         # RᵀR = I, det = +1
        assert np.linalg.det(f.rotation_matrix) == pytest.approx(1.0)
        assert f.quaternion[3] >= 0
    assert made > 400


def test_pv_may_be_anywhere_in_the_plane_on_the_plus_y_side():
    a = teach((0.40, 0.12, 0.30))
    b = teach((0.55, 0.30, 0.30))                           # same half plane, other place
    assert np.allclose(a.rotation_matrix, b.rotation_matrix, atol=1e-12)
    # mirrored side: Z flips (left-handed teach)
    c = teach((0.40, -0.12, 0.30))
    assert c.rotation_matrix[:, 2] == pytest.approx([0, 0, -1])


def test_collinear_and_degenerate_points_are_rejected():
    with pytest.raises(FrameError, match='aynı doğru'):
        teach((0.70, 0.00, 0.30))                           # on the X axis
    with pytest.raises(FrameError, match='aynı doğru'):
        teach((0.30, 0.00, 0.30))                           # on the X axis, opposite way
    with pytest.raises(FrameError, match='aynı doğru'):
        teach(O)                                            # Pv == origin
    with pytest.raises(FrameError, match='mm arada'):
        frame_from_three_points('t', O, O, (0.4, 0.1, 0.3))


def test_x_distance_limit_is_20_mm():
    ok = frame_from_three_points('t', O, (O[0] + 0.0201, O[1], O[2]), (0.4, 0.1, 0.3))
    assert ok.name == 't'
    with pytest.raises(FrameError, match=r'19\.9 mm'):
        frame_from_three_points('t', O, (O[0] + 0.0199, O[1], O[2]), (0.4, 0.1, 0.3))
    assert frame_from_three_points('t', O, (O[0] + 0.0105, O[1], O[2]), (0.4, 0.1, 0.3),
                                   # a setting, not a constant
                                   FrameLimits(min_x_distance=0.01))


def test_sine_thresholds_reject_below_017_and_warn_below_05():
    with pytest.raises(FrameError, match='aynı doğru'):
        teach(pv_at(0.16))
    assert teach(pv_at(0.18)).warnings and 'düşük doğruluk' in teach(pv_at(0.18)).warnings[0]
    assert teach(pv_at(0.49)).warnings
    assert teach(pv_at(0.51)).warnings == ()
    assert teach(pv_at(1.0)).warnings == ()
    assert teach(pv_at(0.12), min_sin=0.1, warn_sin=0.5).warnings               # tunable


def test_bad_numbers_and_names():
    for bad in ((math.nan, 0, 0), (0, math.inf, 0), ('a', 'b', 'c'), (1, 2), (400, 0, 0)):
        with pytest.raises(FrameError):
            teach(bad)
    for name in ('', '1abc', 'a b', 'x' * 33, 'World', 'tool', 'ÇERÇEVE', None):
        with pytest.raises(FrameError):
            frame_from_three_points(name, O, PX, (0.4, 0.1, 0.3))


# ---- store ------------------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path):
    return FrameStore(tmp_path / 'data' / 'frames.yaml')


def test_store_round_trip(store):
    assert store.names() == []                                   # missing file = empty store
    f = store.teach('table', O, PX, (0.40, 0.12, 0.30))
    store.teach('jig', (0.3, -0.2, 0.25), (0.3, -0.05, 0.25), (0.2, -0.2, 0.25))
    again = FrameStore(store.path)
    assert again.names() == ['table', 'jig']
    assert np.allclose(again.get('table').transform, f.transform, atol=1e-12)
    assert again.get('jig').rotation_matrix @ [1, 0, 0] == pytest.approx([0, 1, 0], abs=1e-12)
    assert yaml.safe_load(store.path.read_text(encoding='utf-8'))['frames_version'] == 1
    store.remove('table')
    assert FrameStore(store.path).names() == ['jig']
    with pytest.raises(FrameError, match='yok'):
        store.remove('table')


def test_store_limits_names_unique_and_max_10(store):
    for i in range(10):
        store.teach(f'f{i}', O, PX, (0.40, 0.12, 0.30))
    with pytest.raises(FrameError, match='en çok 10'):
        store.teach('f10', O, PX, (0.40, 0.12, 0.30))
    with pytest.raises(FrameError, match='zaten var'):
        # names are unique, case-insensitive
        store.teach('F3', O, PX, (0.40, 0.12, 0.30))
    assert len(FrameStore(store.path)) == 10
    store.remove('f0')
    store.teach('f10', O, PX, (0.40, 0.12, 0.30))                # room again


def test_failed_teach_changes_nothing(store):
    store.teach('a', O, PX, (0.40, 0.12, 0.30))
    before = store.path.read_bytes()
    with pytest.raises(FrameError):
        store.teach('b', O, PX, (0.7, 0.0, 0.3))
    assert store.path.read_bytes() == before and store.names() == ['a']


def test_write_is_atomic_and_leaves_no_temp_files(store, monkeypatch):
    store.teach('a', O, PX, (0.40, 0.12, 0.30))
    good = store.path.read_bytes()

    def boom(src, dst):
        raise OSError('disk gitti')
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        store.teach('b', O, PX, (0.40, 0.12, 0.30))
    monkeypatch.undo()
    assert store.path.read_bytes() == good                       # the old file is intact
    assert store.names() == ['a'] and FrameStore(store.path).names() == ['a']
    assert sorted(p.name for p in store.path.parent.iterdir()) == ['frames.yaml']   # temp removed


def tamper(store, edit):
    data = yaml.safe_load(store.path.read_text(encoding='utf-8'))
    edit(data)
    store.path.write_text(yaml.safe_dump(data), encoding='utf-8')


@pytest.mark.parametrize('edit, match', [
    (lambda d: d['frames'][0]['pose']['position'].__setitem__(0, 0.9), 'uyuşmuyor'),
    (lambda d: d['frames'][0]['pose'].update(quaternion_xyzw=[0, 0, 0.7071, 0.7071]), 'uyuşmuyor'),
    # derived != stored
    (lambda d: d['frames'][0]['points'].update(x=[0.55, 0.05, 0.3]), 'uyuşmuyor'),
    (lambda d: d['frames'][0]['points'].update(x=[0.401, 0.0, 0.3]), 'mm arada'),
    (lambda d: d.update(frames_version=2), 'frames_version'),
    (lambda d: d.update(extra=1), 'bilinmeyen'),
    (lambda d: d['frames'][0].pop('pose'), 'eksik'),
    (lambda d: d['frames'].append(d['frames'][0]), 'tekrar'),
    (lambda d: d.update(frames=d['frames'] * 11), 'en çok 10'),
    (lambda d: d['frames'][0].update(name='World'), 'ayrılmış'),
])
def test_load_recomputes_pose_and_rejects_inconsistent_files(store, edit, match):
    store.teach('a', O, PX, (0.40, 0.12, 0.30))
    tamper(store, edit)
    with pytest.raises(FrameError, match=match):
        FrameStore(store.path)


def test_load_names_the_file(tmp_path):
    path = tmp_path / 'frames.yaml'
    path.write_text('a: [1', encoding='utf-8')
    with pytest.raises(FrameError, match='frames.yaml'):
        FrameStore(path)
    path.write_text('- 1\n', encoding='utf-8')
    with pytest.raises(FrameError, match='frames.yaml'):
        FrameStore(path)


def test_example_file_and_schema_agree_with_the_code():
    example = SRC / 'robot_core' / 'config' / 'frames.example.yaml'
    store = FrameStore(example)                                   # load only; never written here
    assert store.names() == ['table', 'fixture_a']
    schema = json.loads((SRC / 'robot_core' / 'config' / 'frames.schema.json').read_text('utf-8'))
    jsonschema = pytest.importorskip('jsonschema')
    doc = yaml.safe_load(example.read_text(encoding='utf-8'))
    jsonschema.validate(doc, schema)
    doc['frames'][0]['name'] = 'World'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_resolve_rotation(store):
    store.teach('table', O, PX, (0.40, 0.12, 0.30))
    assert np.allclose(resolve_rotation('World'), np.eye(3))
    # follows the TCP, resolved per tick
    assert resolve_rotation('tool') is None
    assert np.allclose(resolve_rotation('table', store), np.eye(3))
    with pytest.raises(FrameError, match='bilinmeyen'):
        resolve_rotation('nope', store)


def test_frames_module_is_ros_free():
    code = ('import sys, robot_core.frames, robot_core.jog; '
            "bad = [m for m in sys.modules if m.split('.')[0] in ('rclpy', 'moveit')]; "
            'sys.exit(1 if bad else 0)')
    env = {**os.environ, 'PYTHONPATH': str(SRC / 'robot_core')}
    assert subprocess.run([sys.executable, '-c', code], env=env).returncode == 0
