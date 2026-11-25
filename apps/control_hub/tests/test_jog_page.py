"""P1 Kontrol sayfası: node (saf mantık + jog motoru), dosya sunumu ve güvenlik kabulü (sahte saatle)."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
LEASE = 'p1-page-lease'


@pytest.fixture
def api():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    backend = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    client = TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000))
    yield client, hub, backend
    hub.close()


def joints(b):
    return [j['deg'] for j in b.snapshot()['joints']]


def test_logic_and_engine_in_node():
    if shutil.which('node') is None:
        pytest.skip('node yok')
    script = Path(__file__).parent / 'js' / 'jog_logic_check.js'
    r = subprocess.run(['node', str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'JOG_LOGIC_JS_OK' in r.stdout, r.stdout + r.stderr


def test_page_is_registered_with_styles_and_assets_are_served(api):
    c, _, _ = api
    assert 'jog' in {p['id'] for p in c.get('/api/pages').json()['pages']}
    page = c.get('/pages/jog.js').text
    assert "styles: ['/pages/jog.css']" in page and "'/jog_logic.js'" in page and "'/jog.js'" in page
    for path in ('/pages/jog.css', '/jog_logic.js', '/jog.js', '/frames.js', '/sphere.js'):
        assert c.get(path).status_code == 200, path


def test_no_named_pose_buttons_and_no_unsafe_dom_in_page_sources():
    """K11: isimli poz düğmesi yok; K4: innerHTML/inline/CDN/eval yok."""
    for name in ('pages/jog.js', 'jog.js', 'jog_logic.js'):
        src = (WEB / name).read_text(encoding='utf-8')
        for banned in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'eval(', 'new Function', 'onclick=',
                       'http://', 'https://'):
            assert banned not in src, (name, banned)
        for pose in ('Hazır', 'above_green', 'yeşile'):
            assert pose not in src, (name, pose)
        assert len(src.splitlines()) < 400, name


def test_dry_check_violation_blocks_motion_and_moves_nothing(api):
    c, _, sim = api
    before = joints(sim)
    body = {'type': 'joint', 'joints_deg': [before[0], 10.0, 10.0, *before[3:]]}  # güvenli kutu dışı
    chk = c.post('/api/move/check', json=body).json()
    assert chk['ok'] is False and chk['violations'][0]['kind'] == 'outside_safe_zone'
    sim.advance(3.0)
    assert joints(sim) == before  # kontrol robotu oynatmadı
    r = c.post('/api/move', json=body)
    assert r.status_code == 409 and r.json()['error'] == 'outside_safe_zone'
    sim.advance(3.0)
    assert joints(sim) == before  # ret = hareket yok


def test_estop_gives_423_on_every_page_motion_path_and_cuts_a_running_move(api):
    c, hub, sim = api
    home = joints(sim)
    go = {'type': 'joint', 'joints_deg': [home[0] + 5, *home[1:]]}
    assert c.post('/api/move', json=go).status_code == 202
    sim.advance(0.5)
    assert c.post('/api/estop').status_code == 200
    frozen = joints(sim)
    sim.advance(3.0)
    assert joints(sim) == frozen  # anında kesildi, devam etmez
    jog_body = {'lease': LEASE, 'increment': {'axis': 0, 'amount': 1}, 'frame': 'World'}
    paths = [('/api/jog', jog_body), ('/api/jog', {'lease': LEASE, 'twist': [0.01, 0, 0, 0, 0, 0]}),
             ('/api/jog/joint', {'lease': LEASE, 'joint': 0, 'dir': 1, 'ttl_ms': 250}),
             ('/api/move', {'type': 'joint', 'joints_deg': home}),
             ('/api/move', {'type': 'tool', 'open_pct': 50})]
    for path, body in paths:
        r = c.post(path, json=body)
        assert r.status_code == 423 and r.json()['error'] == 'estop_latched', (path, body)
    assert c.post('/api/estop/reset', json={}).status_code in (200, 204)
    sim.advance(3.0)
    assert joints(sim) == frozen  # sıfırlama otomatik hareket etmez


def test_cartesian_hold_jog_stops_when_heartbeat_stops(api):
    """Kartezyen basılı-tut (robot jog'u): her komut heartbeat; susarsa TTL (250 ms) sonunda durur."""
    c, _, sim = api
    start = np.array(sim.snapshot()['tcp']['pos_m'])
    twist = {'lease': LEASE, 'frame': 'World', 'twist': [0, 0, -0.01, 0, 0, 0], 'ttl_ms': 250}
    r = c.post('/api/jog', json=twist)
    assert r.status_code == 200, r.text
    for i in range(10):  # 0,2 sn: heartbeat sürüyor
        sim.advance(0.02)
        if i % 5 == 4:
            c.post('/api/jog', json=twist)
    moved = np.array(sim.snapshot()['tcp']['pos_m'])
    assert moved[2] < start[2] - 1e-4
    sim.advance(1.0)  # heartbeat yok -> TTL doldu
    stopped = np.array(sim.snapshot()['tcp']['pos_m'])
    sim.advance(1.0)
    assert np.allclose(np.array(sim.snapshot()['tcp']['pos_m']), stopped, atol=1e-6)
    assert stopped[2] > start[2] - 0.01  # 1 sn x 10 mm/s'ye devam etmedi
