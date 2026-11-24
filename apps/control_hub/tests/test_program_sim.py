"""P2 + S1: Program koşucusu GERÇEK SİM arka ucuna bağlı (``hub.backend.move_*``), mock yalnız ``--fake``'te.

Zaman ``SimClock`` + ``advance`` ile ilerler (SİM tarafı deterministik); koşucu kendi iş parçacığında gerçek
saatle poll eder, test thread'i SİM saatini ilerletir."""
import math
import time

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, FakeBackend, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.program_move import (
    BackendMoveClient,
    SimMoveClient,
    make_move_client,
    supports_move,
)
from control_hub.hub_core.program_runner import MoveRefused
from control_hub.hub_core.runner import EstopLatchedError
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG

TARGET = [10, 100, 60, 5, -10, 5]                       # test_sim_backend ile aynı, limit içi hedef
JOINT = {'type': 'joint', 'joints_deg': TARGET, 'speed_pct': 100}


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('HUB_DATA_DIR', str(tmp_path / 'data'))


@pytest.fixture
def sim():
    b = SimBackend(clock=SimClock(), autostart=False)
    yield b
    b.close()


@pytest.fixture
def mover(sim):
    return BackendMoveClient(sim)


def joints(b):
    return [j['deg'] for j in b.snapshot()['joints']]


def finish(mover, sim, job, limit_s=120):
    for _ in range(int(limit_s / sim.tick_s)):
        st = mover.status(job)
        if st['state'] != 'running':
            return st
        sim.advance(sim.tick_s)
    raise AssertionError('iş bitmedi')


# --- hangi istemci seçilir ---------------------------------------------------------------------------
def test_backend_with_move_methods_gets_the_real_adapter_and_fake_gets_the_mock(sim):
    assert supports_move(sim) and not supports_move(FakeBackend())
    ui_hub = type('H', (), {'backend': sim})()
    real = make_move_client(ui_hub)
    assert isinstance(real, BackendMoveClient) and real.mock is False
    assert (real.label, real.measured) == ('SİM', False)          # SİM: tahmini, ölçüm değil; ama SAHTE değil
    fake = make_move_client(type('H', (), {'backend': FakeBackend(time_scale=0.02)})())
    assert isinstance(fake, SimMoveClient) and fake.mock is True and fake.label == 'SAHTE (mock)'
    half = type('B', (), {'move_submit': lambda self, b: (202, {})})()      # eksik yöntem: yedeğe düş
    assert not supports_move(half)


# --- adaptör: sözleşme çevirisi ----------------------------------------------------------------------
def test_pose_is_the_backend_snapshot_and_a_round_trip_move_to_it_is_valid(mover, sim):
    pose = mover.pose()
    assert set(pose) == {'joints_deg', 'pos_m', 'rpy_deg', 'open_pct'}
    assert pose['joints_deg'] == joints(sim)
    assert len(pose['pos_m']) == 3 and len(pose['rpy_deg']) == 3
    for body in ({'type': 'joint', 'joints_deg': pose['joints_deg'], 'speed_pct': 50},
                 {'type': 'linear', 'pos_m': pose['pos_m'], 'rpy_deg': pose['rpy_deg'], 'speed_pct': 50}):
        assert mover.check(body) == {'ok': True, 'violations': []}, body      # kaydedilen poz gidilebilir


def test_check_does_not_move_the_robot(mover, sim):
    before = joints(sim)
    assert mover.check(JOINT)['ok'] is True
    bad = mover.check({'type': 'joint', 'joints_deg': [0, 200, 0, 0, 0, 0], 'speed_pct': 50})
    assert bad['ok'] is False and bad['violations'][0]['kind'] == 'joint_limit'
    sim.advance(1)
    assert joints(sim) == before


def test_start_runs_the_job_and_the_robot_really_moves(mover, sim):
    home = joints(sim)
    job = mover.start(JOINT)
    assert mover.status(job)['state'] == 'running'
    sim.advance(0.5)
    mid = joints(sim)
    assert mid != home and mid != [pytest.approx(v, abs=0.11) for v in TARGET]   # yolda: ara poz
    st = finish(mover, sim, job)
    assert st['state'] == 'done' and st['error'] is None
    assert joints(sim) == [pytest.approx(v, abs=0.11) for v in TARGET]


def test_refusals_become_move_refused_with_the_backend_reason(mover, sim):
    with pytest.raises(MoveRefused) as exc:
        mover.start({'type': 'joint', 'joints_deg': [0, 200, 0, 0, 0, 0], 'speed_pct': 50})
    assert (exc.value.status, exc.value.error) == (409, 'joint_limit')
    mover.start(JOINT)
    with pytest.raises(MoveRefused) as exc:
        mover.start(JOINT)
    assert (exc.value.status, exc.value.error) == (409, 'busy')
    with pytest.raises(MoveRefused) as exc:
        mover.start({'type': 'x'})
    assert exc.value.status == 400


def test_pause_freezes_resume_continues_stop_cancels(mover, sim):
    job = mover.start(JOINT)
    sim.advance(0.4)
    mover.pause()
    frozen = joints(sim)
    sim.advance(1)
    assert joints(sim) == frozen and mover.status(job)['state'] == 'running'   # duraklı iş "bitmedi"
    mover.resume()
    sim.advance(0.4)
    assert joints(sim) != frozen
    mover.stop()
    assert mover.status(job)['state'] == 'stopped'
    after = joints(sim)
    sim.advance(1)
    assert joints(sim) == after


def test_pause_resume_stop_without_a_running_job_are_harmless(mover, sim):
    mover.pause(), mover.resume(), mover.stop()                                # 409 not_running yutulur
    job = mover.start(JOINT)
    finish(mover, sim, job)
    mover.pause(), mover.resume(), mover.stop()                      # iş az önce bitti: yine zararsız


def test_an_unexpected_check_answer_is_a_violation_never_a_pass(sim):
    """Arka uç 200 dışı ya da bozuk gövde dönerse kuru çalıştırma GEÇMEZ (güvenli yön)."""
    class Odd:
        def __init__(self, reply):
            self.reply = reply

        def snapshot(self):
            return sim.snapshot()

        def move_check(self, body):
            return self.reply

    for reply in ((500, {'error': 'boom', 'message': 'patladı'}), (200, {'ok': True}), (200, {})):
        res = BackendMoveClient(Odd(reply)).check(JOINT)
        assert res['ok'] is False and res['violations'][0]['kind'], reply


def test_unknown_job_is_a_failure_not_an_exception(mover):
    st = mover.status('yok')
    assert st['state'] == 'failed' and st['error'] == 'unknown_job'


def test_estop_cancels_the_job_and_latched_calls_answer_423(mover, sim):
    job = mover.start(JOINT)
    sim.advance(0.3)
    sim.emergency_stop()
    st = mover.status(job)
    assert st['state'] == 'stopped' and st['error'] == 'estop_latched'
    frozen = joints(sim)
    sim.advance(1)
    assert joints(sim) == frozen
    with pytest.raises(MoveRefused) as exc:
        mover.start(JOINT)
    assert (exc.value.status, exc.value.error) == (423, 'estop_latched')
    with pytest.raises(EstopLatchedError):
        mover.resume()
    mover.stop()                                              # durdurmak ACİL DUR'da da serbest


# --- uçtan uca: Program sayfası uçları + SİM ---------------------------------------------------------
@pytest.fixture
def api():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    backend = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    client = TestClient(create_app(hub), base_url='http://localhost')
    yield client, hub, backend
    client.app.state.program_runner.cancel('test sonu')
    hub.close()


def program(steps):
    return {'version': 1, 'name': 'Sim', 'waypoints': [], 'steps': steps}


def drive(c, sim, pred, limit_s=60.0):
    """Gerçek saniyeyle sınırlı: koşucu poll eder; biz SİM saatini ilerletir, kalp atışı göndeririz."""
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        st = c.get('/api/program/state').json()['runner']
        if pred(st):
            return st
        c.post('/api/program/heartbeat', json={})
        sim.advance(0.25)
        time.sleep(0.03)
    raise AssertionError(f'zaman aşımı: {c.get("/api/program/state").json()["runner"]}')


def test_state_says_sim_not_mock_and_pose_is_the_simulated_robot(api):
    c, hub, sim = api
    body = c.get('/api/program/state').json()
    assert body['backend'] == {'label': 'SİM', 'measured': False, 'mock': False}
    assert body['pose']['joints_deg'] == joints(sim) and 'mock' not in body['pose']


def test_program_run_moves_the_3d_robot_to_every_target(api):
    c, hub, sim = api
    home = joints(sim)
    steps = [{'kind': 'move_j', 'joints_deg': TARGET}, {'kind': 'wait', 's': 0.01},
             {'kind': 'move_j', 'joints_deg': home}]
    r = c.post('/api/program/run', json={'program': program(steps), 'speed_pct': 100})
    assert r.status_code == 202, r.text
    seen = []
    def watch(st):
        seen.append(c.get('/api/state').json()['robot']['joints'][1]['deg'])
        return st['state'] == 'finished'

    drive(c, sim, watch)
    assert max(seen) > home[1] + 5                                  # ara: 3B robotun durumu gerçekten oynadı
    assert joints(sim) == [pytest.approx(v, abs=0.11) for v in home]      # sonra: başlangıca döndü
    robot = c.get('/api/state').json()['robot']
    assert robot['joints'][1]['deg'] == pytest.approx(home[1], abs=0.11)


def test_dry_run_violation_blocks_run_and_robot_stays_put(api):
    c, hub, sim = api
    before = joints(sim)
    bad = program([{'kind': 'move_j', 'joints_deg': [0, 200, 0, 0, 0, 0]}])
    chk = c.post('/api/program/check', json={'program': bad, 'speed_pct': 50}).json()
    assert chk['ok'] is False and chk['violations'] == 1
    assert chk['rows'][0]['violations'][0]['kind'] == 'joint_limit'
    r = c.post('/api/program/run', json={'program': bad, 'speed_pct': 50})
    assert r.status_code == 409 and r.json()['error'] == 'program_invalid'
    sim.advance(2)
    assert joints(sim) == before


def test_estop_during_a_program_cancels_it_stops_the_robot_and_never_resumes(api):
    c, hub, sim = api
    steps = [{'kind': 'move_j', 'joints_deg': TARGET}, {'kind': 'move_j', 'joints_deg': joints(sim)}]
    assert c.post('/api/program/run', json={'program': program(steps), 'speed_pct': 100}).status_code == 202
    drive(c, sim, lambda st: st['state'] == 'running' and sim.snapshot()['moving'], limit_s=10)
    assert c.post('/api/estop').status_code == 200
    st = drive(c, sim, lambda s: not s['running'], limit_s=10)
    assert st['state'] in ('cancelled', 'failed')
    stopped = joints(sim)
    sim.advance(2)
    assert joints(sim) == stopped
    r = c.post('/api/program/run', json={'program': program(steps), 'speed_pct': 100})
    assert r.status_code == 423 and r.json()['error'] == 'estop_latched'
    c.post('/api/estop/reset', json={})
    sim.advance(2)
    assert joints(sim) == stopped                                 # Reset sonrası kendiliğinden devam YOK


def test_goto_moves_the_sim_robot_and_second_move_is_busy(api):
    c, hub, sim = api
    step = {'kind': 'move_j', 'joints_deg': TARGET}
    r = c.post('/api/program/goto', json={'step': step, 'speed_pct': 100})
    assert r.status_code == 202 and r.json()['job_id']
    again = c.post('/api/program/goto', json={'step': step, 'speed_pct': 100})
    assert again.status_code == 409 and again.json()['error'] == 'busy'
    sim.advance(60)
    assert joints(sim) == [pytest.approx(v, abs=0.11) for v in TARGET]
    assert math.isfinite(c.get('/api/program/state').json()['pose']['pos_m'][0])


# --- zincirleyen kuru çalıştırma (arka ucun move_check'i yalnız ŞU ANKİ duruştan planlar) -----------------
UPPER = [45, 60, 50, 0, -10, 0]


def poses(sim):
    """Üst nokta (eklem) ve 5 cm altındaki doğrusal hedef; robot başlangıca döner (yalnız test kurulumu)."""
    home = sim._q.copy()
    sim.set_joints([math.radians(v) for v in UPPER])
    snap = sim.snapshot()
    up = {'joints_deg': joints(sim), 'pos_m': snap['tcp']['pos_m'], 'rpy_deg': snap['tcp']['rpy_deg']}
    sim.set_joints(home)
    down = [up['pos_m'][0], up['pos_m'][1], round(up['pos_m'][2] - 0.05, 4)]
    return up, down


def chain_program(up, down):
    return {'version': 1, 'name': 'Zincir', 'waypoints': [
        {'name': 'Ust', 'joints_deg': up['joints_deg'], 'pos_m': up['pos_m'], 'rpy_deg': up['rpy_deg']},
        {'name': 'Alt', 'pos_m': down, 'rpy_deg': up['rpy_deg']}],
        'steps': [{'kind': 'move_j', 'waypoint': 'Ust'}, {'kind': 'move_l', 'waypoint': 'Alt'},
                  {'kind': 'tool', 'open_pct': 40}]}


def test_chained_dry_run_judges_each_step_from_the_previous_one(sim, mover):
    from control_hub.hub_core.program_runner import dry_run
    up, down = poses(sim)
    linear = {'type': 'linear', 'pos_m': down, 'rpy_deg': up['rpy_deg'], 'speed_pct': 100}
    assert mover.check(linear)['ok'] is False                # "şu anki duruştan": yanlış ret (başlangıç->Alt)
    before = joints(sim)
    res = dry_run(chain_program(up, down), mover.checker(), 100)
    assert res['ok'] is True and res['violations'] == 0, res    # zincirle: Üst'ten Alt'a 5 cm inmek geçerli
    assert joints(sim) == before                                 # kuru çalıştırma robotu OYNATMAZ


def test_chained_dry_run_still_reports_real_violations_per_row(sim, mover):
    from control_hub.hub_core.program_runner import dry_run
    up, down = poses(sim)
    prog = chain_program(up, down)
    prog['waypoints'][1]['pos_m'] = [up['pos_m'][0], up['pos_m'][1], 0.5]    # kutu z üst sınırı 0.22
    prog['steps'].append({'kind': 'move_j', 'joints_deg': [0, 200, 0, 0, 0, 0]})
    res = dry_run(prog, mover.checker(), 100)
    kinds = {r['line']: [v['kind'] for v in r['violations']] for r in res['rows']}
    assert res['ok'] is False and res['violations'] == 2, res
    assert kinds[0] == [] and kinds[1] == ['outside_safe_zone'] and kinds[3] == ['joint_limit'], kinds


def test_each_dry_run_starts_from_the_real_pose_not_from_the_last_dry_run(sim, mover):
    from control_hub.hub_core.program_runner import dry_run
    up, down = poses(sim)
    prog = chain_program(up, down)
    assert dry_run(prog, mover.checker(), 100)['ok'] is True
    sim.set_joints([math.radians(v) for v in UPPER])         # robot gerçekten Üst'te: Üst'e J yine geçerli
    assert dry_run(prog, mover.checker(), 100)['ok'] is True


def test_program_with_j_then_l_previews_runs_and_ends_at_the_linear_target(api):
    c, hub, sim = api
    up, down = poses(sim)
    prog = chain_program(up, down)
    chk = c.post('/api/program/check', json={'program': prog, 'speed_pct': 100}).json()
    assert chk['ok'] is True, chk                                   # önizleme GEÇER (düz denetçiyle geçmezdi)
    r = c.post('/api/program/run', json={'program': prog, 'speed_pct': 100})
    assert r.status_code == 202, r.text
    drive(c, sim, lambda st: st['state'] == 'finished')
    end = sim.snapshot()
    assert end['tcp']['pos_m'] == [pytest.approx(v, abs=0.002) for v in down]
    assert end['gripper']['open_pct'] == pytest.approx(40, abs=0.1)


def test_chain_survives_a_tool_step_and_a_rejected_step_in_between(sim, mover):
    from control_hub.hub_core.program_runner import dry_run
    up, down = poses(sim)
    base = chain_program(up, down)
    j, ln, tool = base['steps']
    with_tool = dict(base, steps=[j, tool, ln])                              # alet adımı zinciri KOPARMAZ
    assert dry_run(with_tool, mover.checker(), 100)['ok'] is True
    bad = {'kind': 'move_j', 'joints_deg': [0, 200, 0, 0, 0, 0]}
    res = dry_run(dict(base, steps=[j, bad, ln]), mover.checker(), 100)   # reddedilen adım sıfırlamaz
    assert res['violations'] == 1 and [r['ok'] for r in res['rows']] == [True, False, True], res
