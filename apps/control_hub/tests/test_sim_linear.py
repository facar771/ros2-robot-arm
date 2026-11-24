"""S1.1: dokun-git SİM doğrusal hareketi (çok tohumlu IK, yönelim 'free', sıçrama = açık hata) ve hız ölçeği.

Noktalar karma_robot home'undan (0.194, 0, 0.179) ``--sim`` güvenli kutusu içinde seçildi; deterministik
(IK tohumları sabit)."""
import math

import numpy as np
import pytest

from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.hub_core.sim_jobs import parse_move
from control_hub.hub_core.sim_plan import (
    IK_SEED_RNG,
    PlanError,
    _pose_samples,
    duration_for,
    plan_joint,
    plan_linear,
    solve_ik,
    solve_ik_multi,
)

FAR_POINTS = [(0.1167, -0.09, 0.09), (0.21, 0.09, 0.09), (0.1633, 0.03, 0.15), (0.07, 0.03, 0.03)]
JUMP_POINT = (0.07, -0.03, 0.03)        # tek tohumla 'unreachable'; çok tohumla çözüm var ama eklem sıçrıyor


@pytest.fixture
def sim():
    b = SimBackend(clock=SimClock(), autostart=False)
    yield b
    b.close()


def q_home(b):
    return np.array(list(b.joint_dict().values()))


@pytest.mark.parametrize('point', FAR_POINTS)
def test_free_orientation_reaches_where_fixed_orientation_cannot(sim, point):
    q = q_home(sim)
    with pytest.raises(PlanError) as err:
        plan_linear(sim.sm, q, point, None, 'fixed', 50)
    assert err.value.kind in ('unreachable', 'path_blocked')
    plan = plan_linear(sim.sm, q, point, None, 'free', 50)
    end = sim.sm.tcp(plan.path[-1])[:3, 3]
    assert np.allclose(end, point, atol=2e-4)                       # konum hedefi tutuyor
    assert np.all(plan.path >= sim.sm.lower - 1e-9) and np.all(plan.path <= sim.sm.upper + 1e-9)
    steps = np.max(np.abs(np.diff(plan.path, axis=0)), axis=1)
    assert float(steps.max()) <= 0.35                              # yol sürekli (sıçrama yok)


def test_free_orientation_is_straight_in_tcp_space(sim):
    q = q_home(sim)
    start = sim.sm.tcp(q)[:3, 3]
    goal = np.array(FAR_POINTS[0])
    plan = plan_linear(sim.sm, q, goal, None, 'free', 50)
    pts = np.array([sim.sm.tcp(p)[:3, 3] for p in plan.path])
    line = (goal - start) / np.linalg.norm(goal - start)
    off = (pts - start) - np.outer((pts - start) @ line, line)
    assert float(np.linalg.norm(off, axis=1).max()) < 5e-4


def test_free_with_rpy_or_bad_orientation_is_bad_request(sim):
    q = q_home(sim)
    for args in ((FAR_POINTS[0], [0, 0, 0], 'free'), (FAR_POINTS[0], None, 'serbest')):
        with pytest.raises(PlanError) as err:
            plan_linear(sim.sm, q, args[0], args[1], args[2], 50)
        assert err.value.kind == 'bad_request'
    with pytest.raises(PlanError):
        parse_move({'type': 'linear', 'pos_m': [0.1, 0, 0.1], 'bogus': 1}, 50)
    assert parse_move({'type': 'linear', 'pos_m': [0.1, 0, 0.1], 'orientation': 'free'}, 50)[1][2] == 'free'
    assert parse_move({'type': 'linear', 'pos_m': [0.1, 0, 0.1]}, 50)[1][2] == 'fixed'


def test_multi_seed_finds_solution_single_seed_misses(sim):
    """Yol örneklerinde ilerle: tek tohum yakınsamayan ilk örnekte çok tohumlu çözüm bulunmalı."""
    q = q_home(sim)
    start = sim.sm.tcp(q)
    goal = start.copy()
    goal[:3, 3] = JUMP_POINT
    prev, found = q, None
    for target in _pose_samples(start, goal)[1:]:
        single = solve_ik(sim.sm, target, prev)
        if single is None:
            found = (target, solve_ik_multi(sim.sm, target, prev))
            break
        prev = single
    assert found is not None, 'bu noktada tek tohum hiç takılmadı: test noktası güncellenmeli'
    target, multi = found
    assert multi is not None and np.allclose(sim.sm.tcp(multi)[:3, 3], target[:3, 3], atol=2e-4)
    again = solve_ik_multi(sim.sm, target, prev)
    assert again is not None and np.allclose(again, multi) and IK_SEED_RNG    # tekrarlanabilir


def test_joint_jump_on_linear_path_is_explicit_error_and_never_joint_space(sim):
    q = q_home(sim)
    with pytest.raises(PlanError) as err:
        plan_linear(sim.sm, q, JUMP_POINT, None, 'fixed', 50)
    assert err.value.kind == 'path_blocked' and 'otomatik' in err.value.message and err.value.at_pct > 0
    before = [j['deg'] for j in sim.snapshot()['joints']]
    status, doc = sim.move_submit({'type': 'linear', 'pos_m': list(JUMP_POINT)})
    assert status == 409 and doc['error'] == 'path_blocked'          # eklem-uzayına DÜŞÜLMEDİ: iş yok
    assert sim.move_check({'type': 'linear', 'pos_m': list(JUMP_POINT)})[1]['ok'] is False
    sim.advance(5.0)
    assert [j['deg'] for j in sim.snapshot()['joints']] == before    # robot hiç oynamadı


def test_free_move_runs_end_to_end_through_backend(sim):
    status, doc = sim.move_submit({'type': 'linear', 'pos_m': list(FAR_POINTS[0]), 'orientation': 'free',
                                   'speed_pct': 100})
    assert status == 202
    for _ in range(int(120 / sim.tick_s)):
        if sim.move_get(doc['job_id'])[1]['job']['state'] != 'running':
            break
        sim.advance(sim.tick_s)
    assert sim.move_get(doc['job_id'])[1]['job']['state'] == 'done'
    assert np.allclose(sim.snapshot()['tcp']['pos_m'], FAR_POINTS[0], atol=3e-4)


# --- hız ölçeği: yüzde TEK kez uygulanır --------------------------------------------------------
def test_speed_pct_scales_once_against_profile_ceiling(sim):
    q = q_home(sim)
    target = np.degrees(q)
    target[0], target[1] = 20.0, 100.0
    cap = sim.sm.velocity_cap
    for pct in (30, 60, 100):
        plan = plan_joint(sim.sm, q, target, pct)
        assert plan.speed_scale == pytest.approx(pct / 100 * cap)     # çarpım yalnız bir kez (pct x tavan)
        want = duration_for(np.radians(target) - q, sim.sm.vmax, sim.sm.amax, pct / 100 * cap)
        assert plan.duration_s == pytest.approx(want)
    slow, fast = plan_joint(sim.sm, q, target, 50), plan_joint(sim.sm, q, target, 100)
    assert slow.duration_s / fast.duration_s == pytest.approx(2.0, rel=0.02)   # hız sınırlı: süre ~ 1/yüzde


def test_run_speed_pct_in_body_overrides_global_and_is_not_multiplied_by_it(sim):
    q = q_home(sim)
    target = np.degrees(q)
    target[0] = 20.0
    sim.set_speed(20)                                                 # genel hız %20
    status, doc = sim.move_submit({'type': 'joint', 'joints_deg': target.tolist(), 'speed_pct': 60})
    assert status == 202
    job = sim._jobs[doc['job_id']]
    assert job.plan.speed_scale == pytest.approx(0.6 * sim.sm.velocity_cap)   # %20 ile ÇARPILMAZ
    sim.move_stop()


def test_slowest_joint_sets_duration_profile_limit_not_a_scaling_bug(sim):
    """J2 profil hız sınırı 0.08 rad/s (tahmini) x tavan 0.3: 11,2° ~ 0,195 rad -> %100'de ~10,8 s."""
    q = q_home(sim)
    d = np.zeros(6)
    d[1] = math.radians(11.2)
    t = duration_for(d, sim.sm.vmax, sim.sm.amax, sim.sm.velocity_cap)
    assert t == pytest.approx(d[1] / (sim.sm.vmax[1] * sim.sm.velocity_cap) / 0.75, rel=1e-6)
    assert 9.0 < t < 12.0 and q is not None
