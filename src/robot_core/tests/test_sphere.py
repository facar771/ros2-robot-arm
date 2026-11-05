"""L7 sphere frame: geometry, azimuth/elevation/r jog through the DLS engine, reach region."""
import math

import numpy as np
import pytest
from robot_core.frames import FrameError, SphereFrame, sphere_frame, sphere_from_dict
from robot_core.jog import (
    DlsJogEngine,
    JogConfig,
    JogError,
    JogStatus,
    SphereJog,
    SphereJogConfig,
    reachable_region,
    solve_sphere_pose,
)
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH

DT = 0.05
STOPS = (JogStatus.SINGULARITY, JogStatus.JOINT_LIMIT, JogStatus.BOX_STOP, JogStatus.TRACKING)
SEED = np.array([0.0, 0.357, 1.79, 0.0, -0.751, -0.142])      # near the start pose found by search
AXIS_C, AXIS_R, AXIS_EL, AXIS_ROLL = (0.0, 0.0, 0.2), 0.7, math.radians(10), -3.0


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def chain(profile):
    return Chain(profile)


@pytest.fixture(scope='module')
def frame():
    # C on the base axis; the arm's tool -z side points at it (tool +z points away from the base)
    return sphere_frame('dome', AXIS_C, AXIS_R, '-z')


@pytest.fixture(scope='module')
def q_start(chain, frame):
    q = solve_sphere_pose(chain, frame, 0.0, AXIS_EL, SEED, AXIS_R, AXIS_ROLL)
    assert q is not None
    return q


def make(chain, profile, frame, **sphere_cfg):
    eng = DlsJogEngine(chain, profile.safety, JogConfig(speed_scale=0.5))
    return SphereJog(eng, frame, SphereJogConfig(**sphere_cfg))


def run(sj, q, rates, ticks, dt=DT, align=False):
    steps = []
    for _ in range(ticks):
        step = sj.step(q, rates, dt, align=align)
        q = step.q_next
        steps.append(step)
    return q, steps


def in_box(profile, p):
    return bool(np.all(np.array(profile.safety.zone.lower) <= p)
                and np.all(p <= np.array(profile.safety.zone.upper)))


# ---- frame type -----------------------------------------------------------------------------
def test_sphere_frame_validation_and_dict_round_trip():
    f = sphere_frame('Dome1', (0, 0, 0.1), 0.4)
    assert isinstance(f, SphereFrame) and f.tool_axis == 'z'
    assert sphere_from_dict(f.as_dict()).as_dict() == f.as_dict()
    for bad in (dict(name='World', center=(0, 0, 0), radius=0.3),          # reserved
                dict(name='a', center=(0, 0, 0), radius=0.01),             # too small
                dict(name='a', center=(0, 0, 0), radius=5.0),              # too big
                dict(name='a', center=(0, 0, 0), radius=float('nan')),
                dict(name='a', center=(0, 0, 9), radius=0.3),              # unit mistake
                dict(name='a', center=(0, 0), radius=0.3),
                dict(name='a', center=(0, 0, 0), radius=0.3, tool_axis='w')):
        with pytest.raises(FrameError):
            sphere_frame(**bad)
    with pytest.raises(FrameError):
        sphere_from_dict({**f.as_dict(), 'extra': 1})
    with pytest.raises(FrameError):
        sphere_from_dict({'type': 'user', 'name': 'a'})


@pytest.mark.parametrize('axis', ['x', 'y', 'z', '-x', '-y', '-z'])
def test_tool_axis_looks_at_center_and_rotation_is_proper(axis):
    f = sphere_frame('d', (0.1, -0.2, 0.3), 0.5, axis)
    rng = np.random.default_rng(2)
    for _ in range(20):
        az, el = rng.uniform(-math.pi, math.pi), rng.uniform(-1.4, 1.4)
        rot = f.rotation(az, el, roll=rng.uniform(-3, 3))
        assert np.allclose(rot.T @ rot, np.eye(3), atol=1e-12) and np.linalg.det(rot) > 0
        to_c = f.center_array - f.position(az, el)
        assert rot @ f.tool_axis_vector == pytest.approx(to_c / np.linalg.norm(to_c), abs=1e-12)
        assert f.angles(f.position(az, el)) == pytest.approx((az, el, 0.5))
        assert f.roll_of(f.rotation(az, el, 0.7), az, el) == pytest.approx(0.7)


def test_twist_formula_matches_the_finite_difference_of_the_frame_pose():
    f = sphere_frame('d', (0, 0, 0.2), 0.6)
    az, el, eps, az_r, el_r = 0.4, 0.5, 1e-6, 0.3, -0.2
    p0, r0 = f.position(az, el), f.rotation(az, el)
    p1, r1 = f.position(az + az_r * eps, el + el_r * eps), f.rotation(az + az_r * eps,
                                                                      el + el_r * eps)
    e_theta = np.array([-math.sin(az), math.cos(az), 0])
    w = az_r * np.array([0, 0, 1.0]) - el_r * e_theta
    rho = f.direction(az, el)
    e_phi = np.array([-math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el)])
    v = 0.6 * (el_r * e_phi + math.cos(el) * az_r * e_theta)
    assert (p1 - p0) / eps == pytest.approx(v, abs=1e-5) and rho @ v == pytest.approx(0, abs=1e-9)
    spin = (r1 @ r0.T - np.eye(3)) / eps                    # ~ skew(w)
    assert np.array([spin[2, 1], spin[0, 2], spin[1, 0]]) == pytest.approx(w, abs=1e-5)


def test_config_cannot_exceed_85_degrees():
    SphereJogConfig(max_elevation_deg=85)
    for bad in (85.1, 90, 0, -5):
        with pytest.raises(JogError):
            SphereJogConfig(max_elevation_deg=bad)


# ---- jog ------------------------------------------------------------------------------------
def test_start_pose_is_on_the_sphere_looking_at_c(chain, frame, q_start):
    pose = chain.fk(q_start)
    az, el, dist = frame.angles(pose[:3, 3])
    assert (az, el, dist) == pytest.approx((0.0, AXIS_EL, AXIS_R), abs=1e-3)
    to_c = frame.center_array - pose[:3, 3]
    assert pose[:3, :3] @ frame.tool_axis_vector == pytest.approx(to_c / dist, abs=1e-3)


def test_azimuth_with_c_on_the_base_axis_is_pure_joint_1(chain, profile, frame, q_start):
    sj = make(chain, profile, frame)
    q, steps = run(sj, q_start, (0.2, 0, 0), 200)
    assert all(s.status in (JogStatus.OK, JogStatus.LIMITED) for s in steps)
    assert q[1:] == pytest.approx(q_start[1:], abs=1e-4)          # J2..J6 untouched
    assert q[0] - q_start[0] > 0.2                                # J1 did move (rad)
    az, el, dist = sj.angles(q)
    assert az == pytest.approx(q[0], abs=2e-3) and el == pytest.approx(AXIS_EL, abs=2e-3)
    assert dist == pytest.approx(AXIS_R, abs=1e-3)
    q2, _ = run(sj, q, (-0.2, 0, 0), 200)                          # and back
    assert q2[0] == pytest.approx(q_start[0], abs=2e-3)


def test_elevation_keeps_r_and_look_at_and_level_horizon(chain, profile, frame, q_start):
    sj = make(chain, profile, frame)
    roll0 = frame.roll_of(chain.fk(q_start)[:3, :3], *sj.angles(q_start)[:2])
    q, steps = q_start, []
    for sign in (1, -1):
        q, s = run(sj, q, (0, 0.2 * sign, 0), 60)
        steps += s
    q_up, _ = run(sj, q_start, (0, 0.2, 0), 60)
    az, el, dist = sj.angles(q_up)
    assert el > AXIS_EL + 0.1 and dist == pytest.approx(AXIS_R, abs=1e-3)
    pose = chain.fk(q_up)
    assert pose[:3, :3] @ frame.tool_axis_vector == pytest.approx(
        (frame.center_array - pose[:3, 3]) / dist, abs=2e-3)
    assert frame.roll_of(pose[:3, :3], az, el) == pytest.approx(roll0, abs=3e-3)
    assert all(in_box(profile, np.array(s.tcp)) for s in steps)
    assert sj.angles(q)[1] == pytest.approx(AXIS_EL, abs=5e-3)    # up then down returns


def test_r_jog_changes_only_r_and_the_sphere_follows(chain, profile, frame, q_start):
    sj = make(chain, profile, frame)
    q, steps = run(sj, q_start, (0, 0, -0.1), 30)
    az, el, dist = sj.angles(q)
    assert dist < AXIS_R - 0.02 and sj.radius == pytest.approx(dist, abs=1e-6)
    assert az == pytest.approx(0, abs=2e-3) and el == pytest.approx(AXIS_EL, abs=2e-3)
    q2, _ = run(sj, q, (0.2, 0, 0), 30)                            # azimuth now holds the NEW r
    assert sj.angles(q2)[2] == pytest.approx(dist, abs=1e-3)
    assert all(in_box(profile, np.array(s.tcp)) for s in steps)


def test_elevation_ceiling_stops_the_motion_and_says_why(chain, profile, frame, q_start):
    sj = make(chain, profile, frame, max_elevation_deg=15.0)
    q, steps = run(sj, q_start, (0, 0.3, 0), 200)
    assert math.degrees(sj.angles(q)[1]) <= 15.0 + 1e-3
    assert math.degrees(sj.angles(q)[1]) > 14.0
    assert any('yükselme' in m and '15' in m for s in steps for m in s.messages)


def test_unreachable_region_stops_jog_and_names_the_cause(chain, profile, frame, q_start):
    sj = make(chain, profile, frame)
    q, steps = q_start, []
    for _ in range(600):
        step = sj.step(q, (0, 0.5, 0), DT)
        steps.append(step)
        if step.status in STOPS:
            break
        q = step.q_next
    last = steps[-1]
    assert last.status in STOPS and not last.moved
    assert any('erişilemeyen bölge' in m and 'yükselme' in m for m in last.messages)
    assert all(in_box(profile, np.array(s.tcp)) for s in steps)
    again = sj.step(q, (0, 0.5, 0), DT)                           # keeps refusing, no movement
    assert again.status in STOPS and not again.moved
    back = sj.step(q, (0, -0.5, 0), DT)                           # the other way is still free
    assert back.moved


def test_off_sphere_is_refused_until_aligned(chain, profile, frame, q_start):
    q_off = solve_sphere_pose(chain, frame, 0.0, AXIS_EL, q_start, AXIS_R + 0.03, AXIS_ROLL)
    sj = make(chain, profile, frame)
    step = sj.step(q_off, (0.1, 0, 0), DT)
    assert step.status is JogStatus.TRACKING and not step.moved
    assert 'küre üzerinde değil' in step.messages[0] and 'hizalayın' in step.messages[0]
    q, steps = run(sj, q_off, (0, 0, 0), 120, align=True)
    assert sj.angles(q)[2] == pytest.approx(AXIS_R, abs=1e-3)
    assert steps[-1].status is JogStatus.IDLE and 'hizalı' in steps[-1].messages[0]
    assert sj.step(q, (0.1, 0, 0), DT).moved


def test_rates_are_validated(chain, profile, frame, q_start):
    sj = make(chain, profile, frame)
    for bad in ((1, 2), (float('nan'), 0, 0), (0, 0, 0, 0)):
        with pytest.raises(JogError):
            sj.step(q_start, bad, DT)
    with pytest.raises(JogError):
        sj.step(q_start, (0, 0, 0), 0)
    assert sj.step(q_start, (0, 0, 0), DT).status is JogStatus.IDLE


def test_speed_ceiling_scales_all_three_rates_together(chain, profile, frame, q_start):
    sj = make(chain, profile, frame)
    step = sj.step(q_start, (5.0, 5.0, 5.0), DT)                  # absurd request
    assert any('küresel hız tavanı' in m for m in step.messages)
    p0, p1 = chain.fk(q_start)[:3, 3], chain.fk(step.q_next)[:3, 3]
    assert np.linalg.norm(p1 - p0) <= 0.1 * 0.5 * DT * 1.01 + 1e-9    # 0.1 m/s x min(0.5, 0.5)


# ---- reachable range ------------------------------------------------------------------------
def test_reachable_region_is_consistent_with_box_and_ik(chain, profile, frame, q_start):
    eng = DlsJogEngine(chain, profile.safety, JogConfig())
    reach = reachable_region(eng, frame, q_start, az_step_deg=45, el_step_deg=10,
                             max_elevation_deg=40)
    assert reach.reachable and reach.contains(0.0, 10.0)
    lo, hi = reach.elevation_range
    assert lo <= 10.0 <= hi
    assert (-180.0, 180.0) not in reach.azimuth_intervals(10.0)    # no full turn
    assert any(a <= 0.0 <= (b if b <= 180 else b - 360) or a <= 0.0 <= b
               for a, b in reach.azimuth_intervals(10.0))
    assert 'yükselme' in reach.summary() and 'azimut' in reach.summary()
    zone = profile.safety.zone
    for i, el in enumerate(reach.elevations):                      # every True cell is in the box
        for j, az in enumerate(reach.azimuths):
            if reach.grid[i, j]:
                p = frame.position(math.radians(az), math.radians(el))
                assert in_box(profile, p), (az, el, p, zone)


def test_unreachable_sphere_reports_empty(chain, profile, q_start):
    eng = DlsJogEngine(chain, profile.safety, JogConfig())
    far = sphere_frame('far', (0, 0, 0.2), 1.9, '-z')
    reach = reachable_region(eng, far, q_start, az_step_deg=45, el_step_deg=20,
                             max_elevation_deg=40)
    assert not reach.reachable and reach.elevation_range is None
    assert 'yok' in reach.summary()
    with pytest.raises(JogError):
        reachable_region(eng, far, q_start, el_step_deg=0)


def test_jog_stops_inside_the_computed_elevation_range(chain, profile, frame, q_start):
    eng = DlsJogEngine(chain, profile.safety, JogConfig())
    reach = reachable_region(eng, frame, q_start, az_step_deg=90, el_step_deg=5,
                             max_elevation_deg=60)
    sj = make(chain, profile, frame)
    q = q_start
    for _ in range(800):
        step = sj.step(q, (0, 0.5, 0), DT)
        if step.status in STOPS:
            break
        q = step.q_next
    stop_el = math.degrees(sj.angles(q)[1])
    # the grid is coarse (5 deg) and the jog uses a small margin: they agree to about one cell
    assert abs(stop_el - reach.elevation_range[1]) <= 6.0
