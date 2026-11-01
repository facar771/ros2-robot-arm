"""POST /api/stop, /api/reset and the 423 lock over real HTTP (fake skills and arm)."""
import http.client
import json
import threading

import pytest
from robot_core.supervisor import EStop, Supervisor
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.fakes import FakeArm, FakeSkills, FakeTool, RecordingSink

JSON = {'Content-Type': 'application/json'}
NAMES = ('joint_1', 'joint_2')


class Source:
    def snapshot(self):
        return Snapshot()


class Rig:
    def __init__(self, arm=None, skills=None):
        self.arm = arm or FakeArm(NAMES)
        self.skills = skills or FakeSkills()
        self.sink = RecordingSink()
        self.estop = EStop()
        self.runner = CommandRunner(self.skills, FakeTool((0, 0, 1)), self.sink, estop=self.estop)
        self.supervisor = Supervisor(self.estop, self.arm, NAMES, stop_port=self.arm,
                                     runner=self.runner, mode='gazebo', events=self.sink)
        self.view = WebView(Source(), port=0, commands=self.runner, supervisor=self.supervisor,
                            mode='gazebo')
        self.view.start_background()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=20)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), json.loads(data) if data else None
        finally:
            conn.close()

    def post(self, path, payload=None, headers=JSON):
        body = None if payload is None else json.dumps(payload).encode()
        return self.request('POST', path, body, headers)

    def robot(self):
        return self.request('GET', '/api/state')[2]['robot']


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.skills.block and r.skills.block.set()
    r.view.shutdown()


def test_state_has_the_v1_robot_block(rig):
    status, _, body = rig.request('GET', '/api/state')
    assert status == 200 and body['api_version'] == 1
    robot = body['robot']
    assert robot['state'] == 'idle' and robot['mode'] == 'gazebo' and robot['moving'] is False
    assert robot['estop'] == {'tripped': False, 'reason': None, 'since': None}


def test_stop_without_any_body_works_and_latches(rig):
    status, _, body = rig.request('POST', '/api/stop')             # no body, no Content-Type
    assert status == 200 and body['ok'] and body['stopped'] and body['latched']
    assert body['state'] == 'estopped' and rig.robot()['state'] == 'estopped'
    assert rig.robot()['estop']['tripped'] is True


def test_stop_takes_a_reason_and_ignores_garbage_bodies(rig):
    assert rig.post('/api/stop', {'reason': 'ESP32 butonu'})[0] == 200
    assert rig.robot()['estop']['reason'] == 'ESP32 butonu'
    status, _, body = rig.request('POST', '/api/stop', b'{not json', JSON)
    assert status == 200 and body['ok']                            # a stop is never refused


def test_stop_during_a_running_command_is_not_blocked_and_ends_it():
    release = threading.Event()
    skills = FakeSkills(block=release)
    skills.entered = threading.Event()
    arm = FakeArm(NAMES, speed=0.7)
    arm_stop = arm.stop
    arm.stop = lambda: (arm_stop(), release.set())                 # cancelling frees the command
    rig = Rig(arm, skills)
    try:
        answers = []
        body = {'command': 'above green 0.25'}
        worker = threading.Thread(target=lambda: answers.append(rig.post('/api/command', body)))
        worker.start()
        assert skills.entered.wait(5) and rig.robot()['state'] == 'busy'
        assert rig.robot()['current_job']['command'] == 'above green 0.25'
        status, _, body = rig.post('/api/stop', {})                # runner busy: still answers
        worker.join(10)
        assert status == 200 and body['stopped'] and body['was_running']
        assert answers and answers[0][0] == 200                    # the fake finished normally
        assert rig.robot()['state'] == 'estopped'
    finally:
        release.set()
        rig.view.shutdown()


def test_commands_are_423_while_latched_and_never_reach_the_robot(rig):
    rig.post('/api/stop', {})
    for command in ('home', 'above green 0.25', 'demo_scenario', 'objects'):
        status, _, body = rig.post('/api/command', {'command': command})
        assert status == 423 and body['state'] == 'estopped' and 'reset' in body['error']
    assert rig.skills.calls == []
    kinds = [k for k, _ in rig.sink.events]
    assert kinds.count('command_rejected') == 4 and 'estop' in kinds


def test_invalid_commands_are_still_400_while_latched(rig):
    rig.post('/api/stop', {})
    assert rig.post('/api/command', {'command': 'goto 1 2 3'})[0] == 400


def test_reset_needs_json_and_an_empty_object(rig):
    rig.post('/api/stop', {})
    assert rig.request('POST', '/api/reset', b'{}', {'Content-Type': 'text/plain'})[0] == 415
    assert rig.post('/api/reset', {'x': 1})[0] == 400
    assert rig.request('POST', '/api/reset', b'{}', JSON)[0] == 200
    assert rig.robot()['state'] == 'idle'


def test_reset_is_refused_while_the_arm_moves_and_succeeds_after(rig):
    rig.post('/api/stop', {})
    rig.arm.speed = 0.4
    status, _, body = rig.post('/api/reset', {})
    assert status == 409 and not body['ok'] and 'hareket' in body['error']
    assert body['state'] == 'estopped' and rig.post('/api/command', {'command': 'home'})[0] == 423
    rig.arm.speed = 0.0
    status, _, body = rig.post('/api/reset', {})
    assert status == 200 and body['changed'] and body['state'] == 'idle'
    assert rig.post('/api/command', {'command': 'home'})[0] == 200


def test_reset_when_nothing_is_wrong_is_a_no_op(rig):
    status, _, body = rig.post('/api/reset', {})
    assert status == 200 and body['changed'] is False and body['state'] == 'idle'


@pytest.mark.parametrize('path', ['/api/stop', '/api/reset'])
def test_only_post_is_allowed(rig, path):
    for method in ('GET', 'PUT', 'DELETE'):
        status, headers, _ = rig.request(method, path, b'{}' if method != 'GET' else None, JSON)
        assert status == 405 and headers['Allow'] == 'POST'


@pytest.mark.parametrize('path', ['/api/stop', '/api/reset'])
@pytest.mark.parametrize('headers', [
    {'Origin': 'http://evil.example', **JSON}, {'Origin': 'null', **JSON},
    {'Sec-Fetch-Site': 'cross-site', **JSON},
])
def test_cross_site_requests_cannot_stop_or_reset(rig, path, headers):
    assert rig.post(path, {}, headers)[0] == 403
    assert rig.robot()['estop']['tripped'] is False


@pytest.mark.parametrize('path', ['/api/stop', '/api/reset'])
def test_foreign_host_header_is_403(rig, path):
    assert rig.post(path, {}, {'Host': 'evil.example', **JSON})[0] == 403


def test_stop_and_reset_are_403_without_commands():
    view = WebView(Source(), port=0)
    view.start_background()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=5)
        for path in ('/api/stop', '/api/reset'):
            conn.request('POST', path, body=b'{}', headers=JSON)
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 403
            conn.close()
            conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=5)
        conn.close()
    finally:
        view.shutdown()
