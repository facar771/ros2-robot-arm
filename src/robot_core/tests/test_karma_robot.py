"""K8 / A2: the karma robot profile = Ferhat's KUCUK_ROBOT (6 joints: 2 x step, J3/J5 mirrored SG90
pairs, J4/J6 single SG90; the gripper motor is a TOOL ACTUATOR, not a joint).

The kinematics come from the CAD (STL bore centres); every physical value is still a placeholder or
an estimate. These tests guard the STRUCTURE (axes, CAD transcription, mapping, home inside the
safe box, jog lock, fail-closed behaviour), never a physical truth."""
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
    parse_quality,
)
from robot_core.profile import ProfileError, load_profile, parse_profile
from robot_core.profile_gen import PROFILE_PATH, generate, main, output_paths, profile_rel

from tests.conftest import SRC

DIR = SRC / 'robot_description' / 'profile' / 'karma_robot'
KARMA = DIR / 'karma_robot.yaml'
HARDWARE = DIR / 'hardware.profile.yaml'
QUALITY = DIR / 'quality.profile.yaml'
JOINTS = tuple(f'joint_{i}' for i in range(1, 7))
STEP_JOINTS = ('joint_1', 'joint_2')
SERVO_JOINTS = ('joint_3', 'joint_4', 'joint_5', 'joint_6')
MIRRORED = ('joint_3', 'joint_5')
# Joint positions at the zero (CAD) pose, mm, from the bore centres of the STL exports.
CAD_JOINTS_MM = {'joint_1': (0, 0, 61), 'joint_2': (0, 0, 121), 'joint_3': (-120, 0, 171),
                 'joint_4': (-40, 0, 171), 'joint_5': (-15, 0, 171), 'joint_6': (35, 0, 171)}
CAD_AXES = {'joint_1': (0, 0, 1), 'joint_2': (0, 1, 0), 'joint_3': (0, -1, 0),
            'joint_4': (1, 0, 0), 'joint_5': (0, -1, 0), 'joint_6': (1, 0, 0)}
CAD_TCP_MM = (41, 0, 171)                     # face of the flange plate (j6 tutucu)


@pytest.fixture(scope='module')
def profile():
    return load_profile(KARMA)


@pytest.fixture(scope='module')
def raw():
    return yaml.safe_load(KARMA.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def hw(profile):
    return load_hardware(HARDWARE, profile)


@pytest.fixture(scope='module')
def raw_hw():
    return yaml.safe_load(HARDWARE.read_text(encoding='utf-8'))


def joint_frames(profile, q):
    """World position and axis of every joint for joint vector ``q`` (plain forward chain)."""
    t, rot, out = np.zeros(3), np.eye(3), {}
    for j, qi in zip(profile.joints, q):
        t = t + rot @ np.array(j.origin)
        axis = rot @ np.array(j.axis)
        out[j.name] = (t.copy(), axis)
        k = np.array(j.axis) / np.linalg.norm(j.axis)
        kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        rot = rot @ (np.eye(3) + np.sin(qi) * kx + (1 - np.cos(qi)) * kx @ kx)
    return out


# ---- the robot --------------------------------------------------------------------------------
def test_six_joints_and_no_gripper_in_the_chain(profile):
    assert profile.joint_names == JOINTS
    assert all(j.jog == 'free' for j in profile.joints)
    assert profile.links[-1].name == 'link_6'


def test_zero_pose_reproduces_the_cad_joint_positions_and_axes(profile):
    """The profile is a transcription of the CAD: at q = 0 every joint sits on its STL bore."""
    frames = joint_frames(profile, [0.0] * 6)
    for name, mm in CAD_JOINTS_MM.items():
        pos, axis = frames[name]
        assert pos * 1000 == pytest.approx(mm, abs=0.01), name
        assert axis == pytest.approx(CAD_AXES[name]), name
    tip = Chain(profile).fk(np.zeros(6))[:3, 3] * 1000
    assert tip == pytest.approx(CAD_TCP_MM, abs=0.01)


def test_chain_forward_kinematics_agrees_with_the_plain_chain(profile):
    """Same tool point two ways: Chain vs the plain joint walk + the TCP offset along the roll axis
    (joint_6 = roll about the flange's x axis, so the 6 mm offset follows that axis)."""
    q = np.array(profile.named_poses['ready'])
    j6, x_axis = joint_frames(profile, q)['joint_6']
    expected = j6 + x_axis * profile.tcp.offset[0]
    assert Chain(profile).fk(q)[:3, 3] == pytest.approx(expected, abs=1e-9)


def test_zero_pose_is_alignment_only_outside_the_folded_limits(profile):
    """The CAD pose is the arm folded against its end stops: it must never be a commanded pose."""
    lims = {j.name: (j.lower, j.upper) for j in profile.joints}
    assert lims['joint_2'][0] > 0.0 and lims['joint_3'][0] > 0.0
    for name, pose in profile.named_poses.items():
        assert pose != [0.0] * 6, name


def test_every_named_pose_is_inside_limits_and_the_safe_box(profile):
    chain = Chain(profile)
    assert set(profile.named_poses) >= {'home', 'ready'}
    for name, q in profile.named_poses.items():
        q = np.array(q)
        assert np.all(q >= chain.lower) and np.all(q <= chain.upper), name
        tip = chain.fk(q)[:3, 3]
        assert profile.safety.zone.check(tuple(tip)) is None, f'{name}: tip {tip.tolist()}'


def test_simulation_starts_at_home_far_from_the_limits(profile):
    """F4b lesson: the robot must not start outside its safe box (nor at an end stop)."""
    home = np.array(profile.named_poses['home'])
    assert [profile.initial_positions[n] for n in JOINTS] == list(home)
    chain = Chain(profile)
    assert np.minimum(home - chain.lower, chain.upper - home).min() >= 1.0


def test_ready_pose_is_a_usable_jog_start(profile):
    jac = Chain(profile).jacobian(profile.named_poses['ready'])
    assert np.linalg.svd(jac, compute_uv=False)[-1] > 0.06       # above the jog's sigma_slow


def test_safe_box_is_sized_for_this_small_arm(profile):
    six = load_profile(PROFILE_PATH)
    box, big = profile.safety.zone, six.safety.zone
    assert np.prod(np.subtract(box.upper, box.lower)) < np.prod(np.subtract(big.upper, big.lower))
    assert box.lower[0] > 0.0                                     # in FRONT of the robot
    corners = [(x, y, z) for x in (box.lower[0], box.upper[0]) for y in (box.lower[1], box.upper[1])
               for z in (box.lower[2], box.upper[2])]
    far = max(np.linalg.norm(np.subtract(c, profile.reach_center)) for c in corners)
    assert far <= profile.reach_radius


# ---- link length 0 (horizontal links) ---------------------------------------------------------
@pytest.mark.parametrize('shape', [{'type': 'box', 'size': [0.1, 0.02]},
                                   {'type': 'cylinder', 'radius': 0.01}])
def test_zero_length_link_needs_its_own_shape_extent(raw, shape):
    data = copy.deepcopy(raw)
    data['links']['link_3']['shape'] = shape
    with pytest.raises(ProfileError, match='links.link_3.shape'):
        parse_profile(data)


def test_tiny_nonzero_length_is_still_a_typo(raw):
    data = copy.deepcopy(raw)
    data['links']['link_3']['length'] = 0.0004
    with pytest.raises(ProfileError, match='alt sınır'):
        parse_profile(data)


# ---- jog: locked (the mechanism stays; no joint of this robot uses it) -----------------------
@pytest.fixture(scope='module')
def locked(raw):
    data = copy.deepcopy(raw)
    data['joints']['joint_6']['jog'] = 'locked'
    return parse_profile(data)


def test_locked_joint_has_a_zero_jacobian_column(locked):
    jac = Chain(locked).jacobian(locked.named_poses['ready'])
    assert not np.any(jac[:, 5])
    assert all(np.any(jac[:, i]) for i in range(5))


def test_locking_a_joint_of_a_6_joint_arm_stops_the_jog_instead_of_guessing(locked):
    """6 joints, one locked = 5 usable columns for a 6-D twist: sigma_min is 0, the jog engine
    reports a singularity and commands nothing (fail-closed). Locking is for a joint BEYOND six."""
    engine = DlsJogEngine(Chain(locked), locked.safety, JogConfig(speed_scale=1.0))
    q = np.array(locked.named_poses['ready'])
    step = engine.step(q, (0.02, 0, 0, 0, 0, 0), 0.02)
    assert step.status.value == 'singularity' and not np.any(step.dq)


def test_bad_jog_value_is_rejected(raw):
    data = copy.deepcopy(raw)
    data['joints']['joint_6']['jog'] = 'maybe'
    with pytest.raises(ProfileError, match='joint_6.jog'):
        parse_profile(data)


# ---- hardware mapping -------------------------------------------------------------------------
def test_hardware_maps_every_joint_with_the_right_actuator(hw):
    assert hw.joint_names == JOINTS
    assert hw.servo_axes == SERVO_JOINTS
    for name in STEP_JOINTS:
        assert hw.axis(name).motor_type == 'step' and hw.axis(name).open_loop
    for name in SERVO_JOINTS:
        assert isinstance(hw.axis(name), ServoAxis)
    assert {n for n in SERVO_JOINTS if hw.axis(n).mirror is not None} == set(MIRRORED)
    assert hw.axis('joint_1').gear_ratio == 4.0 and hw.axis('joint_2').gear_ratio == 16.0


def test_home_matches_every_reference(profile, hw):
    home = dict(zip(JOINTS, profile.named_poses['home']))
    for name in STEP_JOINTS:
        assert hw.axis(name).homing.position == pytest.approx(home[name])
    for name in SERVO_JOINTS:
        axis = hw.axis(name)
        assert axis.reference.pose == pytest.approx(home[name])
        assert axis.rest.angle == pytest.approx(home[name])


def test_mirrored_pair_pulses_are_opposite(profile, hw):
    q = list(profile.named_poses['ready'])
    first, second = hw.servo_pulses_us(q), hw.servo_mirror_pulses_us(q)
    assert set(second) == set(MIRRORED)
    for name in MIRRORED:
        p = hw.axis(name).pulse_range
        assert first[name] + second[name] == pytest.approx(p.min_us + p.max_us, abs=1)


def test_single_servo_has_no_mirror(hw):
    with pytest.raises(HardwareError, match='aynalı çift tanımlı değil'):
        hw.axis('joint_4').mirror_pulse_us(0.0)


def test_hardware_axes_must_match_the_robot_joints(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    data['axes']['joint_7'] = data['tool_actuators'].pop('joint_7')    # gripper as a joint: no
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
        hw.joints_to_steps([0.0] * 6)


# ---- tool actuator (gripper motor) ------------------------------------------------------------
def test_gripper_motor_is_a_tool_actuator_not_a_joint(hw):
    assert hw.tool_actuator_names == ('joint_7',)
    grip = hw.tool_actuator('joint_7')
    assert grip.tool == 'gripper' and grip.axis.motor_type == 'step' and grip.axis.open_loop
    assert grip.axis.driver_model == 'A4988'
    assert 'joint_7' not in hw.joint_names
    assert any('Takım aktüatörleri: joint_7' in w for w in hw.warnings)


def test_tool_actuator_keeps_the_home_switch_rule(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    data['tool_actuators']['joint_7']['homing']['method'] = 'manual_zero'
    with pytest.raises(HardwareError, match='tool_actuators.joint_7'):
        parse_hardware(data, profile)


@pytest.mark.parametrize('mutate, match', [
    (lambda t: t.update(tool='hand'), 'tool_actuators.joint_7.tool'),
    (lambda t: t.pop('travel'), 'tool_actuators.joint_7.travel'),
    (lambda t: t['travel'].update(position=[0.5, 0.1]), 'travel.position'),
    (lambda t: t['motor'].update(type='hobby_servo'), "yalnız 'step'"),
    (lambda t: t['travel'].update(position=[-0.5, 0.8]), 'tool_actuators.joint_7'),   # > motor
])
def test_tool_actuator_is_validated(profile, raw_hw, mutate, match):
    data = copy.deepcopy(raw_hw)
    mutate(data['tool_actuators']['joint_7'])
    with pytest.raises(HardwareError, match=match):
        parse_hardware(data, profile)


def test_tool_actuator_cannot_take_a_joint_name(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    data['tool_actuators'] = {'joint_6': data['tool_actuators']['joint_7']}
    with pytest.raises(HardwareError, match='robot eklemiyle aynı ad'):
        parse_hardware(data, profile)


def test_a_robot_without_tool_actuators_is_unchanged(profile, raw_hw):
    data = copy.deepcopy(raw_hw)
    del data['tool_actuators']
    assert parse_hardware(data, profile).tool_actuators == ()


# ---- quality / readiness ----------------------------------------------------------------------
def test_nothing_is_measured_and_hardware_stays_closed(profile, hw):
    m = maturity(profile, hw, load_quality(QUALITY, profile))
    assert m.level == SIM_READY and m.level != HARDWARE_READY
    statuses = {g.status for g in m.gaps}
    assert statuses <= {'placeholder', 'estimated', 'başarısız'}
    for name in MIRRORED:
        assert f'axes.{name}.servo.mirror_pulse_range' in m.missing_fields
    assert 'axes.joint_4.servo.mirror_pulse_range' not in m.missing_fields
    assert 'tool_actuators.joint_7.homing.position' in m.missing_fields
    assert not gate('real', m).open and gate('sim', m).open


def test_measured_tool_actuator_tags_alone_cannot_open_the_gate(profile, hw):
    data = yaml.safe_load(QUALITY.read_text(encoding='utf-8'))
    for path in list(data['fields']):
        if path.startswith('tool_actuators.'):
            data['fields'][path] = 'measured'
    m = maturity(profile, hw, parse_quality(data, profile))
    assert not any(g.field.startswith('tool_actuators.') for g in m.gaps)
    assert m.level == SIM_READY


def test_unknown_tool_actuator_field_is_rejected(profile):
    data = yaml.safe_load(QUALITY.read_text(encoding='utf-8'))
    data['fields']['tool_actuators.joint_7.colour'] = 'measured'
    with pytest.raises(ProfileError, match='bilinmeyen alan yolu'):
        parse_quality(data, profile)


def test_load_maturity_reads_the_sidecar_beside_the_files():
    m = load_maturity(KARMA, HARDWARE, QUALITY)
    assert m.level == SIM_READY
    assert not any(g.status == 'measured' for g in m.gaps)


def test_quality_file_names_only_known_fields_and_no_physical_value_measured(profile):
    data = yaml.safe_load(QUALITY.read_text(encoding='utf-8'))
    assert data['robot'] == 'karma_robot'
    measured = {p for p, t in data['fields'].items() if t == 'measured'}
    assert measured == {'axes.joint_1.driver.model', 'axes.joint_2.driver.model'}   # A2b: bench
    assert data['fields']['links.link_2.length'] == 'estimated'           # CAD = estimate
    assert data['fields']['joints.joint_1.limits.position'] == 'estimated'   # Ferhat: cables OK


def test_driver_model_tag_is_informational_only(profile, hw):
    """ULN2003 verified on the bench (A2b): recorded, but it is no critical field, so it neither
    opens a gate nor removes a gap; untagging it changes nothing either."""
    data = yaml.safe_load(QUALITY.read_text(encoding='utf-8'))
    tagged = maturity(profile, hw, parse_quality(data, profile))
    for path in ('axes.joint_1.driver.model', 'axes.joint_2.driver.model'):
        del data['fields'][path]
    untagged = maturity(profile, hw, parse_quality(data, profile))
    assert tagged.level == untagged.level == SIM_READY
    assert [str(g) for g in tagged.gaps] == [str(g) for g in untagged.gaps]
    assert not any('driver.model' in g.field for g in tagged.gaps)


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


def test_check_mode_passes_for_karma_and_detects_a_stale_file(tmp_path, profile):
    assert main(['--check', '--profile', str(KARMA)]) == 0
    stale = tmp_path / 'src'
    for key, rel in output_paths(profile.robot).items():
        target = stale / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generate(profile)[key], encoding='utf-8')
    (stale / output_paths(profile.robot)['urdf']).write_text('eski', encoding='utf-8')
    assert main(['--check', '--profile', str(KARMA), '--src', str(stale)]) == 1


def test_urdf_has_six_revolute_joints_and_no_gripper(profile):
    urdf = generate(profile)['urdf']
    assert urdf.count('type="revolute"') == 6
    assert 'joint_7' not in urdf and 'link_7' not in urdf
