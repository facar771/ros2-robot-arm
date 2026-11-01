import ast
import re

import pytest
from robot_core.scenario import DEFAULT_STEPS, ScenarioStep, run_demo_scenario

from tests.conftest import SRC
from tests.fakes import FakeSkills, FakeTool, RecordingSink

HOME = (0.0, 0.0, 1.24)
GREEN_TARGET = (0.40, -0.10, 0.35)       # top of the fake green cylinder (0.10) + 0.25
RED_TARGET = (0.35, 0.15, 0.36)          # top of the fake red box (0.06) + 0.30


def run(tool_positions, skills=None, **kw):
    lines, slept, sink = [], [], RecordingSink()
    skills = skills or FakeSkills()
    result = run_demo_scenario(skills, FakeTool(*tool_positions), lines.append, events=sink,
                               sleep=slept.append, **kw)
    return result, skills, lines, slept, sink


def good_positions():
    return [HOME, GREEN_TARGET, RED_TARGET, HOME]


def test_all_four_steps_pass_and_are_narrated_in_turkish():
    result, skills, lines, slept, sink = run(good_positions())
    assert result.ok and len(result.steps) == 4 and all(s.ok for s in result.steps)
    assert skills.calls == [('go_to_named_pose', 'home'), ('go_above', 'green', 0.25),
                            ('go_above', 'red', 0.30), ('go_to_named_pose', 'home')]
    assert [line[:5] for line in lines if line.startswith('[')][:4] == [
        '[1/4]', '[2/4]', '[3/4]', '[4/4]']
    text = '\n'.join(lines)
    assert 'Yeşil silindir' in text and 'kırmızı kutu' in text and "'home'" in text
    assert 'doğrulandı' in text and lines[-1].startswith('[+] senaryo tamamlandı')
    assert len(slept) == 4                                   # settled before every measurement
    assert result.steps[1].error_m == pytest.approx(0.0)
    assert result.steps[3].target == HOME                    # the second home is checked against
    kind, payload = sink.events[-1]                          # the first one
    assert kind == 'scenario_result' and payload['ok'] and len(payload['steps']) == 4


def test_report_as_dict_is_json_friendly():
    result, *_ = run(good_positions())
    d = result.steps[1].as_dict()
    assert d == {'step': 2, 'say': DEFAULT_STEPS[1].say, 'ok': True, 'message': 'doğrulandı',
                 'target': [0.4, -0.1, 0.35], 'measured': [0.4, -0.1, 0.35], 'error_cm': 0.0}
    assert result.steps[0].as_dict()['target'] is None       # first home has no target, only a
    assert result.steps[0].as_dict()['measured'] == [0.0, 0.0, 1.24]   # measured reference


def test_tool_too_far_from_target_stops_the_scenario():
    off = (GREEN_TARGET[0] + 0.03, GREEN_TARGET[1], GREEN_TARGET[2])
    result, skills, lines, _, sink = run([HOME, off, RED_TARGET, HOME])
    assert not result.ok and len(result.steps) == 2 and not result.steps[1].ok
    assert '2. adımda' in result.message and '3.0 cm' in result.message
    assert ('go_above', 'red', 0.30) not in skills.calls     # never sent on to the next step
    assert 'TOLERANS AŞILDI' in '\n'.join(lines)
    assert sink.events[-1][1]['ok'] is False


def test_tolerance_is_a_parameter():
    off = (GREEN_TARGET[0] + 0.03, GREEN_TARGET[1], GREEN_TARGET[2])
    result, *_ = run([HOME, off, RED_TARGET, HOME], tolerance=0.05)
    assert result.ok


def test_failed_skill_stops_with_its_message():
    class FailsOnRed(FakeSkills):
        def go_above(self, object_name, height=0.10):
            if object_name == 'red':
                self.calls.append(('go_above', 'red', height))
                from robot_core.models import SkillResult
                return SkillResult(False, 'go_above', 'erişim alanı dışında, robot hareket etmedi')
            return super().go_above(object_name, height)
    result, skills, *_ = run(good_positions(), skills=FailsOnRed())
    assert not result.ok and len(result.steps) == 3
    assert '3. adımda' in result.message and 'robot hareket etmedi' in result.message
    assert skills.calls[-1] == ('go_above', 'red', 0.30)     # the final home was not attempted


def test_unreadable_tf_is_a_failure_not_a_pass():
    result, *_ = run([HOME, None, RED_TARGET, HOME])
    assert not result.ok and 'TF ile ölçülemedi' in result.message and len(result.steps) == 2


def test_home_must_repeat():
    far_home = (0.0, 0.05, 1.24)
    result, *_ = run([HOME, GREEN_TARGET, RED_TARGET, far_home])
    assert not result.ok and '4. adımda' in result.message
    assert "ilk 'home' konumuna" in result.message and '3.0 cm' in result.message


def test_home_may_sway_a_little_but_targets_may_not():
    sway = (0.0, 0.02, 1.24)                    # 2 cm: fine for the free-hanging home pose
    assert run([HOME, GREEN_TARGET, RED_TARGET, sway])[0].ok
    off = (GREEN_TARGET[0] + 0.02, GREEN_TARGET[1], GREEN_TARGET[2])   # 2 cm: too much for a target
    assert not run([HOME, off, RED_TARGET, HOME])[0].ok


def test_a_raising_skill_is_reported_not_raised():
    result, *_ = run(good_positions(), skills=FakeSkills(raises=RuntimeError('boom')))
    assert not result.ok and 'RuntimeError' in result.message and len(result.steps) == 1


def test_a_broken_event_sink_does_not_matter():
    class Broken:
        def emit(self, kind, payload):
            raise RuntimeError('db down')
    result = run_demo_scenario(FakeSkills(), FakeTool(*good_positions()), lambda _: None,
                               events=Broken(), sleep=lambda _: None)
    assert result.ok


def test_custom_steps():
    steps = (ScenarioStep('sadece yeşil', ('above', 'green', 0.2)),)
    result, skills, *_ = run([(0.40, -0.10, 0.30)], steps=steps)
    assert result.ok and skills.calls == [('go_above', 'green', 0.2)]


def test_default_steps_use_clearances_pose_check_proves_reachable():
    """pose_check established the lowest clearance this arm reaches reliably (joint 3 limit)."""
    source = (SRC / 'robot_controller' / 'robot_controller' / 'pose_check.py').read_text()
    cases = ast.literal_eval(re.search(r'^CASES = (\(.*\))$', source, re.M).group(1))
    reachable = {label: clearance for label, clearance, must_reach in cases if must_reach}
    used = {s.action[1]: s.action[2] for s in DEFAULT_STEPS if s.action[0] == 'above'}
    assert set(used) == {'green', 'red'}
    for label, clearance in used.items():
        assert clearance >= reachable[label], f'{label}: {clearance} < {reachable[label]}'
