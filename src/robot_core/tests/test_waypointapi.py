"""O2: /api/waypoints, /api/waypoints/record and the goto_waypoint job over HTTP."""

import http.client
import json
import time

import numpy as np
import pytest
from robot_core.frames import FrameStore
from robot_core.jobs import JobManager
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.jogapi import READY_POSE, JogController
from robot_core.kinematics import Chain
from robot_core.models import MotionResult
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.supervisor import CancelFlag, EStop, Supervisor
from robot_core.waypointapi import WaypointApi
from robot_core.waypoints import WaypointStore
from robot_core.webcmd import CommandError, CommandRunner, parse_command
from robot_core.webview import Snapshot, WebView

from tests.fakes import CancelAwareSkills, FakeArm, FakeMotion, FakeTool, RecordingSink

P = load_profile(PROFILE_PATH)
CHAIN = Chain(P)
NAMES = CHAIN.joint_names
ZONE = P.safety.zone
Q0 = (0.15, -0.231, 1.8, -0.471, 1.404, 1.131)  # TCP ~ (0.45, 0, 0.45), inside the box
HOME = (0.0,) * 6  # TCP z=1.24: outside the box


class Source:
    def snapshot(self):
        return Snapshot()


class Plant:
    def __init__(self, q):
        self.q = np.array(q, float)

    def joint_positions(self):
        return dict(zip(NAMES, self.q.tolist()))

    def send(self, names, positions, times_s):
        self.q = np.array(positions[0])

    def hold(self, names, position):
        pass


class Rig:
    def __init__(self, tmp_path, q=Q0, motion=None, with_frames=True):
        self.estop, self.cancel, self.sink = EStop(), CancelFlag(), RecordingSink()
        self.motion = motion or FakeMotion()
        self.skills = CancelAwareSkills(self.cancel, self.estop)
        self.arm = FakeArm(NAMES)
        self.frames = FrameStore(tmp_path / 'frames.yaml') if with_frames else None
        self.store = WaypointStore(tmp_path / 'waypoints.yaml', CHAIN, ZONE, frames=self.frames)
        self.runner = CommandRunner(
            self.skills,
            FakeTool((0, 0, 1)),
            self.sink,
            estop=self.estop,
            waypoints=self.store,
            joint_motion=self.motion,
        )
        self.jobs = JobManager(self.runner, self.cancel, self.estop)
        self.plant = Plant(q)
        self.jog = JogController(
            DlsJogEngine(CHAIN, P.safety, JogConfig()),
            CHAIN,
            self.plant,
            self.plant,
            self.estop,
            self.frames or FrameStore(tmp_path / 'f.yaml'),
            busy=lambda: self.jobs.is_running,
        )
        self.jobs.jog_gate = lambda: self.jog.active
        self.api = WaypointApi(self.store, self.plant, self._busy)
        self.supervisor = Supervisor(
            self.estop, self.arm, NAMES, stop_port=self.arm, runner=self.runner,
            events=self.sink, jog=self.jog)
        self.view = WebView(
            Source(), port=0, commands=self.runner, supervisor=self.supervisor, jobs=self.jobs,
            jog=self.jog, frames=self.frames, waypoints=self.api)
        self.view.start_background()
        self.jog.start()
        self.conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=10)

    def _busy(self):
        if self.jobs.is_running:
            return 'bir iş çalışıyor'
        if self.jog.active:
            return 'jog etkin'
        return None

    def req(self, method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        h = {'Content-Type': 'application/json'} if body is not None else {}
        h.update(headers or {})
        self.conn.request(method, path, body=body, headers=h)
        r = self.conn.getresponse()
        data = r.read()
        return r.status, json.loads(data) if data else None

    def wait_job(self, job_id, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            _, doc = self.req('GET', f'/api/jobs/{job_id}')
            if doc['state'] != 'running':
                return doc
            time.sleep(0.02)
        raise AssertionError('iş bitmedi')

    def close(self):
        self.jog.close()
        self.view.shutdown()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


# ---- CRUD ---------------------------------------------------------------------------------------
def test_empty_list_and_document_shape(rig):
    status, doc = rig.req('GET', '/api/waypoints')
    assert status == 200 and doc['waypoints'] == [] and doc['joint_names'] == list(NAMES)
    assert doc['frames'] == ['World'] and doc['max_waypoints'] >= 10


def test_put_creates_then_replaces_and_get_returns_it(rig):
    status, doc = rig.req('PUT', '/api/waypoints/pick_a', {'joints': list(Q0)})
    assert status == 201 and doc['name'] == 'pick_a' and doc['replaced'] is False
    assert doc['tcp']['frame'] == 'World' and len(doc['tcp']['quaternion_xyzw']) == 4
    status, doc = rig.req('PUT', '/api/waypoints/pick_a', {'joints': list(READY_POSE)})
    assert status == 200 and doc['replaced'] is True and doc['joints'] == list(READY_POSE)
    status, doc = rig.req('GET', '/api/waypoints/pick_a')
    assert status == 200 and doc['joints'] == list(READY_POSE)
    assert rig.req('GET', '/api/waypoints')[1]['waypoints'][0]['name'] == 'pick_a'
    assert WaypointStore(rig.store.path, CHAIN, ZONE).names() == ['pick_a']      # on disk


def test_put_refuses_limits_and_outside_the_box_and_writes_nothing(rig):
    bad = list(Q0)
    bad[0] = float(CHAIN.upper[0]) + 0.1
    for joints, text in ((bad, 'limit'), (list(HOME), 'kutusunun dışında'),
                         ([0.0] * 5, 'sayı'), ('x', 'sayı')):
        status, doc = rig.req('PUT', '/api/waypoints/p', {'joints': joints})
        assert status == 400 and text in doc['error'], (joints, doc)
    assert not rig.store.path.exists() and rig.store.names() == []


def test_put_body_and_name_rules(rig):
    for body in ({}, {'joints': list(Q0), 'x': 1}, {'frame': 'World'}, [1], 'a',
                 {'joints': list(Q0), 'rename_to': 'b'}):
        assert rig.req('PUT', '/api/waypoints/p', body)[0] == 400, body
    assert rig.req('PUT', '/api/waypoints/1bad', {'joints': list(Q0)})[0] == 400
    assert rig.req('PUT', '/api/waypoints/record', {'joints': list(Q0)})[0] == 405   # the POST door
    assert rig.req('GET', '/api/waypoints/record')[0] == 405
    assert rig.req('DELETE', '/api/waypoints/record')[0] == 405
    assert rig.req('PUT', '/api/waypoints/p', {'joints': list(Q0), 'frame': 'Nope'})[0] == 400
    assert rig.req('PUT', '/api/waypoints/p', {'joints': list(Q0), 'frame': 5})[0] == 400


def test_get_unknown_and_delete(rig):
    assert rig.req('GET', '/api/waypoints/nope')[0] == 404
    rig.req('PUT', '/api/waypoints/a', {'joints': list(Q0)})
    status, doc = rig.req('DELETE', '/api/waypoints/a')
    assert status == 200 and doc['deleted'] == 'a' and doc['count'] == 0
    assert rig.req('DELETE', '/api/waypoints/a')[0] == 404
    assert rig.req('GET', '/api/waypoints/a')[0] == 404


def test_rename(rig):
    rig.req('PUT', '/api/waypoints/a', {'joints': list(Q0)})
    rig.req('PUT', '/api/waypoints/b', {'joints': list(READY_POSE)})
    status, doc = rig.req('PUT', '/api/waypoints/a', {'rename_to': 'c'})
    assert status == 200 and doc['name'] == 'c' and doc['joints'] == list(Q0)
    assert rig.req('PUT', '/api/waypoints/c', {'rename_to': 'b'})[0] == 409
    assert rig.req('PUT', '/api/waypoints/zzz', {'rename_to': 'q'})[0] == 404
    assert rig.req('PUT', '/api/waypoints/c', {'rename_to': '9x'})[0] == 400
    assert rig.req('PUT', '/api/waypoints/c', {'rename_to': 'record'})[0] == 400
    assert [w['name'] for w in rig.req('GET', '/api/waypoints')[1]['waypoints']] == ['c', 'b']


def test_count_limit_is_409(rig):
    rig.store.max_waypoints = 2
    for n in ('a', 'b'):
        assert rig.req('PUT', f'/api/waypoints/{n}', {'joints': list(Q0)})[0] == 201
    assert rig.req('PUT', '/api/waypoints/c', {'joints': list(Q0)})[0] == 409
    assert rig.req('PUT', '/api/waypoints/a', {'joints': list(READY_POSE)})[0] == 200


def test_edits_are_allowed_while_the_estop_is_latched(rig):
    rig.estop.trip('test')
    assert rig.req('PUT', '/api/waypoints/a', {'joints': list(Q0)})[0] == 201
    assert rig.req('DELETE', '/api/waypoints/a')[0] == 200


# ---- record -------------------------------------------------------------------------------------
def test_record_saves_the_measured_pose(rig):
    status, doc = rig.req('POST', '/api/waypoints/record', {'name': 'here'})
    assert status == 201 and doc['source'] == 'measured'
    np.testing.assert_allclose(doc['joints'], Q0, atol=1e-9)
    np.testing.assert_allclose(doc['tcp']['position'], CHAIN.fk(Q0)[:3, 3], atol=1e-9)
    assert rig.req('POST', '/api/waypoints/record', {'name': 'here'})[0] == 409     # exists
    rig.plant.q = np.array(READY_POSE)
    status, doc = rig.req('POST', '/api/waypoints/record', {'name': 'here', 'overwrite': True})
    assert status == 200 and doc['replaced'] and doc['joints'] == list(READY_POSE)


def test_record_refuses_a_pose_outside_the_box_or_the_limits(tmp_path):
    for q in (HOME, np.array(Q0) + np.array([5, 0, 0, 0, 0, 0])):
        r = Rig(tmp_path / str(len(str(q))), q=q)
        try:
            status, doc = r.req('POST', '/api/waypoints/record', {'name': 'x'})
            assert status == 400
            assert "kutusunun dışında" in doc["error"] or "limit" in doc["error"]
            assert r.store.names() == [] and not r.store.path.exists()
        finally:
            r.close()


def test_record_needs_joint_state(tmp_path):
    r = Rig(tmp_path)
    try:
        r.plant.joint_positions = lambda: None
        assert r.req('POST', '/api/waypoints/record', {'name': 'x'})[0] == 503
        r.plant.joint_positions = lambda: {NAMES[0]: float('nan'), **{n: 0.0 for n in NAMES[1:]}}
        assert r.req('POST', '/api/waypoints/record', {'name': 'x'})[0] == 503
    finally:
        r.close()


def test_record_bad_bodies(rig):
    for body in ({}, {'name': 'a', 'x': 1}, {'name': 5}, {'name': 'a', 'overwrite': 'yes'},
                 {'name': 'a', 'frame': ''}, {'name': 'record'}, [1]):
        assert rig.req('POST', '/api/waypoints/record', body)[0] == 400, body


def test_record_is_refused_while_a_job_runs_or_jogging(rig):
    import threading
    release = threading.Event()
    rig.skills.release = release
    status, job = rig.req('POST', '/api/jobs', {'command': 'above red'})
    assert status == 202
    status, doc = rig.req('POST', '/api/waypoints/record', {'name': 'x'})
    assert status == 409 and 'iş' in doc['error']
    release.set()
    rig.wait_job(job['id'])
    status, _ = rig.req('POST', '/api/jog', {'lease': 'client-aaaaaaaa', 'frame': 'World',
                                             'increment': {'axis': 0, 'amount': 1},
                                             'ttl_ms': 250})
    assert status == 200
    status, doc = rig.req('POST', '/api/waypoints/record', {'name': 'x'})
    assert status == 409 and 'jog' in doc['error']


# ---- goto_waypoint ------------------------------------------------------------------------------
def test_parse_goto_waypoint():
    c = parse_command('goto_waypoint pick_a')
    assert (c.verb, c.target) == ('goto_waypoint', 'pick_a') and c.line == 'goto_waypoint pick_a'
    for bad in ('goto_waypoint', 'goto_waypoint a b', 'goto_waypoint 1x', 'goto_waypoint a-b',
                'goto_waypoint ' + 'a' * 40, 'goto_waypoint a;b'):
        with pytest.raises(CommandError):
            parse_command(bad)
    assert 'goto_waypoint <name>' in CommandRunner(CancelAwareSkills(), FakeTool((0, 0, 1))).allowed


def test_goto_waypoint_asks_moveit_for_a_joint_goal_of_exactly_the_saved_joints(rig):
    rig.req('PUT', '/api/waypoints/pick_a', {'joints': list(READY_POSE)})
    status, job = rig.req('POST', '/api/jobs', {'command': 'goto_waypoint pick_a'})
    assert status == 202
    doc = rig.wait_job(job['id'])
    assert doc['state'] == 'done', doc
    (target,) = rig.motion.targets
    assert target.values == READY_POSE and target.label == 'pick_a'
    assert rig.skills.calls == [] or all(c[0] != 'go_to_named_pose' for c in rig.skills.calls)


def test_goto_unknown_waypoint_fails_without_moving(rig):
    status, job = rig.req('POST', '/api/jobs', {'command': 'goto_waypoint nope'})
    doc = rig.wait_job(job['id'])
    assert doc['state'] == 'failed' and 'nokta yok' in doc['error'] and rig.motion.targets == []


def test_goto_rechecks_the_box_and_limits_at_run_time(rig):
    rig.req('PUT', '/api/waypoints/pick_a', {'joints': list(Q0)})
    from dataclasses import replace
    rig.store.zone = replace(ZONE, upper=(ZONE.lower[0] + 0.01,) + tuple(ZONE.upper[1:]))
    status, job = rig.req('POST', '/api/jobs', {'command': 'goto_waypoint pick_a'})
    doc = rig.wait_job(job['id'])
    assert doc['state'] == 'failed' and 'kutusunun dışında' in doc['error']
    assert rig.motion.targets == []


def test_goto_reports_a_moveit_refusal_and_no_motion_claim(tmp_path):
    bad = MotionResult(ok=False, code=-1, code_name='GOAL_IN_COLLISION')
    r = Rig(tmp_path, motion=FakeMotion(bad))
    try:
        r.req('PUT', '/api/waypoints/pick_a', {'joints': list(Q0)})
        status, job = r.req('POST', '/api/jobs', {'command': 'goto_waypoint pick_a'})
        doc = r.wait_job(job['id'])
        assert doc['state'] == 'failed' and 'GOAL_IN_COLLISION' in doc['error']
        assert 'Robot hareket etmedi' in doc['error']
    finally:
        r.close()


def test_goto_is_423_under_estop_409_while_jogging_or_busy(rig):
    rig.req('PUT', '/api/waypoints/pick_a', {'joints': list(Q0)})
    status, _ = rig.req('POST', '/api/jog', {'lease': 'client-aaaaaaaa', 'frame': 'World',
                                             'increment': {'axis': 0, 'amount': 1},
                                             'ttl_ms': 250})
    assert status == 200
    status, doc = rig.req('POST', '/api/jobs', {'command': 'goto_waypoint pick_a'})
    assert status == 409 and doc['state'] == 'jogging' and rig.motion.targets == []
    rig.estop.trip('test')
    status, doc = rig.req('POST', '/api/jobs', {'command': 'goto_waypoint pick_a'})
    assert status == 423 and rig.motion.targets == []


def test_without_a_store_goto_fails_cleanly(tmp_path):
    r = Rig(tmp_path)
    try:
        r.runner.waypoints = None
        status, job = r.req('POST', '/api/jobs', {'command': 'goto_waypoint a'})
        assert r.wait_job(job['id'])['state'] == 'failed'
    finally:
        r.close()


# ---- guards -------------------------------------------------------------------------------------
def test_writes_need_json_and_the_same_origin(rig):
    rig.conn.request('PUT', '/api/waypoints/a', body=b'{"joints": []}',
                     headers={'Content-Type': 'text/plain'})
    assert rig.conn.getresponse().status == 415
    rig.conn.close()
    status, doc = rig.req('PUT', '/api/waypoints/a', {'joints': list(Q0)},
                          headers={'Origin': 'http://evil.example'})
    assert status == 403
    rig.conn.close()
    status, _ = rig.req('POST', '/api/waypoints/record', {'name': 'a'},
                        headers={'Sec-Fetch-Site': 'cross-site'})
    assert status == 403
    rig.conn.close()
    assert rig.store.names() == []


def test_foreign_host_header_is_refused(rig):
    rig.conn.request('GET', '/api/waypoints', headers={'Host': 'evil.example'})
    assert rig.conn.getresponse().status == 403


def test_delete_with_a_body_and_oversized_body_are_refused(rig):
    rig.req('PUT', '/api/waypoints/a', {'joints': list(Q0)})
    rig.conn.request('DELETE', '/api/waypoints/a', body=b'{}',
                     headers={'Content-Type': 'application/json'})
    assert rig.conn.getresponse().status == 400
    rig.conn.close()
    big = {'joints': list(Q0), 'pad': 'x' * 2000}
    assert rig.req('PUT', '/api/waypoints/b', big)[0] == 413


def test_closed_and_broken_store_answers(tmp_path):
    r = Rig(tmp_path)
    try:
        r.view.waypoints = None
        assert r.req('GET', '/api/waypoints')[0] == 403
        r.view.waypoints_error = 'dosya bozuk'
        status, doc = r.req('GET', '/api/waypoints')
        assert status == 503 and 'dosya bozuk' in doc['error']
        assert r.req('POST', '/api/waypoints/record', {'name': 'a'})[0] == 503
    finally:
        r.close()


def test_the_list_endpoint_has_no_write_verbs(rig):
    assert rig.req('PUT', '/api/waypoints', {'joints': list(Q0)})[0] == 405
    assert rig.req('DELETE', '/api/waypoints')[0] == 405
    assert rig.req('POST', '/api/waypoints', {'name': 'a'})[0] == 405


# ---- frames <-> waypoints -----------------------------------------------------------------------
FRAME = {'name': 'bench',
         'points': {'origin': [0.3, 0.1, 0.2], 'x': [0.4, 0.1, 0.2], 'xy': [0.3, 0.2, 0.2]}}


def test_a_frame_with_waypoints_cannot_be_removed_or_changed(rig):
    assert rig.req('PUT', '/api/frames', {'frames': [FRAME]})[0] == 200
    assert rig.req('PUT', '/api/waypoints/p', {'joints': list(Q0), 'frame': 'bench'})[0] == 201
    status, doc = rig.req('PUT', '/api/frames', {'frames': []})
    assert status == 409 and 'p' in doc['error']
    moved = {**FRAME, 'points': {**FRAME['points'], 'origin': [0.31, 0.1, 0.2]}}
    assert rig.req('PUT', '/api/frames', {'frames': [moved]})[0] == 409
    assert rig.req('PUT', '/api/frames', {'frames': [FRAME]})[0] == 200          # same: fine
    assert [f['name'] for f in rig.req('GET', '/api/frames')[1]['frames']] == ['bench']
    rig.req('DELETE', '/api/waypoints/p')
    assert rig.req('PUT', '/api/frames', {'frames': []})[0] == 200
    WaypointStore(rig.store.path, CHAIN, ZONE, frames=rig.frames)               # file still loads


def test_waypoint_in_a_taught_frame_is_listed_with_its_frame(rig):
    rig.req('PUT', '/api/frames', {'frames': [FRAME]})
    doc = rig.req('GET', '/api/waypoints')[1]
    assert doc['frames'] == ['World', 'bench']
    rig.req('PUT', '/api/waypoints/p', {'joints': list(Q0), 'frame': 'bench'})
    assert rig.req('GET', '/api/waypoints/p')[1]['tcp']['frame'] == 'bench'
