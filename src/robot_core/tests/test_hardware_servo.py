"""K6a: actuator type ``hobby_servo`` (data model, validation, quality tags; no ROS, no firmware).

Real SG90 hardware, its torque/current datasheet and any firmware are NOT exercised here: every
number below is a stand-in for the validator, not a measurement.
"""
import copy
import dataclasses
import math
from dataclasses import replace

import pytest
import yaml
from robot_core.hardware import (
    ESTIMATE_LABEL,
    MANUAL_ZERO_RECONFIRM,
    OPEN_LOOP_WARNING,
    Axis,
    HardwareError,
    ServoAxis,
    describe_hardware,
    load_hardware,
    parse_hardware,
)
from robot_core.hardware_gen import HARDWARE_PATH, generate, main
from robot_core.maturity import HARDWARE_READY, SIM_READY, Quality, gate, maturity, parse_quality
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH

from robot_core import maturity as mat

SERVO_JOINTS = ('joint_2', 'joint_5')       # +-1.57 rad in the robot profile


def servo_axis(**over):
    """A valid servo axis block (joint units: rad, rad/s, us)."""
    axis = {
        'motor': {'type': 'hobby_servo', 'model': 'SG90'},
        'servo': {
            'pulse_range': {'min_us': 500, 'max_us': 2400,
                            'angle_at_min_us': -1.7, 'angle_at_max_us': 1.7},
            'slew_limit': 2.0,
            'rest': {'angle': 0.0, 'speed': 0.2},
        },
        'reference': {'mode': 'manual_zero', 'pose': 0.0},
        'limits': {'position': [-1.7, 1.7]},
    }
    axis.update(over)
    return axis


@pytest.fixture(scope='module')
def robot():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def raw():
    return yaml.safe_load(HARDWARE_PATH.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def step_hw(robot):
    return load_hardware(HARDWARE_PATH, robot)


def mixed_raw(raw):
    data = copy.deepcopy(raw)
    for joint in SERVO_JOINTS:
        data['axes'][joint] = servo_axis()
    return data


@pytest.fixture(scope='module')
def mixed(raw, robot):
    return parse_hardware(mixed_raw(raw), robot)


def bad(raw, robot, mutate, match, joint='joint_2'):
    data = mixed_raw(raw)
    mutate(data['axes'][joint])
    with pytest.raises(HardwareError, match=match):
        parse_hardware(data, robot)


# ---- mixed robot: every axis keeps its own type ----------------------------------------------
def test_mixed_robot_each_axis_has_its_own_type(mixed, step_hw):
    assert [type(a) for a in mixed.axes] == [Axis, ServoAxis, Axis, Axis, ServoAxis, Axis]
    assert mixed.servo_axes == SERVO_JOINTS
    assert [a.motor_type for a in mixed.axes] == ['step', 'hobby_servo', 'step', 'step',
                                                  'hobby_servo', 'step']
    # step axes come out exactly as in the all-step profile: the new type changes nothing for them
    for name in ('joint_1', 'joint_3', 'joint_4', 'joint_6'):
        assert mixed.axis(name) == step_hw.axis(name)


def test_shipped_all_step_profile_has_no_servo_axis(step_hw):
    assert step_hw.servo_axes == () and all(isinstance(a, Axis) for a in step_hw.axes)
    assert describe_hardware()['servo_axes'] == []


# ---- pulse <-> angle --------------------------------------------------------------------------
def test_pulse_angle_mapping_is_linear_and_round_trips(mixed):
    a = mixed.axis('joint_2')
    assert a.angle_to_pulse_us(-1.7) == pytest.approx(500.0)
    assert a.angle_to_pulse_us(1.7) == pytest.approx(2400.0)
    assert a.angle_to_pulse_us(0.0) == pytest.approx(1450.0)
    for angle in (-1.6, -0.3, 0.0, 0.9, 1.65):
        assert a.pulse_us_to_angle(a.angle_to_pulse_us(angle)) == pytest.approx(angle)
    assert a.angle_to_pulse_us_int(0.0) == 1450


def test_mirrored_mount_swaps_the_angle_direction(raw, robot):
    data = mixed_raw(raw)
    pr = data['axes']['joint_5']['servo']['pulse_range']
    pr['angle_at_min_us'], pr['angle_at_max_us'] = 1.7, -1.7
    a = parse_hardware(data, robot).axis('joint_5')
    assert a.angle_to_pulse_us(1.7) == pytest.approx(500.0)
    assert a.angle_to_pulse_us(-1.7) == pytest.approx(2400.0)


def test_angle_outside_the_limits_is_refused_not_clamped(mixed):
    a = mixed.axis('joint_2')
    for angle in (1.7001, -1.8, math.inf, math.nan):
        with pytest.raises(HardwareError, match='açı sınırı'):
            a.angle_to_pulse_us(angle)
    with pytest.raises(HardwareError, match='darbe aralığı'):
        a.pulse_us_to_angle(2500)
    with pytest.raises(HardwareError, match='darbe aralığı'):
        a.pulse_us_to_angle(400)


def test_servo_pulses_for_a_joint_vector_cover_servo_axes_only(mixed):
    pulses = mixed.servo_pulses_us([0.1, 0.0, 0.2, 0.3, -0.5, 0.0])
    assert set(pulses) == set(SERVO_JOINTS) and pulses['joint_2'] == 1450
    with pytest.raises(HardwareError, match='açı sınırı'):
        mixed.servo_pulses_us([0, 1.71, 0, 0, 0, 0])
    with pytest.raises(ValueError, match='değer bekleniyordu'):
        mixed.servo_pulses_us([0, 0])


# ---- software slew limit ----------------------------------------------------------------------
def test_slew_step_never_exceeds_the_cap_and_never_overshoots(mixed):
    a = mixed.axis('joint_2')                      # slew_limit 2.0 rad/s
    assert a.slew_step(0.0, 1.0, 0.1) == pytest.approx(0.2)            # 2.0 * 0.1
    assert a.slew_step(0.0, -1.0, 0.1) == pytest.approx(-0.2)
    assert a.slew_step(0.9, 1.0, 0.1) == pytest.approx(1.0)            # arrives, no overshoot
    assert a.slew_step(0.0, 1.0, 0.1, speed=0.5) == pytest.approx(0.05)    # narrower wins
    assert a.slew_step(0.0, 1.0, 0.1, speed=99.0) == pytest.approx(0.2)    # wider cannot win
    assert a.slew_step(0.3, 0.3, 0.1) == 0.3


def test_slew_step_rejects_bad_input(mixed):
    a = mixed.axis('joint_2')
    with pytest.raises(HardwareError, match='açı sınırı'):
        a.slew_step(0.0, 2.0, 0.1)                 # target outside the limits
    with pytest.raises(HardwareError, match='açı sınırı'):
        a.slew_step(2.0, 0.0, 0.1)                 # current outside the limits
    for dt in (-0.1, math.nan, math.inf):
        with pytest.raises(HardwareError, match='dt'):
            a.slew_step(0.0, 0.5, dt)
    with pytest.raises(HardwareError, match='pozitif'):
        a.slew_step(0.0, 0.5, 0.1, speed=0.0)


def test_rest_speed_defaults_to_the_slow_value(raw, robot):
    data = mixed_raw(raw)
    del data['axes']['joint_2']['servo']['rest']['speed']
    assert parse_hardware(data, robot).axis('joint_2').rest.speed == pytest.approx(0.2)


# ---- validation: missing fields ---------------------------------------------------------------
@pytest.mark.parametrize('mutate,match', [
    (lambda a: a.pop('servo'), 'eksik anahtar.*servo'),
    (lambda a: a.pop('reference'), 'eksik anahtar.*reference'),
    (lambda a: a.pop('limits'), 'eksik anahtar.*limits'),
    (lambda a: a['servo'].pop('pulse_range'), 'eksik anahtar.*pulse_range'),
    (lambda a: a['servo'].pop('slew_limit'), 'eksik anahtar.*slew_limit'),
    (lambda a: a['servo'].pop('rest'), 'eksik anahtar.*rest'),
    (lambda a: a['servo']['rest'].pop('angle'), 'eksik anahtar.*angle'),
    (lambda a: a['servo']['pulse_range'].pop('max_us'), 'eksik anahtar.*max_us'),
    (lambda a: a['servo']['pulse_range'].pop('angle_at_max_us'), 'eksik anahtar.*angle_at_max_us'),
    (lambda a: a['reference'].pop('mode'), 'eksik anahtar.*mode'),
    (lambda a: a['reference'].pop('pose'), 'eksik anahtar.*pose'),
    (lambda a: a['limits'].pop('position'), 'eksik anahtar.*position'),
])
def test_missing_servo_field_is_rejected_with_its_place(raw, robot, mutate, match):
    bad(raw, robot, mutate, match)


def test_error_messages_name_the_axis_in_the_yaml(raw, robot):
    bad(raw, robot, lambda a: a['servo'].pop('slew_limit'), r'axes\.joint_5\.servo', 'joint_5')


# ---- validation: angle / pulse ranges ---------------------------------------------------------
@pytest.mark.parametrize('mutate,match', [
    # angle limits outside what the calibrated pulse range can reach
    (lambda a: a['limits'].update(position=[-1.7, 1.8]), 'darbe aralığının açılarının'),
    (lambda a: a['limits'].update(position=[-1.8, 1.7]), 'darbe aralığının açılarının'),
    (lambda a: a['limits'].update(position=[0.5, 0.5]), 'alt sınır'),
    (lambda a: a['limits'].update(position=[-1.0]), r'\[alt, üst\]'),
    (lambda a: a['limits'].update(position=[-90, 90]), 'sınır'),            # degrees typed
    # joint (URDF) limits must sit inside the servo limits minus the margin
    (lambda a: a['limits'].update(position=[-1.6, 1.6]), 'servo açı limiti'),
    (lambda a: a.update(margin={'position': 0.2}), 'servo açı limiti'),
    # pulse widths: unit sanity and ordering
    (lambda a: a['servo']['pulse_range'].update(min_us=0.5, max_us=2.4), 'alt sınır'),   # ms
    (lambda a: a['servo']['pulse_range'].update(max_us=50000), 'üst sınır'),
    (lambda a: a['servo']['pulse_range'].update(min_us=2400, max_us=500), 'min_us < max_us'),
    (lambda a: a['servo']['pulse_range'].update(min_us=1400, max_us=1500), 'en az'),
    (lambda a: a['servo']['pulse_range'].update(angle_at_min_us=0, angle_at_max_us=0), 'sıfır'),
    (lambda a: a['servo']['pulse_range'].update(angle_at_min_us=-180, angle_at_max_us=180),
     'sınır'),
    (lambda a: a['servo']['pulse_range'].update(angle_at_min_us=-3.5, angle_at_max_us=3.5),
     'bir turdan'),
    (lambda a: a['servo']['pulse_range'].update(max_us='2400'), 'sayı'),
    (lambda a: a['servo']['pulse_range'].update(max_us=True), 'sayı'),
    (lambda a: a['servo']['pulse_range'].update(max_us=math.nan), 'sayı'),
    (lambda a: a['servo']['pulse_range'].update(extra=1), 'bilinmeyen'),
])
def test_out_of_range_angles_and_pulses_are_rejected(raw, robot, mutate, match):
    bad(raw, robot, mutate, match)


@pytest.mark.parametrize('mutate,match', [
    (lambda a: a['servo'].update(slew_limit=1.0), 'yazılım hız sınırı'),    # joint 1.0 > 1.0-0.05
    (lambda a: a['servo'].update(slew_limit=0), 'pozitif'),
    (lambda a: a['servo'].update(slew_limit=-1.0), 'pozitif'),
    (lambda a: a['servo'].update(slew_limit=99.0), 'üst sınır'),
    (lambda a: a['servo']['rest'].update(angle=1.6), 'eklem limiti'),
    (lambda a: a['servo']['rest'].update(speed=2.5), 'aşıyor'),
    (lambda a: a['servo']['rest'].update(speed=0), 'pozitif'),
    (lambda a: a['limits'].update(velocity=1.0), 'servo.slew_limit'),
    (lambda a: a['limits'].update(acceleration=1.0), 'servo.slew_limit'),
])
def test_slew_and_rest_rules(raw, robot, mutate, match):
    bad(raw, robot, mutate, match)


# ---- validation: no feedback, no step fields --------------------------------------------------
@pytest.mark.parametrize('mutate,match', [
    (lambda a: a.update(encoder='incremental'), 'geri besleme taşımaz'),
    (lambda a: a.update(encoder='absolute'), 'geri besleme taşımaz'),
    (lambda a: a['servo'].update(position_source='measured'), 'bilinmeyen'),
    (lambda a: a['servo'].update(measured=True), 'bilinmeyen'),
    (lambda a: a.update(homing={'method': 'switch', 'position': 0.0}), 'step alanı.*homing'),
    (lambda a: a.update(gear_ratio=2.0), 'step alanı.*gear_ratio'),
    (lambda a: a.update(driver={'microstep': 16}), 'step alanı.*driver'),
    (lambda a: a.update(direction=1), 'step alanı.*direction'),
    (lambda a: a.update(zero_offset=0.0), 'step alanı.*zero_offset'),
    (lambda a: a['motor'].update(steps_per_rev=200), 'bilinmeyen'),
    (lambda a: a.update(unknown=1), 'bilinmeyen'),
])
def test_servo_has_no_feedback_and_no_step_fields(raw, robot, mutate, match):
    bad(raw, robot, mutate, match)


def test_the_estimate_label_cannot_be_dropped(mixed):
    for name in SERVO_JOINTS:
        a = mixed.axis(name)
        assert a.position_source == ESTIMATE_LABEL == 'kestirim'
        assert a.open_loop and a.encoder == 'none'
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.position_source = 'measured'       # also a read-only property
    assert set(mixed.open_loop_axes) >= set(SERVO_JOINTS)


def test_unsupported_motor_type_still_rejected(raw, robot):
    data = copy.deepcopy(raw)
    data['axes']['joint_3']['motor']['type'] = 'dc'
    with pytest.raises(HardwareError, match="yalnızca 'step' ve 'hobby_servo'"):
        parse_hardware(data, robot)


def test_axis_without_a_motor_type_is_not_taken_for_a_servo(raw, robot):
    """No ``motor.type``: the servo blocks are not honoured, the step parser refuses the axis."""
    data = mixed_raw(raw)
    del data['axes']['joint_2']['motor']['type']
    with pytest.raises(HardwareError, match=r'axes\.joint_2.*bilinmeyen anahtar'):
        parse_hardware(data, robot)


# ---- manual_zero: the extended reference rule -------------------------------------------------
@pytest.mark.parametrize('mode', ['switch', 'index', 'absolute', 'none', 'auto', '', None, 1])
def test_servo_reference_other_than_manual_zero_is_rejected(raw, robot, mode):
    bad(raw, robot, lambda a: a['reference'].update(mode=mode), 'reference.mode')


@pytest.mark.parametrize('key', ['reconfirm', 'reconfirm_on', 'remember', 'persist', 'confirmed'])
def test_reconfirmation_cannot_be_disabled_or_remembered_by_the_profile(raw, robot, key):
    bad(raw, robot, lambda a: a['reference'].update({key: False}), 'sabit kural')


@pytest.mark.parametrize('pose,match', [(1.6, 'eklem limiti'), (-1.6, 'eklem limiti'),
                                        (math.nan, 'sayı'), ('0', 'sayı')])
def test_reference_pose_must_be_inside_the_limits(raw, robot, pose, match):
    bad(raw, robot, lambda a: a['reference'].update(pose=pose), match)


def test_manual_zero_pose_and_fixed_reconfirm_rule_in_the_data_model(mixed):
    ref = mixed.axis('joint_2').reference
    assert (ref.mode, ref.pose) == ('manual_zero', 0.0)
    assert ref.reconfirm_on == MANUAL_ZERO_RECONFIRM == ('açılış', 'ACİL DUR')
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.reconfirm_on = ()


def test_manual_zero_is_not_allowed_on_a_step_axis(raw, robot):
    """The open-loop step rule is untouched: no switch -> no reference. manual_zero is refused
    with a message that says why (and the other no-switch combinations still fail as before)."""
    data = copy.deepcopy(raw)
    data['axes']['joint_3']['homing'] = {'method': 'manual_zero', 'position': 0.0}
    with pytest.raises(HardwareError, match="manual_zero' step ekseninde İZİNLİ DEĞİL"):
        parse_hardware(data, robot)
    data = copy.deepcopy(raw)
    data['axes']['joint_3']['homing'] = {'method': 'absolute', 'position': 0.0}   # encoder: none
    with pytest.raises(HardwareError, match='açık döngü'):
        parse_hardware(data, robot)


def test_step_axis_may_not_borrow_the_servo_blocks(raw, robot):
    data = copy.deepcopy(raw)
    data['axes']['joint_3']['reference'] = {'mode': 'manual_zero', 'pose': 0.0}
    with pytest.raises(HardwareError, match='bilinmeyen anahtar.*reference'):
        parse_hardware(data, robot)
    data = copy.deepcopy(raw)
    data['axes']['joint_3']['servo'] = servo_axis()['servo']
    with pytest.raises(HardwareError, match='bilinmeyen anahtar.*servo'):
        parse_hardware(data, robot)


# ---- fail closed: nothing step-only may quietly use a servo axis ------------------------------
def test_step_only_conversions_refuse_a_profile_with_servo_axes(mixed):
    with pytest.raises(HardwareError, match='hobby_servo'):
        mixed.joints_to_steps([0] * 6)
    with pytest.raises(HardwareError, match='hobby_servo'):
        mixed.steps_to_joints([0] * 6)


def test_firmware_header_generator_refuses_servo_axes(mixed, robot, raw, tmp_path):
    with pytest.raises(HardwareError, match='firmware başlığı.*hobby_servo'):
        generate(mixed, robot)
    # CLI: a clear message and exit code 2, nothing written, no traceback
    (tmp_path / 'six_axis_robot.yaml').write_text(PROFILE_PATH.read_text(encoding='utf-8'),
                                                  encoding='utf-8')
    hw_path = tmp_path / 'hardware.profile.yaml'
    hw_path.write_text(yaml.safe_dump(mixed_raw(raw)), encoding='utf-8')
    assert main(['--hardware', str(hw_path), '--src', str(tmp_path)]) == 2
    assert not (tmp_path / 'robot_description').exists()


# ---- warnings and the Hub read function -------------------------------------------------------
def test_warnings_say_estimate_for_servo_and_open_loop_for_step(mixed, step_hw):
    text = ' '.join(mixed.warnings)
    assert 'kestirim' in text and 'GERİ BESLEME YOK' in text
    assert 'joint_2, joint_5' in text and 'AÇIK DÖNGÜ' in text
    assert len(mixed.warnings) == 2
    # the all-step profile's warning is byte-identical to before
    assert step_hw.warnings == (
        f"{OPEN_LOOP_WARNING} Eksenler: {', '.join(step_hw.open_loop_axes)}.",)


def test_describe_hardware_reports_servo_axes_and_never_raises(raw, mixed, tmp_path):
    (tmp_path / 'six_axis_robot.yaml').write_text(PROFILE_PATH.read_text(encoding='utf-8'),
                                                  encoding='utf-8')
    path = tmp_path / 'hardware.profile.yaml'
    path.write_text(yaml.safe_dump(mixed_raw(raw)), encoding='utf-8')
    d = describe_hardware(path)
    assert d['ok'] and d['servo_axes'] == list(SERVO_JOINTS) and d['open_loop']
    by_joint = {a['joint']: a for a in d['axes']}
    s = by_joint['joint_2']
    assert s['motor']['type'] == 'hobby_servo' and s['loop'] == 'open'
    assert s['position_source'] == 'kestirim' and s['encoder'] == 'none'
    assert s['reference'] == {'mode': 'manual_zero', 'pose': 0.0,
                              'reconfirm_on': ['açılış', 'ACİL DUR'], 'position': 'kestirim'}
    assert s['servo']['slew_limit'] == 2.0 and s['servo']['rest'] == {'angle': 0.0, 'speed': 0.2}
    assert 'steps_per_rev' not in s['motor'] and 'homing' not in s
    assert by_joint['joint_1']['motor']['type'] == 'step' and 'homing' in by_joint['joint_1']
    assert any('kestirim' in w for w in d['warnings'])
    # a broken servo block is an error dict, not an exception
    broken = mixed_raw(raw)
    del broken['axes']['joint_2']['servo']['slew_limit']
    path.write_text(yaml.safe_dump(broken), encoding='utf-8')
    d = describe_hardware(path)
    assert d['ok'] is False and 'slew_limit' in d['error']


# ---- J5 quality tags and maturity for servo fields --------------------------------------------
SERVO_PATHS = ('servo.pulse_range', 'servo.slew_limit', 'servo.rest_angle', 'reference.pose',
               'limits.position')


@pytest.fixture(scope='module')
def strong(robot):
    return replace(robot, joints=tuple(replace(j, effort=1000.0) for j in robot.joints))


def measured_all(robot, hw, **tags):
    q = Quality()
    for path in mat._critical_paths(robot, hw):
        q = q.with_tag(path, 'measured', pin=mat.field_value(robot, hw, path))
    for path, tag in tags.items():
        q = q.with_tag(path, tag)
    return q


def test_servo_axes_require_their_own_fields_not_the_step_ones(strong, mixed):
    paths = set(mat._critical_paths(strong, mixed))
    for joint in SERVO_JOINTS:
        for p in SERVO_PATHS:
            assert f'axes.{joint}.{p}' in paths
        for step_only in mat.STEP_ONLY:
            assert f'axes.{joint}.{step_only}' not in paths
    assert 'axes.joint_1.gear_ratio' in paths and 'axes.joint_1.servo.slew_limit' not in paths
    for joint in SERVO_JOINTS:                       # every required field has a real value
        for p in SERVO_PATHS:
            assert mat.field_value(strong, mixed, f'axes.{joint}.{p}') is not None


def test_untagged_servo_fields_are_placeholders_and_keep_real_closed(strong, mixed):
    m = maturity(strong, mixed)
    assert m.level == SIM_READY
    gaps = {g.field: g.status for g in m.gaps}
    for joint in SERVO_JOINTS:
        for p in SERVO_PATHS:
            assert gaps[f'axes.{joint}.{p}'] == 'placeholder'
    assert not gate('real', m).open
    assert 'axes.joint_2.servo.pulse_range' in gate('real', m).missing
    assert gate('sim', m).open                       # simulation is not blocked by missing data


def test_all_servo_fields_measured_is_hardware_ready_but_gate_waits_for_f4b(strong, mixed):
    m = maturity(strong, mixed, measured_all(strong, mixed))
    assert m.level == HARDWARE_READY and m.gaps == ()
    assert not gate('real', m).open                  # F4b still gates real, as before K6a


@pytest.mark.parametrize('joint', SERVO_JOINTS)
@pytest.mark.parametrize('path', SERVO_PATHS)
@pytest.mark.parametrize('lower', ['estimated', 'placeholder', None])
def test_dropping_a_servo_tag_lowers_the_maturity(strong, mixed, joint, path, lower):
    full = f'axes.{joint}.{path}'
    assert maturity(strong, mixed, measured_all(strong, mixed)).level == HARDWARE_READY
    m = maturity(strong, mixed, measured_all(strong, mixed, **{full: lower}))
    assert m.level == SIM_READY and m.missing_fields == (full,)
    assert gate('real', m).missing == (full,) and not gate('real', m).open


def test_changing_a_pinned_servo_value_makes_the_tag_stale(strong, mixed):
    q = measured_all(strong, mixed)
    slower = replace(mixed.axis('joint_2'), slew_limit=3.0)
    hw2 = replace(mixed, axes=tuple(slower if a.joint == 'joint_2' else a for a in mixed.axes))
    m = maturity(strong, hw2, q)
    assert m.level == SIM_READY
    assert {g.field: g.status for g in m.gaps} == {'axes.joint_2.servo.slew_limit': 'stale'}


def test_quality_file_may_tag_servo_fields_and_rejects_typos(strong):
    ok = {'quality_version': 1, 'robot': strong.robot,
          'fields': {'axes.joint_2.servo.slew_limit': 'estimated',
                     'axes.joint_2.servo.pulse_range': {'tag': 'measured', 'value': [500]},
                     'axes.joint_2.servo.rest_angle': None,
                     'axes.joint_2.reference.pose': 'placeholder'}}
    q = parse_quality(ok, strong)
    assert q.tag('axes.joint_2.servo.slew_limit') == 'estimated'
    assert q.tag('axes.joint_2.servo.rest_angle') is None
    for typo in ('axes.joint_2.servo.slew', 'axes.joint_2.servo.rest', 'axes.joint_2.reference'):
        data = copy.deepcopy(ok)
        data['fields'] = {typo: 'measured'}
        with pytest.raises(Exception, match='bilinmeyen alan yolu'):
            parse_quality(data, strong)
