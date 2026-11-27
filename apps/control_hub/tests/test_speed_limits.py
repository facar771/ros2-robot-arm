"""P1d: hız kartının sunucu tarafı — GET /api/speed/limits, POST /api/sim/speed {scale}.

Formül: etkin üst hız = profil limiti × tavan ölçeği × hız %. Eklem hareketinde (move_j) ve jog'da
en yavaş eklemle birlikte varır (senkron profil / adım orantılı küçülür). Zaman sahte saatle ilerler."""
import math
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, FakeBackend, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.hub_core.sim_plan import RAMP, SCALE_MAX, plan_joint
from control_hub.server import create_app
from tests.conftest import CONFIG

BODY_423 = ('{"ok":false,"error":"estop_latched","message":"ACİL DUR aktif: önce kilidi aç",'
            '"state":"estopped"}')


def build(backend):
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    return TestClient(create_app(hub), base_url='http://localhost', client=('127.0.0.1', 50000)), hub


@pytest.fixture
def sim_api():
    backend = SimBackend(clock=SimClock(), autostart=False)
    client, hub = build(backend)
    yield client, hub, backend
    hub.close()


def limits(client):
    r = client.get('/api/speed/limits')
    assert r.status_code == 200, r.text
    return r.json()


# --- formül ------------------------------------------------------------------------------------
def test_default_scale_is_the_profile_ceiling_and_limits_follow_the_formula(sim_api):
    client, _, sim = sim_api
    d = limits(client)
    assert d['mode'] == 'sim' and d['scale_locked'] is False
    assert d['scale'] == pytest.approx(0.3) == d['scale_default'] and d['speed_pct'] == 50
    assert [j['name'] for j in d['joints']] == list(sim.profile.joint_names)
    for j, v in zip(d['joints'], sim.sm.vmax):
        assert j['limit_rad_s'] == pytest.approx(v, abs=1e-4)
        assert j['limit_deg_s'] == pytest.approx(math.degrees(v), abs=0.01)
        assert j['effective_rad_s'] == pytest.approx(v * 0.3 * 0.5, abs=1e-4)           # limit × ölçek × %
        assert j['effective_deg_s'] == pytest.approx(math.degrees(v) * 0.3 * 0.5, abs=0.01)
    assert d['limiting_joint'] == 'joint_2'                                # en yavaş (0,08 rad/s)


def test_effective_speed_tracks_pct_and_scale(sim_api):
    client, _, _ = sim_api
    assert client.post('/api/sim/speed', json={'speed_pct': 100, 'scale': 1.0}).status_code == 200
    d = limits(client)
    assert d['speed_pct'] == 100 and d['scale'] == 1.0
    assert all(j['effective_rad_s'] == pytest.approx(j['limit_rad_s'], abs=1e-4) for j in d['joints'])
    assert d['example_move']['joint'] == 'joint_2' and d['example_move']['deg'] == 6.0


@pytest.mark.parametrize('scale', [0.001, 0.05, 0.3, 0.5, 1.0])
def test_scale_up_to_one_is_accepted_and_stays_within_the_profile_limit(sim_api, scale):
    client, _, _ = sim_api
    r = client.post('/api/sim/speed', json={'speed_pct': 100, 'scale': scale})
    assert r.status_code == 200 and r.json()['scale'] == scale
    d = limits(client)
    assert d['scale'] == scale and d['above_profile'] is False
    assert all(j['effective_rad_s'] <= j['limit_rad_s'] + 1e-9 for j in d['joints'])


@pytest.mark.parametrize('scale', [SCALE_MAX + 0.1, 0, -0.3, 'x', None, True])
def test_out_of_range_scale_is_rejected_not_clipped(sim_api, scale):
    client, _, _ = sim_api
    r = client.post('/api/sim/speed', json={'speed_pct': 80, 'scale': scale})
    assert r.status_code == 400 and r.json()['error'] == 'bad_request'
    d = limits(client)
    assert d['scale'] == pytest.approx(0.3) and d['speed_pct'] == 50     # ikisi de değişmedi


def test_invalid_pct_with_valid_scale_changes_nothing(sim_api):
    client, _, _ = sim_api
    assert client.post('/api/sim/speed', json={'speed_pct': 100001, 'scale': 0.8}).status_code == 400
    assert limits(client)['scale'] == pytest.approx(0.3)


def test_scale_alone_keeps_the_percent_and_pct_alone_keeps_the_old_behaviour(sim_api):
    client, _, _ = sim_api
    r = client.post('/api/sim/speed', json={'scale': 0.6})
    assert r.json() == {'ok': True, 'speed_pct': 50, 'scale': 0.6, 'accel_scale': 0.6, 'accel_linked': True}
    assert client.post('/api/sim/speed', json={'speed_pct': 20}).json() == {'ok': True, 'speed_pct': 20}
    assert limits(client)['scale'] == 0.6


# --- gerçek/tezgâh modu ------------------------------------------------------------------------
def test_non_sim_backend_locks_the_scale_with_409_sim_only():
    client, hub = build(FakeBackend(time_scale=0.02))
    try:
        r = client.post('/api/sim/speed', json={'speed_pct': 50, 'scale': 0.8})
        assert r.status_code == 409 and r.json()['error'] == 'sim_only'
        d = limits(client)
        assert d['scale_locked'] is True and d['scale'] is None and d['joints'] == []
        assert 'SİM' in d['locked_reason']
    finally:
        hub.close()


# --- ACİL DUR ----------------------------------------------------------------------------------
def test_estop_blocks_scale_changes_with_423_but_not_reads(sim_api):
    client, hub, _ = sim_api
    assert client.post('/api/estop').status_code == 200 and hub.estopped
    r = client.post('/api/sim/speed', json={'speed_pct': 100, 'scale': 1.0})
    assert r.status_code == 423 and r.text == BODY_423
    assert limits(client)['scale'] == pytest.approx(0.3)


# --- "bir SONRAKİ komutta geçerli" --------------------------------------------------------------
def test_running_job_keeps_its_plan_and_the_next_one_uses_the_new_scale(sim_api):
    client, _, sim = sim_api
    r = client.post('/api/move', json={'type': 'joint', 'speed_pct': 100,
                                       'joints_deg': [d + (4 if i == 1 else 0) for i, d in
                                                      enumerate(j['deg'] for j in sim.snapshot()['joints'])]})
    assert r.status_code == 202
    first = sim._job.plan.duration_s
    assert client.post('/api/sim/speed', json={'scale': 1.0}).status_code == 200
    assert sim._job.plan.duration_s == first                          # çalışan iş değişmedi
    assert sim.jog.engine.safety.max_velocity_scale == 1.0           # jog tavanı canlı okur


# --- doğrulama (madde 5): tüm eklemler en yavaş eklemle birlikte varır ---------------------------
def test_move_j_joints_arrive_together_and_duration_follows_the_slowest(sim_api):
    _, _, sim = sim_api
    q0 = sim.home_q.copy()
    goal = np.degrees(q0) + np.array([5.0, 3.0, 8.0, 10.0, 6.0, 12.0])
    for pct in (100, 40):
        plan = plan_joint(sim.sm, q0, goal, pct)
        k = sim.sm.scale_for(pct)
        delta = np.abs(np.radians(goal) - q0)
        t_vel = float(np.max(delta / (sim.sm.vmax * k))) / (1.0 - RAMP)
        # yukarıdaki satır: T = max(|Δq|/(v·ölçek·%)) / (1-rampa)
        assert plan.duration_s >= t_vel - 1e-9                              # ivme sınırı yalnız uzatabilir
        # tek skaler s(t): her eklem aynı oranda ilerler, hepsi T anında hedefte
        s_end = (plan.path[-1] - q0) / (np.radians(goal) - q0)
        assert np.allclose(s_end, 1.0, atol=1e-9)
        mid = plan.q_at(plan.duration_s * 0.37)
        frac = (mid - q0) / (np.radians(goal) - q0)
        assert np.allclose(frac, frac[0], atol=1e-9)
        peak = delta / (plan.duration_s * (1.0 - RAMP))                    # trapez tepe hızı
        assert np.all(peak <= sim.sm.vmax * k + 1e-9)                       # hiçbir eklem limitini aşmaz


def test_jog_step_shrinks_proportionally_so_the_slowest_joint_sets_the_pace(sim_api):
    _, _, sim = sim_api
    eng = sim.jog.engine
    eng.config = replace(eng.config, speed_scale=1.0, max_linear_speed=50.0)
    eng.safety = replace(eng.safety, max_velocity_scale=1.0)
    q, dt = sim.home_q.copy(), 0.02
    tiny = eng.step(q, [0.0005, 0, 0, 0, 0, 0], dt)
    eng2_big = eng.step(q, [30.0, 0, 0, 0, 0, 0], dt)
    assert any('eklem hız sınırı' in m for m in eng2_big.messages)
    ratio = np.abs(eng2_big.dq) / dt / sim.sm.vmax
    assert ratio.max() == pytest.approx(1.0, rel=1e-6)                       # en kısıtlı eklem tam limitte
    a, b = tiny.dq / np.linalg.norm(tiny.dq), eng2_big.dq / np.linalg.norm(eng2_big.dq)
    assert float(a @ b) == pytest.approx(1.0, abs=1e-3)       # yön korunur: adım orantılı küçülür


def test_card_asks_with_its_own_percent_and_bad_percent_is_400(sim_api):
    client, _, _ = sim_api
    d = client.get('/api/speed/limits', params={'speed_pct': 20}).json()
    assert d['speed_pct'] == 20
    assert d['joints'][1]['effective_rad_s'] == pytest.approx(0.08 * 0.3 * 0.2, abs=1e-4)
    assert limits(client)['speed_pct'] == 50                           # robotun genel hızı değişmedi
    assert client.get('/api/speed/limits', params={'speed_pct': 0}).status_code == 400
    assert client.get('/api/speed/limits', params={'speed_pct': 100001}).status_code == 400
