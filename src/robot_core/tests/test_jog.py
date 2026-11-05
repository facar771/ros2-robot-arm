"""Kinematics from the profile + the DLS jog engine (every test is a few ticks to ~1000 ticks)."""
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from robot_core.frames import FrameStore
from robot_core.jog import DlsJogEngine, JogConfig, JogError, JogStatus
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.safezone import SafetyConfig, SafeZone
from robot_core.se3 import quat_angle, quat_from_matrix, rotvec_from_matrix

from tests.conftest import URDF
from tests.robot_snapshot import forward_kinematics

DT = 0.02
Q0 = np.array([0.15, -0.231, 1.8, -0.471, 1.404, 1.131])     # TCP ~ (0.45, 0, 0.45), sigma_min 0.24
HUGE = SafetyConfig(SafeZone((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0)), 1.0)


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def chain(profile):
    return Chain(profile)


def engine(chain, profile, safety=None, **config):
    return DlsJogEngine(chain, safety or profile.safety, JogConfig(**config))


def run(eng, q, twist, ticks, dt=DT):
    """Closed loop with a perfect plant: the commanded q is the next measured q."""
    steps = []
    for _ in range(ticks):
        step = eng.step(q, twist, dt)
        q = step.q_next
        steps.append(step)
    return q, steps


def tcp(chain, q):
    return chain.fk(q)[:3, 3]


def box(lower, upper, scale=1.0):
    return SafetyConfig(SafeZone(tuple(lower), tuple(upper)), scale)


# ---- kinematics -------------------------------------------------------------------------------
def test_fk_equals_the_urdf_chain(chain):
    joints = []
    for j in ET.parse(URDF).getroot().findall('joint'):
        if j.get('type') == 'revolute':
            joints.append({'child': j.find('child').get('link'),
                           'origin': [float(v) for v in j.find('origin').get('xyz').split()],
                           'axis': [float(v) for v in j.find('axis').get('xyz').split()]})
    rng = np.random.default_rng(11)
    for _ in range(25):
        q = rng.uniform(chain.lower, chain.upper)
        expected = forward_kinematics(joints, q)['link_6']
        pose = chain.fk(q)
        assert pose[:3, 3] == pytest.approx(expected[:3], abs=1e-12)
        assert pose[:3, :3].ravel() == pytest.approx(expected[3:], abs=1e-12)
    assert chain.tip_link == 'link_6' and chain.joint_names[0] == 'joint_1'


def test_jacobian_matches_the_numerical_derivative_of_fk(chain):
    rng = np.random.default_rng(5)
    eps = 1e-6
    for _ in range(20):
        q = rng.uniform(chain.lower * 0.9, chain.upper * 0.9)
        jac = chain.jacobian(q)
        pose = chain.fk(q)
        for i in range(chain.dof):
            d = np.zeros(chain.dof)
            d[i] = eps
            plus, minus = chain.fk(q + d), chain.fk(q - d)
            lin = (plus[:3, 3] - minus[:3, 3]) / (2 * eps)
            ang = rotvec_from_matrix(plus[:3, :3] @ minus[:3, :3].T) / (2 * eps)   # base frame
            assert jac[:3, i] == pytest.approx(lin, abs=1e-6)
            assert jac[3:, i] == pytest.approx(ang, abs=1e-6)
        assert np.allclose(chain.fk_and_jacobian(q)[0], pose)


def test_home_is_a_wrist_singularity(chain):
    assert np.linalg.svd(chain.jacobian(np.zeros(6)), compute_uv=False)[-1] < 1e-9


def test_chain_rejects_bad_q(chain):
    for bad in ([0] * 5, [math.nan] * 6):
        with pytest.raises(ValueError):
            chain.fk(bad)


# ---- jogging in World / Tool / User frames ----------------------------------------------------
def test_world_linear_jog_is_a_straight_line_without_rotation(chain, profile):
    eng = engine(chain, profile)
    # asks 0.1, scale 0.2*0.1=0.02 m/s
    q, steps = run(eng, Q0, [0, 0.0, 0.1, 0, 0, 0], 100)
    delta = tcp(chain, q) - tcp(chain, Q0)
    assert delta == pytest.approx([0, 0, 0.02 * DT * 100], abs=2e-5)
    assert quat_angle(quat_from_matrix(chain.fk(q)[:3, :3]),
                      quat_from_matrix(chain.fk(Q0)[:3, :3])) < 1e-4
    assert all(s.status in (JogStatus.OK, JogStatus.LIMITED) for s in steps)


def test_angular_jog_turns_about_the_tcp_without_moving_it(chain, profile):
    eng = engine(chain, profile)
    # world Z, 0.5 asked -> 0.1 rad/s
    q, _ = run(eng, Q0, [0, 0, 0, 0, 0, 0.5], 100)
    assert tcp(chain, q) == pytest.approx(tcp(chain, Q0), abs=2e-5)
    turn = rotvec_from_matrix(chain.fk(q)[:3, :3] @ chain.fk(Q0)[:3, :3].T)
    assert turn == pytest.approx([0, 0, 0.1 * DT * 100], abs=1e-4)


def test_tool_frame_follows_the_tcp_orientation(chain, profile):
    eng = engine(chain, profile)
    eng.set_frame(None)                                            # Tool
    q, _ = run(eng, Q0, [0, 0, 0.1, 0, 0, 0], 60)
    tool_z = chain.fk(Q0)[:3, :3][:, 2]
    assert tcp(chain, q) - tcp(chain, Q0) == pytest.approx(tool_z * 0.02 * DT * 60, abs=5e-5)
    assert abs(np.dot(tool_z, [0, 0, 1])) < 0.99                   # genuinely not a world axis


def test_user_frame_axes(chain, profile, tmp_path):
    store = FrameStore(tmp_path / 'f.yaml')
    store.teach('t', (0.4, 0, 0.3), (0.4, 0.2, 0.3), (0.3, 0.0, 0.3))      # X along world +Y
    eng = engine(chain, profile)
    eng.set_frame(store.get('t').rotation_matrix)
    q, _ = run(eng, Q0, [0.1, 0, 0, 0, 0, 0], 60)
    assert tcp(chain, q) - tcp(chain, Q0) == pytest.approx([0, 0.02 * DT * 60, 0], abs=5e-5)


@pytest.mark.parametrize('axis', [0, 1, 2])
def test_angular_twist_is_an_angular_velocity_vector_about_each_world_axis(chain, profile, axis):
    eng = engine(chain, profile)
    twist = np.zeros(6)
    # asked 0.5 -> capped at 0.1 rad/s
    twist[3 + axis] = 0.5
    q, _ = run(eng, Q0, twist, 50)
    turn = rotvec_from_matrix(chain.fk(q)[:3, :3] @ chain.fk(Q0)[:3, :3].T)
    expected = np.zeros(3)
    expected[axis] = 0.1 * DT * 50
    # about that axis only, no coupling
    assert turn == pytest.approx(expected, abs=1e-4)
    assert tcp(chain, q) == pytest.approx(tcp(chain, Q0), abs=2e-5)


# ---- singularity ------------------------------------------------------------------------------
def test_singular_configuration_refuses_with_a_message(chain, profile):
    eng = engine(chain, profile)
    step = eng.step(np.zeros(6), [0.1, 0, 0, 0, 0, 0], DT)
    assert step.status is JogStatus.SINGULARITY and not step.moved
    assert 'tekillik' in step.messages[-1] and step.sigma_min < 1e-9
    assert np.array_equal(step.q_next, np.zeros(6))


def test_no_velocity_jump_approaching_a_singularity(chain, profile):
    cfg = dict(speed_scale=1.0, max_linear_speed=0.05, max_angular_speed=0.2)
    eng = engine(chain, profile, **cfg)
    twist = np.array([0.02, 0.01, 0, 0, 0, 0.1])
    speeds, undamped, sigmas = [], [], []
    for s in np.linspace(0.6, 0.0, 121):                           # wrist joint_5 -> 0
        q = Q0.copy()
        q[4] = s
        eng.reset()
        step = eng.step(q, twist, DT)
        speeds.append(np.abs(step.dq).max() / DT)
        sigmas.append(step.sigma_min)
        undamped.append(np.abs(np.linalg.pinv(chain.jacobian(q), rcond=1e-12) @ twist).max())
    speeds = np.array(speeds)
    # plain pseudo-inverse explodes ...
    assert max(undamped) > 5.0
    assert speeds.max() < 0.6                                      # ... the engine does not
    assert np.abs(np.diff(speeds)).max() < 0.05                    # no jump between neighbours
    assert speeds[-1] == 0.0 and speeds[-20:].max() < 0.1          # it ramps down to a stop
    assert sigmas[0] > sigmas[60] > sigmas[-1]


def test_stops_below_the_threshold_and_names_the_reason(chain, profile):
    eng = engine(chain, profile)
    q = Q0.copy()
    q[4] = 0.05
    step = eng.step(q, [0.1, 0, 0, 0, 0, 0], DT)
    assert step.status is JogStatus.SINGULARITY and 'σ_min' in step.messages[-1]


# ---- safe box ---------------------------------------------------------------------------------
def inside(zone, p):
    return zone.check(tuple(p)) is None


def test_never_leaves_the_safe_box_in_a_random_walk(chain, profile):
    zone = SafeZone((0.40, -0.06, 0.40), (0.50, 0.06, 0.50))
    eng = engine(chain, profile, box(zone.lower, zone.upper), speed_scale=1.0,
                 max_linear_speed=0.4, max_angular_speed=1.0)
    rng = np.random.default_rng(2)
    q, twist = Q0.copy(), np.zeros(6)
    assert inside(zone, tcp(chain, q))
    stats = set()
    for tick in range(1500):
        if tick % 25 == 0:
            twist = np.concatenate([rng.normal(size=3) * 0.4, rng.normal(size=3) * 0.5])
        step = eng.step(q, twist, DT)
        q = step.q_next
        stats.add(step.status)
        assert inside(zone, tcp(chain, q)), f'tick {tick}: {tcp(chain, q)}'
    assert JogStatus.LIMITED in stats


def test_wall_pushes_back_by_sliding_not_crossing(chain, profile):
    zone = SafeZone((0.40, -0.20, 0.40), (0.50, 0.20, 0.50))
    eng = engine(chain, profile, box(zone.lower, zone.upper), speed_scale=1.0, max_linear_speed=0.2)
    q, steps = run(eng, Q0, [0.1, 0.05, 0, 0, 0, 0], 120)          # into the +X wall, sliding in +Y
    p = tcp(chain, q)
    assert p[0] <= 0.50 - 0.005 + 1e-4                              # stops at the margin, not past
    assert p[1] > 0.04                                              # still slid along the wall
    assert any('kaydırıldı' in m for s in steps for m in s.messages)
    assert all(inside(zone, tcp(chain, s.q_next)) for s in steps)


def test_head_on_wall_goes_nowhere_and_stays_inside(chain, profile):
    zone = SafeZone((0.40, -0.20, 0.40), (0.50, 0.20, 0.50))
    eng = engine(chain, profile, box(zone.lower, zone.upper), speed_scale=1.0, max_linear_speed=0.2)
    q, steps = run(eng, Q0, [0.2, 0, 0, 0, 0, 0], 150)
    assert tcp(chain, q)[0] <= 0.50 and inside(zone, tcp(chain, q))
    assert all(inside(zone, tcp(chain, s.q_next)) for s in steps)


def test_outside_the_box_only_moves_toward_it(chain, profile):
    # start (x=0.45) is outside, x too low
    zone = SafeZone((0.55, -0.20, 0.30), (0.70, 0.20, 0.60))
    eng = engine(chain, profile, box(zone.lower, zone.upper), speed_scale=1.0, max_linear_speed=0.2)
    assert not inside(zone, tcp(chain, Q0))
    q, _ = run(eng, Q0, [-0.1, 0, 0, 0, 0, 0], 40)                  # further out: not allowed
    assert tcp(chain, q)[0] >= tcp(chain, Q0)[0] - 1e-4
    eng.reset()
    q, _ = run(eng, Q0, [0.1, 0, 0, 0, 0, 0], 40)                   # toward the box: allowed
    assert tcp(chain, q)[0] > tcp(chain, Q0)[0] + 0.05


def test_above_the_ceiling_only_downward_is_allowed(chain, profile):
    """The upright home pose is outside the real box (z=1.24 > 0.7): same rule, z axis."""
    assert not inside(profile.safety.zone, tcp(chain, np.zeros(6)))
    zone = SafeZone((0.30, -0.30, 0.10), (0.60, 0.30, 0.30))       # TCP z=0.45 is above the ceiling
    eng = engine(chain, profile, box(zone.lower, zone.upper), speed_scale=1.0,
                 max_linear_speed=0.2)
    q, _ = run(eng, Q0, [0, 0, 0.1, 0, 0, 0], 40)
    assert tcp(chain, q)[2] <= tcp(chain, Q0)[2] + 1e-4
    eng.reset()
    q, _ = run(eng, Q0, [0, 0, -0.1, 0, 0, 0], 40)
    assert tcp(chain, q)[2] < tcp(chain, Q0)[2] - 0.05


# ---- drift, joint limits, speed ---------------------------------------------------------------
def test_drift_stays_bounded_over_1000_ticks_with_a_sloppy_plant(chain, profile):
    """Out for 500 ticks, back for 500: the plant executes +6 % then -3 % plus noise; the
    target-pose feedback keeps the end point and orientation near the start."""
    rng = np.random.default_rng(4)

    def trip(gain):
        eng = engine(chain, profile, HUGE, speed_scale=1.0, max_linear_speed=0.02,
                     max_angular_speed=0.1, feedback_gain=gain)
        q = Q0.copy()
        for sign, plant_gain in ((1, 1.06), (-1, 0.97)):            # different error on each leg
            twist = sign * np.array([0.0, 0.02, 0.0, 0.0, 0.0, 0.05])
            for _ in range(500):
                step = eng.step(q, twist, 0.01)
                assert step.status in (JogStatus.OK, JogStatus.LIMITED), step.messages
                q = q + step.dq * plant_gain + rng.normal(size=6) * 2e-6
        start, end = chain.fk(Q0), chain.fk(q)
        return (np.linalg.norm(end[:3, 3] - start[:3, 3]),
                np.linalg.norm(rotvec_from_matrix(end[:3, :3] @ start[:3, :3].T)))

    pos_with, rot_with = trip(5.0)
    pos_without, _ = trip(0.0)
    assert pos_with < 1e-3 and rot_with < 2e-3
    assert pos_with < pos_without / 3                               # the feedback does the work


def test_joint_limits_are_never_exceeded_and_the_stop_names_the_joint(chain, profile):
    eng = engine(chain, profile, HUGE, speed_scale=1.0, max_angular_speed=0.5)
    q, steps = Q0.copy(), []
    for _ in range(400):
        # world Y rotation drives joint_5
        step = eng.step(q, [0, 0, 0, 0, 0.5, 0], DT)
        q = step.q_next
        steps.append(step)
        assert np.all(q <= chain.upper) and np.all(q >= chain.lower)
        assert np.all(q <= chain.upper - 0.02 + 1e-9) or step.status is JogStatus.JOINT_LIMIT
    limited = [s for s in steps if s.status is JogStatus.JOINT_LIMIT]
    assert limited and 'eklem limiti: joint_' in limited[0].messages[0]


def test_joint_velocity_is_clipped(chain, profile):
    # absurd cartesian cap
    eng = engine(chain, profile, HUGE, speed_scale=1.0, max_linear_speed=50.0)
    step = eng.step(Q0, [30.0, 0, 0, 0, 0, 0], DT)
    assert np.all(np.abs(step.dq) / DT <= chain.velocity + 1e-9)
    assert step.moved
    assert np.max(np.abs(step.dq) / DT / chain.velocity) == pytest.approx(1.0, rel=1e-6)
    assert any('eklem hız sınırı' in m for m in step.messages)


def test_existing_velocity_ceiling_applies(chain, profile):
    fast = engine(chain, profile, HUGE, speed_scale=1.0)
    slow = engine(chain, profile, SafetyConfig(HUGE.zone, 0.1), speed_scale=1.0)
    a = fast.step(Q0, [0.5, 0, 0, 0, 0, 0], DT)                     # asks far above both caps
    b = slow.step(Q0, [0.5, 0, 0, 0, 0, 0], DT)
    ratio = np.linalg.norm(b.dq) / np.linalg.norm(a.dq)
    assert ratio == pytest.approx(0.1, rel=0.05)                    # max_velocity_scale 0.1 caps it


def test_idle_tick_does_not_move_and_forgets_the_target(chain, profile):
    eng = engine(chain, profile)
    step = eng.step(Q0, np.zeros(6), DT)
    assert step.status is JogStatus.IDLE and not step.moved
    q, _ = run(eng, Q0, [0, 0, 0.1, 0, 0, 0], 10)
    other = Q0 + 0.2                                                # someone else moved the arm
    step = eng.step(other, np.zeros(6), DT)
    assert not step.moved                                           # no snap back to the old target


def test_tracking_error_stops_the_jog(chain, profile):
    eng = engine(chain, profile)
    eng.step(Q0, [0, 0, 0.1, 0, 0, 0], DT)
    away = Q0.copy()
    away[0] += 0.1                                                  # the arm did not follow
    step = eng.step(away, [0, 0, 0.1, 0, 0, 0], DT)
    assert step.status is JogStatus.TRACKING and not step.moved


# ---- incremental mode (same engine) -----------------------------------------------------------
@pytest.mark.parametrize('index, amount', [(0, 0.001), (1, -0.001), (2, 0.01), (0, 0.0001)])
def test_linear_increments_land_on_the_distance_and_stop(chain, profile, index, amount):
    eng = engine(chain, profile)
    eng.begin_increment(Q0, index, amount)
    q, done = Q0.copy(), None
    for tick in range(2000):
        step = eng.step(q, np.zeros(6), DT)                         # the twist argument is ignored
        q = step.q_next
        if step.status is JogStatus.DONE:
            done = tick
            break
    assert done is not None and done < 1500
    delta = tcp(chain, q) - tcp(chain, Q0)
    expected = np.zeros(3)
    expected[index] = amount
    assert delta == pytest.approx(expected, abs=2e-6)
    assert not eng.step(q, np.zeros(6), DT).moved                   # finished: stays put


@pytest.mark.parametrize('index, amount', [(5, math.radians(1)), (3, -math.radians(10)),
                                           (4, math.radians(0.1))])
def test_angular_increments_turn_about_the_tcp(chain, profile, index, amount):
    eng = engine(chain, profile)
    eng.begin_increment(Q0, index, amount)
    q = Q0.copy()
    for _ in range(3000):
        step = eng.step(q, np.zeros(6), DT)
        q = step.q_next
        if step.status is JogStatus.DONE:
            break
    assert step.status is JogStatus.DONE
    turn = rotvec_from_matrix(chain.fk(q)[:3, :3] @ chain.fk(Q0)[:3, :3].T)
    axis = np.zeros(3)
    axis[index - 3] = amount
    assert turn == pytest.approx(axis, abs=2e-5)
    assert tcp(chain, q) == pytest.approx(tcp(chain, Q0), abs=1e-5)


def test_tool_frame_increment_is_a_straight_line_in_the_start_orientation(chain, profile):
    eng = engine(chain, profile)
    eng.set_frame(None)
    eng.begin_increment(Q0, 2, 0.01)
    q = Q0.copy()
    for _ in range(1500):
        step = eng.step(q, np.zeros(6), DT)
        q = step.q_next
        if step.status is JogStatus.DONE:
            break
    tool_z = chain.fk(Q0)[:3, :3][:, 2]
    assert tcp(chain, q) - tcp(chain, Q0) == pytest.approx(tool_z * 0.01, abs=2e-6)


def test_increment_against_the_wall_aborts_with_a_reason(chain, profile):
    zone = SafeZone((0.40, -0.20, 0.40), (0.50, 0.20, 0.50))
    eng = engine(chain, profile, box(zone.lower, zone.upper), speed_scale=1.0, max_linear_speed=0.2)
    eng.begin_increment(Q0, 0, 0.1)                                 # 10 cm toward a wall 5 cm away
    q, last = Q0.copy(), None
    for _ in range(600):
        last = eng.step(q, np.zeros(6), DT)
        q = last.q_next
        assert inside(zone, tcp(chain, q))
        if last.status in (JogStatus.ABORTED, JogStatus.BOX_STOP):
            break
    assert last.status in (JogStatus.ABORTED, JogStatus.BOX_STOP)
    assert tcp(chain, q)[0] < 0.50
    assert not eng.step(q, np.zeros(6), DT).moved                   # nothing left running


def test_increment_input_validation(chain, profile):
    eng = engine(chain, profile)
    for index, amount in ((6, 0.001), (-1, 0.001), (True, 0.001), (0, 0.0), (0, math.nan)):
        with pytest.raises(JogError):
            eng.begin_increment(Q0, index, amount)


# ---- inputs, config, purity -------------------------------------------------------------------
def test_bad_inputs_raise(chain, profile):
    eng = engine(chain, profile)
    for q, tw, dt in (([0] * 5, np.zeros(6), DT), (Q0, np.zeros(5), DT),
                      (Q0, [math.nan] * 6, DT), (Q0, np.zeros(6), 0.0), (Q0, np.zeros(6), -1.0),
                      (Q0, np.zeros(6), 5.0), (Q0, np.zeros(6), math.nan)):
        with pytest.raises(JogError):
            eng.step(q, tw, dt)
    with pytest.raises(JogError):
        eng.set_frame(np.diag([1, 1, -1]))                          # a mirror is not a frame


@pytest.mark.parametrize('bad', [dict(speed_scale=0), dict(speed_scale=1.5),
                                 dict(sigma_stop=0.1, sigma_slow=0.05), dict(max_dt=0),
                                 dict(max_linear_speed=-1), dict(feedback_gain=-1)])
def test_config_validation(bad):
    with pytest.raises(JogError):
        JogConfig(**bad)


def test_defaults_are_the_narrow_side():
    cfg = JogConfig()
    assert cfg.speed_scale <= 0.2 and cfg.joint_limit_margin > 0 and cfg.box_margin > 0
    assert cfg.max_linear_speed <= 0.1 and cfg.max_angular_speed <= 0.5


def test_engine_has_no_side_effects_on_its_inputs(chain, profile):
    eng = engine(chain, profile)
    q = Q0.copy()
    tw = np.array([0.0, 0.0, 0.1, 0.0, 0.0, 0.0])
    q_before, tw_before = q.copy(), tw.copy()
    step = eng.step(q, tw, DT)
    # a proposal, nothing moved
    assert np.array_equal(q, q_before) and np.array_equal(tw, tw_before)
    assert np.array_equal(step.q_next, q + step.dq)
    again = engine(chain, profile).step(q, tw, DT)
    assert np.array_equal(again.dq, step.dq)                                # deterministic


def test_a_thousand_ticks_take_seconds(chain, profile):
    import time
    eng = engine(chain, profile, HUGE, speed_scale=1.0, max_linear_speed=0.02)
    start = time.perf_counter()
    run(eng, Q0, [0, 0.02, 0, 0, 0, 0], 1000, dt=0.01)
    assert time.perf_counter() - start < 10.0



# ---- TCP from the profile (J4) ----------------------------------------------------------------
def test_chain_uses_the_profile_tcp(profile):
    import copy

    import yaml
    from robot_core.profile import parse_profile
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding='utf-8'))
    raw = copy.deepcopy(raw)
    raw['tcp'] = {'offset': [0.0, 0.0, 0.12], 'wpr': [0.0, 0.0, 0.0]}
    with_tool = Chain(parse_profile(raw))
    flange = Chain(profile)
    q = Q0
    t_flange, t_tool = flange.fk(q), with_tool.fk(q)
    assert np.allclose(t_flange[:3, :3], t_tool[:3, :3])
    assert t_tool[:3, 3] - t_flange[:3, 3] == pytest.approx(t_flange[:3, :3] @ [0, 0, 0.12])
    # the Jacobian is about the TCP: numerical derivative of the shifted FK
    eps = 1e-6
    for i in range(6):
        d = np.zeros(6)
        d[i] = eps
        lin = (with_tool.fk(q + d)[:3, 3] - with_tool.fk(q - d)[:3, 3]) / (2 * eps)
        assert with_tool.jacobian(q)[:3, i] == pytest.approx(lin, abs=1e-6)
    # an explicit argument still overrides the profile
    assert np.allclose(Chain(parse_profile(raw), tcp=np.eye(4)).fk(q), t_flange)
