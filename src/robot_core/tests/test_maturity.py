"""J5: quality tags, maturity levels, real gate (no ROS)."""
import copy
from dataclasses import replace

import pytest
import yaml
from robot_core.hardware import HardwareError, load_hardware, parse_hardware
from robot_core.hardware_gen import HARDWARE_PATH
from robot_core.maturity import (
    DRAFT,
    HARDWARE_READY,
    SIM_READY,
    Quality,
    gate,
    known_paths,
    load_maturity,
    load_quality,
    maturity,
    parse_quality,
)
from robot_core.profile import ProfileError, load_profile
from robot_core.profile_gen import PROFILE_PATH

from robot_core import maturity as mat

QUALITY_PATH = PROFILE_PATH.with_name('quality.profile.yaml')


@pytest.fixture(scope='module')
def robot():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def hw(robot):
    return load_hardware(HARDWARE_PATH, robot)


def all_measured(robot, hw, **tags):
    """A quality map where every critical field is measured (pinned to today's value)."""
    q = Quality()
    for path in mat._critical_paths(robot, hw):
        q = q.with_tag(path, 'measured', pin=mat.field_value(robot, hw, path))
    for path, tag in tags.items():
        q = q.with_tag(path, tag)
    return q


@pytest.fixture
def strong(robot):
    """Same robot with torque ratings high enough for the static check (real-ready subject)."""
    return replace(robot, joints=tuple(replace(j, effort=1000.0) for j in robot.joints))


# ---- backward compatibility -------------------------------------------------------------------
def test_existing_profile_is_sim_ready_and_all_placeholder(robot, hw):
    m = maturity(robot, hw)
    assert m.level == SIM_READY and m.name == 'simülasyona hazır'
    assert m.gaps and all(g.status in ('placeholder', 'başarısız') for g in m.gaps)
    assert {'links.link_2.length', 'axes.joint_1.gear_ratio'} <= set(m.missing_fields)
    assert load_maturity(PROFILE_PATH, HARDWARE_PATH, QUALITY_PATH).level == SIM_READY


def test_shipped_quality_file_marks_everything_and_claims_nothing_measured(robot, hw):
    q = load_quality(QUALITY_PATH, robot)
    assert q.tags and set(q.tags.values()) == {'placeholder', 'estimated'}   # never measured
    assert all(q.tag(p) != 'measured' for p in known_paths(robot))
    assert q.tag('links.link_2.mass') == 'placeholder' and q.tag('axes.joint_1.gear_ratio') == \
        'placeholder' and q.tag('links.link_2.length') == 'estimated'
    assert maturity(robot, hw, q).level == SIM_READY
    assert load_quality(None, robot) == Quality()
    assert load_quality(QUALITY_PATH.with_name('yok.yaml'), robot) == Quality()


# ---- gate: incomplete opens in simulation, real rejects ---------------------------------------
def test_incomplete_profile_opens_in_sim_and_real_names_missing_fields(robot, hw):
    m = maturity(robot, hw)
    assert gate('sim', m).open
    g = gate('real', m)
    assert not g.open and 'donanıma hazır değil' in g.reasons[0]
    assert 'links.link_1.length' in g.missing and len(g.missing) == len(m.gaps)


def test_without_hardware_profile_real_is_closed_and_says_so(robot):
    m = maturity(robot, None)
    assert m.level == SIM_READY and gate('sim', m).open
    assert 'donanım profili' in m.missing_fields and not gate('real', m).open


def test_real_stays_closed_until_f4b_even_when_hardware_ready(strong, hw, monkeypatch):
    m = maturity(strong, hw, all_measured(strong, hw))
    assert m.level == HARDWARE_READY and m.gaps == ()
    g = gate('real', m)
    assert not g.open and any('F4b' in r for r in g.reasons)       # F4B_DONE is False today
    monkeypatch.setattr(mat, 'F4B_DONE', True)
    assert gate('real', m).open
    assert not gate('real', maturity(strong, hw)).open             # F4b alone never opens it


def test_unknown_mode_is_an_error(robot, hw):
    with pytest.raises(ValueError):
        gate('hardware', maturity(robot, hw))


# ---- tags: downgrade, null, pin, parsing ------------------------------------------------------
def test_tag_downgrade_lowers_maturity(strong, hw):
    q = all_measured(strong, hw)
    assert maturity(strong, hw, q).level == HARDWARE_READY
    low = q.with_tag('axes.joint_3.gear_ratio', 'estimated')
    m = maturity(strong, hw, low)
    assert m.level == SIM_READY and m.missing_fields == ('axes.joint_3.gear_ratio',)
    assert m.gaps[0].status == 'estimated'
    assert maturity(strong, hw, low.with_tag('axes.joint_3.gear_ratio', 'measured')).level == 3


def test_null_means_unknown_and_counts_as_missing(strong, hw):
    m = maturity(strong, hw, all_measured(strong, hw).with_tag('links.link_2.length', None))
    assert m.level == SIM_READY and m.gaps[0].status == 'unknown'


def test_pinned_value_that_changed_is_stale_not_measured(robot, hw):
    q = all_measured(robot, hw)
    longer = replace(robot, links=tuple(
        replace(link, length=link.length + 0.01) if link.name == 'link_2' else link
        for link in robot.links))
    stale = [g for g in maturity(longer, hw, q).gaps if g.status == 'stale']
    assert [g.field for g in stale] == ['links.link_2.length']


def test_parse_quality_rejects_typos_and_bad_tags(robot):
    base = {'quality_version': 1, 'robot': 'six_axis_robot'}
    with pytest.raises(ProfileError, match='bilinmeyen alan yolu'):
        parse_quality({**base, 'fields': {'links.link_2.lenght': 'measured'}}, robot)
    with pytest.raises(ProfileError, match='measured'):
        parse_quality({**base, 'fields': {'links.link_2.length': 'measuered'}}, robot)
    with pytest.raises(ProfileError, match='bilinmeyen'):
        parse_quality({**base, 'fields': {}, 'extra': 1}, robot)
    with pytest.raises(ProfileError, match='robot profili'):
        parse_quality({**base, 'robot': 'other', 'fields': {}}, robot)
    pinned = {'tag': 'measured', 'value': 0.35}
    ok = parse_quality({**base, 'fields': {'links.link_2.length': pinned,
                                           'axes.joint_1.gear_ratio': None}}, robot)
    assert ok.tag('links.link_2.length') == 'measured'
    assert ok.tag('axes.joint_1.gear_ratio') is None
    assert ok.tag('links.link_3.length') == 'placeholder'          # untagged
    assert all(p in known_paths(robot) for p in ok.tags)


# ---- torque check, servo, draft ---------------------------------------------------------------
def test_torque_check_fails_hardware_readiness_even_if_all_measured(robot, hw):
    weak = replace(robot, joints=tuple(replace(j, effort=0.5) for j in robot.joints))
    m = maturity(weak, hw, all_measured(weak, hw))
    assert m.level == SIM_READY and 'tork denetimi' in m.missing_fields
    assert mat.static_torques(robot)['joint_2'] > mat.static_torques(robot)['joint_6'] > 0


def test_hobby_servo_axis_needs_pulse_range_not_steps(strong, hw):
    servo_axis = replace(hw.axes[0], motor_type='hobby_servo')
    hw2 = replace(hw, axes=(servo_axis,) + hw.axes[1:])
    paths = mat._critical_paths(strong, hw2)
    assert 'axes.joint_1.servo.pulse_range' in paths and 'axes.joint_1.gear_ratio' not in paths
    m = maturity(strong, hw2, all_measured(strong, hw2))
    assert m.missing_fields == ('axes.joint_1.servo.pulse_range',)   # no value stored yet: eksik


def test_pose_outside_limits_is_draft_and_closed_everywhere(robot, hw):
    bad = replace(robot, initial_positions={**robot.initial_positions, 'joint_2': 3.0})
    m = maturity(bad, hw)
    assert m.level == DRAFT and m.name == 'taslak' and 'joint_2' in m.blockers[0]
    assert not gate('sim', m).open and not gate('real', m).open


def test_missing_home_pose_is_draft(robot, hw):
    poses = {k: v for k, v in robot.named_poses.items() if k != 'home'}
    assert maturity(replace(robot, named_poses=poses), hw).level == DRAFT


def test_unparseable_hardware_is_a_gap_not_a_sim_blocker(tmp_path):
    raw = yaml.safe_load(HARDWARE_PATH.read_text(encoding='utf-8'))
    raw = copy.deepcopy(raw)
    raw['axes']['joint_1']['direction'] = 2
    bad = tmp_path / 'hardware.profile.yaml'
    bad.write_text(yaml.safe_dump(raw), encoding='utf-8')
    robot_copy = tmp_path / 'six_axis_robot.yaml'
    robot_copy.write_text(PROFILE_PATH.read_text(encoding='utf-8'), encoding='utf-8')
    m = load_maturity(robot_copy, bad)
    assert m.level == SIM_READY and m.gaps[0].field == 'donanım profili'
    assert 'direction' in m.gaps[0].detail
    with pytest.raises(HardwareError):
        parse_hardware(raw, load_profile(robot_copy))


def test_schema_invalid_robot_profile_is_not_even_a_draft(tmp_path):
    p = tmp_path / 'bad.yaml'
    p.write_text('profile_version: 3\n', encoding='utf-8')
    with pytest.raises(ProfileError):
        load_maturity(p)
