import math

import pytest
from robot_core.models import MotionResult, ObjectInfo
from robot_core.skills import TOOL_DOWN, Skills

from tests.fakes import FakeMotion, FakeObjects, FakePose, RecordingSink

RED = ObjectInfo('obj_red_1', 'red', (0.35, 0.15, 0.03), (0.07, 0.07, 0.07))
GREEN = ObjectInfo('obj_green_1', 'green', (0.40, -0.10, 0.05), (0.07, 0.07, 0.11))
RED2 = ObjectInfo('obj_red_2', 'red', (0.50, 0.30, 0.03), (0.07, 0.07, 0.07))


def make(objects=None, pose_result=None, config=None, **kw):
    pose, motion, sink = FakePose(pose_result), FakeMotion(), RecordingSink()
    objs = FakeObjects({o.name: o for o in (objects if objects is not None else [RED, GREEN])})
    skills = Skills(config, motion, pose, objs, sink, **kw)
    return skills, pose, motion, sink


@pytest.fixture
def parts(config):
    return make(config=config)


def test_go_above_targets_top_of_object_plus_height_tool_down(parts):
    skills, pose, _, _ = parts
    result = skills.go_above('obj_red_1', 0.10)
    assert result.ok and result.data['object'] == 'obj_red_1'
    (target,) = pose.targets
    assert (target.x, target.y) == (0.35, 0.15)
    assert target.z == pytest.approx(0.03 + 0.035 + 0.10)       # centre + half height + clearance
    assert target.orientation == TOOL_DOWN and target.frame == 'world'
    assert result.data['target'] == pytest.approx([0.35, 0.15, 0.165])


def test_colour_label_resolves_when_unique(parts):
    skills, pose, _, _ = parts
    assert skills.go_above('red').ok and skills.go_above('GREEN').ok
    assert pose.targets[1].x == 0.40


def test_ambiguous_label_is_an_error_that_names_the_candidates(config):
    skills, pose, _, _ = make([RED, RED2], config=config)
    result = skills.go_above('red')
    assert not result.ok and 'obj_red_1' in result.message and 'obj_red_2' in result.message
    assert pose.targets == []
    assert skills.go_above('obj_red_2').ok


def test_unknown_object_lists_what_is_known(parts):
    skills, pose, _, _ = parts
    result = skills.go_above('purple_cow')
    assert not result.ok and 'obj_green_1' in result.message and 'obj_red_1' in result.message
    assert pose.targets == []


@pytest.mark.parametrize('height', [-0.01, 0.6, math.nan, math.inf, 'high', None, True])
def test_bad_height_is_refused_without_moving(parts, height):
    skills, pose, _, _ = parts
    result = skills.go_above('red', height)
    assert not result.ok and 'height' in result.message and pose.targets == []


def test_unreachable_goal_gives_an_explicit_error_and_no_motion(config):
    skills, pose, _, _ = make(config=config)
    result = skills.go_to_position(3.0, 0.0, 0.5)
    assert not result.ok and 'erişim alanı dışında' in result.message
    assert 'robot hareket etmedi' in result.message and pose.targets == []


def test_goal_below_ground_is_refused(parts):
    skills, pose, _, _ = parts
    assert not skills.go_to_position(0.3, 0.0, -0.05).ok and pose.targets == []


@pytest.mark.parametrize('xyz', [(math.nan, 0, 0.2), (0, math.inf, 0.2), ('a', 0, 0.2)])
def test_non_finite_coordinates_are_refused(parts, xyz):
    skills, pose, _, _ = parts
    assert not skills.go_to_position(*xyz).ok and pose.targets == []


def test_motion_failure_carries_code_and_hint(config):
    failed = MotionResult(False, 99999, 'NO_IK_SOLUTION')
    skills, _, _, _ = make(pose_result=failed, config=config)
    result = skills.go_to_position(0.3, 0.0, 0.3)
    assert not result.ok and result.code_name == 'NO_IK_SOLUTION'
    assert 'IK çözümü yok' in result.message and 'Robot hareket etmedi' in result.message


def test_motion_failure_without_code_still_explains(config):
    skills, _, _, _ = make(pose_result=MotionResult(False, message='sunucu bulunamadı'),
                           config=config)
    assert 'sunucu bulunamadı' in skills.go_to_position(0.3, 0.0, 0.3).message


def test_a_raising_port_never_escapes(config):
    skills = Skills(config, FakeMotion(), FakePose(raises=RuntimeError('boom')),
                    FakeObjects({'obj_red_1': RED}))
    assert 'RuntimeError: boom' in skills.go_to_position(0.3, 0.0, 0.3).message
    skills = Skills(config, FakeMotion(), FakePose(), FakeObjects(raises=OSError('scene gone')))
    assert not skills.go_above('red').ok and skills.list_objects() == []


def test_unreadable_scene_is_reported(config):
    skills = Skills(config, FakeMotion(), FakePose(), FakeObjects(None))
    assert 'okunamadı' in skills.go_above('red').message


def test_named_pose_uses_the_joint_motion_port(parts, config):
    skills, pose, motion, _ = parts
    assert skills.go_to_named_pose('HOME').ok
    assert motion.targets[0].values == config.poses['home'] and pose.targets == []
    bad = skills.go_to_named_pose('nowhere')
    assert not bad.ok and 'home' in bad.message


def test_list_objects_is_sorted(parts):
    skills = parts[0]
    assert [o.name for o in skills.list_objects()] == ['obj_green_1', 'obj_red_1']


def test_every_call_emits_a_skill_result_event(parts):
    skills, _, _, sink = parts
    skills.go_above('red', 0.2)
    skills.go_above('nope')
    events = [p for k, p in sink.events if k == 'skill_result']
    assert [e['ok'] for e in events] == [True, False]
    assert events[0]['skill'] == 'go_above' and events[0]['args'] == {'object_name': 'red',
                                                                      'height': 0.2}


def test_catalog_lists_the_skills_with_documentation():
    catalog = {c['name']: c for c in Skills.catalog()}
    assert set(catalog) == {'list_objects', 'go_to_named_pose', 'go_to_position', 'go_above'}
    assert all(len(c['description']) > 40 for c in catalog.values())
    assert 'height' in catalog['go_above']['signature']


def test_every_public_method_is_in_the_catalog():
    public = {n for n in dir(Skills) if not n.startswith('_') and callable(getattr(Skills, n))}
    assert public - {'catalog'} == {c['name'] for c in Skills.catalog()}
