"""O1: waypoint store - atomic YAML, schema, version, validation on save and on load."""

import os
import threading

import numpy as np
import pytest
import yaml
from robot_core.frames import FrameStore
from robot_core.jogapi import READY_POSE
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.waypoints import (
    MAX_WAYPOINTS,
    WAYPOINTS_VERSION,
    Waypoint,
    WaypointError,
    WaypointStore,
)

P = load_profile(PROFILE_PATH)
CHAIN = Chain(P)
ZONE = P.safety.zone
Q0 = (0.15, -0.231, 1.8, -0.471, 1.404, 1.131)      # TCP ~ (0.45, 0, 0.45), inside the box
STAMP = '2026-11-05T12:00:00Z'


def store(tmp_path, **kw):
    kw.setdefault('clock', lambda: STAMP)
    return WaypointStore(tmp_path / 'waypoints.yaml', CHAIN, ZONE, **kw)


def read(tmp_path):
    return yaml.safe_load((tmp_path / 'waypoints.yaml').read_text(encoding='utf-8'))


def write(tmp_path, doc):
    (tmp_path / 'waypoints.yaml').write_text(yaml.safe_dump(doc), encoding='utf-8')


def outside_box_joints():
    """A joint vector inside the joint limits whose TCP is outside the safe box (home: z=1.24)."""
    q = (0.0,) * CHAIN.dof
    assert ZONE.check(tuple(CHAIN.fk(q)[:3, 3])) is not None
    return q


def test_fixtures_are_what_the_tests_assume():
    assert ZONE.check(tuple(CHAIN.fk(Q0)[:3, 3])) is None
    assert ZONE.check(tuple(CHAIN.fk(READY_POSE)[:3, 3])) is None
    assert np.all(CHAIN.lower <= Q0) and np.all(np.array(Q0) <= CHAIN.upper)


def test_empty_when_the_file_is_missing(tmp_path):
    s = store(tmp_path)
    assert s.names() == [] and len(s) == 0
    assert not (tmp_path / 'waypoints.yaml').exists()        # reading never creates the file


def test_add_saves_joints_pose_frame_and_time(tmp_path):
    s = store(tmp_path)
    wp = s.add('pick_a', Q0)
    assert isinstance(wp, Waypoint) and wp.created == STAMP and wp.frame == 'World'
    np.testing.assert_allclose(wp.position, CHAIN.fk(Q0)[:3, 3], atol=1e-12)
    doc = read(tmp_path)
    assert doc['waypoints_version'] == WAYPOINTS_VERSION
    assert doc['joint_names'] == list(CHAIN.joint_names)
    e = doc['waypoints'][0]
    assert e['name'] == 'pick_a' and e['joints'] == list(Q0) and e['created'] == STAMP
    assert set(e['tcp']) == {'frame', 'position', 'quaternion_xyzw'}
    assert e['tcp']['frame'] == 'World' and len(e['tcp']['quaternion_xyzw']) == 4


def test_reload_gives_the_same_waypoints_in_order(tmp_path):
    s = store(tmp_path)
    s.add('b_second', READY_POSE)
    s.add('a_first', Q0)
    again = store(tmp_path)
    assert again.names() == ['b_second', 'a_first']
    for name in again.names():
        assert again.get(name).joints == s.get(name).joints
        assert again.get(name).position == pytest.approx(s.get(name).position, abs=1e-12)
        assert again.get(name).created == STAMP


def test_joint_limit_violation_is_refused_and_nothing_is_written(tmp_path):
    s = store(tmp_path)
    s.add('ok', Q0)
    before = (tmp_path / 'waypoints.yaml').read_bytes()
    for j in range(CHAIN.dof):
        for edge in (CHAIN.lower[j] - 0.01, CHAIN.upper[j] + 0.01):
            q = list(Q0)
            q[j] = float(edge)
            with pytest.raises(WaypointError, match=f'{CHAIN.joint_names[j]}.*limit'):
                s.add('bad', q)
    assert s.names() == ['ok'] and (tmp_path / 'waypoints.yaml').read_bytes() == before


def test_joint_values_exactly_on_a_limit_are_accepted_if_the_tcp_is_safe(tmp_path):
    q = list(Q0)
    q[5] = float(CHAIN.upper[5])
    s = store(tmp_path)
    if ZONE.check(tuple(CHAIN.fk(q)[:3, 3])) is None:
        assert s.add('edge', q).joints[5] == q[5]
    else:                                  # the wrist roll does not move the TCP point: guard
        pytest.skip('limit pose leaves the box for this profile')


def test_tcp_outside_the_safe_box_is_refused(tmp_path):
    s = store(tmp_path)
    with pytest.raises(WaypointError, match='güvenli çalışma kutusunun dışında'):
        s.add('too_high', outside_box_joints())
    assert s.names() == [] and not (tmp_path / 'waypoints.yaml').exists()


def test_every_box_wall_is_enforced(tmp_path):
    from dataclasses import replace
    p = CHAIN.fk(Q0)[:3, 3]
    for axis in range(3):                   # box that excludes Q0's TCP on one axis at a time
        lower, upper = list(ZONE.lower), list(ZONE.upper)
        lower[axis] = float(p[axis]) + 0.01
        upper[axis] = max(upper[axis], lower[axis] + 0.1)
        tight = replace(ZONE, lower=tuple(lower), upper=tuple(upper))
        s = WaypointStore(tmp_path / f'w{axis}.yaml', CHAIN, tight)
        with pytest.raises(WaypointError, match='kutusunun dışında'):
            s.add('x', Q0)


@pytest.mark.parametrize('bad', [
    None, 'abc', [0.0] * 5, [0.0] * 7, [float('nan')] * 6, [float('inf')] + [0.0] * 5,
    ['a'] * 6, [True] * 6, {'q': 1}])
def test_malformed_joint_values_are_refused(tmp_path, bad):
    with pytest.raises(WaypointError, match='sayı'):
        store(tmp_path).add('x', bad)


@pytest.mark.parametrize('name', ['', '1abc', 'a b', 'a-b', 'a' * 33, None, 5, 'ğ'])
def test_bad_names_are_refused(tmp_path, name):
    with pytest.raises(WaypointError, match='nokta adı'):
        store(tmp_path).add(name, Q0)


def test_duplicate_needs_explicit_overwrite(tmp_path):
    t = iter(['2026-11-05T12:00:00Z', '2026-11-05T13:00:00Z'])
    s = store(tmp_path, clock=lambda: next(t))
    s.add('p', Q0)
    with pytest.raises(WaypointError, match='zaten var'):
        s.add('p', READY_POSE)
    assert s.get('p').joints == Q0
    wp = s.add('p', READY_POSE, overwrite=True)
    assert wp.joints == READY_POSE and wp.created == '2026-11-05T13:00:00Z'
    assert s.names() == ['p'] and store(tmp_path).get('p').joints == READY_POSE


def test_overwrite_with_a_bad_pose_keeps_the_old_one(tmp_path):
    s = store(tmp_path)
    s.add('p', Q0)
    with pytest.raises(WaypointError):
        s.add('p', outside_box_joints(), overwrite=True)
    assert store(tmp_path).get('p').joints == Q0


def test_remove_and_rename(tmp_path):
    s = store(tmp_path)
    s.add('a', Q0)
    s.add('b', READY_POSE)
    moved = s.rename('a', 'c')
    assert moved.name == 'c' and moved.joints == Q0 and moved.created == STAMP
    assert s.names() == ['c', 'b'] and store(tmp_path).names() == ['c', 'b']
    with pytest.raises(WaypointError, match='zaten var'):
        s.rename('c', 'b')
    with pytest.raises(WaypointError, match='nokta adı'):
        s.rename('c', '9x')
    with pytest.raises(WaypointError, match='nokta yok'):
        s.rename('zzz', 'q')
    s.remove('c')
    assert store(tmp_path).names() == ['b']
    with pytest.raises(WaypointError, match='nokta yok'):
        s.remove('c')
    with pytest.raises(WaypointError, match='nokta yok'):
        s.get('c')
    assert 'b' in s and 'c' not in s


def test_count_limit(tmp_path):
    s = store(tmp_path, max_waypoints=2)
    s.add('a', Q0)
    s.add('b', READY_POSE)
    with pytest.raises(WaypointError, match='en çok 2'):
        s.add('c', Q0)
    s.add('a', READY_POSE, overwrite=True)           # replacing does not count as a new one
    assert MAX_WAYPOINTS >= 10


# ---- frames -----------------------------------------------------------------------------------
def frames_store(tmp_path):
    f = FrameStore(tmp_path / 'frames.yaml')
    f.teach('bench', (0.3, 0.1, 0.2), (0.4, 0.1, 0.2), (0.3, 0.2, 0.2))
    return f


def test_pose_in_a_taught_frame_roundtrips_to_world(tmp_path):
    fr = frames_store(tmp_path)
    s = store(tmp_path, frames=fr)
    wp = s.add('on_bench', Q0, frame='bench')
    assert wp.frame == 'bench'
    f = fr.get('bench')
    world = np.array(f.origin) + f.rotation_matrix @ np.array(wp.position)
    np.testing.assert_allclose(world, CHAIN.fk(Q0)[:3, 3], atol=1e-9)
    again = store(tmp_path, frames=fr)
    assert again.get('on_bench').position == pytest.approx(wp.position, abs=1e-12)


def test_unknown_frame_is_refused(tmp_path):
    with pytest.raises(WaypointError, match='bilinmeyen çerçeve'):
        store(tmp_path).add('p', Q0, frame='bench')           # no FrameStore at all
    with pytest.raises(WaypointError, match='bilinmeyen çerçeve'):
        store(tmp_path, frames=frames_store(tmp_path)).add('p', Q0, frame='nope')
    with pytest.raises(WaypointError, match='bilinmeyen çerçeve'):
        store(tmp_path).add('p', Q0, frame='Tool')            # Tool follows the arm: no fixed pose
    with pytest.raises(WaypointError, match='çerçeve adı'):
        store(tmp_path).add('p', Q0, frame='')


def test_load_fails_when_the_frame_vanished_or_changed(tmp_path):
    fr = frames_store(tmp_path)
    store(tmp_path, frames=fr).add('p', Q0, frame='bench')
    fr.remove('bench')
    with pytest.raises(WaypointError, match='bilinmeyen çerçeve'):
        store(tmp_path, frames=fr)
    fr.teach('bench', (0.3, 0.0, 0.2), (0.4, 0.0, 0.2), (0.3, 0.1, 0.2))   # taught differently
    with pytest.raises(WaypointError, match='yeniden hesaplananla uyuşmuyor'):
        store(tmp_path, frames=fr)


# ---- load validation --------------------------------------------------------------------------
def good_doc(tmp_path):
    store(tmp_path).add('p', Q0)
    return read(tmp_path)


def mutate(tmp_path, fn):
    doc = good_doc(tmp_path)
    fn(doc)
    write(tmp_path, doc)


@pytest.mark.parametrize('fn, match', [
    (lambda d: d.update(waypoints_version=2), 'waypoints_version'),
    (lambda d: d.update(waypoints_version=True), 'waypoints_version'),
    (lambda d: d.pop('waypoints_version'), 'eksik'),
    (lambda d: d.update(extra=1), 'bilinmeyen'),
    (lambda d: d.update(joint_names=['a', 'b']), 'joint_names'),
    (lambda d: d.update(waypoints={}), 'liste'),
    (lambda d: d['waypoints'][0].update(extra=1), 'bilinmeyen'),
    (lambda d: d['waypoints'][0].pop('created'), 'eksik'),
    (lambda d: d['waypoints'][0].update(created='yesterday'), 'created'),
    (lambda d: d['waypoints'][0].update(created='2026-13-45T99:00:00Z'), 'geçerli bir tarih'),
    (lambda d: d['waypoints'][0].update(name='1bad'), 'nokta adı'),
    (lambda d: d['waypoints'][0].update(joints=[0.0] * 5), 'sayı'),
    (lambda d: d['waypoints'][0]['joints'].__setitem__(0, 99.0), 'limit'),
    (lambda d: d['waypoints'][0]['joints'].__setitem__(0, float('nan')), 'sayı'),
    (lambda d: d['waypoints'][0]['tcp'].update(extra=1), 'bilinmeyen'),
    (lambda d: d['waypoints'][0]['tcp'].update(position=[0, 0, 0]), 'uyuşmuyor'),
    (lambda d: d['waypoints'][0]['tcp'].update(position=[0, 0]), 'sayı'),
    (lambda d: d['waypoints'][0]['tcp'].update(quaternion_xyzw=[0, 0, 0, 1]), 'uyuşmuyor'),
    (lambda d: d['waypoints'][0]['tcp'].update(frame='Nope'), 'bilinmeyen çerçeve'),
    (lambda d: d['waypoints'].append(dict(d['waypoints'][0])), 'tekrar'),
])
def test_corrupt_files_are_refused_on_load_with_the_file_named(tmp_path, fn, match):
    mutate(tmp_path, fn)
    with pytest.raises(WaypointError, match=match) as err:
        store(tmp_path)
    assert 'waypoints.yaml' in str(err.value)


def test_a_point_outside_the_box_in_the_file_is_refused_on_load(tmp_path):
    from dataclasses import replace
    store(tmp_path).add('p', Q0)
    tight = replace(ZONE, upper=(ZONE.lower[0] + 0.01,) + tuple(ZONE.upper[1:]))
    with pytest.raises(WaypointError, match='kutusunun dışında'):
        WaypointStore(tmp_path / 'waypoints.yaml', CHAIN, tight)


def test_hand_written_point_outside_the_box_is_refused_on_load(tmp_path):
    def far(doc):
        e = doc['waypoints'][0]
        e['joints'] = list(outside_box_joints())
    mutate(tmp_path, far)
    with pytest.raises(WaypointError, match='kutusunun dışında'):
        store(tmp_path)


def test_not_yaml_and_wrong_root(tmp_path):
    (tmp_path / 'waypoints.yaml').write_text('a: [unclosed', encoding='utf-8')
    with pytest.raises(WaypointError, match='geçerli YAML değil'):
        store(tmp_path)
    (tmp_path / 'waypoints.yaml').write_text('- 1\n- 2\n', encoding='utf-8')
    with pytest.raises(WaypointError, match='eşleme'):
        store(tmp_path)
    (tmp_path / 'waypoints.yaml').write_text('', encoding='utf-8')
    with pytest.raises(WaypointError):
        store(tmp_path)


def test_load_does_not_execute_yaml_tags(tmp_path):
    (tmp_path / 'waypoints.yaml').write_text('!!python/object/apply:os.system ["true"]\n',
                                             encoding='utf-8')
    with pytest.raises(WaypointError):
        store(tmp_path)


def test_box_in_another_frame_is_not_silently_accepted(tmp_path):
    from dataclasses import replace
    with pytest.raises(WaypointError, match='world'):
        WaypointStore(tmp_path / 'w.yaml', CHAIN, replace(ZONE, frame='base_link'))


# ---- atomic write -----------------------------------------------------------------------------
def test_failed_write_leaves_the_old_file_and_no_temp_files(tmp_path, monkeypatch):
    s = store(tmp_path)
    s.add('a', Q0)
    before = (tmp_path / 'waypoints.yaml').read_bytes()

    def boom(src, dst):
        raise OSError('disk full')
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError, match='disk full'):
        s.add('b', READY_POSE)
    monkeypatch.undo()
    assert (tmp_path / 'waypoints.yaml').read_bytes() == before
    assert s.names() == ['a']                              # memory did not move ahead of the file
    assert sorted(p.name for p in tmp_path.iterdir()) == ['waypoints.yaml']
    for op in (lambda: s.remove('a'), lambda: s.rename('a', 'z')):
        monkeypatch.setattr(os, 'replace', boom)
        with pytest.raises(OSError):
            op()
        monkeypatch.undo()
        assert s.names() == ['a'] and (tmp_path / 'waypoints.yaml').read_bytes() == before


def test_the_file_is_never_seen_half_written(tmp_path):
    s = store(tmp_path)
    s.add('seed', Q0)
    stop = threading.Event()
    seen = []

    def reader():
        while not stop.is_set():
            try:
                names = store(tmp_path).names()
                seen.append(len(names))
            except Exception as exc:                       # a torn file would land here
                seen.append(exc)
    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(25):
            s.add(f'p{i}', Q0 if i % 2 else READY_POSE)
    finally:
        stop.set()
        t.join()
    errors = [x for x in seen if not isinstance(x, int)]
    assert seen and not errors, errors[:1]


def test_parent_directory_is_created(tmp_path):
    s = WaypointStore(tmp_path / 'data' / 'sub' / 'waypoints.yaml', CHAIN, ZONE)
    s.add('a', Q0)
    assert (tmp_path / 'data' / 'sub' / 'waypoints.yaml').exists()


def test_clock_default_is_utc_iso(tmp_path):
    wp = WaypointStore(tmp_path / 'w.yaml', CHAIN, ZONE).add('a', Q0)
    assert wp.created.endswith('Z') and len(wp.created) == 20
