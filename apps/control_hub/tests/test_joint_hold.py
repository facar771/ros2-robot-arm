"""P1 Kontrol: eklem basılı-tut jog'u (sunucu dead-man) — sahte saatle, gerçek bekleme yok.

Kural: bırakınca ya da ``ttl_ms`` (<= 500 ms) içinde yenilenmezse hareket DURUR; ACİL DUR'da 423; limitte ve
güvenli kutuda ret = hareket yok; telefondan (yerel olmayan istek) 403."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.joint_hold import HOLD_TTL_MAX_MS, clamp_ttl_ms, hold_target
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG

LEASE = 'p1-test-lease'


@pytest.fixture
def api():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    backend = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    client = TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000))
    yield client, hub, backend
    hub.close()


def deg(b):
    return [j['deg'] for j in b.snapshot()['joints']]


def hold(c, joint=0, sign=1, ttl=250, **extra):
    body = {'lease': LEASE, 'joint': joint, 'dir': sign, 'ttl_ms': ttl, **extra}
    return c.post('/api/jog/joint', json=body)


def test_pure_helpers():
    assert hold_target(10, -170, 170, 1, 60) == 70
    assert hold_target(150, -170, 170, 1, 60) == 170            # limite kırpılır
    assert hold_target(-160, -170, 170, -1, 60) == -170
    assert clamp_ttl_ms(5000) == HOLD_TTL_MAX_MS and clamp_ttl_ms(None) == 250 and clamp_ttl_ms(-3) == 1


def test_hold_moves_joint_and_release_stops_at_once(api):
    c, hub, sim = api
    r = hold(c)
    assert r.status_code == 200 and r.json()['state'] == 'holding'
    start = deg(sim)[0]
    for i in range(100):                                        # 2 sn sahte saat; her 100 ms heartbeat
        sim.advance(0.02)
        if i % 5 == 4:
            assert hold(c).status_code == 200
    moved = deg(sim)[0]
    assert moved > start + 1.0
    assert c.post('/api/jog/joint', json={'lease': LEASE, 'ttl_ms': 0}).json()['state'] == 'released'
    sim.advance(1.0)
    assert deg(sim)[0] == pytest.approx(moved, abs=1e-6)        # bıraktıktan sonra ilerlemez
    assert not hub.joint_hold.active


def test_deadman_stops_when_heartbeat_missing(api):
    """Tarayıcı bağlantısı kesilirse (komut gelmezse) TTL sonunda sunucu durdurur; <= 3 sn şartı."""
    c, hub, sim = api
    assert hold(c, ttl=250).status_code == 200
    job_id = hub.joint_hold.job_id
    sim.advance(0.1)
    hub.joint_hold.tick()
    assert hub.joint_hold.active                                # TTL dolmadı
    sim.advance(0.4)                                            # 0,5 sn heartbeat yok (> 250 ms)
    assert hub.joint_hold.tick() is True
    frozen = deg(sim)[0]
    sim.advance(2.0)
    assert deg(sim)[0] == pytest.approx(frozen, abs=1e-6)
    assert sim.move_get(job_id)[1]['job']['state'] == 'cancelled'


def test_ttl_is_capped_so_client_cannot_keep_motion_alive(api):
    c, hub, sim = api
    assert hold(c, ttl=60000).status_code == 200                # istemci 60 sn ister
    sim.advance(0.7)                                            # üst sınır 0,5 sn
    assert hub.joint_hold.tick() is True


def test_joint_limit_is_rejected_without_motion(api):
    c, hub, sim = api
    sim.set_joints([0, sim.sm.upper[1] - 0.0005, *sim.home_q[2:]])      # J2 üst limitte
    before = deg(sim)
    r = hold(c, joint=1, sign=1)
    assert r.status_code == 409 and r.json()['error'] == 'joint_limit'
    assert not hub.joint_hold.active and deg(sim) == before


def test_safe_zone_shrinks_target_or_rejects_and_never_leaves_box(api):
    c, hub, sim = api
    r = hold(c, joint=0, sign=1)                                # J1 +60° yayı kutu dışına çıkar -> kısaltılır
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        for _ in range(int(20 / sim.tick_s)):
            sim.advance(sim.tick_s)
            hold(c, joint=0, sign=1)
            if not hub.joint_hold.active:
                break
        y = sim.snapshot()['tcp']['pos_m'][1]
        lo, hi = sim.snapshot()['safe_zone']['min'][1], sim.snapshot()['safe_zone']['max'][1]
        assert lo - 1e-6 <= y <= hi + 1e-6


def test_estop_returns_423_and_cuts_motion(api):
    c, hub, sim = api
    assert hold(c).status_code == 200
    assert c.post('/api/estop').status_code == 200
    assert hold(c).status_code == 423 and hold(c).json()['error'] == 'estop_latched'
    assert not hub.joint_hold.active
    frozen = deg(sim)
    sim.advance(1.0)
    assert deg(sim) == frozen
    release = c.post('/api/jog/joint', json={'lease': LEASE, 'ttl_ms': 0})
    assert release.status_code == 200                             # bırakma ACİL DUR'da da serbest


def test_other_lease_is_busy_and_bad_bodies_are_400(api):
    c, hub, sim = api
    assert hold(c).status_code == 200
    other = c.post('/api/jog/joint', json={'lease': 'baska', 'joint': 1, 'dir': 1, 'ttl_ms': 250})
    assert other.status_code == 409 and other.json()['error'] == 'busy'
    bads = ({}, {'lease': LEASE}, {'lease': LEASE, 'joint': 9, 'dir': 1},
            {'lease': LEASE, 'joint': 0, 'dir': 2}, {'lease': LEASE, 'joint': True, 'dir': 1})
    for bad in bads:
        assert c.post('/api/jog/joint', json=bad).status_code == 400, bad


def test_direction_change_replaces_job_and_remote_client_is_refused(api):
    c, hub, sim = api
    hold(c, joint=0, sign=1)
    first = hub.joint_hold.job_id
    assert hold(c, joint=0, sign=-1).status_code == 200 and hub.joint_hold.job_id != first
    phone = TestClient(create_app(hub), base_url='http://192.168.1.50', client=('192.168.1.50', 40000))
    r = phone.post('/api/jog/joint', json={'lease': 'telefon', 'joint': 0, 'dir': 1, 'ttl_ms': 250})
    assert r.status_code == 403 and r.json()['error'] == 'hold_local_only'


def test_status_reports_joint_hold_and_home_deg(api):
    c, hub, sim = api
    assert c.get('/api/jog').json()['joint_hold'] is True
    want = [round(float(x), 1) for x in np.degrees(sim.home_q)]
    assert c.get('/api/state').json()['robot']['home_deg'] == want
