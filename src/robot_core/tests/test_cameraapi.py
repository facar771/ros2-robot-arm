"""H4: /api/camera (pan/tilt/slide platform) - body rules, node-reply mapping, e-stop, frames."""

import http.client
import json
import logging

import numpy as np
import pytest
from robot_core.cameraapi import CameraApi, CameraStop
from robot_core.supervisor import EStop, Supervisor
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.fakes import CancelAwareSkills, FakeArm, FakeTool, RecordingSink

LIMITS = {'pan': [-1.2, 1.2], 'tilt': [-0.6, 1.2], 'slide': [0.0, 2.5],
          'rate_caps': {'pan': 0.75, 'tilt': 0.5, 'slide': 0.4}}
Q = {'pan': 0.1, 'tilt': 0.2, 'slide': 1.0}


class FakePort:
    def __init__(self):
        self.sent = []
        self.stops = 0
        self.age = 0.1
        self.state = 'idle'
        self.q = dict(Q)
        self.present = True
        self.reply = None                       # None = answer 'accepted'; dict = fixed reply
        self.silent = False
        self.on_send = None

    def send(self, command, timeout_s=1.0):
        self.sent.append(command)
        if self.on_send:
            self.on_send(command)
        if self.silent:
            return None
        if command['op'] == 'stop':
            self.stops += 1
        return self.reply or {'accepted': True, 'op': command['op'], 'code': 'ok', 'message': ''}

    def publish_stop(self):
        self.stops += 1

    def status(self):
        if not self.present:
            return None
        return {'state': self.state, 'q': self.q, 'last': {'op': 'goto'}, 'tracking_busy': False,
                'age_s': self.age}

    def limits(self):
        return LIMITS


class Rig:
    def __init__(self, with_supervisor=False):
        self.estop = EStop()
        self.port = FakePort()
        self.api = CameraApi(self.port, self.estop)
        self.arm = FakeArm(['joint_1'])
        kwargs = {}
        if with_supervisor:
            kwargs['supervisor'] = Supervisor(
                self.estop, self.arm, ['joint_1'], stop_port=CameraStop(self.arm, self.port),
                events=RecordingSink())
        self.runner = CommandRunner(CancelAwareSkills(), FakeTool((0, 0, 1)), RecordingSink(),
                                    estop=self.estop)
        self.view = WebView(Source(), port=0, commands=self.runner, camera=self.api, **kwargs)
        self.view.start_background()

    def req(self, method, path, payload=None, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=10)
        body = raw if raw is not None else json.dumps(payload).encode()
        h = {'Content-Type': 'application/json'} if body is not None else {}
        h.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            data = r.read()
            try:
                return r.status, json.loads(data) if data else None
            except ValueError:
                return r.status, data
        finally:
            conn.close()

    def post(self, payload, **kw):
        return self.req('POST', '/api/camera', payload, **kw)

    def close(self):
        self.view.shutdown()


class Source:
    def snapshot(self):
        return Snapshot()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def test_status_document(rig):
    status, doc = rig.req('GET', '/api/camera')
    assert status == 200 and doc['available'] is True and doc['estop'] is False
    assert doc['q'] == Q and doc['state'] == 'idle' and doc['limits'] == LIMITS
    assert 'AŞAĞI' in doc['tilt_note']
    rig.port.age = 5.0
    assert rig.req('GET', '/api/camera')[1]['available'] is False        # node went quiet
    assert rig.req('GET', '/api/camera')[1]['q'] is None
    rig.port.present = False
    doc = rig.req('GET', '/api/camera')[1]
    assert doc['available'] is False and doc['status_age_s'] is None


def test_goto_is_passed_through_absolute(rig):
    status, doc = rig.post({'op': 'goto', 'pan': 0.3, 'tilt': 0.1, 'slide': 1.5, 'speed': 0.5})
    assert status == 200 and doc['ok'] and rig.port.sent[-1] == {
        'op': 'goto', 'pan': 0.3, 'tilt': 0.1, 'slide': 1.5, 'speed': 0.5}
    assert rig.post({'op': 'goto', 'pan': 0})[0] == 200 and rig.port.sent[-1] == {
        'op': 'goto', 'pan': 0.0}


def test_step_is_relative_to_the_measured_pose(rig):
    status, _ = rig.post({'op': 'step', 'pan': 0.2, 'slide': -0.5})
    assert status == 200
    cmd = rig.port.sent[-1]
    assert cmd['op'] == 'goto' and cmd['pan'] == pytest.approx(0.3) \
        and cmd['slide'] == pytest.approx(0.5) and 'tilt' not in cmd
    rig.port.age = 9.0                                       # no fresh pose: no relative command
    status, doc = rig.post({'op': 'step', 'pan': 0.1})
    assert status == 503 and 'okunamıyor' in doc['error']
    assert len(rig.port.sent) == 1
    rig.port.age, rig.port.q = 0.1, {'pan': None, 'tilt': None, 'slide': None}
    assert rig.post({'op': 'step', 'pan': 0.1})[0] == 503


def test_jog_ttl_is_clamped_on_the_server(rig):
    for sent, expect in ((None, 0.25), (5000, 0.5), (1, 0.05), (400, 0.4)):
        body = {'op': 'jog', 'pan_rate': 0.2}
        if sent is not None:
            body['ttl_ms'] = sent
        assert rig.post(body)[0] == 200
        assert rig.port.sent[-1]['ttl_s'] == pytest.approx(expect)
    assert rig.post({'op': 'jog'})[0] == 400                 # needs a rate
    assert rig.post({'op': 'jog', 'ttl_ms': 100})[0] == 400


@pytest.mark.parametrize('body', [
    None, [], 'x', {}, {'op': 'fly'}, {'op': 5},
    {'op': 'goto'}, {'op': 'goto', 'pan': 'a'}, {'op': 'goto', 'pan': True},
    {'op': 'goto', 'pan': None}, {'op': 'goto', 'pan': 0.1, 'extra': 1},
    {'op': 'goto', 'pan': 0.1, 'speed': 0}, {'op': 'goto', 'pan': 0.1, 'speed': 2},
    {'op': 'goto', 'pan_rate': 0.1}, {'op': 'jog', 'pan': 0.1},
    {'op': 'jog', 'pan_rate': float('inf')}, {'op': 'step'}, {'op': 'stop', 'x': 1}])
def test_bad_bodies_are_400_and_never_reach_the_node(rig, body):
    if isinstance(body, dict) and body.get('op') == 'stop':
        pytest.skip('stop ignores extra keys by design')         # see test below
    status, doc = rig.post(body)
    assert status == 400 and doc['error'], (body, status, doc)
    assert rig.port.sent == []


def test_nan_and_infinity_literals_are_refused(rig):
    for raw in (b'{"op":"goto","pan":NaN}', b'{"op":"goto","pan":Infinity}',
                b'{"op":"jog","pan_rate":-Infinity}'):
        assert rig.post(None, raw=raw)[0] == 400
    assert rig.port.sent == []


def test_node_refusals_are_mapped(rig):
    for code, http_status in (('out_of_limits', 400), ('rate_too_high', 400), ('busy', 409),
                              ('no_state', 503), ('bad_command', 400), ('weird', 400)):
        rig.port.reply = {'accepted': False, 'op': 'goto', 'code': code, 'message': 'm'}
        status, doc = rig.post({'op': 'goto', 'pan': 5.0})
        assert status == http_status and doc['code'] == code and code in doc['error'], code
    rig.port.silent = True
    status, doc = rig.post({'op': 'goto', 'pan': 0.1})
    assert status == 504 and 'yanıt vermedi' in doc['error']


def test_stop_is_always_accepted_and_idempotent(rig):
    assert rig.post({'op': 'stop'})[0] == 200
    rig.estop.trip('test')
    assert rig.post({'op': 'stop'})[0] == 200
    assert [c['op'] for c in rig.port.sent] == ['stop', 'stop']
    rig.port.silent = True
    status, _ = rig.post({'op': 'stop'})
    assert status == 504 and rig.port.stops >= 3             # a lost reply still re-sends the stop


# ---- e-stop -----------------------------------------------------------------------------------
def test_motion_is_423_while_the_estop_is_latched_and_never_sent(rig):
    rig.estop.trip('test')
    for body in ({'op': 'goto', 'pan': 0.1}, {'op': 'step', 'pan': 0.1},
                 {'op': 'jog', 'pan_rate': 0.1}):
        status, doc = rig.post(body)
        assert status == 423 and doc['state'] == 'estopped'
    assert rig.port.sent == []
    assert rig.req('GET', '/api/camera')[1]['estop'] is True


def test_latch_landing_during_a_send_stops_the_camera(rig):
    rig.port.on_send = lambda cmd: rig.estop.trip('late')
    status, _ = rig.post({'op': 'goto', 'pan': 0.2})
    assert status == 423 and rig.port.stops >= 1


def test_a_motion_that_outlives_the_latch_is_stopped_by_the_status_poll(rig):
    rig.estop.trip('test')
    rig.port.state = 'jog'
    before = rig.port.stops
    rig.req('GET', '/api/camera')
    assert rig.port.stops == before + 1
    rig.port.state = 'idle'
    rig.req('GET', '/api/camera')
    assert rig.port.stops == before + 1


def test_api_stop_stops_the_camera_platform_too():
    r = Rig(with_supervisor=True)
    try:
        status, doc = r.req('POST', '/api/stop', {})
        assert status == 200 and r.estop.is_set
        assert r.arm.stop_calls >= 1 and r.port.stops >= 1
        assert r.post({'op': 'goto', 'pan': 0.1})[0] == 423
    finally:
        r.close()


def test_camera_stop_stops_the_camera_even_if_the_arm_raises():
    class Boom:
        def stop(self):
            raise RuntimeError('arm down')
    port = FakePort()
    with pytest.raises(RuntimeError):
        CameraStop(Boom(), port).stop()
    assert port.stops == 1
    port.publish_stop = lambda: (_ for _ in ()).throw(OSError('x'))
    arm = FakeArm(['joint_1'])
    CameraStop(arm, port).stop()                              # camera failure never hides the arm
    assert arm.stop_calls == 1


# ---- guards -----------------------------------------------------------------------------------
def test_writes_need_json_same_origin_and_commands(rig):
    conn = http.client.HTTPConnection('127.0.0.1', rig.view.port, timeout=5)
    conn.request('POST', '/api/camera', body=b'{"op":"stop"}',
                 headers={'Content-Type': 'text/plain'})
    assert conn.getresponse().status == 415
    conn.close()
    assert rig.post({'op': 'stop'}, headers={'Origin': 'http://evil.example'})[0] == 403
    assert rig.post({'op': 'stop'}, headers={'Sec-Fetch-Site': 'cross-site'})[0] == 403
    big = {'op': 'goto', 'pan': 0.1, 'pad': 'x' * 1000}
    assert rig.post(big)[0] == 413
    assert rig.req('PUT', '/api/camera', {'op': 'stop'})[0] == 405
    assert rig.req('DELETE', '/api/camera')[0] == 405
    assert rig.port.sent == []


def test_endpoint_is_closed_without_a_camera_api(rig):
    rig.view.camera = None
    assert rig.req('GET', '/api/camera')[0] == 403
    assert rig.post({'op': 'stop'})[0] == 403


# ---- frames: no credentials leak --------------------------------------------------------------
def test_frames_ignore_the_query_echo_nothing_and_log_no_query(caplog):
    rgb = np.full((24, 32, 3), (10, 20, 30), dtype=np.uint8)
    view = WebView(Source(), port=0)
    view.source = type('S', (), {'snapshot': lambda self: Snapshot(rgb=rgb)})()
    view.start_background()
    try:
        secret = 'PIN-4711-SECRET'
        headers = {'Authorization': f'Bearer {secret}', 'Cookie': f'session={secret}',
                   'X-Api-Key': secret}
        with caplog.at_level(logging.DEBUG):
            for path in ('/frame/rgb.png', f'/frame/rgb.png?pin={secret}',
                         f'/frame/depth.png?token={secret}'):
                conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=5)
                conn.request('GET', path, headers=headers)
                r = conn.getresponse()
                body = r.read()
                hdrs = {k.lower(): v for k, v in r.getheaders()}
                conn.close()
                assert r.status in (200, 503)
                assert secret.encode() not in body
                assert 'set-cookie' not in hdrs and 'authorization' not in hdrs
                assert secret not in json.dumps(hdrs)
                assert 'no-store' in hdrs['cache-control']
        assert secret not in caplog.text
    finally:
        view.shutdown()
