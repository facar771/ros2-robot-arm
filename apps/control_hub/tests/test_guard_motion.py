"""``guard_motion`` (docs/MIMARI.md §7 R3): 423 gövdesi birebir ve jog "bırak" istisnası kapalı/açık."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from control_hub.server.guards import guard_motion, is_release
from tests.conftest import CONFIG

BODY_423 = {'ok': False, 'error': 'estop_latched', 'message': 'ACİL DUR aktif: önce kilidi aç',
            'state': 'estopped'}
REQ = SimpleNamespace(url=SimpleNamespace(path='/api/jog'))
RELEASE = {'lease': 'guardtest0001', 'ttl_ms': 0}
HOLD = {'lease': 'guardtest0001', 'twist': [0.01, 0, 0, 0, 0, 0], 'ttl_ms': 250}


def test_not_latched_returns_none_whatever_the_flags():
    hub = SimpleNamespace(estopped=False)
    for allow in (False, True):
        for body in (None, RELEASE, HOLD, [1]):
            assert guard_motion(hub, REQ, allow, body) is None


def test_latched_gives_the_exact_423_body():
    resp = guard_motion(SimpleNamespace(estopped=True), REQ)
    assert resp.status_code == 423
    assert resp.body == json.dumps(BODY_423, separators=(',', ':'), ensure_ascii=False).encode()


@pytest.mark.parametrize('body', [RELEASE, HOLD, None, [1], {'ttl_ms': 250}])
def test_exception_closed_blocks_everything_even_release(body):
    """allow_release=False (varsayılan): bırak gövdesi dahi 423 alır; hareket rotaları için istisna YOK."""
    resp = guard_motion(SimpleNamespace(estopped=True), REQ, False, body)
    assert resp is not None and resp.status_code == 423


def test_exception_open_lets_only_release_through():
    hub = SimpleNamespace(estopped=True)
    assert guard_motion(hub, REQ, True, RELEASE) is None                    # bırakma ACİL DUR'da çalışır
    assert guard_motion(hub, REQ, True, {'ttl_ms': 0.0}) is None            # 0 == 0.0 (eskiyle aynı)
    for body in (HOLD, None, [1], 'ttl_ms', {'ttl_ms': None}, {'lease': 'x'}):
        resp = guard_motion(hub, REQ, True, body)
        assert resp is not None and resp.status_code == 423, body


def test_is_release_matches_the_old_inline_expression():
    def old(body):                                                         # jog_routes'taki eski satır
        return isinstance(body, dict) and body.get('ttl_ms') == 0
    for body in (RELEASE, HOLD, None, [], {'ttl_ms': 0.0}, {'ttl_ms': False}, {'ttl_ms': '0'}, {}):
        assert is_release(body) == old(body)


@pytest.fixture
def local():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    hub = Hub(SimBackend(clock=SimClock(), autostart=False), scen, ui, AccessControl('open', '123456'))
    yield TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000)), hub
    hub.close()


@pytest.mark.parametrize('path,hold,release', [
    ('/api/jog', HOLD, RELEASE),
    ('/api/jog/joint',
     {'lease': 'guardtest0001', 'joint': 0, 'dir': 1, 'speed_pct': 50, 'ttl_ms': 250},
     RELEASE)])
def test_http_jog_release_works_under_estop_but_hold_gets_423(local, path, hold, release):
    c, hub = local
    assert c.post('/api/estop').status_code == 200 and hub.estopped
    r = c.post(path, json=hold)
    assert r.status_code == 423 and r.json() == BODY_423
    r = c.post(path, json=release)
    # Hub'ın kilidi bırakmayı kesmedi (arka uç kendi yanıtını verir)
    assert r.json() != BODY_423, r.text


@pytest.mark.parametrize('path,body', [
    ('/api/move', {'type': 'linear', 'pos_m': [0.15, 0.0, 0.12], 'orientation': 'free', 'speed_pct': 50}),
    ('/api/move/resume', {}), ('/api/sim/reset', {}), ('/api/program/run', {}), ('/api/program/step', {})])
def test_http_other_motion_routes_ignore_the_release_shape(local, path, body):
    """İstisna yalnız jog'da: gövdesi ``ttl_ms: 0`` olsa bile diğer hareket rotaları kilitte 423
    verir."""
    c, _ = local
    c.post('/api/estop')
    r = c.post(path, json={**body, 'ttl_ms': 0})
    assert r.status_code == 423 and r.json()['error'] == 'estop_latched'
