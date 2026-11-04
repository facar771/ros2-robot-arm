"""Hardware profile: validation, joint<->motor conversion, firmware header, Hub read function."""
import copy
import math
import os
import shutil
import subprocess
import sys

import pytest
import yaml
from robot_core.hardware import (
    OPEN_LOOP_WARNING,
    HardwareError,
    describe_hardware,
    load_hardware,
    parse_hardware,
    soft_limits,
)
from robot_core.hardware_gen import HARDWARE_PATH, HEADER_REL, generate, main
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.profile_gen import generate as generate_robot

from tests.conftest import SRC

REL = 1e-9


@pytest.fixture(scope='module')
def robot():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def raw():
    return yaml.safe_load(HARDWARE_PATH.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def hw(robot):
    return load_hardware(HARDWARE_PATH, robot)


def bad(raw, robot, mutate, match):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(HardwareError, match=match):
        parse_hardware(data, robot)


def good(raw, robot, mutate):
    data = copy.deepcopy(raw)
    mutate(data)
    return parse_hardware(data, robot)


def axis_edit(joint, **changes):
    def mutate(d):
        d['axes'][joint].update(changes)
    return mutate


# ---- today's placeholder hardware -------------------------------------------------------------
def test_today_loads(hw, robot):
    assert hw.joint_names == robot.joint_names
    a = hw.axis('joint_2')
    assert (a.motor_type, a.encoder) == ('step', 'none')
    assert (a.steps_per_motor_rev, a.direction) == (3200, 1)
    assert a.open_loop and a.steps_per_joint_rad == pytest.approx(3200 / (2 * math.pi))
    assert hw.open_loop_axes == robot.joint_names


def test_hardware_profile_is_not_part_of_the_robot_profile(raw):
    robot_yaml = yaml.safe_load(PROFILE_PATH.read_text(encoding='utf-8'))
    assert 'motors' not in robot_yaml and 'axes' not in robot_yaml
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            keys.update(node)
            for v in node.values():
                walk(v)
    walk(robot_yaml)
    assert not keys & {'direction', 'gear_ratio', 'steps_per_rev', 'microstep', 'encoder', 'homing'}


# ---- conversion -------------------------------------------------------------------------------
def test_formula_matches_the_documented_one(raw, robot):
    hw = good(raw, robot, lambda d: d['axes']['joint_2'].update(
        direction=-1, gear_ratio=10.0, zero_offset=0.1,
        limits={'position': [-3.2, 3.2], 'velocity': 20.0, 'acceleration': 50.0}))
    a = hw.axis('joint_2')
    q = math.pi / 5
    # motor = direction * (joint - offset) * gear * steps_per_rad    (steps_per_rad = spr*ms / 2pi)
    expected = -1 * (q - 0.1) * 10.0 * (200 * 16 / (2 * math.pi))
    assert a.to_steps(q) == pytest.approx(expected, rel=REL)
    assert a.to_steps_int(q) == round(expected)
    assert a.to_motor(0.6) == pytest.approx(-10.0 * 0.5)


@pytest.mark.parametrize('direction', [1, -1])
@pytest.mark.parametrize('gear, offset, ms', [(1.0, 0.0, 16), (5.18, 0.3, 8), (100.0, -1.2, 1)])
def test_round_trip_joint_motor_joint(raw, robot, direction, gear, offset, ms):
    def mutate(d):
        for j in d['axes'].values():
            j.update(direction=direction, gear_ratio=gear, zero_offset=offset)
            j['driver']['microstep'] = ms
            j['limits'].update(position=[-4.0, 4.0], velocity=5.0, acceleration=5.0)
    hw = good(raw, robot, mutate)
    for a in hw.axes:
        lsb = 1 / a.steps_per_joint_rad                  # one microstep, in joint radians
        for q in (-1.5, -0.3, 0.0, 0.123456, 0.7, 1.5):
            q = max(min(q, 1.5), -1.5)
            assert a.to_joint(a.to_motor(q)) == pytest.approx(q, abs=1e-12)        # exact path
            assert a.from_steps(a.to_steps(q)) == pytest.approx(q, abs=1e-12)
            assert abs(a.from_steps(a.to_steps_int(q)) - q) <= lsb / 2 + 1e-12      # step rounding
    qs = [0.1, -0.2, 0.3, 0.0, 0.5, -0.5]
    back = hw.steps_to_joints(hw.joints_to_steps(qs))
    assert all(abs(x - y) <= 1 / a.steps_per_joint_rad for x, y, a in zip(back, qs, hw.axes))


def test_vector_helpers_check_length(hw):
    with pytest.raises(ValueError, match='6 değer'):
        hw.joints_to_steps([0.0] * 5)


def test_reversing_a_motor_changes_steps_but_never_the_simulation(raw, robot, hw):
    """direction is a bridge/firmware matter: the same joint targets, mirrored motor steps, the same
    generated robot files, and a joint read back from the (mirrored) counter is the same joint."""
    flipped = good(raw, robot, lambda d: [d['axes'][j].update(direction=-1) for j in d['axes']])
    qs = [0.3, -0.4, 0.5, 1.0, -0.2, 2.0]
    assert flipped.joints_to_steps(qs) == [-s for s in hw.joints_to_steps(qs)]
    assert flipped.steps_to_joints(flipped.joints_to_steps(qs)) == pytest.approx(
        hw.steps_to_joints(hw.joints_to_steps(qs)), abs=1 / 509)
    for a, b in zip(hw.axes, flipped.axes):
        assert soft_limits(b, robot) == (-soft_limits(a, robot)[1], -soft_limits(a, robot)[0])
        assert b.homing.direction == a.homing.direction          # joint-space meaning is unchanged
    # the simulation side cannot even see it: nothing the robot generator reads has direction
    generated = generate_robot(robot)
    assert generated == generate_robot(load_profile(PROFILE_PATH))
    blob = '\n'.join(generated.values())
    for word in ('direction', 'gear_ratio', 'steps_per_rev', 'microstep', 'zero_offset', 'homing'):
        assert word not in blob
    # ... and the header the firmware gets differs only by the mirrored step values
    assert generate(flipped, robot) != generate(hw, robot)


def test_reversing_the_hardware_file_leaves_generated_robot_files_untouched(tmp_path):
    """End to end on disk: flip every direction in a copy of the profile folder."""
    copy_dir = tmp_path / 'profile'
    copy_dir.mkdir()
    (copy_dir / 'six_axis_robot.yaml').write_bytes(PROFILE_PATH.read_bytes())
    text = HARDWARE_PATH.read_text(encoding='utf-8').replace('direction: 1 ', 'direction: -1')
    (copy_dir / 'hardware.profile.yaml').write_text(text, encoding='utf-8')
    hw = load_hardware(copy_dir / 'hardware.profile.yaml')
    assert all(a.direction == -1 for a in hw.axes)
    assert generate_robot(load_profile(copy_dir / 'six_axis_robot.yaml')) == generate_robot(
        load_profile(PROFILE_PATH))


# ---- validation -------------------------------------------------------------------------------
@pytest.mark.parametrize('mutate, match', [
    (axis_edit('joint_3', direction=2), r'\+1 ya da -1'),
    (axis_edit('joint_3', direction=0), r'\+1 ya da -1'),
    (axis_edit('joint_3', direction='cw'), r'\+1 ya da -1'),
    (axis_edit('joint_3', direction=True), r'\+1 ya da -1'),
    (axis_edit('joint_3', gear_ratio=0), 'pozitif'),
    (axis_edit('joint_3', gear_ratio=-2.0), 'pozitif'),
    (axis_edit('joint_3', encoder='optical'), 'none | incremental | absolute'),
    (axis_edit('joint_3', zero_offset=90), 'üst sınır'),
    (axis_edit('joint_3', unknown=1), 'bilinmeyen anahtar'),
    (lambda d: d['axes']['joint_3'].pop('limits'), 'eksik anahtar'),
    (lambda d: d['axes']['joint_3'].pop('homing'), 'eksik anahtar'),
    (lambda d: d['axes']['joint_3']['driver'].update(microstep=0), 'aralık'),
    (lambda d: d['axes']['joint_3']['driver'].update(microstep=512), 'aralık'),
    (lambda d: d['axes']['joint_3']['driver'].update(microstep=1.5), 'tam sayı'),
    (lambda d: d['axes']['joint_3']['motor'].update(steps_per_rev=0), 'aralık'),
    (lambda d: d['axes']['joint_3']['motor'].update(type='dc'), "yalnızca 'step'"),
    (lambda d: d.update(hardware_version=2), 'hardware_version'),
    (lambda d: d.update(robot='other_robot'), 'other_robot'),
    (lambda d: d.update(extra=1), 'bilinmeyen anahtar'),
])
def test_validation(raw, robot, mutate, match):
    bad(raw, robot, mutate, match)


def test_axes_must_match_the_joints(raw, robot):
    bad(raw, robot, lambda d: d['axes'].pop('joint_6'), 'axes: robot profilindeki eklemlerle')
    bad(raw, robot, lambda d: d.update(axes=dict(reversed(list(d['axes'].items())))), 'aynı adlar')


def test_encoder_is_optional_and_defaults_to_none(raw, robot):
    def drop(d):
        d['axes']['joint_1'].pop('encoder')
    assert good(raw, robot, drop).axis('joint_1').encoder == 'none'


def test_joint_limits_must_lie_inside_motor_limits_minus_margin(raw, robot):
    def limits(joint, pos, margin=None):
        def mutate(d):
            d['axes'][joint]['limits']['position'] = pos
            if margin is not None:
                d['axes'][joint]['margin'] = {'position': margin, 'velocity': 0.0}
        return mutate
    bad(raw, robot, limits('joint_2', [-1.5, 1.57]), r'motor limiti .* içinde değil')
    bad(raw, robot, limits('joint_2', [-1.57, 1.5]), r'motor limiti .* içinde değil')
    bad(raw, robot, axis_edit('joint_2', margin={'position': 0.01, 'velocity': 0.0}), r'marj 0\.01')
    good(raw, robot, limits('joint_2', [-1.7, 1.7], 0.1))
    bad(raw, robot, limits('joint_2', [-1.7, 1.7], 0.2), r'motor limiti .* içinde değil')


def test_omitted_margin_is_the_narrow_default(raw, robot):
    def widen(d):
        a = d['axes']['joint_2']
        a.pop('margin')
        a['limits'].update(position=[-1.62, 1.62], velocity=1.05)          # exactly 0.05 slack
    a = good(raw, robot, widen).axis('joint_2')
    assert (a.margin_position, a.margin_velocity) == (0.05, 0.05)

    def too_tight(d):
        d['axes']['joint_2'].pop('margin')
        d['axes']['joint_2']['limits']['position'] = [-1.6, 1.6]
    bad(raw, robot, too_tight, 'marj 0.05')


def test_joint_speed_and_acceleration_must_lie_inside_the_motor(raw, robot):
    def lim(**kw):
        return lambda d: d['axes']['joint_1']['limits'].update(**kw)
    bad(raw, robot, lim(velocity=0.9), 'hız limiti')
    bad(raw, robot, axis_edit('joint_1', margin={'position': 0.0, 'velocity': 0.1}), 'hız limiti')
    bad(raw, robot, lim(acceleration=0.9), 'ivme limiti')
    good(raw, robot, lim(velocity=1.2, acceleration=1.2))


# ---- homing and open loop ---------------------------------------------------------------------
def homing(**changes):
    def mutate(d):
        d['axes']['joint_2']['homing'] = {**d['axes']['joint_2']['homing'], **changes}
    return mutate


def test_open_loop_axis_needs_a_home_switch(raw, robot):
    bad(raw, robot, homing(method='absolute'), r'encoder: none ile olmaz.*ŞART')
    bad(raw, robot, homing(method='index'), r'encoder: none ile olmaz')
    good(raw, robot, homing(method='switch'))


def test_homing_methods_follow_the_encoder(raw, robot):
    both = lambda enc, **h: (lambda d: (d['axes']['joint_2'].update(encoder=enc), homing(**h)(d)))  # noqa: E731
    good(raw, robot, both('incremental', method='index'))
    good(raw, robot, both('incremental', method='switch'))
    bad(raw, robot, both('incremental', method='absolute'), 'artımlı encoder')

    def absolute(d):
        d['axes']['joint_2'].update(encoder='absolute')
        d['axes']['joint_2']['homing'] = {'method': 'absolute', 'position': 0.0}
    a = good(raw, robot, absolute).axis('joint_2')
    assert a.homing.method == 'absolute' and a.homing.direction == 0 and not a.open_loop


def test_homing_values(raw, robot):
    bad(raw, robot, homing(direction=0), r'\+1 ya da -1')
    bad(raw, robot, lambda d: d['axes']['joint_2']['homing'].pop('direction'), 'direction')
    bad(raw, robot, homing(position=2.0), 'eklem limiti .* dışında')
    bad(raw, robot, homing(speed=2.0), 'hızını')
    bad(raw, robot, homing(speed=0), 'pozitif')
    bad(raw, robot, homing(backoff=-0.1), 'alt sınır')
    bad(raw, robot, homing(method='magnet'), 'switch | index | absolute')

    def no_speed(d):
        d['axes']['joint_2']['homing'].pop('speed')
        d['axes']['joint_2']['homing'].pop('backoff')
    h = good(raw, robot, no_speed).axis('joint_2').homing
    assert (h.speed, h.backoff) == (0.1, 0.05)                    # the narrow defaults


def test_step_ranges_must_fit_the_wire_format(raw, robot):
    def huge(d):
        d['axes']['joint_1']['gear_ratio'] = 10000.0
        d['axes']['joint_1']['motor']['steps_per_rev'] = 100000
    bad(raw, robot, huge, 'int32')
    # a speed that rounds to less than one step per second cannot be produced
    bad(raw, robot, lambda d: d['axes']['joint_1']['homing'].update(speed=1e-4), r'homing hızı 0')


def test_load_hardware_names_the_file_and_the_problem(tmp_path, robot):
    path = tmp_path / 'hardware.profile.yaml'
    path.write_text('hardware_version: 1\n', encoding='utf-8')
    with pytest.raises(HardwareError, match=r'hardware\.profile\.yaml.*eksik anahtar'):
        load_hardware(path, robot)
    with pytest.raises(HardwareError, match='okunamadı'):
        load_hardware(tmp_path / 'missing.yaml', robot)
    path.write_text('a: [1', encoding='utf-8')
    with pytest.raises(HardwareError, match='geçerli YAML değil'):
        load_hardware(path, robot)


# ---- warning ----------------------------------------------------------------------------------
def test_open_loop_warning_is_explicit(hw, raw, robot):
    assert 'adım kaybı ALGILANAMAZ' in OPEN_LOOP_WARNING and 'homing' in OPEN_LOOP_WARNING
    assert hw.warnings and all(j in hw.warnings[0] for j in robot.joint_names)

    def closed(d):
        for a in d['axes'].values():
            a['encoder'] = 'incremental'
    cl = good(raw, robot, closed)
    assert cl.warnings == () and cl.open_loop_axes == ()
    text = generate(cl, robot)
    assert 'ROBOT_OPEN_LOOP_ANY 0' in text and 'AÇIK DÖNGÜ' not in text.replace('(AÇIK DÖNGÜ)', '')


# ---- firmware header --------------------------------------------------------------------------
def test_header_is_up_to_date(hw, robot):
    """Fails when hardware.profile.yaml / the robot profile / the generator changed without
    regenerating. Fix: cd src/robot_core && python3 -m robot_core.hardware_gen"""
    assert (SRC / HEADER_REL).read_text(encoding='utf-8') == generate(hw, robot)


def test_header_check_cli_detects_stale_and_writes(tmp_path):
    (tmp_path / 'robot_description' / 'profile' / 'generated').mkdir(parents=True)
    args = ['--src', str(tmp_path)]
    assert main(['--check', *args]) == 1                         # missing = stale
    assert main(args) == 0 and main(['--check', *args]) == 0
    stale = tmp_path / HEADER_REL
    stale.write_text(stale.read_text(encoding='utf-8') + '/* edited */\n', encoding='utf-8')
    assert main(['--check', *args]) == 1
    assert main(args) == 0 and 'edited' not in stale.read_text(encoding='utf-8')


def test_header_says_generated_and_open_loop(hw, robot):
    text = generate(hw, robot)
    assert text.startswith('/* ÜRETİLDİ') and 'ELLE DÜZENLEME' in text.splitlines()[0]
    assert '!!! AÇIK DÖNGÜ UYARISI !!!' in text and 'ROBOT_OPEN_LOOP_ANY 1' in text
    assert 'ROBOT_OPEN_LOOP_MASK 0x3Fu' in text and 'adım kaybı ALGILANAMAZ' in text
    assert text == generate(hw, robot)                                           # deterministic


def test_header_limits_are_the_urdf_limits_in_steps_rounded_inward(raw, robot):
    def mutate(d):
        d['axes']['joint_2'].update(direction=-1, gear_ratio=3.0, zero_offset=0.1,
                                    limits={'position': [-9.0, 9.0], 'velocity': 9.0,
                                            'acceleration': 9.0})
    hw = good(raw, robot, mutate)
    a = hw.axis('joint_2')
    lo, hi = soft_limits(a, robot)
    joint = next(j for j in robot.joints if j.name == 'joint_2')
    exact = sorted([a.to_steps(joint.lower), a.to_steps(joint.upper)])
    assert lo >= exact[0] and hi <= exact[1] and lo - exact[0] < 1 and exact[1] - hi < 1
    assert lo < 0 < hi and f'{lo}, {hi},' in generate(hw, robot)


def test_header_encoder_and_homing_codes_match_the_wire_protocol():
    sys.path.insert(0, str(SRC / 'robot_hw_bridge'))
    try:
        from robot_hw_bridge.protocol import MAX_AXES, EncoderMode
    finally:
        sys.path.remove(str(SRC / 'robot_hw_bridge'))
    from robot_core.hardware import ENCODERS
    assert [EncoderMode[e.upper()].value for e in ENCODERS] == [0, 1, 2]
    assert len(load_hardware(HARDWARE_PATH).axes) <= MAX_AXES


@pytest.mark.skipif(shutil.which('cc') is None and shutil.which('gcc') is None,
                    reason='no C compiler')
def test_header_compiles_and_values_read_back(tmp_path, hw, robot):
    src = tmp_path / 't.c'
    src.write_text('#include <stdio.h>\n#include "robot_limits.h"\nint main(void){'
                   'for(int i=0;i<ROBOT_AXIS_COUNT;i++)printf("%d %d %u\\n",'
                   '(int)ROBOT_AXES[i].soft_min,(int)ROBOT_AXES[i].soft_max,'
                   '(unsigned)ROBOT_AXES[i].max_speed);'
                   'puts(ROBOT_OPEN_LOOP_WARNING);return 0;}\n', encoding='utf-8')
    cc = shutil.which('cc') or shutil.which('gcc')
    out = tmp_path / 't'
    include = (SRC / HEADER_REL).parent
    subprocess.run([cc, '-std=c99', '-Wall', '-Wextra', '-Werror', f'-I{include}', str(src), '-o',
                    str(out)], check=True)
    run = subprocess.run([str(out)], check=True, capture_output=True, text=True)
    lines = run.stdout.splitlines()
    for line, a in zip(lines, hw.axes):
        lo, hi = soft_limits(a, robot)
        assert line.split()[:2] == [str(lo), str(hi)]
    assert 'ALGILANAMAZ' in lines[-1]


# ---- Hub: read-only, ROS-free view ------------------------------------------------------------
def test_describe_hardware(hw):
    info = describe_hardware()
    assert info['ok'] and info['robot'] == 'six_axis_robot' and info['open_loop']
    assert info['warnings'] and 'ALGILANAMAZ' in info['warnings'][0]
    assert [a['joint'] for a in info['axes']] == list(hw.joint_names)
    j2 = info['axes'][1]
    assert (j2['loop'], j2['encoder']) == ('open', 'none')
    assert j2['soft_limit_steps'] == [-799, 799]
    assert j2['homing']['method'] == 'switch'
    import json
    assert json.loads(json.dumps(info)) == info                   # plain JSON types only


def test_describe_hardware_never_raises_and_never_writes(tmp_path):
    before = sorted(p.name for p in HARDWARE_PATH.parent.iterdir())
    assert describe_hardware(tmp_path / 'nope.yaml')['ok'] is False
    broken = tmp_path / 'hardware.profile.yaml'
    broken.write_text('robot: [1', encoding='utf-8')
    assert describe_hardware(broken)['ok'] is False
    broken.write_text('- a\n- b\n', encoding='utf-8')
    assert describe_hardware(broken)['ok'] is False
    broken.write_text('robot: six_axis_robot\nhardware_version: 1\naxes: {}\n', encoding='utf-8')
    info = describe_hardware(broken)                              # no robot yaml beside it
    assert info['ok'] is False and 'error' in info
    assert sorted(p.name for p in HARDWARE_PATH.parent.iterdir()) == before


def test_hardware_module_is_ros_free():
    code = ('import sys; import robot_core.hardware, robot_core.hardware_gen; '
            "bad=[m for m in sys.modules if m.split('.')[0] in ('rclpy','moveit','rospy')]; "
            'sys.exit(1 if bad else 0)')
    env = {**os.environ, 'PYTHONPATH': str(SRC / 'robot_core')}
    assert subprocess.run([sys.executable, '-c', code], env=env).returncode == 0
