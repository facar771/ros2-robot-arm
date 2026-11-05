"""J3: jog/çerçeve uçları (sahte robot) ve Robot API geçişi (taklit HTTP sunucu)."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import RobotApiBackend
from control_hub.hub_core.commands import CommandError, parse_robot_command
from control_hub.server import create_app

from .conftest import make_hub

LEASE_A, LEASE_B = 'lease-aaaaaaaa', 'lease-bbbbbbbb'


@pytest.fixture
def local(scenarios, ui):
    """Yerel (127.0.0.1) istemci + hub; ``hold`` açık."""
    hub = make_hub(scenarios, ui)
    yield TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000)), hub
    hub.close()


@pytest.fixture
def remote(scenarios, ui):
    hub = make_hub(scenarios, ui)
    app = create_app(hub)
    yield TestClient(app, base_url='http://192.168.1.20:8765', client=('192.168.1.50', 50000)), hub
    hub.close()


def inc(client, axis=0, amount=1, lease=LEASE_A, **kw):
    return client.post('/api/jog', json={'lease': lease, 'increment': {'axis': axis, 'amount': amount},
                                         'ttl_ms': 250, **kw})


def test_ready_command_whitelisted_and_runs(local):
    client, hub = local
    assert parse_robot_command('ready').name == 'ready'
    with pytest.raises(CommandError):
        parse_robot_command('ready now')
    assert client.post('/api/scenarios/ready/run', json={}).status_code == 202
    assert hub.runner.wait_idle(10)
    assert hub.backend.history[-1] == 'ready'
    assert hub.backend.snapshot()['tool'] == [0.51, 0.0, 0.43]


def test_increment_moves_tcp_and_state_exposes_tcp_and_jog(local):
    client, hub = local
    x0 = hub.backend.snapshot()['tcp']['position'][0]
    r = inc(client, 0, 10)
    assert r.status_code == 200 and r.json()['mode'] == 'increment' and r.json()['ok']
    state = client.get('/api/state').json()['robot']
    assert state['tcp']['position'][0] == pytest.approx(x0 + 0.010, abs=1e-4)
    assert state['jog']['active'] is True


def test_default_modes_and_bad_requests(local):
    client, _ = local
    assert inc(client, 0, 5).status_code == 400                          # yalnızca 0.1/1/10
    assert inc(client, 9, 1).status_code == 400
    assert client.post('/api/jog', json={'lease': 'kisa'}).status_code == 400
    assert client.post('/api/jog', json={'lease': LEASE_A, 'twist': [0] * 6,
                                         'increment': {'axis': 0, 'amount': 1}}).status_code == 400
    assert inc(client, 0, 1, frame='yok').status_code == 400


def test_second_client_gets_409_until_release(local):
    client, _ = local
    assert inc(client, lease=LEASE_A).status_code == 200
    r = inc(client, lease=LEASE_B)
    assert r.status_code == 409 and 'başka' in r.json()['message']
    assert client.post('/api/jog', json={'lease': LEASE_A, 'ttl_ms': 0}).status_code == 200
    assert inc(client, lease=LEASE_B).status_code == 200


def test_estop_gives_423_without_asking_the_robot(local):
    client, hub = local
    hub.estop('test')
    r = inc(client)
    assert r.status_code == 423 and r.json()['error'] == 'estop_latched'
    hub.wait_estop_report()


def test_hold_is_local_only_and_dead_man_stops_after_ttl(local, remote):
    rclient, _ = remote
    r = rclient.post('/api/jog', json={'lease': LEASE_A, 'twist': [0.02, 0, 0, 0, 0, 0], 'ttl_ms': 250})
    assert r.status_code == 403 and r.json()['error'] == 'hold_local_only'
    assert inc(rclient).status_code == 200                              # telefonda artımlı çalışır
    client, hub = local
    x0 = hub.backend.snapshot()['tcp']['position'][0]
    body = {'lease': LEASE_A, 'twist': [0.02, 0, 0, 0, 0, 0], 'ttl_ms': 9999}
    r = client.post('/api/jog', json=body)
    assert r.status_code == 200 and r.json()['ttl_clamped'] is True and r.json()['ttl_ms'] == 500
    time.sleep(0.7)                                                       # heartbeat yok -> TTL doldu
    x1 = hub.backend.snapshot()['tcp']['position'][0]
    assert 0 < x1 - x0 <= 0.02 * 0.5 + 1e-3                              # en çok TTL kadar ilerledi
    time.sleep(0.2)
    assert hub.backend.snapshot()['tcp']['position'][0] == pytest.approx(x1, abs=1e-6)


def test_jog_status_reports_hold_allowed_with_reason(local, remote):
    st = local[0].get('/api/jog').json()
    assert st['hold_allowed'] is True and st['hold_reason'] is None
    st = remote[0].get('/api/jog').json()
    assert st['hold_allowed'] is False and 'bilgisayar' in st['hold_reason']
    local[1].backend.jog.hold_enabled = False
    st = local[0].get('/api/jog').json()
    assert st['hold_allowed'] is False and 'kapalı' in st['hold_reason']
    r = local[0].post('/api/jog', json={'lease': LEASE_A, 'twist': [0.02, 0, 0, 0, 0, 0]})
    assert r.status_code == 403


def test_jog_and_scenarios_exclude_each_other(local):
    client, hub = local
    assert inc(client).status_code == 200
    assert client.post('/api/scenarios/home/run', json={}).status_code == 202   # runner kabul eder...
    assert hub.runner.wait_idle(10)
    assert any('jog sürüyor' in (e.message or '') or e.kind for e in hub.events.since(0))
    assert hub.backend.history[-1] == 'home'
    hub.backend.jog._lease = None                                         # jog bitti sayılsın
    hub.backend._moving = True                                            # iş sürerken jog 409
    assert inc(client).status_code == 409
    hub.backend._moving = False


def test_frames_roundtrip_validation_and_409_while_jogging(local):
    client, hub = local
    pts = {'origin': [0.3, 0, 0.1], 'x': [0.4, 0, 0.1], 'xy': [0.3, 0.1, 0.1]}
    good = {'frames': [{'name': 'Tezgah', 'points': pts}]}
    r = client.put('/api/frames', json=good)
    assert r.status_code == 200 and r.json()['frames'][0]['name'] == 'Tezgah'
    assert [f['name'] for f in client.get('/api/frames').json()['frames']] == ['Tezgah']
    assert client.put('/api/frames', json={'frames': [{'name': 'World', 'points': {}}]}).status_code == 400
    assert inc(client, frame='Tezgah').status_code == 200
    r = client.put('/api/frames', json={'frames': []})
    assert r.status_code == 409
    assert [f['name'] for f in client.get('/api/frames').json()['frames']] == ['Tezgah']


def test_pin_mode_needs_login_for_jog(make_client):
    client, _ = make_client('pin')
    assert client.get('/api/jog').status_code == 401
    assert client.post('/api/jog', json={'lease': LEASE_A}).status_code == 401
    assert client.put('/api/frames', json={'frames': []}).status_code == 401


def test_static_panel_served_and_hub_never_writes_frames_file():
    web = Path(__file__).resolve().parents[1] / 'control_hub'
    assert 'jog.js' in (web / 'web' / 'index.html').read_text(encoding='utf-8')
    for f in ('server/jog_routes.py', 'hub_core/robot_api_backend.py', 'hub_core/fake_jog.py'):
        src = (web / f).read_text(encoding='utf-8')
        assert not re.search(r'\bopen\(|write_text|write_bytes', src), f


# --- Robot API geçişi (taklit robot) --------------------------------------------------------------
class Stub(BaseHTTPRequestHandler):
    seen = []
    reply = (200, {'ok': True})

    def _do(self):
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        Stub.seen.append((self.command, self.path, body))
        status, doc = Stub.reply if self.path.startswith(('/api/jog', '/api/frames')) else (200, {})
        if self.path == '/api/state':
            status, doc = 200, {'robot': {'api_version': 1, 'state': 'idle', 'jog': None},
                                'commands': {'enabled': True},
                                'tcp': {'position': [0.5, 0, 0.4], 'wpr_deg': [0, 0, 0]}}
        data = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _do

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_robot():
    Stub.seen, Stub.reply = [], (200, {'ok': True})
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()
    srv.server_close()


def test_robot_api_backend_passes_status_and_body_through(scenarios, ui, stub_robot):
    from control_hub.hub_core import AccessControl, Hub
    backend = RobotApiBackend(stub_robot, poll_s=0.05)
    backend.wait_first_poll()
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    try:
        client = TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 1))
        for code, err in ((200, None), (409, 'jog başka bir istemcinin kiralamasında'),
                          (423, 'acil dur'), (403, 'basılı-tut jog uzak (loopback dışı) istemcilere kapalı')):
            Stub.reply = (code, {'ok': code == 200, 'error': err} if err else {'ok': True})
            r = inc(client)
            assert r.status_code == code
            if err:
                assert err in r.json()['message']           # robotun nedeni kullanıcıya olduğu gibi gider
        sent = {'lease': LEASE_A, 'increment': {'axis': 0, 'amount': 1}, 'ttl_ms': 250}
        assert ('POST', '/api/jog', sent) in Stub.seen
        Stub.reply = (200, {'builtin': ['World', 'Tool'], 'max_frames': 10, 'frames': []})
        assert client.get('/api/frames').json()['max_frames'] == 10
        client.put('/api/frames', json={'frames': []})
        assert ('PUT', '/api/frames', {'frames': []}) in Stub.seen
        assert hub.snapshot()['robot']['tcp']['position'] == [0.5, 0, 0.4]
    finally:
        hub.close()


def test_unreachable_robot_gives_502(scenarios, ui):
    from control_hub.hub_core import AccessControl, Hub
    backend = RobotApiBackend('http://127.0.0.1:9', poll_s=5, autostart=False)
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    client = TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 1))
    r = inc(client)
    assert r.status_code == 502 and r.json()['error'] == 'robot_unreachable'
    hub.close()


# --- L3: 3 nokta öğretme (Hub yalnızca geçirir; doğrulama robotta) -----------------------------------
def _frame(name, o, x, xy):
    return {'name': name, 'points': {'origin': o, 'x': x, 'xy': xy}}


def test_frame_teaching_invalid_points_show_robot_text(local):
    client, _ = local
    near = _frame('Yakin', [0.3, 0, 0.1], [0.31, 0, 0.1], [0.3, 0.1, 0.1])            # 10 mm < 20 mm
    r = client.put('/api/frames', json={'frames': [near]})
    assert r.status_code == 400 and '20 mm' in r.json()['message']
    line = _frame('Dogru', [0.3, 0, 0.1], [0.4, 0, 0.1], [0.5, 0, 0.1])               # aynı doğru
    r = client.put('/api/frames', json={'frames': [line]})
    assert r.status_code == 400 and 'aynı doğru' in r.json()['message']
    assert client.get('/api/frames').json()['frames'] == []                           # hiçbir şey yazılmadı


def test_frame_teaching_appends_and_deletes_whole_list(local):
    client, _ = local
    a = _frame('A', [0.3, 0, 0.1], [0.4, 0, 0.1], [0.3, 0.1, 0.1])
    b = _frame('B', [0.2, 0, 0.1], [0.2, 0.1, 0.1], [0.1, 0.1, 0.1])
    assert client.put('/api/frames', json={'frames': [a]}).status_code == 200
    got = client.get('/api/frames').json()['frames']
    both = [{'name': f['name'], 'points': f['points']} for f in got] + [b]
    assert client.put('/api/frames', json={'frames': both}).status_code == 200
    assert [f['name'] for f in client.get('/api/frames').json()['frames']] == ['A', 'B']
    assert client.put('/api/frames', json={'frames': [b]}).status_code == 200
    assert [f['name'] for f in client.get('/api/frames').json()['frames']] == ['B']
    assert inc(client, frame='B').status_code == 200
    assert inc(client, frame='A').status_code == 400                                   # silinen çerçeve yok


def test_wizard_assets_served(local):
    client, _ = local
    assert 'frames.js' in client.get('/').text
    js = client.get('/frames.js').text
    assert 'window.FrameWizard' in js and "'/api/frames', 'PUT'" in js and '.innerHTML' not in js
