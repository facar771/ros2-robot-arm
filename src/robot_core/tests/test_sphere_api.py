"""L8: sphere frames over the Robot API - /api/frames (type, types, PUT rules), spheres.yaml,
sphere jog (azimuth / elevation / r / align through the lease+dead-man controller), reach range."""

import http.client
import json
import math

import numpy as np
import pytest
import yaml
from robot_core.frames import FrameError, FrameStore, sphere_frame
from robot_core.jog import DlsJogEngine, JogConfig, solve_sphere_pose
from robot_core.jogapi import (
    JogApiConfig,
    JogController,
    JogDenied,
    frames_document,
    replace_frames,
    sphere_reach,
)
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.spheres import SphereStore
from robot_core.supervisor import EStop
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.fakes import CancelAwareSkills, FakeTool, RecordingSink
from tests.test_jogapi import LEASE, TICK, Clock, Plant

P = load_profile(PROFILE_PATH)
CHAIN = Chain(P)
SEED = np.array([0.0, 0.357, 1.79, 0.0, -0.751, -0.142])
CENTER, RADIUS, EL, ROLL = (0.0, 0.0, 0.2), 0.7, math.radians(10), -3.0
DOME = {'type': 'sphere', 'name': 'dome', 'center': list(CENTER), 'radius': RADIUS,
        'tool_axis': '-z'}
THREE = {'origin': [0.3, 0.0, 0.1], 'x': [0.4, 0.0, 0.1], 'xy': [0.3, 0.1, 0.1]}


def on_sphere():
    frame = sphere_frame('dome', CENTER, RADIUS, '-z')
    q = solve_sphere_pose(CHAIN, frame, 0.0, EL, SEED, RADIUS, ROLL)
    assert q is not None
    return q


Q_ON = on_sphere()


class Rig:
    def __init__(self, tmp_path, q=Q_ON, hold=True, with_dome=True):
        self.clock, self.plant, self.estop = Clock(), Plant(q), EStop()
        self.plant.clock = self.clock
        self.frames = FrameStore(tmp_path / 'frames.yaml')
        self.spheres = SphereStore(tmp_path / 'spheres.yaml')
        if with_dome:
            self.spheres.replace_all([sphere_frame('dome', CENTER, RADIUS, '-z')])
        self.jog = JogController(
            DlsJogEngine(CHAIN, P.safety, JogConfig()), CHAIN, self.plant, self.plant, self.estop,
            self.frames, spheres=self.spheres, config=JogApiConfig(hold_enabled=hold),
            clock=self.clock)

    def tick(self, n=1):
        for _ in range(n):
            self.clock.t += TICK
            self.jog.tick()

    def cmd(self, **body):
        return self.jog.command({'lease': LEASE, 'frame': 'dome', **body})

    def angles(self):
        sph = self.spheres.get('dome')
        return sph.angles(CHAIN.fk(self.plant.q)[:3, 3])

    def look_error(self):
        sph = self.spheres.get('dome')
        pose = CHAIN.fk(self.plant.q)
        to_c = sph.center_array - pose[:3, 3]
        look = pose[:3, :3] @ sph.tool_axis_vector
        return math.acos(max(-1.0, min(1.0, float(look @ to_c / np.linalg.norm(to_c)))))


def denied(fn, *a, **k):
    with pytest.raises(JogDenied) as exc:
        fn(*a, **k)
    return exc.value


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ---- spheres.yaml -----------------------------------------------------------------------------
def test_store_round_trip_atomic_and_strict(tmp_path):
    path = tmp_path / 'spheres.yaml'
    store = SphereStore(path)
    store.replace_all([sphere_frame('a', (0, 0, 0.1), 0.4), sphere_frame('b', (0.1, 0, 0), 0.3)])
    again = SphereStore(path)
    assert [s.as_dict() for s in again.all()] == [s.as_dict() for s in store.all()]
    assert not list(tmp_path.glob('*.tmp'))
    doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert doc['spheres_version'] == 1 and doc['spheres'][0]['type'] == 'sphere'
    with pytest.raises(FrameError, match='ad tekrar'):
        store.replace_all([sphere_frame('a', (0, 0, 0), 0.3), sphere_frame('A', (0, 0, 0), 0.3)])
    assert len(store) == 2                                    # untouched after the error


@pytest.mark.parametrize('mutate, reason', [
    (lambda d: d['spheres'][0].update(type='torus'), "bilinmeyen çerçeve türü 'torus'"),
    (lambda d: d.update(spheres_version=2), 'spheres_version 1 olmalı'),
    (lambda d: d['spheres'][0].update(extra=1), 'bilinmeyen anahtar'),
    (lambda d: d['spheres'][0].update(radius=9.0), 'yarıçap'),
    (lambda d: d.update(extra=1), '{spheres_version, spheres}'),
])
def test_unknown_or_bad_entries_are_a_clear_load_error_never_skipped(tmp_path, mutate, reason):
    path = tmp_path / 'spheres.yaml'
    SphereStore(path).replace_all([sphere_frame('a', (0, 0, 0.1), 0.4)])
    doc = yaml.safe_load(path.read_text(encoding='utf-8'))
    mutate(doc)
    path.write_text(yaml.safe_dump(doc), encoding='utf-8')
    with pytest.raises(FrameError, match=reason):
        SphereStore(path)


# ---- /api/frames document and PUT rules ---------------------------------------------------------
def test_document_is_additive_and_lists_the_types(tmp_path):
    frames = FrameStore(tmp_path / 'frames.yaml')
    store = SphereStore(tmp_path / 'spheres.yaml')
    replace_frames(frames, {'frames': [{'name': 'tbl', 'points': THREE}],
                            'spheres': [DOME]}, None, store)
    doc = frames_document(frames, store)
    assert doc['types'] == ['user', 'sphere'] and doc['builtin'] == ['World', 'Tool']
    assert doc['frames'][0]['type'] == 'user' and doc['frames'][0]['points'] == THREE
    assert doc['spheres'] == [DOME] and doc['max_spheres'] == 10
    old = frames_document(frames)                              # no sphere support wired
    assert old['types'] == ['user'] and old['spheres'] == []


def test_old_hub_put_cannot_delete_or_corrupt_spheres(tmp_path):
    frames = FrameStore(tmp_path / 'frames.yaml')
    store = SphereStore(tmp_path / 'spheres.yaml')
    replace_frames(frames, {'frames': [], 'spheres': [DOME]}, None, store)
    # exactly what the old Hub sends: only the user frames, no "spheres" key
    doc = replace_frames(frames, {'frames': [{'name': 'tbl', 'points': THREE}]}, None, store)
    assert [s['name'] for s in doc['spheres']] == ['dome'] and len(frames) == 1
    assert SphereStore(tmp_path / 'spheres.yaml').names() == ['dome']
    # ... and it may copy a GET entry back (the additive "type": "user" key is accepted)
    doc = replace_frames(frames, {'frames': [{'type': 'user', 'name': 'tbl', 'points': THREE}]},
                         None, store)
    assert doc['frames'][0]['name'] == 'tbl' and store.names() == ['dome']
    replace_frames(frames, {'frames': [], 'spheres': []}, None, store)   # explicit: clear them
    assert store.names() == []


def test_frames_yaml_is_untouched_by_sphere_writes(tmp_path):
    frames = FrameStore(tmp_path / 'frames.yaml')
    store = SphereStore(tmp_path / 'spheres.yaml')
    replace_frames(frames, {'frames': [{'name': 'tbl', 'points': THREE}]}, None, store)
    before = (tmp_path / 'frames.yaml').read_bytes()
    replace_frames(frames, {'frames': [{'name': 'tbl', 'points': THREE}], 'spheres': [DOME]},
                   None, store)
    assert (tmp_path / 'frames.yaml').read_bytes() == before
    assert yaml.safe_load(before)['frames_version'] == 1       # an old reader still loads it
    FrameStore(tmp_path / 'frames.yaml')


@pytest.mark.parametrize('body, reason', [
    ({'frames': [{'type': 'torus', 'name': 'x', 'points': THREE}]},
     "bilinmeyen çerçeve türü 'torus'"),
    ({'frames': [{'type': 'sphere', 'name': 'x', 'center': [0, 0, 0], 'radius': 0.3}]},
     '"spheres" listesinde'),
    ({'frames': [], 'spheres': [{**DOME, 'type': 'torus'}]}, "bilinmeyen çerçeve türü 'torus'"),
    ({'frames': [], 'spheres': [{**DOME, 'radius': 9.0}]}, 'yarıçap'),
    ({'frames': [], 'spheres': [{**DOME, 'color': 'red'}]}, 'bilinmeyen'),
    ({'frames': [], 'spheres': [{'type': 'sphere', 'name': 'x'}]}, 'eksik'),
    ({'frames': [], 'spheres': [DOME, {**DOME, 'name': 'DOME'}]}, 'ad tekrar'),
    ({'frames': [], 'spheres': [{**DOME, 'name': 'World'}]}, 'ayrılmış'),
    ({'frames': [{'name': 'dome', 'points': THREE}], 'spheres': [DOME]}, 'hem kullanıcı hem küre'),
    ({'frames': [], 'spheres': 'x'}, 'liste olmalı'),
    ({'frames': [], 'extra': 1}, 'gövde'),
])
def test_bad_bodies_are_refused_with_the_reason_and_write_nothing(tmp_path, body, reason):
    frames = FrameStore(tmp_path / 'frames.yaml')
    store = SphereStore(tmp_path / 'spheres.yaml')
    replace_frames(frames, {'frames': [{'name': 'tbl', 'points': THREE}],
                            'spheres': [{**DOME, 'name': 'keep'}]}, None, store)
    snapshot = ((tmp_path / 'frames.yaml').read_bytes(), (tmp_path / 'spheres.yaml').read_bytes())
    with pytest.raises(FrameError, match=reason):
        replace_frames(frames, body, None, store)
    assert ((tmp_path / 'frames.yaml').read_bytes(),
            (tmp_path / 'spheres.yaml').read_bytes()) == snapshot
    assert store.names() == ['keep'] and frames.names() == ['tbl']


def test_spheres_without_a_store_are_refused_with_the_reason(tmp_path):
    frames = FrameStore(tmp_path / 'frames.yaml')
    with pytest.raises(FrameError, match='kullanılamıyor: dosya bozuk'):
        replace_frames(frames, {'frames': [], 'spheres': [DOME]}, None, None, 'dosya bozuk')
    assert frames_document(frames, None, 'dosya bozuk')['spheres_error'] == 'dosya bozuk'


# ---- sphere jog ---------------------------------------------------------------------------------
def test_azimuth_hold_moves_on_the_sphere_and_looks_at_c(rig):
    az0, el0, r0 = rig.angles()
    doc = rig.cmd(twist=[0.1, 0, 0])
    assert doc['ok'] and doc['frame'] == 'dome' and doc['sphere']['on_sphere'] is True
    for _ in range(20):
        rig.cmd(twist=[0.1, 0, 0])
        rig.tick(3)
    az, el, r = rig.angles()
    assert az - az0 > math.radians(1) and abs(el - el0) < 1e-3 and abs(r - r0) < 2e-3
    assert rig.look_error() < 0.02
    st = rig.jog.status()['sphere']
    assert st['name'] == 'dome' and abs(st['radius_m'] - RADIUS) < 3e-3


def test_elevation_and_radius_hold(rig):
    az0, el0, r0 = rig.angles()
    for _ in range(15):
        rig.cmd(twist=[0, 0.1, 0])
        rig.tick(3)
    assert rig.angles()[1] - el0 > math.radians(1) and abs(rig.angles()[2] - r0) < 2e-3
    r1 = rig.angles()[2]
    for _ in range(15):
        rig.cmd(twist=[0, 0, 0.05])
        rig.tick(3)
    assert rig.angles()[2] - r1 > 2e-3 and rig.look_error() < 0.03


def test_dead_man_stops_the_sphere_jog_by_itself(rig):
    for _ in range(5):
        rig.cmd(twist=[0.1, 0, 0])
        rig.tick(3)
    rig.tick(40)                                               # no more commands: TTL runs out
    n = len(rig.plant.sent)
    az = rig.angles()[0]
    rig.tick(20)
    assert len(rig.plant.sent) == n and rig.angles()[0] == az
    assert not rig.jog.status()['moving']


@pytest.mark.parametrize('axis, amount, index', [
    (0, 10, 0), (0, -1, 0), (1, 1, 1), (1, -10, 1), (2, 10, 2), (2, -1, 2), (0, 0.1, 0)])
def test_increments_land_on_the_requested_amount(rig, axis, amount, index):
    before = np.array(rig.angles())
    rig.cmd(increment={'axis': axis, 'amount': amount})
    for _ in range(1500):
        rig.tick()
        if rig.jog.status()['status'] == 'done':
            break
    assert rig.jog.status()['status'] == 'done'
    delta = np.array(rig.angles()) - before
    want = amount * (math.pi / 180 if axis < 2 else 1e-3)
    assert delta[index] == pytest.approx(want, abs=max(2e-5, abs(want) * 0.02))
    others = [i for i in range(3) if i != index]
    tol = np.array([2e-3, 2e-3, 3e-4])
    assert all(abs(delta[i]) < tol[i] + abs(want) * 0.05 for i in others)


def test_off_sphere_is_refused_with_a_hint_then_align_brings_it_back(tmp_path):
    off = Q_ON.copy()
    off[2] += 0.25                                             # not on the sphere any more
    rig = Rig(tmp_path, q=off)
    rig.cmd(twist=[0.1, 0, 0])
    rig.tick(3)
    status = rig.jog.status()
    assert status['status'] == 'tracking' and 'hizalayın' in ' '.join(status['messages'])
    assert status['sphere']['on_sphere'] is False
    for _ in range(300):
        rig.cmd(align=True)
        rig.tick(3)
        if 'hizalı' in ' '.join(rig.jog.status()['messages']):
            break
    assert 'hizalı' in ' '.join(rig.jog.status()['messages'])
    assert rig.jog.status()['sphere']['on_sphere'] is True and rig.look_error() < 0.01
    rig.cmd(twist=[0.1, 0, 0])
    rig.tick(3)
    assert rig.jog.status()['status'] in ('ok', 'limited')


@pytest.mark.parametrize('body, reason', [
    ({'twist': [0, 0, 0, 0, 0, 0]}, 'küre çerçevesinde twist 3'),
    ({'twist': [0, 0]}, 'küre çerçevesinde twist 3'),
    ({'twist': [float('nan'), 0, 0]}, 'küre çerçevesinde twist 3'),
    ({'twist': [True, 0, 0]}, 'küre çerçevesinde twist 3'),
    ({'twist': [5, 0, 0]}, 'küre çerçevesinde twist 3'),
    ({'twist': [0, 0, 0], 'align': True}, 'tam olarak biri'),
    ({'align': False}, '"align" true olmalı'),
    ({'increment': {'axis': 3, 'amount': 1}}, 'increment'),
    ({'increment': {'axis': 0, 'amount': 5}}, 'artım yalnızca'),
    ({'increment': {'axis': 1, 'amount': 10}}, None),          # fine from 10°, see below
])
def test_sphere_bodies_are_validated(rig, body, reason):
    if reason is None:
        rig.cmd(**body)
        return
    assert reason in denied(rig.cmd, **body).message


def test_world_frame_keeps_six_numbers_and_refuses_align(rig):
    assert denied(rig.jog.command, {'lease': LEASE, 'frame': 'World', 'twist': [0, 0, 0]}
                  ).status == 400
    assert 'yalnız küre' in denied(rig.jog.command, {'lease': LEASE, 'frame': 'World',
                                                      'align': True}).message
    assert rig.jog.command({'lease': LEASE, 'frame': 'World',
                            'twist': [0.01, 0, 0, 0, 0, 0]})['ok']
    assert 'bilinmeyen çerçeve' in denied(rig.jog.command, {
        'lease': LEASE, 'frame': 'nope', 'twist': [0, 0, 0]}).message


def test_elevation_increment_beyond_the_ceiling_is_refused(tmp_path):
    frame = sphere_frame('dome', CENTER, RADIUS, '-z')
    q = solve_sphere_pose(CHAIN, frame, 0.0, math.radians(80), SEED, RADIUS, ROLL)
    assert q is not None
    rig = Rig(tmp_path, q=q)
    assert 'sınırını aşar' in denied(rig.cmd, increment={'axis': 1, 'amount': 10}).message


def test_hold_and_align_follow_the_hold_switch_but_increments_do_not(tmp_path):
    rig = Rig(tmp_path, hold=False)                            # e.g. mode: real
    assert denied(rig.cmd, twist=[0.1, 0, 0]).status == 403
    assert denied(rig.cmd, align=True).status == 403
    assert rig.cmd(increment={'axis': 0, 'amount': 1})['ok']


def test_estop_drops_the_sphere_session_and_answers_423(rig):
    rig.cmd(twist=[0.1, 0, 0])
    rig.tick(3)
    rig.estop.trip('test')
    assert denied(rig.cmd, twist=[0.1, 0, 0]).status == 423
    assert denied(rig.cmd, align=True).status == 423
    n = len(rig.plant.sent)
    rig.tick(10)
    assert len(rig.plant.sent) == n and rig.jog.status()['sphere'] is None


# ---- reachable range ----------------------------------------------------------------------------
def test_reach_describes_the_range_without_moving(rig):
    sent = len(rig.plant.sent)
    doc = sphere_reach(rig.jog, {'center': list(CENTER), 'radius': RADIUS, 'tool_axis': '-z'})
    assert doc['ok'] and doc['reachable'] is True
    lo, hi = doc['elevation_range_deg']
    assert lo <= math.degrees(EL) <= hi and doc['rows'] and doc['summary'].startswith('r=0.700')
    cells = doc['grid']['cells']
    assert len(cells) == len(doc['grid']['elevation_deg'])
    assert all(len(row) == len(doc['grid']['azimuth_deg']) for row in cells)
    assert len(rig.plant.sent) == sent and not rig.jog.active and 'çarpışma' in doc['note']


def test_unreachable_sphere_and_bad_bodies(rig):
    doc = sphere_reach(rig.jog, {'center': [0.0, 0.0, 0.2], 'radius': 2.0})
    assert doc['ok'] and doc['reachable'] is False and doc['elevation_range_deg'] is None
    for body in ({}, {'center': [0, 0, 0]}, {'center': [0, 0, 0], 'radius': 0.01},
                 {'center': [0, 0], 'radius': 0.3}, {'center': [0, 0, 0], 'radius': 0.3, 'x': 1},
                 {'center': [0, 0, 0], 'radius': 0.3, 'tool_axis': 'w'}, 'x'):
        assert denied(sphere_reach, rig.jog, body).status == 400


def test_reach_is_refused_while_jogging_in_estop_or_without_joints(rig):
    body = {'center': list(CENTER), 'radius': RADIUS}
    rig.cmd(twist=[0.1, 0, 0])
    assert denied(sphere_reach, rig.jog, body).status == 409
    rig.estop.trip('test')
    assert denied(sphere_reach, rig.jog, body).status == 423


def test_a_second_reach_computation_is_refused_not_queued(rig):
    assert rig.jog._reach_lock.acquire(blocking=False)
    try:
        assert denied(sphere_reach, rig.jog, {'center': list(CENTER), 'radius': RADIUS}
                      ).status == 409
    finally:
        rig.jog._reach_lock.release()


# ---- HTTP ---------------------------------------------------------------------------------------
class Source:
    def snapshot(self):
        return Snapshot()


class Http:
    def __init__(self, tmp_path):
        self.rig = Rig(tmp_path, with_dome=False)
        self.rig.clock = Clock()
        runner = CommandRunner(CancelAwareSkills(), FakeTool((0, 0, 1)), RecordingSink(),
                               estop=self.rig.estop)
        self.view = WebView(Source(), port=0, commands=runner, jog=self.rig.jog,
                            frames=self.rig.frames, spheres=self.rig.spheres)
        self.view.start_background()

    def req(self, method, path, payload=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=30)
        body = None if payload is None else json.dumps(payload).encode()
        try:
            conn.request(method, path, body=body, headers={'Content-Type': 'application/json'})
            r = conn.getresponse()
            data = r.read()
            return r.status, (json.loads(data) if data else None), dict(r.getheaders())
        finally:
            conn.close()

    def close(self):
        self.view.shutdown()


@pytest.fixture
def web(tmp_path):
    w = Http(tmp_path)
    yield w
    w.close()


def test_http_put_get_old_client_and_errors(web):
    status, doc, _ = web.req('GET', '/api/frames')
    assert status == 200 and doc['types'] == ['user', 'sphere'] and doc['spheres'] == []
    status, doc, _ = web.req('PUT', '/api/frames', {'frames': [], 'spheres': [DOME]})
    assert status == 200 and doc['spheres'] == [DOME]
    assert web.req('PUT', '/api/frames', {'frames': [{'name': 't', 'points': THREE}]})[0] == 200
    assert web.req('GET', '/api/frames')[1]['spheres'] == [DOME]       # old client: kept
    status, doc, _ = web.req('PUT', '/api/frames', {'frames': [{'type': 'torus', 'name': 'x',
                                                                'points': THREE}]})
    assert status == 400 and 'torus' in doc['error']
    assert web.req('GET', '/api/frames')[1]['spheres'] == [DOME]


def test_http_reach_post_only_and_errors(web):
    status, doc, _ = web.req('POST', '/api/frames/reach', {'center': list(CENTER),
                                                           'radius': RADIUS, 'tool_axis': '-z'})
    assert status == 200 and doc['reachable'] is True
    status, _, headers = web.req('GET', '/api/frames/reach')
    assert status == 405 and headers['Allow'] == 'POST'
    assert web.req('POST', '/api/frames/reach', {'center': [0, 0, 0]})[0] == 400
    assert web.req('POST', '/api/frames/reach', {'padding': 'x' * 600})[0] == 413
    web.rig.estop.trip('test')
    assert web.req('POST', '/api/frames/reach', {'center': list(CENTER), 'radius': RADIUS}
                   )[0] == 423


def test_http_frames_put_is_refused_while_jogging(web):
    web.req('PUT', '/api/frames', {'frames': [], 'spheres': [DOME]})
    web.rig.jog.command({'lease': LEASE, 'frame': 'dome', 'align': True})
    status, doc, _ = web.req('PUT', '/api/frames', {'frames': [], 'spheres': []})
    assert status == 409 and web.req('GET', '/api/frames')[1]['spheres'] == [DOME]
