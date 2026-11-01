"""E-stop semantics (ROS-free): latch, guarded motion, verified stop, reset rules, robot state."""
import threading
import time

import pytest
from robot_core.models import JointTarget, MotionResult, PoseTarget
from robot_core.supervisor import (
    API_VERSION,
    ESTOP_MESSAGE,
    EStop,
    GuardedMotion,
    Supervisor,
    max_speed,
)
from robot_core.webcmd import CommandRunner, EStopped, parse_command

from tests.fakes import (
    FakeArm,
    FakeClock,
    FakeMotion,
    FakePose,
    FakeSkills,
    FakeTool,
    RecordingSink,
)

NAMES = ('joint_1', 'joint_2')


def make(arm=None, runner=None, mode='gazebo', **kwargs):
    arm = arm or FakeArm(NAMES)
    clock = FakeClock()
    estop = EStop(wall=clock)
    sink = RecordingSink()
    sup = Supervisor(estop, arm, NAMES, stop_port=arm, runner=runner, mode=mode, events=sink,
                     clock=clock, sleep=clock.sleep, **kwargs)
    return sup, arm, estop, sink, clock


def test_estop_latch():
    estop = EStop(wall=lambda: 5.0)
    assert not estop.is_set and estop.info() == {'tripped': False, 'reason': None, 'since': None}
    assert estop.trip('buton') is True and estop.trip('again') is False
    assert estop.is_set and estop.info() == {'tripped': True, 'reason': 'buton', 'since': 5.0}
    assert estop.reset() is True and estop.reset() is False and not estop.is_set


def test_guarded_motion_refuses_while_latched():
    estop, motion, pose = EStop(), FakeMotion(), FakePose()
    guarded = GuardedMotion(type('M', (), {'move_to': motion.move_to,
                                           'move_to_pose': pose.move_to_pose})(), estop)
    assert guarded.move_to(JointTarget((0.0,))).ok and guarded.move_to_pose(PoseTarget(1, 0, 0)).ok
    estop.trip('x')
    for result in (guarded.move_to(JointTarget((0.0,))), guarded.move_to_pose(PoseTarget(1, 0, 0))):
        assert isinstance(result, MotionResult) and not result.ok
        assert result.message == ESTOP_MESSAGE
    assert len(motion.targets) == 1 and len(pose.targets) == 1     # nothing new reached the arm


def test_max_speed():
    assert max_speed({'a': -0.3, 'b': 0.1}, ('a', 'b')) == pytest.approx(0.3)
    assert max_speed({'a': 0.1}, ('a', 'b')) is None and max_speed(None, ('a',)) is None


# ---- stop ----------------------------------------------------------------------------------------
def test_stop_latches_cancels_and_verifies_from_velocities():
    arm = FakeArm(NAMES, speed=0.8)
    sup, arm, estop, sink, _ = make(arm)
    assert sup.moving() is True and sup.state() == 'idle'
    result = sup.stop('test')
    assert estop.is_set and sup.state() == 'estopped'
    assert result.latched and result.stopped and result.max_joint_speed == 0.0
    assert arm.stop_calls == 1 and 0.2 <= result.stop_time_s < 1.0      # quiet window included
    assert sup.moving() is False
    kind, payload = sink.events[-1]
    assert kind == 'estop' and payload['reason'] == 'test' and payload['stopped'] is True


def test_stop_repeats_the_request_until_the_arm_is_quiet():
    sup, arm, *_ = make(FakeArm(NAMES, speed=0.8, stops_needed=3))
    result = sup.stop()
    assert result.stopped and arm.stop_calls == 3         # the first requests were "lost"


def test_stop_reports_honestly_when_the_arm_keeps_moving():
    sup, arm, estop, sink, _ = make(FakeArm(NAMES, speed=0.8, stops_needed=10 ** 6),
                                    stop_timeout_s=2.0)
    result = sup.stop()
    assert estop.is_set and result.latched and not result.stopped
    assert 'durmadı' in result.message and result.max_joint_speed == pytest.approx(0.8)
    assert sink.events[-1][1]['stopped'] is False


def test_stop_cannot_verify_without_velocities_but_still_latches():
    sup, _, estop, *_ = make(FakeArm(NAMES, speed=None), stop_timeout_s=1.0)
    result = sup.stop()
    assert estop.is_set and not result.stopped and result.max_joint_speed is None
    assert 'doğrulanamadı' in result.message


def test_stop_latches_even_if_the_stop_port_fails():
    sup, _, estop, *_ = make(FakeArm(NAMES, speed=0.0, raises=RuntimeError('boom')))
    result = sup.stop()
    assert estop.is_set and result.stopped and 'RuntimeError' in result.message


def test_stop_without_a_stop_port_still_latches():
    clock = FakeClock()
    arm = FakeArm(NAMES)
    sup = Supervisor(EStop(), arm, NAMES, stop_port=None, clock=clock, sleep=clock.sleep)
    assert sup.stop().latched and 'bağlantısı yok' in sup.stop().message


# ---- reset ---------------------------------------------------------------------------------------
def test_reset_only_when_the_arm_stands_still():
    sup, arm, estop, sink, _ = make(FakeArm(NAMES, speed=0.0))
    sup.stop()
    arm.speed = 0.5
    refused = sup.reset()
    assert not refused.ok and 'hareket ediyor' in refused.message and estop.is_set
    arm.speed = None
    assert not sup.reset().ok and estop.is_set                  # unknown = refuse (fail safe)
    arm.speed = 0.0
    done = sup.reset()
    assert done.ok and done.changed and not estop.is_set and sup.state() == 'idle'
    assert sink.events[-1][0] == 'estop_reset'
    again = sup.reset()
    assert again.ok and not again.changed


def test_reset_waits_for_the_cancelled_command_to_unwind():
    release = threading.Event()
    skills = FakeSkills(block=release)
    skills.entered = threading.Event()
    estop = EStop()
    runner = CommandRunner(skills, FakeTool((0, 0, 1)), estop=estop)
    clock = FakeClock()
    arm = FakeArm(NAMES)
    sup = Supervisor(estop, arm, NAMES, stop_port=arm, runner=runner, clock=clock,
                     sleep=lambda s: (clock.sleep(s), release.set(), time.sleep(0.01)))
    worker = threading.Thread(target=lambda: runner.run(parse_command('above green 0.25')))
    worker.start()
    assert skills.entered.wait(5) and sup.state() == 'busy'
    sup.stop()
    result = sup.reset()           # the fake sleep releases the blocked command -> it unwinds
    worker.join(5)
    assert result.ok and sup.state() == 'idle'


def test_reset_refuses_while_a_command_never_ends():
    skills = FakeSkills(block=threading.Event())
    skills.entered = threading.Event()
    runner = CommandRunner(skills, FakeTool((0, 0, 1)))
    sup, *_ = make(runner=runner)
    worker = threading.Thread(target=lambda: runner.run(parse_command('above green')), daemon=True)
    worker.start()
    assert skills.entered.wait(5)
    result = sup.reset()
    assert not result.ok and 'çalışıyor' in result.message
    skills.block.set()
    worker.join(5)


# ---- state ---------------------------------------------------------------------------------------
def test_robot_document_always_has_the_same_keys():
    sup, *_ = make(mode='mock')
    doc = sup.document()
    assert set(doc) == {'api_version', 'mode', 'state', 'current_job', 'last_error', 'moving',
                        'estop'}
    assert doc['api_version'] == API_VERSION == 1 and doc['mode'] == 'mock'
    assert doc['state'] == 'idle' and doc['current_job'] is None and doc['last_error'] is None


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        make(mode='simulation')


def test_state_busy_error_idle_and_estopped_priority():
    skills = FakeSkills(block=threading.Event())
    skills.entered = threading.Event()
    estop = EStop()
    runner = CommandRunner(skills, FakeTool((0, 0, 1)), estop=estop)
    arm = FakeArm(NAMES)
    sup = Supervisor(estop, arm, NAMES, stop_port=arm, runner=runner)
    worker = threading.Thread(target=lambda: runner.run(parse_command('above green 0.25')))
    worker.start()
    assert skills.entered.wait(5)
    doc = sup.document()
    assert doc['state'] == 'busy' and doc['current_job']['command'] == 'above green 0.25'
    assert doc['current_job']['id'] == 'j1' and doc['current_job']['elapsed_s'] >= 0
    estop.trip('x')
    assert sup.state() == 'estopped'                       # outranks busy
    estop.reset()
    skills.block.set()
    worker.join(5)
    assert sup.state() == 'idle'


def test_internal_error_latches_state_error_until_success_or_reset():
    skills = FakeSkills(raises=RuntimeError('boom'))
    estop = EStop()
    runner = CommandRunner(skills, FakeTool((0, 0, 1)), estop=estop)
    arm = FakeArm(NAMES)
    sup = Supervisor(estop, arm, NAMES, stop_port=arm, runner=runner)
    assert runner.run(parse_command('home')).internal_error
    doc = sup.document()
    assert doc['state'] == 'error' and doc['last_error']['internal'] is True
    assert 'RuntimeError' in doc['last_error']['message']
    assert sup.reset().ok and sup.state() == 'idle' and sup.document()['last_error'] is None
    runner.run(parse_command('home'))
    assert sup.state() == 'error'
    skills.raises = None
    assert runner.run(parse_command('home')).ok and sup.state() == 'idle'


def test_an_unreachable_goal_is_not_the_error_state():
    runner = CommandRunner(FakeSkills(fail='ulaşılamaz'), FakeTool((0, 0, 1)))
    sup, *_ = make(runner=runner)
    assert not runner.run(parse_command('home')).ok
    doc = sup.document()
    assert doc['state'] == 'idle' and doc['last_error']['message'] == 'ulaşılamaz'
    assert doc['last_error']['internal'] is False


def test_runner_refuses_commands_while_latched():
    skills = FakeSkills()
    estop = EStop()
    runner = CommandRunner(skills, FakeTool((0, 0, 1)), estop=estop)
    estop.trip('x')
    with pytest.raises(EStopped):
        runner.run(parse_command('home'))
    assert skills.calls == []
    estop.reset()
    assert runner.run(parse_command('home')).ok


def test_job_ids_count_up():
    runner = CommandRunner(FakeSkills(), FakeTool((0, 0, 1)))
    seen = []
    runner._dispatch = lambda c: (seen.append(runner.status()['job']['id']) or (True, 'ok', {}))
    runner.run(parse_command('home'))
    runner.run(parse_command('objects'))
    assert seen == ['j1', 'j2']
