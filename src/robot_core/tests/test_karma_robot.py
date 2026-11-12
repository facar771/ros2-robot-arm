"""K8: the karma robot profile (7 axes: 2 x step, 2 x mirrored SG90 pair, 2 x SG90, gripper step).

Every value of that robot is a placeholder or an estimate; these tests guard the STRUCTURE (axes,
mapping, home inside the safe box, jog lock, fail-closed behaviour), never a physical truth."""
import copy

import numpy as np
import pytest
import yaml
from robot_core.hardware import HardwareError, ServoAxis, load_hardware, parse_hardware
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.kinematics import Chain
from robot_core.maturity import (
    HARDWARE_READY,
    SIM_READY,
    gate,
    load_maturity,
    load_quality,
    maturity,
)
from robot_core.profile import ProfileError, load_profile, parse_profile
from robot_core.profile_gen import PROFILE_PATH, generate, main, output_paths, profile_rel

from tests.conftest import SRC

DIR = SRC / 'robot_description' / 'profile' / 'karma_robot'
KARMA = DIR / 'karma_robot.yaml'
HARDWARE = DIR / 'hardware.profile.yaml'
QUALITY = DIR / 'quality.profile.yaml'
STEP_JOINTS = ('joint_1', 'joint_2', 'joint_7')
SERVO_JOINTS = ('joint_3', 'joint_4', 'joint_5', 'joint_6')
MIRRORED = ('joint_3', 'joint_4')


@pytest.fixture(scope='module')
def profile():
    return load_profile(KARMA)


@pytest.fixture(scope='module')
def hw(profile):
    return load_hardware(HARDWARE, profile)


@pytest.fixture(scope='module')
def raw_hw():
    return yaml.safe_load(HARDWARE.read_text(encoding='utf-8'))


# ---- the robot --------------------------------------------------------------------------------
def test_seven_axes_in_chain_order(profile):
    assert profile.joint_names == tuple(f'joint_{i}' for i in range(1, 8))
    assert [j.jog for j in profile.joints] == ['free'] * 6 + ['locked']


def test_home_and_every_named_pose_are_inside_limits_and_safe_box(profile):
    chain = Chain(profile)
    assert set(profile.named_poses) >= {'home', 'ready'}
    for name, q in profile.named_poses.items():
        q = np.array(q)
        assert np.all(q >= chain.lower) and np.all(q <= chain.upper), name
        tip = chain.fk(q)[:3, 3]
        assert profile.safety.zone.check(tuple(tip)) is None, f'{name}: tip {tip.tolist()}'


def test_simulation_starts_at_home_inside_the_box(profile):
    """F4b lesson: the robot must not start outside its safe box."""
    assert [profile.initial_positions[n] for n in profile.joint_names] == \
        list(profile.named_poses['home'])


def test_home_keeps_clear_of_the_arm_joint_limits(profile):
    chain = Chain(profile)
    home = np.array(profile.named_poses['home'])[:6]
    margin = np.minimum(home - chain.lower[:6], chain.upper[:6] - home)
    assert margin.min() >= 0.5


# ---- jog: locked ------------------------------------------------------------------------------
def test_locked_joint_has_a_zero_jacobian_column_the_rest_do_not(profile):
    jac = Chain(profile).jacobian(profile.named_poses['ready'])
    assert not np.any(jac[:, 6])
    assert all(np.any(jac[:, i]) for i in range(6))


def test_cartesian_jog_never_moves_the_gripper_joint(profile):
    chain = Chain(profile)
    engine = DlsJogEngine(chain, profile.safety, JogConfig(speed_scale=1.0))
    q = np.array(profile.named_poses['ready'])
    start = chain.fk(q)[:3, 3]
    for twist in ((0.02, 0, 0, 0, 0, 0), (0, 0.02, 0, 0, 0, 0), (0, 0, -0.02, 0, 0, 0),
                  (0, 0, 0, 0.1, 0, 0)):
        step = engine.step(q, twist, 0.02)
        assert step.dq[6] == 0.0 and step.q_next[6] == q[6], twist
    moved = False
    for _ in range(40):
        step = engine.step(q, (0, 0.02, 0, 0, 0, 0), 0.02)
        q = step.q_next
        moved = moved or bool(np.any(step.dq[:6]))
    assert moved and q[6] == profile.named_poses['ready'][6]
    assert np.linalg.norm(chain.fk(q)[:3, 3] - start) > 1e-4


def test_free_joints_are_unchanged_and_bad_jog_value_is_rejected():
    six = load_profile(PROFILE_PATH)
    assert all(j.jog == 'free' for j in six.joints)
    assert np.all(np.any(Chain(six).jacobian(six.named_poses['ready']) != 0.0, axis=0))
    data = yaml.safe_load(KARMA.read_text(encoding='utf-8'))
    data['joints']['joint_7']['jog'] = 'maybe'
    with pytest.raises(ProfileError, match='joint_7.jog'):
        parse_profile(data)


def test_locked_joint_stays_in_the_moveit_group(profile):
    """One 7-joint group: MoveIt/FK see the gripper joint, only the Cartesian jog skips it."""
    assert 'joint_7' in profile.joint_names
    chain = Chain(profile)
    q = np.array(profile.named_poses['home'])
    q2 = q.copy()
    q2[6] += 0.3
    assert not np.allclose(chain.fk(q), chain.fk(q2))     # FK does depend on it


# ---- hardware mapping -------------------------------------------------------------------------
def test_hardware_maps_every_joint_with_the_right_actuator(hw):
    assert hw.joint_names == tuple(f'joint_{i}' for i in range(1, 8))
    assert hw.servo_axes == SERVO_JOINTS
    for name in STEP_JOINTS:
        assert hw.axis(name).motor_type == 'step' and hw.axis(name).open_loop
    for name in SERVO_JOINTS:
        assert isinstance(hw.axis(name), ServoAxis)
    assert {n for n in SERVO_JOINTS if hw.axis(n).mirror is not None} == set(MIRRORED)


def test_home_matches_every_reference(profile, hw):
    home = dict(zip(profile.joint_names, profile.named_poses['home']))
    for name in STEP_JOINTS:
        assert hw.axis(name).homing.position == pytest.approx(home[name])
    for name in SERVO_JOINTS:
        axis = hw.axis(name)
        assert axis.reference.pose == pytest.approx(home[name])
        assert axis.rest.angle == pytest.approx(home[name])


def test_mirrored_pair_pulses_are_opposite(profile, hw):
    q = list(profile.named_poses['home'])
    first, second = hw.servo_pulses_us(q), hw.servo_mirror_pulses_us(q)
    assert set(second) == set(MIRRORED)
    for name in MIRRORED:
        p = hw.axis(name).pulse_range
        assert first[name] + second[name] == pytest.approx(p.min_us + p.max_us, abs=1)
    for angle in (-1.5, 0.0, 1.5):                          # joint limits of the pair
        axis = hw.axis('joint_3')
        assert axis.mirror_pulse_us(angle) + axis.angle_to_pulse_us(angle) == \
            pytest.approx(500 + 2400)


def test_single_servo_has_no_mirror(hw):
    with pytest.raises(HardwareError, match='aynalı çift tanımlı değil'):
        hw.axis('joint_5').mirror_pulse_us(0.0)


def test_same_direction_mirror_is_rejected(profile, raw_hw):
    """Two servos pulling the same way would fight each other through the shared joint."""
    data = copy.deepcopy(raw_hw)
    servo = data['axes']['joint_3']['servo']
    servo['mirror']['pulse_range'] = dict(servo['pulse_range'])
    with pytest.raises(HardwareError, match='AZALMALI'):
        parse_hardware(data, profile)


def test_mirror_must_cover_the_joint_limits(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    mirror = data['axes']['joint_3']['servo']['mirror']['pulse_range']
    mirror['angle_at_min_us'], mirror['angle_at_max_us'] = 1.0, -1.0     # narrower than +-1.5
    with pytest.raises(HardwareError, match='aynalı servonun'):
        parse_hardware(data, profile)


def test_mirror_without_pulse_range_is_rejected(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    data['axes']['joint_4']['servo']['mirror'] = {}
    with pytest.raises(HardwareError, match='pulse_range'):
        parse_hardware(data, profile)


def test_hardware_axes_must_match_the_robot_joints(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    del data['axes']['joint_7']
    with pytest.raises(HardwareError, match='axes'):
        parse_hardware(data, profile)


def test_step_axes_keep_the_home_switch_rule(profile, raw_hw):
    for name in STEP_JOINTS:
        data = copy.deepcopy(raw_hw)
        data['axes'][name]['homing']['method'] = 'manual_zero'
        with pytest.raises(HardwareError, match='manual_zero'):
            parse_hardware(data, profile)


def test_the_bridge_side_stays_closed_for_a_servo_robot(hw):
    """K6b does not exist: step-only helpers refuse the mixed robot instead of guessing."""
    with pytest.raises(HardwareError):
        hw.joints_to_steps([0.0] * 7)


# ---- quality / readiness ----------------------------------------------------------------------
def test_nothing_is_measured_and_hardware_stays_closed(profile, hw):
    quality = load_quality(QUALITY, profile)
    m = maturity(profile, hw, quality)
    assert m.level == SIM_READY and m.level != HARDWARE_READY
    assert m.gaps and all(g.status in ('placeholder', 'estimated', 'başarısız') for g in m.gaps)
    assert 'axes.joint_3.servo.mirror_pulse_range' in m.missing_fields
    assert 'axes.joint_4.servo.mirror_pulse_range' in m.missing_fields
    assert 'axes.joint_5.servo.mirror_pulse_range' not in m.missing_fields
    assert not gate('real', m).open and gate('sim', m).open


def test_load_maturity_reads_the_sidecar_beside_the_files():
    m = load_maturity(KARMA, HARDWARE, QUALITY)
    assert m.level == SIM_READY
    assert not any(g.status == 'measured' for g in m.gaps)


def test_quality_file_names_only_known_fields(profile):
    data = yaml.safe_load(QUALITY.read_text(encoding='utf-8'))
    assert data['robot'] == 'karma_robot'
    assert 'measured' not in set(data['fields'].values())
    load_quality(QUALITY, profile)                           # unknown path would raise


# ---- generated files --------------------------------------------------------------------------
def test_generated_files_are_up_to_date(profile):
    """Fix: cd src/robot_core && python3 -m robot_core.profile_gen --profile
    ../robot_description/profile/karma_robot/karma_robot.yaml"""
    for key, text in generate(profile).items():
        rel = output_paths(profile.robot)[key]
        assert (SRC / rel).read_text(encoding='utf-8') == text, f'{rel} güncel değil'


def test_karma_outputs_never_share_a_path_with_the_six_axis_robot(profile):
    six = set(output_paths('six_axis_robot').values())
    karma = set(output_paths(profile.robot).values())
    assert not six & karma
    assert all('/generated/karma_robot/' in p for p in karma)


def test_generated_headers_name_the_karma_profile_and_command(profile):
    urdf = generate(profile)['urdf']
    assert profile_rel('karma_robot') in urdf and '--profile' in urdf
    assert '--profile' not in generate(load_profile(PROFILE_PATH))['urdf']


def test_check_mode_passes_for_karma_and_detects_a_stale_file(tmp_path, profile):
    assert main(['--check', '--profile', str(KARMA)]) == 0
    stale = tmp_path / 'src'
    for key, rel in output_paths(profile.robot).items():
        target = stale / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate(profile)[key], encoding='utf-8')
    (stale / output_paths(profile.robot)['urdf']).write_text('eski', encoding='utf-8')
    assert main(['--check', '--profile', str(KARMA), '--src', str(stale)]) == 1


def test_urdf_has_seven_revolute_joints_and_a_gripper_link(profile):
    urdf = generate(profile)['urdf']
    assert urdf.count('type="revolute"') == 7 and 'name="link_7"' in urdf
