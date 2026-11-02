"""ToolExecutor against a real Robot API v1 server (fake skills): every refusal path."""
import threading
import time

import pytest
from robot_agent.client import HttpRobot, RobotUnreachable
from robot_agent.executor import Policy, ToolExecutor
from robot_core.safezone import SafeZone
from tests.fakes import RecordingSink

from agent_tests.rig import BOX, Rig


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def make(rig, confirm=lambda req: True, **kwargs):
    events = RecordingSink()
    policy = kwargs.pop('policy', None)
    return ToolExecutor(rig.robot, policy, events, confirm, **kwargs), events


def above_calls(rig):
    return [c for c in rig.skills.calls if c[0] == 'go_above']


# ---- the valid path ------------------------------------------------------------------
def test_valid_motion_runs_as_a_job_and_reports_the_target(rig):
    ex, events = make(rig)
    result = ex.execute('go_above_object', {'color': 'green', 'height_m': 0.35})
    assert result.ok and result.status == 'ok' and result.job_id
    assert result.data['target'][:2] == pytest.approx([0.40, -0.10])    # (z: the fake's own)
    assert above_calls(rig) == [('go_above', 'green', 0.35)]
    kind, payload = events.events[-1]
    assert kind == 'agent_tool_call' and payload['status'] == 'ok' and payload['risk'] == 'motion'
    assert payload['tool'] == 'go_above_object' and payload['job_id'] == result.job_id


def test_read_tools(rig):
    ex, _ = make(rig, confirm=None)                  # no confirmer needed for reading
    objects = ex.execute('list_objects', {})
    assert objects.ok and [o['label'] for o in objects.data['objects']] == ['red', 'green']
    state = ex.execute('get_robot_state', {})
    assert state.ok and state.data['state'] == 'idle' and state.data['api_version'] == 1
    assert above_calls(rig) == []


def test_default_height_is_used(rig):
    ex, _ = make(rig)
    assert ex.execute('go_above_object', {'color': 'red'}).ok
    assert above_calls(rig) == [('go_above', 'red', 0.35)]


def test_the_result_json_for_the_llm(rig):
    import json
    ex, _ = make(rig)
    data = json.loads(ex.execute('go_home', {}).to_llm())
    assert data['ok'] is True and data['status'] == 'ok' and data['dry_run'] is False


# ---- rejected: never reaches the robot -----------------------------------------------
@pytest.mark.parametrize('name, arguments', [
    ('exec', {'cmd': 'ls'}), ('reset', {}), ('go_to_position', {'x': 1, 'y': 0, 'z': 0.3}),
    ('', {}), (None, {}), (5, {}), ('GO_HOME', {}), ('go_home; reboot', {}),
])
def test_unknown_tools_are_rejected_and_the_list_of_real_ones_is_given(rig, name, arguments):
    ex, events = make(rig)
    result = ex.execute(name, arguments)
    assert result.status == 'rejected' and not result.ok and 'go_home' in result.message
    assert rig.jobs.recent() == [] and events.events[-1][1]['status'] == 'rejected'


@pytest.mark.parametrize('arguments', [
    {'color': 'purple'}, {'color': 'red', 'height_m': 9}, {'color': 'red', 'height_m': -1},
    {'color': 'red', 'height_m': float('nan')}, {'color': 'red', 'height_m': True}, {},
    {'color': 'red', 'extra': 1}, {'color': ['red']}, 'red', [], 7,
])
def test_invalid_arguments_are_rejected_before_anything_is_sent(rig, arguments):
    ex, _ = make(rig)
    result = ex.execute('go_above_object', arguments)
    assert result.status == 'rejected' and 'geçersiz argüman' in result.message
    assert rig.jobs.recent() == []                       # not even the object lookup happened


def test_none_arguments_mean_no_arguments_for_tools_without_any(rig):
    ex, _ = make(rig)
    assert ex.execute('go_home', None).ok


def test_disabled_tools_are_rejected(rig):
    allowed = frozenset({'get_robot_state', 'emergency_stop'})
    ex, _ = make(rig, policy=Policy(allowed_tools=allowed))
    assert [s['name'] for s in ex.specs()] == ['get_robot_state', 'emergency_stop']
    assert ex.execute('go_home', {}).status == 'rejected'


# ---- safe zone -----------------------------------------------------------------------
def test_a_target_outside_the_safe_box_is_refused_and_nothing_moves():
    rig = Rig(zone=SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.2)))     # ceiling 0.2 m
    try:
        ex, _ = make(rig)
        result = ex.execute('go_above_object', {'color': 'green', 'height_m': 0.35})
        assert result.status == 'refused' and 'güvenli çalışma kutusunun dışında' in result.message
        assert 'z=0.450' in result.message
        assert above_calls(rig) == []
        assert [j['command'] for j in rig.jobs.recent()] == ['objects']    # only the lookup
    finally:
        rig.close()


def test_an_unseen_object_is_refused(rig):
    rig.skills.objects = [o for o in rig.skills.objects if o.label != 'green']
    ex, _ = make(rig)
    result = ex.execute('go_above_object', {'color': 'green'})
    assert result.status == 'refused' and 'görünmüyor' in result.message
    assert above_calls(rig) == []


def test_two_objects_of_one_colour_are_ambiguous(rig):
    rig.skills.objects = rig.skills.objects + [rig.skills.objects[1]]
    ex, _ = make(rig)
    assert 'belirsiz' in ex.execute('go_above_object', {'color': 'green'}).message


# ---- dry run and confirmation --------------------------------------------------------
def test_dry_run_reports_what_would_run_and_sends_no_motion(rig):
    ex, _ = make(rig, dry_run=True)
    result = ex.execute('go_above_object', {'color': 'red', 'height_m': 0.3})
    assert result.status == 'dry_run' and result.ok and result.dry_run
    assert result.data['would_run'] == 'above red 0.300' and result.data['zone'] == 'içeride'
    assert result.data['target'] == pytest.approx([0.35, 0.15, 0.06 + 0.3])
    assert above_calls(rig) == [] and [j['command'] for j in rig.jobs.recent()] == ['objects']
    assert ex.execute('go_home', {}).status == 'dry_run'
    assert ex.execute('run_demo_scenario', {}).status == 'dry_run'
    assert ex.execute('emergency_stop', {}).status == 'dry_run'      # a dry run never stops it
    assert not rig.estop.is_set


def test_dry_run_still_runs_the_safety_checks(rig):
    ex, _ = make(rig, dry_run=True)
    assert ex.execute('go_above_object', {'color': 'purple'}).status == 'rejected'
    rig.supervisor.stop('test')
    assert ex.execute('go_home', {}).status == 'estopped'


def test_motion_needs_confirmation_and_no_confirmer_means_no(rig):
    ex, _ = make(rig, confirm=None)
    result = ex.execute('go_home', {})
    assert result.status == 'refused' and 'onay gerekli' in result.message
    assert rig.jobs.recent() == []
    asked = []
    ex2, _ = make(rig, confirm=lambda req: asked.append(req) or False)
    assert ex2.execute('go_above_object', {'color': 'red'}).status == 'refused'
    assert 'onaylamadı' in ex2.execute('run_demo_scenario', {}).message
    assert [a.command for a in asked] == ['above red 0.350', 'demo_scenario']
    assert asked[0].target and above_calls(rig) == []


def test_the_confirmer_sees_the_exact_command_and_target(rig):
    asked = []
    ex, _ = make(rig, confirm=lambda req: asked.append(req) or True)
    assert ex.execute('go_above_object', {'color': 'green', 'height_m': 0.3}).ok
    assert "'above green 0.300'" in asked[0].describe() and 'hedef' in asked[0].describe()


def test_the_policy_can_skip_confirmation_for_a_risk_class(rig):
    ex, _ = make(rig, confirm=None, policy=Policy(confirm_risks=frozenset({'long'})))
    assert ex.execute('go_home', {}).ok
    assert ex.execute('run_demo_scenario', {}).status == 'refused'


def test_motion_quota(rig):
    ex, _ = make(rig, policy=Policy(max_motion_calls=2))
    assert ex.execute('go_home', {}).ok and ex.execute('go_home', {}).ok
    third = ex.execute('go_home', {})
    assert third.status == 'refused' and 'sınırı' in third.message
    assert ex.execute('get_robot_state', {}).ok and ex.execute('emergency_stop', {}).halts


# ---- robot state ---------------------------------------------------------------------
def test_busy_robot_is_not_given_another_motion():
    rig = Rig(auto_release=False)
    try:
        status, body = rig.robot.post('/api/jobs', {'command': 'above green 0.35'})
        assert status == 202 and rig.skills.entered.wait(5)
        ex, _ = make(rig)
        result = ex.execute('go_home', {})
        assert result.status == 'refused' and 'meşgul' in result.message
        assert ex.execute('get_robot_state', {}).data['state'] == 'busy'
    finally:
        rig.close()


def test_estopped_robot_gets_no_motion_but_can_still_be_read(rig):
    rig.supervisor.stop('insan')
    ex, _ = make(rig)
    for tool, args in (('go_home', {}), ('go_above_object', {'color': 'red'}),
                       ('run_demo_scenario', {}), ('list_objects', {})):
        result = ex.execute(tool, args)
        assert result.status == 'estopped' and result.halts and not result.ok, tool
    assert rig.skills.calls == []
    state = ex.execute('get_robot_state', {})
    assert state.ok and state.data['state'] == 'estopped'


def test_the_executor_never_asks_for_a_reset(rig):
    ex, _ = make(rig)
    rig.supervisor.stop('x')
    assert ex.execute('reset', {}).status == 'rejected' and rig.estop.is_set


# ---- emergency stop has priority -----------------------------------------------------
def test_emergency_stop_tool_stops_and_verifies(rig):
    ex, events = make(rig, confirm=None)                 # no confirmation, no state check
    result = ex.execute('emergency_stop', {'reason': 'LLM şüphelendi'})
    assert result.status == 'estopped' and result.ok and result.halts
    assert rig.estop.is_set and 'LLM şüphelendi' in rig.estop.info()['reason']
    assert 'doğrulandı' in result.message
    assert events.events[-1][1]['status'] == 'estopped'


def test_emergency_stop_works_even_when_the_quota_is_used_up_and_the_robot_is_busy():
    rig = Rig(auto_release=False)
    try:
        ex, _ = make(rig, policy=Policy(max_motion_calls=0))
        rig.robot.post('/api/jobs', {'command': 'above green 0.35'})
        assert rig.skills.entered.wait(5)
        assert ex.execute('emergency_stop', {}).ok and rig.estop.is_set
    finally:
        rig.close()


def test_an_unverified_stop_is_reported_loudly():
    rig = Rig()
    rig.arm.speed = None                                  # no joint velocities: cannot verify
    rig.supervisor._stop_timeout_s = 0.5
    try:
        ex, _ = make(rig)
        result = ex.execute('emergency_stop', {})
        assert result.status == 'estopped' and not result.ok and 'DOĞRULANAMADI' in result.message
        assert rig.estop.is_set
    finally:
        rig.close()


def test_request_stop_from_another_thread_stops_a_running_job():
    rig = Rig(auto_release=False)
    try:
        ex, events = make(rig)
        box = []
        worker = threading.Thread(
            target=lambda: box.append(ex.execute('go_above_object', {'color': 'green'})))
        worker.start()
        assert rig.skills.entered.wait(5)
        stop = ex.request_stop('Ctrl-C')
        worker.join(10)
        assert stop.status == 'estopped' and rig.estop.is_set
        assert box and box[0].status == 'estopped' and box[0].halts
        assert ex.execute('go_home', {}).status == 'estopped'      # and it stays stopped
    finally:
        rig.close()


def test_a_human_pressing_stop_mid_job_ends_the_tool_call_as_estopped():
    rig = Rig(auto_release=False)
    try:
        ex, _ = make(rig)
        box = []
        worker = threading.Thread(target=lambda: box.append(ex.execute('go_home', {})))
        rig.skills.entered = threading.Event()
        worker.start()
        deadline = time.monotonic() + 5
        while not rig.runner.status()['running'] and time.monotonic() < deadline:
            time.sleep(0.01)
        rig.supervisor.stop('insan butonu')
        worker.join(10)
        assert box and box[0].status in ('estopped', 'ok')
        assert rig.estop.is_set and ex.execute('go_home', {}).status == 'estopped'
    finally:
        rig.close()


def test_cancel_current_job_cancels_without_locking(rig):
    rig.skills.release.clear()
    ex, _ = make(rig)
    assert ex.execute('cancel_current_job', {}).message == 'iptal edilecek çalışan iş yok'
    box = []
    args = {'color': 'red'}
    worker = threading.Thread(target=lambda: box.append(ex.execute('go_above_object', args)))
    worker.start()
    assert rig.skills.entered.wait(5)
    result = ex.execute('cancel_current_job', {})
    worker.join(10)
    assert result.status == 'cancelled' and box[0].status == 'cancelled'
    assert not rig.estop.is_set and ex.execute('get_robot_state', {}).data['state'] == 'idle'


# ---- the API is not there ------------------------------------------------------------
def test_unreachable_api_refuses_motion_and_reports_a_failed_stop_loudly():
    dead = HttpRobot('http://127.0.0.1:1', timeout=0.5)
    ex = ToolExecutor(dead, events=RecordingSink(), confirm=lambda r: True, sleep=lambda s: None)
    result = ex.execute('go_home', {})
    assert result.status == 'refused' and 'yanıt vermedi' in result.message
    stop = ex.execute('emergency_stop', {})
    assert stop.status == 'failed' and 'ACİL DUR GÖNDERİLEMEDİ' in stop.message and not stop.ok


def test_only_loopback_urls_are_accepted():
    for url in ('http://192.168.1.5:8080', 'http://example.com:8080', 'https://127.0.0.1:8080',
                'http://127.0.0.1', 'ftp://127.0.0.1:8080'):
        with pytest.raises(ValueError):
            HttpRobot(url)
    assert HttpRobot('http://localhost:8080').port == 8080
    with pytest.raises(RobotUnreachable):
        HttpRobot('http://127.0.0.1:1', timeout=0.3).get('/api/state')


def test_a_broken_event_sink_never_breaks_a_call(rig):
    class Broken:
        def emit(self, kind, payload):
            raise RuntimeError('boom')
    ex = ToolExecutor(rig.robot, events=Broken(), confirm=lambda r: True)
    assert ex.execute('go_home', {}).ok


def test_box_constant_is_what_the_rig_reports(rig):
    ex, _ = make(rig)
    zone = ex.execute('get_robot_state', {}).data['safety']['zone']['box']
    assert zone['z'] == [BOX.lower[2], BOX.upper[2]]
