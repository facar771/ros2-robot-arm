"""L8 (Hub kısmı): küresel çerçeve sihirbazı. Hub yalnızca geçirir; doğrulama ve erişim hesabı robotta.

Sahte robot (``FakeBackend``) Robot API'nin L8 sözleşmesini aynalar (``docs/INTERFACES.md``,
"Küresel çerçeve"). Erişim hesabı sahtedir: burada denenen, Hub yolunun ve arayüz
mantığının doğruluğudur, kinematik değil.
"""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, FakeBackend, Hub, RobotApiBackend
from control_hub.server import create_app

from .conftest import make_hub

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
LEASE = 'lease-aaaaaaaa'


def _client(hub):
    return TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000))


@pytest.fixture
def local(scenarios, ui):
    hub = make_hub(scenarios, ui)
    yield _client(hub), hub
    hub.close()


@pytest.fixture
def old_robot(scenarios, ui):
    """Küreyi bilmeyen robot (``types`` yalnız user)."""
    backend = FakeBackend(time_scale=0.02, sphere_support=False)
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    yield _client(hub), hub
    hub.close()


def _frame(name='Tezgah'):
    return {'name': name, 'points': {'origin': [0.3, 0, 0.1], 'x': [0.4, 0, 0.1], 'xy': [0.3, 0.1, 0.1]}}


def _sphere(name='Kubbe1', center=(0.3, 0.0, 0.5), radius=0.25):
    return {'type': 'sphere', 'name': name, 'center': list(center), 'radius': radius, 'tool_axis': 'z'}


def _names(doc, key):
    return [x['name'] for x in doc[key]]


# --- GET /api/frames: tür bildirimi ----------------------------------------------------------------
def test_get_frames_reports_types_and_spheres(local):
    client, _ = local
    doc = client.get('/api/frames').json()
    assert doc['types'] == ['user', 'sphere'] and doc['spheres'] == [] and doc['max_spheres'] == 10


def test_old_robot_does_not_advertise_sphere(old_robot):
    client, _ = old_robot
    assert 'sphere' not in client.get('/api/frames').json().get('types', [])
    r = client.put('/api/frames', json={'frames': [], 'spheres': [_sphere()]})
    assert r.status_code == 400                       # L8 öncesi robot `spheres` anahtarını tanımaz
    assert client.post('/api/frames/reach', json={'center': [0.3, 0, 0.5], 'radius': 0.25}).status_code == 403


# --- PUT: diğer türler korunur, bilinmeyen tür net hata --------------------------------------------
def test_put_without_spheres_key_keeps_spheres_and_spheres_key_keeps_frames(local):
    client, _ = local
    assert client.put('/api/frames', json={'frames': [_frame()], 'spheres': [_sphere()]}).status_code == 200
    # eski Hub: yalnız frames geri yazar -> küreler KALIR
    r = client.put('/api/frames', json={'frames': [_frame(), _frame('Ikinci')]})
    assert r.status_code == 200 and _names(r.json(), 'spheres') == ['Kubbe1']
    # yeni Hub: ikisini birlikte geri yazar
    both = {'frames': [_frame()], 'spheres': [_sphere(), _sphere('Kubbe2', radius=0.3)]}
    got = client.put('/api/frames', json=both).json()
    assert _names(got, 'frames') == ['Tezgah'] and _names(got, 'spheres') == ['Kubbe1', 'Kubbe2']
    # spheres: [] küreleri bilerek siler (anahtar VAR)
    assert client.put('/api/frames', json={'frames': [_frame()], 'spheres': []}).json()['spheres'] == []


def test_unknown_type_is_an_error_not_silently_skipped(local):
    client, _ = local
    client.put('/api/frames', json={'frames': [_frame()], 'spheres': [_sphere()]})
    before = client.get('/api/frames').json()
    r = client.put('/api/frames', json={'frames': [{**_frame('Silindir'), 'type': 'cylinder'}]})
    msg = r.json()['message']
    assert r.status_code == 400 and 'cylinder' in msg and 'sessizce atlanmaz' in msg
    r = client.put('/api/frames', json={'frames': [], 'spheres': [{**_sphere('Koni'), 'type': 'cone'}]})
    assert r.status_code == 400 and 'cone' in r.json()['message']
    assert client.get('/api/frames').json() == before                  # hiçbir şey yazılmadı


def test_sphere_validation_text_comes_from_robot(local):
    client, _ = local
    r = client.put('/api/frames', json={'frames': [], 'spheres': [_sphere(radius=0.01)]})
    assert r.status_code == 400 and 'yarıçap' in r.json()['message']
    r = client.put('/api/frames', json={'frames': [_frame('Ayni')], 'spheres': [_sphere('Ayni')]})
    assert r.status_code == 400 and 'hem kullanıcı hem küre' in r.json()['message']
    assert client.get('/api/frames').json()['spheres'] == []


# --- POST /api/frames/reach -------------------------------------------------------------------------
def test_reach_returns_range_grid_and_note(local):
    client, _ = local
    r = client.post('/api/frames/reach', json={'center': [0.0, 0.0, 0.3], 'radius': 0.3})
    assert r.status_code == 200
    doc = r.json()
    assert doc['reachable'] is True and len(doc['elevation_range_deg']) == 2
    assert doc['grid']['cells'] and doc['rows'] and 'SAHTE' in doc['note']
    far = client.post('/api/frames/reach', json={'center': [3.0, 0.0, 0.3], 'radius': 0.3}).json()
    assert far['reachable'] is False and far['elevation_range_deg'] is None


def test_reach_errors_pass_through_with_robot_text(local):
    client, _ = local
    r = client.post('/api/frames/reach', json={'center': [0.3, 0, 0.5], 'radius': 0.01})
    assert r.status_code == 400 and 'yarıçap' in r.json()['message']
    assert client.post('/api/frames/reach', json={'center': [0.3, 0, 0.5]}).status_code == 400
    bad = client.post('/api/frames/reach', content=b'{bozuk', headers={'Content-Type': 'application/json'})
    assert bad.status_code == 400


def test_reach_refused_while_jogging_and_when_estopped(local):
    client, hub = local
    body = {'center': [0.0, 0.0, 0.3], 'radius': 0.3}
    inc = {'lease': LEASE, 'increment': {'axis': 0, 'amount': 1}, 'ttl_ms': 250}
    assert client.post('/api/jog', json=inc).status_code == 200
    r = client.post('/api/frames/reach', json=body)
    assert r.status_code == 409 and 'jog' in r.json()['message']
    client.post('/api/jog', json={'lease': LEASE, 'ttl_ms': 0})
    hub.backend.emergency_stop()
    assert client.post('/api/frames/reach', json=body).status_code == 423


def test_reach_needs_control_rights(scenarios, ui):
    hub = make_hub(scenarios, ui, mode='pin')
    try:
        anon = TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 1))
        r = anon.post('/api/frames/reach', json={'center': [0, 0, 0.3], 'radius': 0.3})
        assert r.status_code in (401, 403)
    finally:
        hub.close()


# --- Robot API geçişi (taklit HTTP robot) -----------------------------------------------------------
class Stub(BaseHTTPRequestHandler):
    seen = []
    reply = (200, {'ok': True})

    def _do(self):
        n = int(self.headers.get('Content-Length') or 0)
        Stub.seen.append((self.command, self.path, json.loads(self.rfile.read(n)) if n else None))
        state = {'robot': {'api_version': 1, 'state': 'idle', 'jog': None}, 'commands': {'enabled': True}}
        status, doc = (200, state) if self.path == '/api/state' else Stub.reply
        data = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _do

    def log_message(self, *a):
        pass


def test_robot_api_backend_passes_reach_and_sphere_put_through(scenarios, ui):
    Stub.seen, Stub.reply = [], (200, {'ok': True, 'reachable': True})
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    backend = RobotApiBackend(f'http://127.0.0.1:{srv.server_address[1]}', poll_s=0.05)
    backend.wait_first_poll()
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    try:
        client = _client(hub)
        body = {'center': [0.3, 0.0, 0.5], 'radius': 0.25, 'tool_axis': 'z'}
        assert client.post('/api/frames/reach', json=body).json()['reachable'] is True
        assert ('POST', '/api/frames/reach', body) in Stub.seen          # gövde AYNEN gitti
        put = {'frames': [_frame()], 'spheres': [_sphere()]}
        client.put('/api/frames', json=put)
        assert ('PUT', '/api/frames', put) in Stub.seen
        Stub.reply = (409, {'ok': False, 'error': 'jog ya da iş sürerken erişim aralığı hesaplanmaz'})
        r = client.post('/api/frames/reach', json=body)
        assert r.status_code == 409 and 'erişim aralığı' in r.json()['message']
        Stub.reply = (403, {'error': 'küre çerçevesi bu sunucuda kapalı'})
        assert client.post('/api/frames/reach', json=body).status_code == 403
    finally:
        hub.close()
        srv.shutdown()
        srv.server_close()


def test_unreachable_robot_gives_502_for_reach(scenarios, ui):
    backend = RobotApiBackend('http://127.0.0.1:9', poll_s=5, autostart=False)
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    try:
        r = _client(hub).post('/api/frames/reach', json={'center': [0, 0, 0.3], 'radius': 0.3})
        assert r.status_code == 502 and r.json()['error'] == 'robot_unreachable'
    finally:
        hub.close()


# --- arayüz varlıkları ve mantık --------------------------------------------------------------------
def test_wizard_assets_are_served_in_order_and_safe(local):
    client, _ = local
    html = client.get('/').text
    assert html.index('/sphere.js') < html.index('/frames.js')           # FrameTypes, FrameWizard'dan önce
    js = client.get('/sphere.js')
    assert js.status_code == 200 and js.headers['cache-control'] == 'no-cache'
    assert "'/api/frames/reach', 'POST'" in js.text and "'/api/frames', 'PUT'" in js.text
    for name in ('sphere.js', 'frames.js'):
        assert '.innerHTML' not in (WEB / name).read_text(encoding='utf-8')


@pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')
def test_wizard_logic_and_dom_flow_in_node():
    """Gerçek sphere.js/frames.js sahte DOM üzerinde: sphere yoksa seçenek yok, diğer türler korunur,
    bilinmeyen tür hata verir ve yazmaz, erişilemeyen küre onay ister."""
    script = Path(__file__).parent / 'js' / 'sphere_wizard_check.js'
    r = subprocess.run(['node', str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'SPHERE_WIZARD_JS_OK' in r.stdout, r.stdout + r.stderr


# --- sahte robot, GERÇEK robot doğrulayıcısıyla aynı sözleşmeyi mi veriyor? ---------------------------
def _real_robot(tmp_path):
    pytest.importorskip('numpy')
    pytest.importorskip('yaml')
    try:
        from robot_core.frames import FrameError, FrameStore
        from robot_core.jogapi import frames_document, replace_frames
        from robot_core.spheres import SphereStore
    except ImportError:
        pytest.skip('robot_core içe aktarılamadı')
    return FrameStore(tmp_path / 'frames.yaml'), SphereStore(tmp_path / 'spheres.yaml'), \
        FrameError, frames_document, replace_frames


def test_wizard_bodies_are_accepted_by_the_real_robot_validator(tmp_path):
    """Sihirbazın gövde biçimleri GERÇEK ``replace_frames``'te de geçer ve aynı kuralları uygular."""
    store, spheres, FrameError, frames_document, replace_frames = _real_robot(tmp_path)
    doc = replace_frames(store, {'frames': [_frame()], 'spheres': [_sphere()]}, spheres=spheres)
    assert doc['types'] == ['user', 'sphere'] and [s['name'] for s in doc['spheres']] == ['Kubbe1']
    # düzlem sihirbazı: yalnız frames -> küreler kalır
    doc = replace_frames(store, {'frames': [_frame(), _frame('Ikinci')]}, spheres=spheres)
    assert [s['name'] for s in doc['spheres']] == ['Kubbe1']
    # küre sihirbazı: robotun GET'te verdiği küre AYNEN geri yollanır + yeni
    current = frames_document(store, spheres)
    body = {'frames': [{'name': f['name'], 'points': f['points']} for f in current['frames']],
            'spheres': [*current['spheres'], _sphere('Kubbe2', radius=0.3)]}
    doc = replace_frames(store, body, spheres=spheres)
    assert [s['name'] for s in doc['spheres']] == ['Kubbe1', 'Kubbe2'] and len(doc['frames']) == 2
    # bilinmeyen tür: gerçek doğrulayıcı da adıyla reddeder, hiçbir şey yazmaz
    for bad in ({'frames': [{**_frame('X'), 'type': 'cylinder'}]},
                {'frames': [], 'spheres': [{**_sphere('K'), 'type': 'cone'}]}):
        with pytest.raises(FrameError, match='sessizce atlanmaz'):
            replace_frames(store, bad, spheres=spheres)
    assert len(spheres.all()) == 2 and len(store) == 2
    # robot küreyi bilmiyorsa (spheres=None) `types` yalnız user: Hub'ın küre kartını gizlemesi buna dayanır
    assert frames_document(store, None)['types'] == ['user']


def test_fake_and_real_robot_agree_on_the_frames_document_shape(local, tmp_path):
    store, spheres, _, frames_document, replace_frames = _real_robot(tmp_path)
    client, _ = local
    body = {'frames': [_frame()], 'spheres': [_sphere()]}
    real = replace_frames(store, body, spheres=spheres)
    fake = client.put('/api/frames', json=body).json()
    for key in ('types', 'max_spheres', 'spheres', 'builtin'):
        assert key in real and key in fake, key
    assert fake['types'] == real['types'] and fake['spheres'] == real['spheres']
    assert {f['type'] for f in real['frames']} == {f['type'] for f in fake['frames']} == {'user'}
