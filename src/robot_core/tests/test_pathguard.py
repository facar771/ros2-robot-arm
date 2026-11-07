"""F4b: PathGuard - plan -> check_path -> execute, the park transit and "Eve dön" (ROS-free)."""
import numpy as np
import pytest
from robot_core.models import JointTarget, MotionResult, PlanResult, PoseTarget
from robot_core.pathcheck import check_path
from robot_core.pathguard import (
    CODE_PARK,
    CODE_PATH,
    GuardConfig,
    PathGuard,
    straight_trajectory,
)
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.safezone import load_park, load_safety
from robot_core.skills import Skills
from robot_core.supervisor import CancelFlag, EStop, GuardedMotion

from robot_core.config import RobotConfig
from tests.conftest import CONFIG_DIR

NAMES = tuple(f'joint_{i}' for i in range(1, 7))
# two points INSIDE the box whose straight joint line leaves it (midpoint 18 cm outside)
OUT_A = (-0.56, 1.31, 0.15, 0.0, -1.48, 0.0)
OUT_B = (0.57, 0.57, 1.25, 0.0, 1.3, 0.0)
# two points inside the box with a straight joint line that stays inside
IN_A = (0.355, 0.908, 1.049, 0.0, 1.361, 0.0)
IN_B = (0.0, -0.1, 1.65, 0.0, 1.2, 0.0)            # = ready


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def park():
    return load_park(CONFIG_DIR / 'safe_zone.park.yaml')


@pytest.fixture(scope='module')
def zone():
    return load_safety(CONFIG_DIR / 'safe_zone.yaml').zone


class State:
    def __init__(self, q):
        self.q = tuple(q)

    def joint_positions(self):
        return dict(zip(NAMES, self.q))


class Planner:
    """Scripted planner: plan_joint returns the queued plans one by one (the last one repeats)."""

    def __init__(self, state, plans=(), valid=None):
        self.state, self.plans, self.valid = state, list(plans), valid
        self.planned, self.executed, self.timed, self.checked = [], [], [], []

    def _next(self, goal):
        self.planned.append(goal)
        plan = self.plans[min(len(self.planned), len(self.plans)) - 1]
        if callable(plan):
            plan = plan(self.state.q)
        return plan

    def plan_joint(self, target):
        return self._next(target)

    def plan_pose(self, target):
        return self._next(target)

    def execute_plan(self, plan):
        self.executed.append(plan)
        self.state.q = tuple(plan.path[-1])
        return MotionResult(True, 1, 'SUCCESS')

    def execute_timed(self, times, positions, velocities):
        self.timed.append((times, positions, velocities))
        self.state.q = tuple(positions[-1])
        return MotionResult(True, 1, 'SUCCESS')

    def states_valid(self, states):
        self.checked.append(list(states))
        return self.valid


def line(a, b):
    return lambda q: PlanResult(True, (tuple(q), tuple(b)))


def make(profile, park, zone, start, plans=(), valid=None, with_park=True, abort=None, **kw):
    state = State(start)
    planner = Planner(state, plans, valid)
    guard = PathGuard(planner, state, NAMES, profile, zone, park if with_park else None,
                      poses=profile.named_poses, abort=abort,
                      **{'velocity_scale': lambda: 0.5, **kw})
    return guard, planner, state


# ---- the data are what the tests say they are ---------------------------------------------------
def test_the_test_points_are_inside_and_the_straight_line_between_them_leaves(profile, zone):
    assert check_path([OUT_A], profile, zone).ok and check_path([OUT_B], profile, zone).ok
    out = check_path([OUT_A, OUT_B], profile, zone)
    assert not out.ok and out.violation is not None
    assert check_path([IN_A, IN_B], profile, zone).ok


# ---- plan -> check -> execute ----------------------------------------------------------------
def test_a_path_inside_the_box_is_executed_once(profile, park, zone):
    guard, planner, state = make(profile, park, zone, IN_A, [line(IN_A, IN_B)])
    out = guard.move_to(JointTarget(IN_B, 'a'))
    assert out.ok and len(planner.executed) == 1 and len(planner.planned) == 1
    assert state.q == IN_B


def test_a_path_leaving_the_box_is_rejected_three_times_and_never_executed(profile, park, zone):
    guard, planner, state = make(profile, park, zone, OUT_A, [line(OUT_A, OUT_B)])
    out = guard.move_to(JointTarget(OUT_B, 'b'))
    assert not out.ok and out.code_name == CODE_PATH
    assert planner.executed == [] and len(planner.planned) == 3         # at most 3 plans in total
    assert state.q == OUT_A                                              # nothing moved
    assert 'güvenli kutunun dışında' in out.message and 'TCP (' in out.message
    assert 'hareket başlamadı' in out.message and 'genişletilmedi' in out.message


def test_the_error_names_the_violation_point_and_its_coordinates(profile, park, zone):
    guard, _, _ = make(profile, park, zone, OUT_A, [line(OUT_A, OUT_B)])
    out = guard.move_to(JointTarget(OUT_B))
    first = check_path([OUT_A, OUT_B], profile, zone).violation
    assert f'({first.point[0]:.3f}, {first.point[1]:.3f}, {first.point[2]:.3f})' in out.message


def test_replanning_finds_a_good_plan_on_the_second_try(profile, park, zone):
    near = (OUT_A[0] + 0.05,) + OUT_A[1:]
    guard, planner, _ = make(profile, park, zone, OUT_A, [line(OUT_A, OUT_B), line(OUT_A, near)])
    out = guard.move_to(JointTarget(near, 'b'))
    assert out.ok and len(planner.planned) == 2 and len(planner.executed) == 1
    assert planner.executed[0].path[-1] == near                          # the CHECKED plan ran


def test_the_number_of_plans_is_configurable_and_at_least_one(profile, park, zone):
    guard, planner, _ = make(profile, park, zone, OUT_A, [line(OUT_A, OUT_B)],
                             config=GuardConfig(max_plans=2))
    assert not guard.move_to(JointTarget(OUT_B)).ok and len(planner.planned) == 2
    with pytest.raises(ValueError):
        make(profile, park, zone, OUT_A, config=GuardConfig(max_plans=0))


def test_a_planner_failure_is_passed_through_without_replanning(profile, park, zone):
    fail = PlanResult(False, code_name='PLANNING_FAILED', message='x')
    guard, planner, _ = make(profile, park, zone, IN_A, [fail])
    out = guard.move_to(JointTarget(IN_B))
    assert not out.ok and out.code_name == 'PLANNING_FAILED' and len(planner.planned) == 1


def test_pose_goals_go_through_the_same_check(profile, park, zone):
    guard, planner, _ = make(profile, park, zone, OUT_A, [line(OUT_A, OUT_B)])
    out = guard.move_to_pose(PoseTarget(0.4, 0.0, 0.3, 'world'))
    assert not out.ok and out.code_name == CODE_PATH and planner.executed == []
    guard, planner, _ = make(profile, park, zone, IN_A, [line(IN_A, IN_B)])
    assert guard.move_to_pose(PoseTarget(0.4, 0.0, 0.3, 'world')).ok and len(planner.executed) == 1


def test_a_stale_plan_is_not_executed(profile, park, zone):
    far = PlanResult(True, (IN_A, IN_B))
    guard, planner, _ = make(profile, park, zone, IN_B, [far])           # robot is NOT at IN_A
    out = guard.move_to(JointTarget(IN_B))
    assert not out.ok and 'eski' in out.message and planner.executed == []


def test_no_joint_state_means_no_motion(profile, park, zone):
    guard, planner, state = make(profile, park, zone, IN_A, [line(IN_A, IN_B)])
    state.joint_positions = lambda: None
    assert not guard.move_to(JointTarget(IN_B)).ok and planner.executed == []


def test_start_outside_the_box_may_only_approach(profile, park, zone):
    home = profile.named_poses['home']
    ready = profile.named_poses['ready']
    guard, planner, _ = make(profile, park, zone, home, [line(home, ready)])
    assert guard.move_to(JointTarget(ready, 'ready')).ok                 # home -> ready: approach
    cnc = profile.named_poses['cnc']
    guard, planner, _ = make(profile, park, zone, ready, [line(ready, cnc)])
    out = guard.move_to(JointTarget(cnc, 'cnc'))                         # ready -> cnc: leaves
    assert not out.ok and planner.executed == []


# ---- e-stop and cancel keep working ----------------------------------------------------------
def test_estop_before_planning_and_before_execution(profile, park, zone):
    estop = EStop()
    refusal = lambda: MotionResult(False, message='ACİL DUR') if estop.is_set else None  # noqa: E731
    guard, planner, state = make(profile, park, zone, IN_A, [line(IN_A, IN_B)], abort=refusal)
    estop.trip('test')
    assert not guard.move_to(JointTarget(IN_B)).ok and planner.planned == []   # not even planned

    def trip_while_planning(q):
        estop.reset()
        estop.trip('during plan')
        return PlanResult(True, (tuple(q), IN_B))

    guard, planner, _ = make(profile, park, zone, IN_A, [trip_while_planning], abort=refusal)
    estop.reset()
    out = guard.move_to(JointTarget(IN_B))
    assert not out.ok and out.message == 'ACİL DUR' and planner.executed == []


def test_guarded_motion_asks_the_estop_and_cancel_flag(profile, park, zone):
    estop, cancel = EStop(), CancelFlag()
    state = State(IN_A)
    planner = Planner(state, [line(IN_A, IN_B)])
    guard = PathGuard(planner, state, NAMES, profile, zone, park, poses=profile.named_poses,
                      velocity_scale=lambda: 0.5)
    motion = GuardedMotion(planner, estop, cancel, guard)
    cancel.set()
    assert not motion.move_to(JointTarget(IN_B)).ok and planner.planned == []
    cancel.clear()
    assert motion.move_to(JointTarget(IN_B)).ok and len(planner.executed) == 1
    estop.trip('x')
    assert not motion.move_to_named_pose('home', profile.named_poses['home']).ok
    assert not motion.return_home(profile.named_poses['home']).ok


# ---- park transit -----------------------------------------------------------------------------
def test_ready_to_home_is_a_straight_slow_move_without_the_planner(profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    guard, planner, state = make(profile, park, zone, ready)
    out = guard.move_to_named_pose('home', home)
    assert out.ok and planner.planned == [] and planner.executed == []   # NOT MoveIt
    assert len(planner.timed) == 1 and state.q == tuple(home)
    assert planner.checked, 'the line is collision-checked against the scene'
    times, pos, vel = planner.timed[0]
    v = np.array(vel)
    assert np.abs(v).max() <= park.max_velocity_fraction * 1.0 + 1e-9   # <= 30 % of the maximum
    assert np.all(np.diff(times) > 0)


def peak_ratio(profile, planner):
    """Largest |joint speed| / joint maximum over the timed park trajectory."""
    vmax = np.array([j.velocity for j in profile.joints])
    return float((np.abs(np.array(planner.timed[0][2])) / vmax).max())


def test_park_speed_obeys_the_global_velocity_scale_too(profile, park, zone):
    """Review finding: the park transit used only park.max_velocity_fraction (0.3) and ignored
    max_velocity_scale / velocity_scale, so max_velocity_scale: 0.2 still ran it at 30 %."""
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    assert park.max_velocity_fraction == 0.3
    for scale, cap in ((0.2, 0.2), (0.1, 0.1), (0.3, 0.3), (0.5, 0.3), (1.0, 0.3)):
        guard, planner, _ = make(profile, park, zone, ready, velocity_scale=lambda s=scale: s)
        assert guard.move_to_named_pose('home', home).ok
        assert peak_ratio(profile, planner) <= cap + 1e-9, (scale, peak_ratio(profile, planner))
    # and the way back, and "Eve dön" (ready -> home) use the same cap
    guard, planner, _ = make(profile, park, zone, home, velocity_scale=lambda: 0.2)
    assert guard.move_to_named_pose('ready', ready).ok
    assert peak_ratio(profile, planner) <= 0.2 + 1e-9
    guard, planner, _ = make(profile, park, zone, ready, velocity_scale=lambda: 0.2)
    assert guard.return_home().ok and peak_ratio(profile, planner) <= 0.2 + 1e-9


def test_a_slower_global_scale_makes_the_park_transit_slower_not_faster(profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    took = {}
    for scale in (0.3, 0.1):
        guard, planner, _ = make(profile, park, zone, ready, velocity_scale=lambda s=scale: s)
        assert guard.move_to_named_pose('home', home).ok
        took[scale] = planner.timed[0][0][-1]
    assert took[0.1] > took[0.3]


def test_global_scale_is_read_per_move_and_a_bad_scale_means_no_park_move(profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    scale = {'v': 0.3}
    guard, planner, state = make(profile, park, zone, ready, velocity_scale=lambda: scale['v'])
    assert guard.move_to_named_pose('home', home).ok
    state.q = tuple(ready)
    planner.timed.clear()
    scale['v'] = 0.1                                   # the cap changed between two moves
    assert guard.move_to_named_pose('home', home).ok and peak_ratio(profile, planner) <= 0.1 + 1e-9
    for bad in (0.0, -1.0, float('nan'), None):
        state.q = tuple(ready)
        n = len(planner.timed)
        scale['v'] = bad
        out = guard.move_to_named_pose('home', home)
        assert not out.ok and out.code_name == CODE_PARK and len(planner.timed) == n


def test_mutation_park_fraction_alone_would_ignore_the_cap(profile, park, zone, monkeypatch):
    """Mutation proof for the speed test above: with the global scale ignored (the old code)
    scale 0.2 yields 30 %, which the test rejects."""
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    monkeypatch.setattr(PathGuard, 'park_fraction', lambda self, scale: park.max_velocity_fraction)
    guard, planner, _ = make(profile, park, zone, ready, velocity_scale=lambda: 0.2)
    assert guard.move_to_named_pose('home', home).ok
    assert peak_ratio(profile, planner) > 0.2 + 1e-9


def test_home_to_ready_is_the_same_move_backwards(profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    guard, planner, state = make(profile, park, zone, home)
    assert guard.move_to_named_pose('ready', ready).ok
    assert planner.planned == [] and state.q == tuple(ready)


def test_a_start_near_ready_within_the_tolerance_is_accepted_beyond_it_rejected(
        profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    near = list(ready)
    near[0] += 0.015
    guard, planner, _ = make(profile, park, zone, near)
    assert guard.move_to_named_pose('home', home).ok
    far = list(ready)
    far[0] += 0.03
    guard, planner, _ = make(profile, park, zone, far)
    out = guard.move_to_named_pose('home', home)
    assert not out.ok and out.code_name == CODE_PARK and planner.timed == []
    assert "'ready' konumunda değil" in out.message and 'Eve dön' in out.message


def test_a_random_point_to_home_is_rejected(profile, park, zone):
    home = profile.named_poses['home']
    rng = np.random.default_rng(5)
    for _ in range(10):
        start = np.array(profile.named_poses['ready']) + rng.uniform(-1.0, 1.0, 6)
        guard, planner, _ = make(profile, park, zone, start)
        out = guard.move_to_named_pose('home', home)
        assert not out.ok and planner.timed == [] and planner.executed == []


def test_the_park_move_is_collision_checked_and_fails_closed(profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    guard, planner, state = make(profile, park, zone, ready, valid='nesne ile çarpışma')
    out = guard.move_to_named_pose('home', home)
    assert not out.ok and out.code_name == CODE_PARK and 'çarpışma' in out.message
    assert planner.timed == [] and state.q == tuple(ready)


def test_no_park_file_means_no_park_move(profile, park, zone):
    ready = profile.named_poses['ready']
    guard, planner, _ = make(profile, park, zone, ready, with_park=False)
    out = guard.return_home()
    assert not out.ok and planner.timed == [] and 'park dosyası' in out.message


def test_a_changed_profile_pose_is_not_exempt(profile, park, zone):
    import dataclasses
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    moved = dataclasses.replace(profile, named_poses={**profile.named_poses,
                                                       'home': (0.0, 0.0, 0.0, 0.0, 0.0, 0.01)})
    state = State(ready)
    planner = Planner(state)
    guard = PathGuard(planner, state, NAMES, moved, zone, park, poses=moved.named_poses,
                      velocity_scale=lambda: 0.5)
    out = guard.move_to_named_pose('home', home)
    assert not out.ok and planner.timed == [] and 'doğrulanmış' in out.message


def test_a_waypoint_or_pose_goal_can_not_use_the_park_envelope(profile, park, zone):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    # a waypoint that happens to be called 'home' and holds the home joints
    guard, planner, _ = make(profile, park, zone, ready, [line(ready, home)])
    out = guard.move_to(JointTarget(tuple(home), 'home'))
    assert not out.ok and out.code_name == CODE_PATH and planner.timed == [] \
        and planner.executed == []
    # a Cartesian goal whose planned path is the park path
    guard, planner, _ = make(profile, park, zone, ready, [line(ready, home)])
    out = guard.move_to_pose(PoseTarget(0.0, 0.0, 1.2, 'world'))
    assert not out.ok and planner.timed == [] and planner.executed == []


def test_other_named_poses_use_the_inner_box_only(profile, park, zone):
    ready = profile.named_poses['ready']
    for name in ('cnc', 'pc', 'dik'):
        guard, planner, _ = make(profile, park, zone, ready,
                                 [line(ready, profile.named_poses[name])])
        out = guard.move_to_named_pose(name, profile.named_poses[name])
        assert not out.ok and planner.timed == [] and planner.executed == []
    assert guard.move_to_named_pose('nowhere').code_name == 'UNKNOWN_POSE'


def test_ready_from_elsewhere_is_a_normal_checked_move(profile, park, zone):
    ready = profile.named_poses['ready']
    guard, planner, _ = make(profile, park, zone, IN_A, [line(IN_A, ready)])
    assert guard.move_to_named_pose('ready', ready).ok
    assert len(planner.planned) == 1 and planner.timed == []


# ---- "Eve dön" --------------------------------------------------------------------------------
def test_return_home_from_ready_is_only_the_park_move(profile, park, zone):
    ready = profile.named_poses['ready']
    guard, planner, state = make(profile, park, zone, ready)
    assert guard.return_home().ok
    assert planner.planned == [] and len(planner.timed) == 1
    assert state.q == tuple(profile.named_poses['home'])


def test_return_home_from_elsewhere_goes_to_ready_first_then_parks(profile, park, zone):
    ready = profile.named_poses['ready']
    guard, planner, state = make(profile, park, zone, IN_A, [line(IN_A, ready)])
    assert guard.return_home().ok
    assert len(planner.planned) == 1 and planner.planned[0].label == 'ready'
    assert len(planner.executed) == 1 and len(planner.timed) == 1       # box-checked, then park
    assert state.q == tuple(profile.named_poses['home'])


def test_return_home_stops_when_the_way_to_ready_leaves_the_box(profile, park, zone):
    guard, planner, state = make(profile, park, zone, OUT_A, [line(OUT_A, OUT_B)])
    out = guard.return_home()
    assert not out.ok and planner.timed == [] and state.q == OUT_A
    assert "ready'ye gidilemedi" in out.message


def test_return_home_when_already_home_is_a_no_op(profile, park, zone):
    guard, planner, _ = make(profile, park, zone, profile.named_poses['home'])
    out = guard.return_home()
    assert out.ok and planner.planned == [] and planner.timed == []


# ---- the trajectory of the park move ----
def test_the_park_trajectory_is_straight_and_within_the_speed_and_acceleration_cap():
    q0 = np.array([0.0, -0.1, 1.65, 0.0, 1.2, 0.0])
    q1 = np.zeros(6)
    times, pos, vel = straight_trajectory(q0, q1, [1.0] * 6, [1.0] * 6, 0.3)
    pos, vel, t = np.array(pos), np.array(vel), np.array(times)
    assert np.allclose(pos[0], q0) and np.allclose(pos[-1], q1) and t[0] == 0
    assert np.abs(vel).max() <= 0.3 + 1e-9
    acc = np.abs(np.diff(vel, axis=0) / np.diff(t)[:, None]).max()
    assert acc <= 0.3 * 1.05                                             # <= 30 % of 1 rad/s^2
    d = (q1 - q0) / np.linalg.norm(q1 - q0)
    for p in pos:                                                         # every point on the line
        r = p - q0
        assert np.linalg.norm(r - d * (r @ d)) < 1e-9
    assert np.all(np.diff(t) > 0) and np.allclose(vel[0], 0) and np.allclose(vel[-1], 0)


def test_the_trajectory_rejects_bad_parameters():
    with pytest.raises(ValueError):
        straight_trajectory(np.zeros(6), np.ones(6), [1.0] * 6, [1.0] * 6, 0.0)
    with pytest.raises(ValueError):
        straight_trajectory(np.zeros(6), np.ones(6), [0.0] * 6, [1.0] * 6, 0.3)


# ---- Skills + GuardedMotion wiring ----
def test_skills_named_pose_and_home_go_through_the_guard(profile, park, zone):
    ready = profile.named_poses['ready']
    config = RobotConfig(NAMES, {}, dict(profile.named_poses))
    state = State(ready)
    planner = Planner(state, [lambda q: PlanResult(True, (tuple(q), tuple(ready)))])
    guard = PathGuard(planner, state, NAMES, profile, zone, park, poses=profile.named_poses,
                      velocity_scale=lambda: 0.5)
    skills = Skills(config, GuardedMotion(planner, EStop(), None, guard), None, None, zone=zone)
    assert skills.go_to_named_pose('home').ok and planner.planned == []   # park move, no MoveIt
    assert skills.go_to_named_pose('ready').ok and planner.timed.__len__() == 2
    state.q = IN_A
    out = skills.go_to_named_pose('home')                                  # not at ready
    assert not out.ok and out.code_name == CODE_PARK and 'Eve dön' in out.message
    assert skills.return_home().ok
