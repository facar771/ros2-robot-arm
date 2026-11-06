"""F4a check_path: the safe box along a joint-space path (ROS-free)."""
import math

import numpy as np
import pytest
from robot_core.kinematics import Chain
from robot_core.pathcheck import PathError, check_path
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.safezone import SafeZone

# ready -> LEAVE: both ends inside the box, the straight joint-space path pokes out of it
READY = [0.0, -0.1, 1.65, 0.0, 1.2, 0.0]
LEAVE = [-0.436, 1.16, 1.114, 0.675, -1.57, -0.681]


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def chain(profile):
    return Chain(profile)


def tcp(chain, q):
    return chain.fk(np.asarray(q, float))[:3, 3]


def excess(profile, point, zone=None):
    z = zone or profile.safety.zone
    return float(max(np.max(np.array(z.lower) - point), np.max(point - np.array(z.upper)), 0.0))


# ---- FIRST JOB: do home / ready start outside the box? -----------------------------------------
def test_named_poses_vs_the_box_home_starts_outside_and_only_ready_is_inside(profile, chain):
    inside = {name: excess(profile, tcp(chain, q)) == 0.0
              for name, q in profile.named_poses.items()}
    assert inside == {'home': False, 'cnc': False, 'pc': False, 'dik': False, 'ready': True}
    assert tcp(chain, profile.named_poses['home']) == pytest.approx([0.0, 0.0, 1.24])


def test_home_to_ready_is_allowed_from_outside_and_enters_the_box(profile):
    home, ready = profile.named_poses['home'], profile.named_poses['ready']
    result = check_path([home, ready], profile)
    assert result.ok and result.starts_outside
    assert result.enters_box_at is not None
    assert excess(profile, np.array(result.enters_box_at)) == 0.0


def test_leaving_the_box_back_to_home_or_cnc_or_pc_is_rejected(profile, chain):
    """Finding for F4b: every named pose except `ready` is OUTSIDE the box, so a move to it from
    inside is a (legitimate) exit and F4 as written refuses it."""
    ready = profile.named_poses['ready']
    for name in ('home', 'cnc', 'pc', 'dik'):
        result = check_path([ready, profile.named_poses[name]], profile)
        assert not result.ok and result.violation is not None
        assert excess(profile, np.array(result.violation.point)) > 0.0
        assert result.violation.point == pytest.approx(tcp(chain, result.violation.q))


# ---- inside -> outside -> inside ----------------------------------------------------------------
def test_two_inside_points_with_an_outside_path_between_are_rejected(profile, chain):
    for q in (READY, LEAVE):
        assert excess(profile, tcp(chain, q)) == 0.0                  # both ends inside
    dense = [tcp(chain, np.array(READY) + (np.array(LEAVE) - READY) * t)
             for t in np.linspace(0, 1, 4001)]
    assert max(excess(profile, p) for p in dense) > 1e-3             # independent: it does leave
    result = check_path([READY, LEAVE], profile)
    assert not result.ok and 'güvenli kutunun dışında' in result.reason
    v = result.violation
    assert v.segment == 0 and 0 < v.fraction < 1 and v.axis == 'x' and v.side == 'upper'
    assert v.point == pytest.approx(tcp(chain, v.q), abs=1e-12)       # reported coordinate is real
    assert excess(profile, np.array(v.point)) == pytest.approx(v.excess)
    assert f'{v.point[0]:.3f}' in result.reason and 'hareket başlamaz' in result.reason


def test_path_that_stays_inside_passes_and_each_waypoint_segment_is_covered(profile):
    near = np.array(READY) + np.array([0.1, 0.05, -0.05, 0.0, 0.05, 0.0])
    result = check_path([READY, near, READY, near], profile)
    assert result.ok and not result.starts_outside and result.samples > 3
    assert check_path([READY], profile).ok
    assert not check_path([profile.named_poses['home']], profile).ok       # single outside point


# ---- start outside: approach only ---------------------------------------------------------------
def test_start_outside_moving_away_is_rejected_at_the_first_step(profile):
    home = profile.named_poses['home']
    result = check_path([home, profile.named_poses['cnc']], profile)       # cnc is farther away
    assert not result.ok and result.violation.side == 'approach'
    assert 'yalnız kutuya yaklaşırken' in result.reason and result.violation.fraction < 0.1


def test_start_outside_must_stay_inside_after_entering(profile):
    home, ready = profile.named_poses['home'], profile.named_poses['ready']
    result = check_path([home, ready, home], profile)                      # enters, then leaves
    assert not result.ok and result.violation.segment == 1 and result.violation.side != 'approach'
    assert result.enters_box_at is not None


def test_start_outside_and_never_entering_is_rejected(profile):
    home = np.array(profile.named_poses['home'])
    # a tiny step that approaches but ends outside the box
    q = home + np.array([0.0, 0.05, 0.0, 0.0, 0.0, 0.0])
    result = check_path([home, q], profile)
    assert not result.ok and 'hiç girmiyor' in result.reason


# ---- sampling bound -----------------------------------------------------------------------------
def test_tcp_displacement_bound_holds_and_sample_spacing_is_capped(profile, chain):
    rng = np.random.default_rng(3)
    for _ in range(200):                                     # the lever bound is a true bound
        a, b = rng.uniform(chain.lower, chain.upper), rng.uniform(chain.lower, chain.upper)
        levers = [sum(float(np.linalg.norm(j.origin)) for j in profile.joints[i + 1:])
                  for i in range(6)]
        assert np.linalg.norm(tcp(chain, a) - tcp(chain, b)) <= np.abs(b - a) @ levers + 1e-9
    for step in (0.02, 0.005):
        a, b = np.array(READY), np.array(READY) + np.array([0.5, -0.4, 0.3, 0.5, -0.3, 0.6])
        result = check_path([a, b], profile, box=SafeZone((-9, -9, -9), (9, 9, 9), 'world'),
                            max_step=step)
        assert result.ok and result.max_chord_bound <= step + 1e-12
        n = round(result.samples)                           # real chords between samples
        pts = [tcp(chain, a + (b - a) * t) for t in np.linspace(0, 1, n)]
        assert max(np.linalg.norm(x - y) for x, y in zip(pts, pts[1:])) <= step
    coarse = check_path([a, b], profile, box=SafeZone((-9, -9, -9), (9, 9, 9), 'world'),
                        max_step=0.02)
    fine = check_path([a, b], profile, box=SafeZone((-9, -9, -9), (9, 9, 9), 'world'),
                      max_step=0.005)
    assert fine.samples > 3 * coarse.samples                  # denser sampling, more samples


def test_sample_budget_is_enforced(profile):
    result = check_path([READY, LEAVE[:]], profile, max_samples=10)
    assert not result.ok and 'örnek sınırı' in result.reason


# ---- walls --------------------------------------------------------------------------------------
def test_endpoint_exactly_on_a_wall_is_inside_and_a_hair_over_is_not(profile, chain):
    b = np.array(READY) + np.array([0.1, 0.05, -0.05, 0.0, 0.05, 0.0])
    a, b = sorted([np.array(READY), b], key=lambda q: tcp(chain, q)[2])    # start = lower end
    top = tcp(chain, b)[2]
    lo = tuple(profile.safety.zone.lower)
    hi = tuple(profile.safety.zone.upper)
    on_wall = SafeZone(lo, (hi[0], hi[1], top), 'world')
    assert check_path([a, b], profile, box=on_wall).ok
    under = SafeZone(lo, (hi[0], hi[1], top - 1e-6), 'world')
    assert not check_path([a, b], profile, box=under).ok


# ---- no leak: random paths ----------------------------------------------------------------------
def test_no_leak_random_paths_against_dense_independent_sampling(profile, chain):
    """Accepted paths stay inside the box (up to the reported leak bound) at 1 mm resolution;
    every rejection reports a point that really is outside."""
    rng = np.random.default_rng(21)
    tight = SafeZone((0.3, -0.12, 0.30), (0.62, 0.12, 0.6), 'world')       # many paths cross it
    accepted = rejected = 0
    for _ in range(60):
        pts = [np.array(READY)] + [np.array(READY) + rng.normal(0, 0.18, 6) for _ in range(2)]
        result = check_path(pts, profile, box=tight, max_step=0.01)
        if result.ok:
            accepted += 1
            for a, b in zip(pts, pts[1:]):
                n = int(np.ceil(np.abs(b - a).sum() / 0.001)) + 1     # lever <= 1.1 m: < 1.2 mm
                for t in np.linspace(0, 1, n):
                    assert excess(profile, tcp(chain, a + (b - a) * t), tight) \
                        <= result.leak_bound + 1e-9
        else:
            rejected += 1
            v = result.violation
            assert v.point == pytest.approx(tcp(chain, v.q), abs=1e-12)
            assert (excess(profile, np.array(v.point), tight) > 0.0) or v.side == 'approach'
    assert accepted >= 5 and rejected >= 5


# ---- input errors -------------------------------------------------------------------------------
def test_malformed_input_raises_path_error(profile):
    for bad in ([], [[0.0] * 5, [0.0] * 5], [[float('nan')] * 6], 'abc', [[0.0] * 6, [0.0] * 5]):
        with pytest.raises(PathError):
            check_path(bad, profile)
    for kwargs in ({'max_step': 0}, {'max_step': -1}, {'min_step': 0}, {'min_step': 1.0},
                   {'max_samples': 1}):
        with pytest.raises(PathError):
            check_path([READY, READY], profile, **kwargs)
    assert math.isfinite(check_path([READY, READY], profile).max_chord_bound)
