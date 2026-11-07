"""L9c: /api/model geçişi, sahte model, WS canlılık işareti (bayatlık ölçümü), görünüm dosyaları."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, RobotApiBackend
from control_hub.hub_core.fake_backend import JOINTS
from control_hub.server import app as server_app
from control_hub.server import create_app

from .conftest import make_hub


@pytest.fixture
def local(scenarios, ui):
    hub = make_hub(scenarios, ui)
    yield TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000)), hub
    hub.close()


def test_fake_model_is_valid_and_matches_fake_joints(local):
    client, _ = local
    r = client.get('/api/model')
    assert r.status_code == 200
    m = r.json()
    assert m['view_model_version'] == 1 and not any(k.startswith('_') for k in m)
    assert [j['name'] for j in m['joints']] == list(JOINTS)            # yanlış eklemi çizmemek için
    assert {s['type'] for s in (link['shape'] for link in m['links'])} <= {'box', 'cylinder'}
    assert m['safe_box']['x'] == [0.05, 0.75] and m['safe_box']['z'] == [0.02, 0.7]


def test_model_is_read_only_and_needs_login_in_pin_mode(make_client):
    client, _ = make_client('pin')
    assert client.get('/api/model').status_code == 401
    assert client.post('/api/model', json={}).status_code in (401, 403, 405)


def test_websocket_sends_tick_when_state_is_static(local, monkeypatch):
    client, _ = local
    monkeypatch.setattr(server_app, 'WS_TICK_S', 0.1)
    got = []
    with client.websocket_connect('/ws', headers={'host': 'localhost', 'origin': 'http://localhost'}) as ws:
        for _ in range(8):
            got.append(ws.receive_json()['type'])
            if got.count('tick') >= 2:
                break
    assert got[0] == 'state' and got.count('tick') >= 2               # sessizlik = kopuk anlamı kalır


class Robot(BaseHTTPRequestHandler):
    reply = (200, {})

    def do_GET(self):                                                  # noqa: N802
        status, doc = Robot.reply if self.path == '/api/model' else (200, {'robot': {'api_version': 1}})
        data = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_robot_model_503_reason_and_unreachable_pass_to_client(scenarios, ui):
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Robot)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    backend = RobotApiBackend(f'http://127.0.0.1:{srv.server_address[1]}', poll_s=5, autostart=False)
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    try:
        client = TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 1))
        Robot.reply = (503, {'error': 'robot modeli kullanılamıyor: eklem adları uyuşmuyor'})
        r = client.get('/api/model')
        assert r.status_code == 503 and 'eklem adları' in r.json()['message']
        Robot.reply = (200, {'view_model_version': 1, 'joints': []})
        assert client.get('/api/model').json()['view_model_version'] == 1
    finally:
        hub.close()
        srv.shutdown()
        srv.server_close()
    dead = Hub(RobotApiBackend('http://127.0.0.1:9', poll_s=5, autostart=False), scenarios, ui,
               AccessControl('open', '123456'))
    r = TestClient(create_app(dead), base_url='http://localhost', client=('127.0.0.1', 1)).get('/api/model')
    assert r.status_code == 502 and r.json()['error'] == 'robot_unreachable'
    dead.close()


def test_view_assets_declare_the_stale_rule_and_no_command_path(local):
    client, _ = local
    assert 'view.js' in client.get('/').text
    js = client.get('/view.js').text
    assert 'STALE_MS = 1000' in js and 'ACİL DUR' in js and 'Görünüm kullanılamıyor' in js
    assert 'innerHTML' not in js.replace('innerHTML yok', '')
    assert "'POST'" not in js and "'PUT'" not in js and "'DELETE'" not in js    # görmek komut vermek değildir
