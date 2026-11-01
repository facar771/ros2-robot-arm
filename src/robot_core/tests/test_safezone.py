"""Safe working box + speed cap: parsing, checking, and the Skills refusal (robot stays put)."""
import math
from pathlib import Path

import pytest
from robot_core.models import ObjectInfo
from robot_core.safezone import (
    SafetyConfigError,
    SafeZone,
    effective_velocity_scale,
    load_safety,
    parse_safety,
)
from robot_core.skills import Skills

from tests.conftest import CONFIG_DIR
from tests.fakes import FakeMotion, FakeObjects, FakePose, RecordingSink

GOOD = {'frame': 'world', 'box': {'x': [0.05, 0.75], 'y': [-0.5, 0.5], 'z': [0.02, 0.7]},
        'max_velocity_scale': 0.5}


def test_shipped_yaml_loads_and_contains_every_world_object():
    cfg = load_safety(CONFIG_DIR / 'safe_zone.yaml')
    assert cfg.max_velocity_scale == 0.5 and cfg.zone.frame == 'world'
    world = Path(__file__).resolve().parents[2] / 'robot_sim' / 'worlds' / 'workspace.sdf'
    from robot_core.workspace import load_workspace
    for shape in load_workspace(str(world)).shapes:
        for height in (0.0, 0.5):          # every clearance the API accepts
            top = shape.center[2] + shape.height / 2 + height
            assert cfg.zone.check((shape.center[0], shape.center[1], top)) is None, (shape.name,
                                                                                    height)


def test_check_inside_edges_and_outside():
    zone = parse_safety(GOOD).zone
    assert zone.check((0.4, 0.0, 0.3)) is None
    assert zone.check((0.05, -0.5, 0.02)) is None and zone.check((0.75, 0.5, 0.7)) is None
    for point, axis in (((0.04, 0, 0.3), 'x='), ((0.4, 0.6, 0.3), 'y='), ((0.4, 0, 0.01), 'z='),
                        ((0.4, 0, 0.71), 'z=')):
        reason = zone.check(point)
        assert reason and axis in reason and 'robot hareket etmedi' in reason
    assert zone.check((0.4, 0.0, math.nan)) is not None and zone.check((math.inf, 0, 0.3))
    both = zone.check((9.0, 9.0, 9.0))
    assert both.count(';') == 2             # every offending axis is named


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d.pop('box'), 'box'),
    (lambda d: d['box'].pop('z'), 'x, y, z'),
    (lambda d: d['box'].update(z=[0.7, 0.02]), 'alt sınır'),
    (lambda d: d['box'].update(z=[0.1]), 'alt, üst'),
    (lambda d: d['box'].update(x=['a', 1]), 'sayı'),
    (lambda d: d['box'].update(x=[float('nan'), 1]), 'sayı'),
    (lambda d: d['box'].update(x=[True, 1]), 'sayı'),
    (lambda d: d.update(max_velocity_scale=0), 'max_velocity_scale'),
    (lambda d: d.update(max_velocity_scale=1.5), 'max_velocity_scale'),
    (lambda d: d.update(frame=''), 'frame'),
    (lambda d: d.update(speed=1), 'bilinmeyen'),
])
def test_bad_configs_are_rejected_with_a_reason(mutate, message):
    import copy
    data = copy.deepcopy(GOOD)
    mutate(data)
    with pytest.raises(SafetyConfigError, match=message):
        parse_safety(data)


def test_root_must_be_a_mapping_and_files_must_exist(tmp_path):
    with pytest.raises(SafetyConfigError):
        parse_safety([1, 2])
    with pytest.raises(SafetyConfigError, match='okunamadı'):
        load_safety(tmp_path / 'yok.yaml')
    broken = tmp_path / 'b.yaml'
    broken.write_text('box: [unclosed', encoding='utf-8')
    with pytest.raises(SafetyConfigError, match='YAML'):
        load_safety(broken)
    bad = tmp_path / 'c.yaml'
    bad.write_text('box: {x: [1, 0], y: [0, 1], z: [0, 1]}', encoding='utf-8')
    with pytest.raises(SafetyConfigError, match='c.yaml'):
        load_safety(bad)


def test_velocity_scale_is_capped():
    assert effective_velocity_scale(0.8, 0.5) == 0.5
    assert effective_velocity_scale(0.3, 0.5) == 0.3
    assert effective_velocity_scale(0.8, None) == 0.8
    assert effective_velocity_scale(7.0, None) == 1.0
    for bad in (0, -1, math.nan, math.inf):
        with pytest.raises(ValueError):
            effective_velocity_scale(bad, 0.5)


# ---- Skills: the refusal is explicit and nothing reaches the motion layer ------------------------
RED = ObjectInfo('obj_red_1', 'red', (0.35, 0.15, 0.03), (0.06, 0.06, 0.06))


def make_skills(config, zone):
    pose, motion, sink = FakePose(), FakeMotion(), RecordingSink()
    skills = Skills(config, motion, pose, FakeObjects({RED.name: RED}), sink, zone=zone)
    return skills, pose, motion, sink


def test_go_to_position_outside_the_box_is_refused_and_does_not_move(config):
    skills, pose, motion, sink = make_skills(config, SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.7)))
    result = skills.go_to_position(0.9, 0.0, 0.3)
    assert not result.ok and result.code_name == 'SAFE_ZONE'
    assert 'güvenli çalışma kutusunun dışında' in result.message and 'x=0.900' in result.message
    assert pose.targets == [] and motion.targets == []
    kind, payload = sink.events[-1]
    assert kind == 'skill_result' and payload['ok'] is False


def test_go_above_outside_the_box_is_refused_without_the_moveit_hint(config):
    skills, pose, _, _ = make_skills(config, SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.2)))
    result = skills.go_above('red', 0.35)           # target z = 0.06 + 0.35 = 0.41 > 0.2
    assert not result.ok and result.code_name == 'SAFE_ZONE' and pose.targets == []
    assert 'z=0.410' in result.message and 'dene' not in result.message
    assert result.data['target'][2] == pytest.approx(0.41)


def test_inside_the_box_nothing_changes_and_no_zone_means_no_check(config):
    inside = SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.7))
    skills, pose, _, _ = make_skills(config, inside)
    assert skills.go_above('red', 0.35).ok and len(pose.targets) == 1
    open_skills, open_pose, _, _ = make_skills(config, None)
    assert open_skills.go_to_position(0.9, 0.0, 0.3).ok and len(open_pose.targets) == 1


def test_named_poses_are_not_checked(config):
    skills, _, motion, _ = make_skills(config, SafeZone((10, 10, 10), (11, 11, 11)))
    assert skills.go_to_named_pose('home').ok and len(motion.targets) == 1


@pytest.mark.parametrize('code, hint', [('FAILURE', True), ('NO_IK_SOLUTION', True),
                                        ('PREEMPTED', False), ('TIMED_OUT', False)])
def test_height_hint_only_for_reachability_failures(config, code, hint):
    from robot_core.models import MotionResult
    pose = FakePose(MotionResult(False, code=-1, code_name=code, message='hareket başarısız'))
    skills = Skills(config, FakeMotion(), pose, FakeObjects({RED.name: RED}))
    message = skills.go_above('red', 0.25).message
    assert ('0.35 dene' in message) is hint
