"""S1: SİM arka ucu (gerçek kinematik) + /api/move* + /api/sim* — UI_CONTRACT §9-A/B kabul kriterleri.

Zaman sahte saatle (``SimClock`` + ``advance``) ilerler: gerçek bekleme/iş parçacığı yok, deterministik."""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_assets import SimAssetsError, available_robots, profile_roots, resolve
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.hub_core.sim_jobs import parse_move
from control_hub.hub_core.sim_plan import PlanError, trapezoid
from control_hub.server import create_app
from tests.conftest import CONFIG

LEASE = 'sim-test-lease-1'


@pytest.fixture
def sim():
    clock = SimClock()
    b = SimBackend(clock=clock, autostart=False)
    yield b
    b.close()


def joints_deg(b):
    return [j['deg'] for j in b.snapshot()['joints']]


def home_deg(b):
    return [math.degrees(v) for v in b.home_q]


def run_until_done(b, job_id, limit_s=120):
    for _ in range(int(limit_s / b.tick_s)):
        state = b.move_get(job_id)[1]['job']['state']
        if state not in ('running', 'paused'):
            return state
        b.advance(b.tick_s)
    raise AssertionError('iş bitmedi')


# --- gerçek kinematik --------------------------------------------------------------------------
def test_zero_pose_tcp_matches_robot_core_exactly(sim):
    from robot_core.kinematics import Chain
    from robot_core.profile import load_profile
    chain = Chain(load_profile(sim._profile_path))
    for q in (np.zeros(6), sim.home_q, np.array([0.3, 1.2, 0.9, 0.2, -0.4, 0.1])):
        sim.set_joints(q)
        got = sim.snapshot()['tcp']['pos_m']
        assert np.allclose(got, chain.fk(np.array(list(sim.joint_dict().values())))[:3, 3], atol=1e-4)
    sim.set_joints(np.zeros(6))      # sıfır poz limit dışı: set_joints KIRPAR (limitleri gevşetmez)
    assert all(lo <= v <= hi for v, lo, hi in zip(sim._q, sim.sm.lower, sim.sm.upper))


def test_snapshot_has_contract_fields_and_keeps_legacy_ones(sim):
    s = sim.snapshot()
    assert s['mode'] == 'sim' and s['maturity'] == 'tahmini' and s['stale'] is False
    assert 'ölçüm değildir' in s['note']
    assert s['connected'] and s['speed_pct'] == 50 and s['gripper'] == {'open_pct': 0.0}
    assert {'frame', 'position', 'wpr_deg', 'pos_m', 'rpy_deg'} <= set(s['tcp'])
    assert s['tcp']['pos_m'] == s['tcp']['position']
    assert s['tcp']['rpy_deg'] == s['tcp']['wpr_deg']
    assert s['safe_zone'] == {'min': [0.06, -0.1, 0.02], 'max': [0.22, 0.1, 0.22]}
    j = s['joints'][1]
    assert {'name', 'rad', 'deg', 'min_deg', 'max_deg', 'near_limit'} <= set(j)
    assert j['min_deg'] < j['deg'] < j['max_deg']
    assert s['estop'] == {'tripped': False, 'reason': None, 'since': None}
    assert sim.caps == ('sim',)


def test_near_limit_flag_is_within_five_percent_of_the_range(sim):
    lo, hi = sim.sm.lower[1], sim.sm.upper[1]
    sim.set_joints([0, lo + 0.04 * (hi - lo), 1.0, 0, 0, 0])
    assert sim.snapshot()['joints'][1]['near_limit'] is True
    sim.set_joints([0, lo + 0.2 * (hi - lo), 1.0, 0, 0, 0])
    assert sim.snapshot()['joints'][1]['near_limit'] is False


# --- doğrulama ve ret kodları ------------------------------------------------------------------
def test_joint_limit_target_is_409_joint_limit(sim):
    st, doc = sim.move_submit({'type': 'joint', 'joints_deg': [0, 200, 60, 0, 0, 0]})
    assert (st, doc['error']) == (409, 'joint_limit') and 'joint_2' in doc['message']
    assert joints_deg(sim)[1] == pytest.approx(math.degrees(sim.home_q[1]), abs=0.1)        # robot oynamadı


def test_target_outside_safe_zone_is_409(sim):
    st, doc = sim.move_submit({'type': 'linear', 'pos_m': [0.5, 0.0, 0.1]})
    assert (st, doc['error']) == (409, 'outside_safe_zone')
    st, doc = sim.move_submit({'type': 'joint', 'joints_deg': [0, 10, 10, 0, 0, 0]})        # FK ucu kutu dışı
    assert (st, doc['error']) == (409, 'outside_safe_zone')


def test_unreachable_orientation_is_409_unreachable(sim):
    tcp = sim.snapshot()['tcp']['pos_m']
    st, doc = sim.move_submit({'type': 'linear', 'pos_m': tcp, 'rpy_deg': [90, 0, 0]})
    assert (st, doc['error']) == (409, 'unreachable') and doc['at_pct'] > 0


@pytest.mark.parametrize('body', [None, [], {'type': 'orbit'}, {'type': 'joint'},
                                  {'type': 'joint', 'joints_deg': [1]},
                                  {'type': 'joint', 'joints_deg': [0, 1, 1, 0, 0, float('nan')]},
                                  {'type': 'joint', 'joints_deg': [0] * 6, 'extra': 1},
                                  {'type': 'linear'}, {'type': 'linear', 'pos_m': [0.1, 0, 'x']},
                                  {'type': 'linear', 'pos_m': [0.1, 0, 0.1], 'speed_pct': 0},
                                  {'type': 'linear', 'pos_m': [0.1, 0, 0.1], 'speed_pct': True},
                                  {'type': 'tool', 'open_pct': 120}])
def test_malformed_bodies_are_400_bad_request(sim, body):
    st, doc = sim.move_submit(body)
    assert (st, doc['ok'], doc['error']) == (400, False, 'bad_request')
    assert sim.move_check(body)[1]['ok'] is False


def test_check_never_moves_the_robot_and_matches_submit(sim):
    before = joints_deg(sim)
    ok_body = {'type': 'joint', 'joints_deg': [20, 95, 65, 0, -10, 0]}
    bad_body = {'type': 'joint', 'joints_deg': [0, 200, 60, 0, 0, 0]}
    assert sim.move_check(ok_body) == (200, {'ok': True, 'violations': []})
    st, doc = sim.move_check(bad_body)
    assert st == 200 and doc['ok'] is False and doc['violations'][0]['kind'] == 'joint_limit'
    assert set(doc['violations'][0]) == {'kind', 'at_pct', 'message'}
    sim.advance(5)
    assert joints_deg(sim) == before and sim.move_get('yok')[0] == 404
    assert sim.snapshot()['job'] is None                       # check iş AÇMAZ
    # aynı gövde submit'te aynı kararı verir
    assert sim.move_submit(ok_body)[0] == 202


# --- hareket: trapez, hız tavanı, süre ----------------------------------------------------------
def test_trapezoid_profile_shape():
    assert trapezoid(0) == 0 and trapezoid(1) == pytest.approx(1.0)
    assert trapezoid(-3) == 0 and trapezoid(9) == 1
    xs = [trapezoid(i / 200) for i in range(201)]
    assert all(b >= a for a, b in zip(xs, xs[1:]))             # tek yönlü
    assert trapezoid(0.5) == pytest.approx(0.5)                # simetri
    assert trapezoid(0.25) + trapezoid(0.75) == pytest.approx(1.0)


def test_joint_move_runs_to_target_within_speed_and_accel_caps(sim):
    target = [10, 100, 60, 5, -10, 5]
    st, doc = sim.move_submit({'type': 'joint', 'joints_deg': target, 'speed_pct': 100})
    assert st == 202
    job = sim._jobs[doc['job_id']]
    dur = job.duration_s
    scale = sim.sm.velocity_cap                                     # %100 = profil tavanı (Hub gevşetemez)
    assert job.plan.speed_scale == pytest.approx(scale)
    qs, prev, vmax_seen = [], np.array(sim.home_q), np.zeros(6)
    for _ in range(int(dur / sim.tick_s) + 2):
        sim.advance(sim.tick_s)
        q = np.array(list(sim.joint_dict().values()))
        vmax_seen = np.maximum(vmax_seen, np.abs(q - prev) / sim.tick_s)
        prev = q
        qs.append(q)
    assert sim.move_get(doc['job_id'])[1]['job']['state'] == 'done'
    assert np.allclose(np.degrees(qs[-1]), target, atol=0.05)
    assert np.all(vmax_seen <= sim.sm.vmax * scale * 1.05 + 1e-6)   # eklem hız tavanı aşılmadı
    assert sim.snapshot()['moving'] is False and sim.snapshot()['job'] is None


def test_speed_pct_scales_duration(sim):
    d = {}
    for pct in (100, 25):
        sim.set_joints(sim.home_q)
        st, doc = sim.move_submit({'type': 'joint', 'joints_deg': [10, 100, 60, 5, -10, 5], 'speed_pct': pct})
        d[pct] = sim._jobs[doc['job_id']].duration_s
        run_until_done(sim, doc['job_id'])
    assert 3.5 < d[25] / d[100] <= 4.0 + 1e-6 or d[25] > d[100] * 3


def test_linear_move_reaches_target_pose_in_a_straight_line(sim):
    from robot_core.kinematics import Chain
    start = np.array(sim.snapshot()['tcp']['pos_m'])
    goal = start + np.array([-0.02, 0.03, -0.015])
    st, doc = sim.move_submit({'type': 'linear', 'pos_m': goal.tolist(), 'speed_pct': 100})
    assert st == 202, doc
    chain = Chain(sim.profile)
    worst = 0.0
    while sim.move_get(doc['job_id'])[1]['job']['state'] == 'running':
        sim.advance(0.1)
        p = chain.fk(np.array(list(sim.joint_dict().values())))[:3, 3]
        line = goal - start
        worst = max(worst, float(np.linalg.norm(np.cross(p - start, line)) / np.linalg.norm(line)))
    end = chain.fk(np.array(list(sim.joint_dict().values())))[:3, 3]
    assert np.linalg.norm(end - goal) < 5e-4                    # hedefe ~0.5 mm içinde
    assert worst < 1.5e-3                                       # doğrudan sapma < 1.5 mm
    assert sim.snapshot()['tcp']['pos_m'] == pytest.approx(goal.round(4).tolist(), abs=6e-4)


def test_pause_resume_stop_and_busy(sim):
    body = {'type': 'joint', 'joints_deg': [10, 100, 60, 5, -10, 5], 'speed_pct': 100}
    st, doc = sim.move_pause()
    assert (st, doc['error']) == (409, 'not_running')
    _, doc = sim.move_submit(body)
    sim.advance(1)
    assert sim.move_submit(body)[1]['error'] == 'busy' and sim.sim_reset()[1]['error'] == 'busy'
    assert sim.move_pause() == (200, {'ok': True})
    frozen = joints_deg(sim)
    sim.advance(2)
    assert joints_deg(sim) == frozen and sim.move_get(doc['job_id'])[1]['job']['state'] == 'paused'
    assert sim.move_submit(body)[1]['error'] == 'busy'          # duraklatılmış iş de meşgul sayılır
    assert sim.move_resume() == (200, {'ok': True})
    sim.advance(1)
    assert joints_deg(sim) != frozen
    assert sim.move_stop()[0] == 200
    held = joints_deg(sim)
    sim.advance(2)
    assert joints_deg(sim) == held and sim.move_get(doc['job_id'])[1]['job']['state'] == 'cancelled'
    assert sim.move_stop()[0] == 409


def test_tool_move_opens_and_closes_gripper(sim):
    st, doc = sim.move_submit({'type': 'tool', 'open_pct': 80})
    assert st == 202 and sim.move_check({'type': 'tool', 'open_pct': 80})[1]['ok']
    assert run_until_done(sim, doc['job_id']) == 'done'
    assert sim.snapshot()['gripper']['open_pct'] == pytest.approx(80.0)
    assert joints_deg(sim) == [pytest.approx(math.degrees(v), abs=0.1) for v in sim.home_q]   # eklemler sabit


# --- ACİL DUR ----------------------------------------------------------------------------------
def test_estop_cuts_motion_blocks_everything_and_never_auto_resumes_or_homes(sim):
    _, doc = sim.move_submit({'type': 'joint', 'joints_deg': [10, 100, 60, 5, -10, 5], 'speed_pct': 100})
    sim.advance(2)
    mid = joints_deg(sim)
    rep = sim.emergency_stop()
    assert rep.stopped is True and rep.details['was_running'] is True and sim.snapshot()['estop']['tripped']
    sim.advance(3)
    assert joints_deg(sim) == mid                               # anında durdu, kendiliğinden ilerlemedi
    job = sim.move_get(doc['job_id'])[1]['job']
    assert job['state'] == 'cancelled' and job['error'] == 'estop_latched'
    home_move = {'type': 'joint', 'joints_deg': home_deg(sim)}
    for call in (lambda: sim.move_submit(home_move), sim.move_resume, sim.sim_reset):
        st, d = call()
        assert (st, d['error']) == (423, 'estop_latched')
    assert sim.move_check(home_move)[0] == 200                  # check hareket etmez: serbest
    assert sim.reset_emergency_stop().ok
    sim.advance(5)
    assert joints_deg(sim) == mid                               # reset'ten sonra ne devam ne home
    assert sim.move_submit(home_move)[0] == 202                 # yeniden komut verilebilir


def test_estop_during_jog_stops_it(sim):
    st, _ = sim.jog_command({'lease': LEASE, 'frame': 'World', 'increment': {'axis': 0, 'amount': 10}})
    assert st == 200
    sim.advance(0.04)
    sim.emergency_stop()
    sim.advance(0.5)
    held = joints_deg(sim)
    sim.advance(1)
    assert joints_deg(sim) == held
    assert sim.jog_command({'lease': LEASE, 'increment': {'axis': 0, 'amount': 1}})[0] == 423


# --- gerçek jog --------------------------------------------------------------------------------
def test_real_jog_increment_moves_the_tcp_through_real_joints(sim):
    p0 = np.array(sim.snapshot()['tcp']['pos_m'])
    # home pozu tekilliğe yakın: motor hızı kendiliğinden düşer (gerçek robot_core davranışı); 1 mm kısa sürer
    st, doc = sim.jog_command({'lease': LEASE, 'frame': 'World', 'increment': {'axis': 2, 'amount': -1}})
    assert st == 200 and doc['mode'] == 'increment'
    sim.advance(4)
    p1 = np.array(sim.snapshot()['tcp']['pos_m'])
    assert p1[2] - p0[2] == pytest.approx(-0.001, abs=1.5e-4) and np.linalg.norm(p1[:2] - p0[:2]) < 3e-4
    assert joints_deg(sim) != home_deg(sim)                     # eklemler gerçekten değişti (sıfır değil)
    assert sim.jog_status()[0] == 200 and sim.jog_status()[1]['tcp']['position'] == p1.round(4).tolist()


def test_jog_and_moves_exclude_each_other(sim):
    sim.jog_command({'lease': LEASE, 'frame': 'World', 'increment': {'axis': 0, 'amount': 10}})
    st, doc = sim.move_submit({'type': 'joint', 'joints_deg': home_deg(sim)})
    assert (st, doc['error']) == (409, 'busy')
    sim.advance(3)                                              # artım biter; jog artık etkin değil
    sim.advance(3)
    st, _ = sim.move_submit({'type': 'joint', 'joints_deg': [10, 95, 65, 0, -10, 0]})
    assert st == 202
    sim.advance(0.2)
    assert sim.jog_command({'lease': LEASE, 'increment': {'axis': 0, 'amount': 1}})[0] in (200, 409)


def test_frames_and_model_endpoints_work_in_sim(sim):
    st, doc = sim.frames_get()
    assert st == 200 and doc['frames'] == [] and 'World' in doc['builtin']
    st, doc = sim.model()
    names = [j['name'] for j in doc['joints']]
    assert st == 200 and doc['robot'] == sim.robot and names == list(sim.profile.joint_names)
    st, doc = sim.frames_put({'frames': 'bozuk'})
    assert st == 400 and doc['ok'] is False
    assert sim.frames_reach({'center': [0.15, 0, 0.1], 'radius': 0.05})[0] in (200, 400)


# --- komutlar (senaryolar) ---------------------------------------------------------------------
def test_scenario_commands_home_ready_objects_and_unknown(sim):
    import threading
    sim.set_joints(sim.home_q + 0.0)
    assert sim.execute('objects', threading.Event()).ok
    r = sim.execute('above red 0.2', threading.Event())
    assert r.ok is False and 'bilmiyor' in r.message
    assert sim.execute('rm -rf /', threading.Event()).data.get('rejected')


def test_execute_home_runs_to_the_named_pose():
    b = SimBackend(time_scale=0.005, tick_s=0.02)               # gerçek saat: iş parçacığı + hızlandırılmış
    try:
        import threading
        b.set_joints(b.home_q)
        assert b.execute('ready', threading.Event()).ok
        ready = b.profile.named_poses['ready']
        assert np.allclose(list(b.joint_dict().values()), ready, atol=1e-3)
        assert b.execute('home', threading.Event()).ok
        assert np.allclose(list(b.joint_dict().values()), b.home_q, atol=1e-3)
    finally:
        b.close()


# --- profil/varlık çözümü ----------------------------------------------------------------------
def test_profile_resolution_default_and_errors(monkeypatch, tmp_path):
    name, prof, view = resolve(None)
    assert name == 'karma_robot' and prof.is_file() and view.name == 'view_model.json'
    assert 'karma_robot' in available_robots(prof.parents[1])
    for bad in ('../x', 'KARMA', 'yok_robot'):
        with pytest.raises(SimAssetsError):
            resolve(bad)
    monkeypatch.setenv('HUB_PROFILE_DIR', str(tmp_path))          # boş klasör: depodaki profile düşer
    assert profile_roots()[0] == tmp_path and resolve(None)[0] == 'karma_robot'


def test_parse_move_defaults_and_speed_validation():
    assert parse_move({'type': 'joint', 'joints_deg': [1]}, 40) == ('joint', ([1],), 40.0)
    assert parse_move({'type': 'linear', 'pos_m': [1, 2, 3]}, 40)[1] == ([1, 2, 3], None, 'fixed')
    for bad in ({'type': 'tool', 'open_pct': -1}, {'type': 'joint', 'speed_pct': 100001}):
        with pytest.raises(PlanError):
            parse_move(bad, 40)


# --- HTTP uçları -------------------------------------------------------------------------------
@pytest.fixture
def api():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    backend = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    client = TestClient(create_app(hub), base_url='http://localhost')
    yield client, hub, backend
    hub.close()


JSON = {'content-type': 'application/json'}


def test_http_move_flow_and_error_codes(api):
    c, hub, sim = api
    body = {'type': 'joint', 'joints_deg': [10, 100, 60, 5, -10, 5], 'speed_pct': 100}
    r = c.post('/api/move/check', json=body)
    assert r.status_code == 200 and r.json() == {'ok': True, 'violations': []}
    r = c.post('/api/move', json=body)
    assert r.status_code == 202 and r.json()['ok'] is True
    job_id = r.json()['job_id']
    again = c.post('/api/move', json=body)
    assert again.status_code == 409 and again.json()['error'] == 'busy'
    sim.advance(1)
    job = c.get(f'/api/move/{job_id}').json()['job']
    assert job['state'] == 'running' and 0 < job['progress_pct'] < 100
    assert c.post('/api/move/pause', json={}).json() == {'ok': True}
    assert c.post('/api/move/resume', json={}).json() == {'ok': True}
    assert c.post('/api/move/stop', json={}).status_code == 200
    assert c.get('/api/move/yok').status_code == 404
    r = c.post('/api/move', json={'type': 'joint', 'joints_deg': [0, 200, 0, 0, 0, 0]})
    assert r.status_code == 409 and r.json()['error'] == 'joint_limit' and r.json()['ok'] is False
    assert c.post('/api/move', json={'type': 'x'}).status_code == 400
    assert c.post('/api/move', content=b'{bozuk', headers=JSON).status_code == 400
    assert c.post('/api/move', content=b'x' * 9000, headers=JSON).status_code == 413
    state = c.get('/api/state').json()
    assert state['robot']['mode'] == 'sim'
    assert state['caps'] == ['sim']


def test_http_estop_returns_423_on_every_motion_path(api):
    c, hub, sim = api
    c.post('/api/move', json={'type': 'joint', 'joints_deg': [10, 100, 60, 5, -10, 5]})
    assert c.post('/api/estop').status_code == 200
    body = {'type': 'joint', 'joints_deg': home_deg(sim)}
    for path in ('/api/move', '/api/move/resume', '/api/sim/reset'):
        r = c.post(path, json=body)
        assert r.status_code == 423 and r.json()['error'] == 'estop_latched', path
    assert c.post('/api/jog', json={'lease': LEASE, 'increment': {'axis': 0, 'amount': 1}}).status_code == 423
    assert c.post('/api/move/check', json=body).status_code == 200


def test_http_sim_reset_and_speed(api):
    c, hub, sim = api
    sim.set_joints([0.5, 1.0, 1.0, 0, 0, 0])
    assert c.post('/api/sim/reset', json={}).json() == {'ok': True}
    assert joints_deg(sim) == [pytest.approx(math.degrees(v), abs=0.1) for v in sim.home_q]
    assert c.post('/api/sim/speed', json={'speed_pct': 80}).json() == {'ok': True, 'speed_pct': 80}
    assert c.get('/api/state').json()['robot']['speed_pct'] == 80
    for bad in (0, -5, 100001, 'x', None, True):
        assert c.post('/api/sim/speed', json={'speed_pct': bad}).status_code == 400


def test_http_pin_mode_protects_move_endpoints(scenarios, ui):
    hub = Hub(SimBackend(clock=SimClock(), autostart=False), scenarios, ui, AccessControl('pin', '123456'))
    try:
        c = TestClient(create_app(hub), base_url='http://localhost')
        for method, path in (('post', '/api/move'), ('post', '/api/move/check'), ('post', '/api/move/stop'),
                             ('get', '/api/move/x'), ('post', '/api/sim/reset'), ('post', '/api/sim/speed')):
            kw = {'json': {}} if method == 'post' else {}
            assert getattr(c, method)(path, **kw).status_code == 401, path
    finally:
        hub.close()


def test_fake_and_real_backends_answer_501_and_409_not_sim(make_client):
    c, _ = make_client('open')
    r = c.post('/api/move', json={'type': 'joint', 'joints_deg': [0] * 6})
    assert r.status_code == 501 and r.json()['error'] == 'move_unsupported'
    r = c.post('/api/sim/reset', json={})
    assert r.status_code == 409 and r.json()['error'] == 'not_sim'


def test_sim_check_config_cli_passes_and_reports_fk(capsys):
    from control_hub.cli import main
    assert main(['--sim', '--check-config', '--no-agent']) == 0
    out = capsys.readouterr().out
    assert 'SİM karma_robot: 6 eklem, FK robot_core ile aynı' in out and 'Yapılandırma geçerli' in out
    assert main(['--sim', '--fake', '--check-config', '--no-agent']) == 2
    assert main(['--sim', '--robot', 'yok_robot', '--check-config', '--no-agent']) == 2
