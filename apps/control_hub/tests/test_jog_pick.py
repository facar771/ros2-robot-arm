"""P1b 3B tıkla-git: sayfanın gönderdiği doğrusal hedef gövdesi (SİM: yönelim 'free') kuru kontrolden geçer,
kontrol robotu oynatmaz, ACİL DUR'da hareket 423 alır. Tıklama/işaret mantığı: tests/js/jog_logic_check.js."""
import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG


@pytest.fixture
def api():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    sim = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(sim, scen, ui, AccessControl('open', '123456'))
    yield TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000)), sim
    hub.close()


def body_for(sim, x, y):
    """Sayfanın üreteceği gövde: x,y tıklamadan, z güvenli kutu ortası, SİM'de orientation='free'."""
    zone = sim.snapshot()['safe_zone']
    z = (zone['min'][2] + zone['max'][2]) / 2
    return {'type': 'linear', 'pos_m': [x, y, z], 'orientation': 'free', 'speed_pct': 50}


def test_default_z_target_passes_dry_check_without_moving_then_goes(api):
    c, sim = api
    zone = sim.snapshot()['safe_zone']
    x, y = (zone['min'][0] + zone['max'][0]) / 2, 0.0
    body = body_for(sim, x, y)
    before = [j['deg'] for j in sim.snapshot()['joints']]
    chk = c.post('/api/move/check', json=body).json()
    assert chk['ok'] is True and chk['violations'] == []
    sim.advance(2.0)
    assert [j['deg'] for j in sim.snapshot()['joints']] == before  # kontrol robotu oynatmadı
    assert c.post('/api/move', json=body).status_code == 202
    sim.advance(60.0)
    tcp = sim.snapshot()['tcp']
    assert tcp['pos_m'] == pytest.approx(body['pos_m'], abs=2e-3)


def test_target_outside_safe_box_is_rejected_with_a_reason_and_nothing_moves(api):
    c, sim = api
    zone = sim.snapshot()['safe_zone']
    body = body_for(sim, zone['max'][0] + 0.2, 0.0)
    before = [j['deg'] for j in sim.snapshot()['joints']]
    chk = c.post('/api/move/check', json=body).json()
    assert chk['ok'] is False and chk['violations'][0]['message']  # sayfa bu metni kırmızı yazar
    assert c.post('/api/move', json=body).status_code == 409
    sim.advance(3.0)
    assert [j['deg'] for j in sim.snapshot()['joints']] == before


def test_estop_gives_423_on_target_move_and_nothing_moves(api):
    c, sim = api
    zone = sim.snapshot()['safe_zone']
    body = body_for(sim, (zone['min'][0] + zone['max'][0]) / 2, 0.0)
    assert c.post('/api/estop').status_code == 200
    before = [j['deg'] for j in sim.snapshot()['joints']]
    r = c.post('/api/move', json=body)
    assert r.status_code == 423 and r.json()['error'] == 'estop_latched'
    sim.advance(3.0)
    assert [j['deg'] for j in sim.snapshot()['joints']] == before
