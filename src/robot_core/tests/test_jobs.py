"""JobManager: asynchronous jobs, single flight, cancel, timeout, e-stop, history."""
import threading
import time

import pytest
from robot_core.jobs import (
    BadTimeout,
    JobBusy,
    JobManager,
    JobNotFound,
    check_timeout,
    parse_progress,
)
from robot_core.scenario import ScenarioResult
from robot_core.supervisor import CancelFlag, EStop, EStopped
from robot_core.webcmd import CommandRunner, parse_command

from tests.fakes import CancelAwareSkills, FakeSkills, FakeTool


def make(skills=None, history=50, scenario=None):
    estop, cancel = EStop(), CancelFlag()
    skills = skills or CancelAwareSkills(cancel, estop)
    kwargs = {} if scenario is None else {'scenario': scenario}
    runner = CommandRunner(skills, FakeTool((0, 0, 1)), estop=estop, **kwargs)
    return JobManager(runner, cancel, estop, history=history), skills, estop, cancel


def wait_state(manager, job_id, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        doc = manager.get(job_id)
        if doc['state'] == state:
            return doc
        time.sleep(0.01)
    raise AssertionError(f"{job_id} {manager.get(job_id)['state']} kaldı, beklenen {state}")


def test_job_runs_and_finishes_done():
    manager, skills, *_ = make(FakeSkills())
    job = manager.submit(parse_command('above green 0.25'))
    doc = wait_state(manager, job.id, 'done')
    assert doc['command'] == 'above green 0.25' and doc['error'] is None
    assert doc['result']['ok'] and doc['result']['data']['object'] == 'obj_green_1'
    assert doc['finished_at'] >= doc['created_at'] and doc['timed_out'] is False
    assert doc['progress']['log'][0] == '> above green 0.25'
    assert skills.calls == [('go_above', 'green', 0.25)]


def test_submit_answers_at_once_while_the_command_runs():
    manager, skills, *_ = make()
    skills.entered = threading.Event()
    started = time.monotonic()
    job = manager.submit(parse_command('above green'))
    assert time.monotonic() - started < 0.5
    assert skills.entered.wait(5) and manager.get(job.id)['state'] == 'running'
    skills.release = threading.Event()
    skills.release.set()
    wait_state(manager, job.id, 'done')


def test_only_one_job_at_a_time_and_the_busy_error_names_it():
    manager, skills, *_ = make()
    skills.entered = threading.Event()
    first = manager.submit(parse_command('above green'))
    assert skills.entered.wait(5)
    with pytest.raises(JobBusy) as busy:
        manager.submit(parse_command('home'))
    assert busy.value.job_id == first.id and first.id in str(busy.value)
    manager.cancel(first.id)
    wait_state(manager, first.id, 'cancelled')
    assert manager.submit(parse_command('objects')) is not None      # slot is free again


def test_cancel_stops_the_job_and_locks_nothing():
    manager, skills, estop, cancel = make()
    skills.entered = threading.Event()
    job = manager.submit(parse_command('above green 0.25'))
    assert skills.entered.wait(5)
    _, was_running = manager.cancel(job.id)
    doc = manager.get(job.id)
    assert was_running and doc['state'] == 'cancelled' and doc['cancelled_by'] == 'user'
    assert doc['cancel_requested'] and 'iptal' in doc['error'] and doc['result']['ok'] is False
    assert not estop.is_set and not cancel.is_set                     # nothing stays latched
    skills.release = threading.Event()
    skills.release.set()
    nxt = manager.submit(parse_command('above red 0.35'))
    assert wait_state(manager, nxt.id, 'done')


def test_cancel_of_a_finished_or_unknown_job():
    manager, *_ = make(FakeSkills())
    job = manager.submit(parse_command('home'))
    wait_state(manager, job.id, 'done')
    assert manager.cancel(job.id)[1] is False
    with pytest.raises(JobNotFound):
        manager.cancel('j999')


def test_timeout_fails_the_job_and_cancels_the_motion():
    manager, skills, _, cancel = make()
    job = manager.submit(parse_command('above green'), timeout_s=0.2)
    doc = wait_state(manager, job.id, 'failed')
    assert doc['timed_out'] and 'zaman aşımı' in doc['error'] and doc['cancelled_by'] is None
    assert not cancel.is_set
    assert manager.submit(parse_command('objects'))


def test_estop_during_a_job_marks_it_cancelled_by_estop():
    manager, skills, estop, _ = make()
    skills.entered = threading.Event()
    job = manager.submit(parse_command('above green'))
    assert skills.entered.wait(5)
    estop.trip('test')
    doc = wait_state(manager, job.id, 'cancelled')
    assert doc['cancelled_by'] == 'estop' and 'acil dur' in doc['error']
    with pytest.raises(EStopped):
        manager.submit(parse_command('home'))


def test_internal_error_is_failed_with_the_reason():
    manager, *_ = make(FakeSkills(raises=RuntimeError('boom')))
    job = manager.submit(parse_command('home'))
    doc = wait_state(manager, job.id, 'failed')
    assert 'RuntimeError' in doc['error'] and doc['result']['ok'] is False


def test_unreachable_goal_is_failed_not_cancelled():
    manager, *_ = make(FakeSkills(fail='ulaşılamaz'))
    job = manager.submit(parse_command('home'))
    doc = wait_state(manager, job.id, 'failed')
    assert doc['error'] == 'ulaşılamaz' and doc['cancelled_by'] is None


def test_progress_follows_the_scenario_narration_while_it_runs():
    gate = threading.Event()

    def scenario(skills, tool, say, events=None):
        say('[1/4] Başlangıç')
        say('    ölçülen uç konum')
        say('[2/4] Yeşilin üstüne')
        gate.wait(5)
        return ScenarioResult(True, 'senaryo tamamlandı', ())

    manager, *_ = make(FakeSkills(), scenario=scenario)
    job = manager.submit(parse_command('demo_scenario'))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and manager.get(job.id)['progress']['step'] != 2:
        time.sleep(0.01)
    progress = manager.get(job.id)['progress']
    assert (progress['step'], progress['steps'], progress['say']) == (2, 4, 'Yeşilin üstüne')
    assert manager.get(job.id)['state'] == 'running'
    gate.set()
    done = wait_state(manager, job.id, 'done')
    assert done['progress']['step'] == 2 and done['result']['message'] == 'senaryo tamamlandı'


def test_parse_progress():
    assert parse_progress([]) == {'step': None, 'steps': None, 'say': None}
    assert parse_progress(['> home', '[+] tamam'])['say'] == '[+] tamam'
    assert parse_progress(['[1/4] a', '[3/4] b', '   x'])['step'] == 3


def test_history_is_trimmed_but_a_running_job_never_is():
    manager, *_ = make(FakeSkills(), history=3)
    ids = []
    for _ in range(6):
        job = manager.submit(parse_command('objects'))
        wait_state(manager, job.id, 'done')
        ids.append(job.id)
    recent = manager.recent(10)
    assert [j['id'] for j in recent] == ids[-3:][::-1]       # newest first, only 3 kept
    assert manager.get(ids[0]) is None


def test_run_sync_returns_the_outcome_like_the_old_endpoint():
    manager, *_ = make(FakeSkills())
    outcome = manager.run_sync(parse_command('objects'))
    assert outcome.ok and outcome.data['objects']
    failing, *_ = make(FakeSkills(fail='olmadı'))
    assert not failing.run_sync(parse_command('home')).ok


@pytest.mark.parametrize('value', [0, 0.5, 601, 'x', None, True, float('nan'), float('inf'), -1])
def test_bad_timeouts(value):
    with pytest.raises(BadTimeout):
        check_timeout(value)


@pytest.mark.parametrize('value', [1, 5.5, 600])
def test_good_timeouts(value):
    assert check_timeout(value) == float(value)
