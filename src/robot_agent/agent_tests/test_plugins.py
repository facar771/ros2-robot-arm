"""run_plugin: the plugin door of the agent (fake runner for the rules, real plugin processes for
the end-to-end behaviour). Everything that moves obeys the executor's pipeline."""
import threading
import time

import pytest
from robot_agent.agent import AgentRunner
from robot_agent.executor import Policy, ToolExecutor
from robot_agent.llm import LLMResponse, RuleLLM, ScriptedLLM, call
from robot_agent.plugins import DEFAULT_ALLOWED, PluginBridge
from robot_agent.tools import build_tools
from robot_core.safezone import SafeZone
from robot_plugins.invoke import catalog
from tests.fakes import RecordingSink

from agent_tests.rig import Rig

REAL_CATALOG = catalog()


class FakeRunner:
    """run_fn stand-in: records calls, answers like robot_plugins.invoke.run_plugin."""

    def __init__(self, result=None, block=None):
        self.calls = []
        self.result = result or {'ok': True, 'data': {'ok': True, 'objects': [{'color': 'red'}]},
                                 'code': None, 'error': None, 'warning': None, 'duration_s': 0.1}
        self.block = block

    def __call__(self, plugin, tool, args, **kwargs):
        self.calls.append((plugin, tool, args, kwargs))
        if self.block is not None:
            stop = kwargs['stop_event']
            deadline = time.monotonic() + 10
            while not stop.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            return {'ok': False, 'code': 'stopped', 'error': 'durduruldu', 'data': None,
                    'warning': None, 'duration_s': 0.2}
        return dict(self.result)


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def make(rig, runner=None, confirm=lambda req: True, allowed=None, entries=None, **kwargs):
    runner = runner or FakeRunner()
    bridge = PluginBridge(allowed, catalog_fn=lambda: entries if entries is not None
                          else REAL_CATALOG, run_fn=runner)
    events = RecordingSink()
    policy = kwargs.pop('policy', None)
    ex = ToolExecutor(rig.robot, policy, events, confirm, plugins=bridge, plugin_frames='synthetic',
                      **kwargs)
    return ex, runner, events


def plugin_call(ex, plugin, tool, arguments=None, **kw):
    args = {'plugin': plugin, 'tool': tool}
    if arguments is not None:
        args['arguments'] = arguments
    return ex.execute('run_plugin', args, **kw)


# ---- what the model is offered ---------------------------------------------------------
def test_run_plugin_lists_exactly_the_allowed_installed_tools(rig):
    ex, _, _ = make(rig)
    spec = next(s for s in ex.specs() if s['name'] == 'run_plugin')
    text = spec['description']
    assert "plugin='find_object'" in text and 'only looks' in text
    assert "plugin='scan_extent'" in text and 'turns the CAMERA' in text
    assert "plugin='follow_color'" in text and 'MOVES THE ROBOT ARM' in text
    props = spec['input_schema']['properties']
    assert props['plugin']['enum'] == ['find_object', 'follow_color', 'scan_extent']
    assert spec['input_schema']['required'] == ['plugin', 'tool']
    assert [s['name'] for s in ex.specs()][-2:] == ['cancel_current_job', 'emergency_stop']


def test_an_uploaded_plugin_is_not_callable_until_it_is_allowed(rig):
    sneaky = {'plugin': 'upload_x', 'name': 'go', 'description': 'd', 'motion': 'arm',
              'timeout_s': 5, 'input_schema': {'type': 'object', 'properties': {},
                                               'additionalProperties': False}}
    ex, runner, _ = make(rig, entries=REAL_CATALOG + [sneaky])
    assert 'upload_x' not in ex.specs()[-3]['description']
    result = plugin_call(ex, 'upload_x', 'go')
    assert result.status == 'rejected' and runner.calls == []
    ex2, runner2, _ = make(rig, entries=REAL_CATALOG + [sneaky],
                           allowed=DEFAULT_ALLOWED | {'upload_x.go'})
    assert plugin_call(ex2, 'upload_x', 'go').ok and len(runner2.calls) == 1   # explicit opt-in


def test_the_tool_is_absent_without_plugins(rig):
    for entries, allowed in (([], None), (REAL_CATALOG, frozenset())):
        ex, _, _ = make(rig, entries=entries, allowed=allowed)
        assert 'run_plugin' not in [s['name'] for s in ex.specs()]
        assert plugin_call(ex, 'find_object', 'find_object').status == 'rejected'


def test_a_broken_catalogue_fails_closed(rig):
    def boom():
        raise RuntimeError('kurulu değil')
    bridge = PluginBridge(catalog_fn=boom)
    ex = ToolExecutor(rig.robot, events=RecordingSink(), plugins=bridge)
    assert 'run_plugin' not in [s['name'] for s in ex.specs()] and 'kurulu değil' in bridge.problem


def test_entries_with_unknown_motion_or_unsupported_schema_are_dropped(rig):
    base = dict(REAL_CATALOG[0])
    for broken in ({**base, 'motion': 'teleport'},
                   {**base, 'input_schema': {'type': 'object', 'oneOf': []}},
                   {**base, 'input_schema': {'type': 'array'}},
                   {k: v for k, v in base.items() if k != 'motion'}):
        ex, _, _ = make(rig, entries=[broken])
        assert 'run_plugin' not in [s['name'] for s in ex.specs()]


# ---- rejected: nothing is run ----------------------------------------------------------
@pytest.mark.parametrize('args', [
    {'plugin': 'find_object', 'tool': 'follow_color'},               # a pair that is not allowed
    {'plugin': 'rm', 'tool': 'find_object'}, {'plugin': 'find_object', 'tool': 'rm'},
    {'plugin': 'find_object'}, {'tool': 'find_object'}, {},
    {'plugin': 'find_object', 'tool': 'find_object', 'arguments': 'red'},
    {'plugin': 'find_object', 'tool': 'find_object', 'extra': 1},
    {'plugin': 5, 'tool': 'find_object'},
])
def test_bad_tool_calls_never_reach_a_plugin(rig, args):
    ex, runner, _ = make(rig)
    assert ex.execute('run_plugin', args).status == 'rejected'
    assert runner.calls == [] and rig.jobs.recent() == []


@pytest.mark.parametrize('plugin, tool, arguments', [
    ('find_object', 'find_object', {'color': 'purple'}),
    ('find_object', 'find_object', {'color': 'red', 'x': 1}),
    ('follow_color', 'follow_color', {}),
    ('follow_color', 'follow_color', {'color': 'red', 'height_m': 9}),
    ('follow_color', 'follow_color', {'color': 'red', 'duration_s': 1e9}),
    ('follow_color', 'follow_color', {'color': 'red', 'height_m': float('nan')}),
    ('follow_color', 'follow_color', {'color': 'red', 'duration_s': float('inf')}),
    ('follow_color', 'follow_color', {'color': 'red', 'height_m': True}),
    ('follow_color', 'follow_color', {'color': ['red']}),
])
def test_plugin_arguments_are_validated_by_the_plugins_own_schema(rig, plugin, tool, arguments):
    ex, runner, _ = make(rig)
    result = plugin_call(ex, plugin, tool, arguments)
    assert result.status == 'rejected' and 'geçersiz eklenti argümanı' in result.message
    assert runner.calls == [] and rig.skills.calls == []


# ---- looking: no confirmation ----------------------------------------------------------
def test_a_looking_tool_runs_without_confirmation(rig):
    ex, runner, events = make(rig, confirm=None)
    result = plugin_call(ex, 'find_object', 'find_object', {'color': 'red'})
    assert result.ok and result.status == 'ok' and result.risk == 'read'
    plugin, tool, args, kwargs = runner.calls[0]
    assert (plugin, tool, args) == ('find_object', 'find_object', {'color': 'red'})
    assert kwargs['robot_url'] == rig.robot.base_url and kwargs['frames'] == 'synthetic'
    assert kwargs['stop_event'] is ex._stop_event
    assert result.data['plugin_data']['objects'] == [{'color': 'red'}]
    assert result.data['motion'] == 'none'
    assert events.events[-1][1]['risk'] == 'read' and events.events[-1][1]['tool'] == 'run_plugin'
    assert ex._motions == 0                                   # looking is not a motion


def test_defaults_from_the_schema_are_filled_in(rig):
    ex, runner, _ = make(rig)
    plugin_call(ex, 'find_object', 'find_object')
    assert runner.calls[0][2] == {'color': 'any'}


def test_looking_is_refused_while_the_robot_is_estopped(rig):
    rig.supervisor.stop('insan')
    ex, runner, _ = make(rig)
    result = plugin_call(ex, 'find_object', 'find_object')
    assert result.status == 'estopped' and result.halts and runner.calls == []


# ---- the camera moves: confirmation, quota ---------------------------------------------
def test_a_camera_tool_needs_confirmation_and_counts_as_a_motion(rig):
    ex, runner, _ = make(rig, confirm=None)
    assert plugin_call(ex, 'scan_extent', 'scan_extent').status == 'refused' and runner.calls == []
    asked = []
    ex, runner, _ = make(rig, confirm=lambda req: asked.append(req) or True,
                         policy=Policy(max_motion_calls=1))
    result = plugin_call(ex, 'scan_extent', 'scan_extent', {'steps': 3})
    assert result.ok and result.risk == 'motion'
    assert asked[0].command == 'eklenti scan_extent.scan_extent'
    assert asked[0].arguments['arguments']['steps'] == 3 and ex._motions == 1
    assert plugin_call(ex, 'scan_extent', 'scan_extent').status == 'refused'      # quota


# ---- the arm moves: everything the other motion tools obey -----------------------------
def test_an_arm_tool_needs_confirmation_and_shows_the_first_target(rig):
    asked = []
    ex, runner, _ = make(rig, confirm=lambda req: asked.append(req) or True)
    result = plugin_call(ex, 'follow_color', 'follow_color', {'color': 'green', 'duration_s': 5})
    assert result.ok and result.risk == 'long' and len(runner.calls) == 1
    assert asked[0].target[:2] == pytest.approx([0.40, -0.10])   # from the real object position
    assert result.data['zone'] == 'içeride'
    assert result.data['target'][:2] == pytest.approx([0.4, -0.1])
    assert runner.calls[0][2]['height_m'] == 0.30                      # schema default applied
    no, _, _ = make(rig, confirm=lambda req: False)
    refused = plugin_call(no, 'follow_color', 'follow_color', {'color': 'green'})
    assert refused.status == 'refused' and 'onaylamadı' in refused.message


def test_an_arm_tool_without_confirmation_is_refused(rig):
    ex, runner, _ = make(rig, confirm=None)
    assert plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red'}).status == 'refused'
    assert runner.calls == []


def test_the_safe_box_is_checked_before_an_arm_plugin_starts():
    rig = Rig(zone=SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.2)))
    try:
        ex, runner, _ = make(rig)
        args = {'color': 'green', 'height_m': 0.4}
        result = plugin_call(ex, 'follow_color', 'follow_color', args)
        assert result.status == 'refused' and 'güvenli çalışma kutusunun dışında' in result.message
        assert runner.calls == [] and result.risk == 'long'
    finally:
        rig.close()


def test_an_unseen_object_stops_an_arm_plugin_before_it_starts(rig):
    rig.skills.objects = [o for o in rig.skills.objects if o.label != 'green']
    ex, runner, _ = make(rig)
    assert 'görünmüyor' in plugin_call(ex, 'follow_color', 'follow_color',
                                       {'color': 'green'}).message and runner.calls == []


def test_an_arm_tool_is_refused_when_the_robot_is_busy_or_estopped():
    rig = Rig(auto_release=False)
    try:
        rig.robot.post('/api/jobs', {'command': 'above green 0.35'})
        assert rig.skills.entered.wait(5)
        ex, runner, _ = make(rig)
        assert 'meşgul' in plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red'}).message
        rig.supervisor.stop('insan')
        assert plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red'}).halts
        assert runner.calls == []
    finally:
        rig.close()


def test_dry_run_reports_what_would_run_and_runs_only_the_looking_tools(rig):
    ex, runner, _ = make(rig, dry_run=True)
    arm = plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red', 'duration_s': 3})
    assert arm.status == 'dry_run' and arm.data['would_run'] == 'eklenti follow_color.follow_color'
    assert arm.data['arguments']['duration_s'] == 3 and arm.data['target']
    assert plugin_call(ex, 'scan_extent', 'scan_extent').status == 'dry_run'
    assert runner.calls == []
    assert plugin_call(ex, 'find_object', 'find_object').ok and len(runner.calls) == 1   # looks


def test_an_arm_tool_needs_the_robot_url(rig):
    class NoUrl:
        def __init__(self, inner):
            self.get, self.post = inner.get, inner.post
    runner = FakeRunner()
    bridge = PluginBridge(catalog_fn=lambda: REAL_CATALOG, run_fn=runner)
    ex = ToolExecutor(NoUrl(rig.robot), events=RecordingSink(), confirm=lambda r: True,
                      plugins=bridge)
    result = plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red'})
    assert result.status == 'refused' and 'robot adresi bilinmiyor' in result.message
    assert runner.calls == []


# ---- mapping the plugin's answer -------------------------------------------------------
def result_of(code, **kw):
    base = {'ok': False, 'code': code, 'error': f'hata {code}', 'data': None, 'warning': None,
            'duration_s': 0.5}
    base.update(kw)
    return base


@pytest.mark.parametrize('code, status', [
    ('estop', 'estopped'), ('stopped', 'cancelled'), ('interrupted', 'cancelled'),
    ('timeout', 'failed'), ('plugin_error', 'failed'), ('start_failed', 'failed'),
    ('exception', 'failed'), ('bad_arguments', 'rejected'), ('permission_denied', 'rejected'),
    ('unknown_plugin', 'rejected'), ('bad_robot_url', 'rejected'),
])
def test_plugin_error_codes_map_to_tool_statuses(rig, code, status):
    ex, _, _ = make(rig, FakeRunner(result_of(code, data={'reason': 'x'})))
    result = plugin_call(ex, 'find_object', 'find_object')
    assert result.status == status and not result.ok and result.halts == (status == 'estopped')
    assert code in result.message or status == 'estopped'


def test_a_timed_out_arm_plugin_gets_its_robot_job_cancelled():
    rig = Rig(auto_release=False)
    try:
        class LeavesAJobBehind(FakeRunner):
            def __call__(self, *a, **k):
                rig.robot.post('/api/jobs', {'command': 'above green 0.35'})   # the plugin's job
                assert rig.skills.entered.wait(5)
                return result_of('timeout')

        ex, _, _ = make(rig, LeavesAJobBehind())
        result = plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red'})
        assert result.status == 'failed' and 'iptal' in result.data['job_cleanup']
        deadline = time.monotonic() + 5
        while rig.runner.status()['running'] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not rig.runner.status()['running'] and not rig.estop.is_set   # cancelled, not locked
    finally:
        rig.close()


def test_a_robot_that_became_estopped_during_an_arm_plugin_is_reported(rig):
    class Stopper(FakeRunner):
        def __call__(self, *a, **k):
            rig.supervisor.stop('insan butonu')
            return super().__call__(*a, **k)

    ex, _, _ = make(rig, Stopper())
    result = plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red'})
    assert result.status == 'estopped' and result.halts and not result.ok


def test_the_plugins_warning_and_huge_results_reach_the_model_in_a_bounded_way(rig):
    huge = {'ok': True, 'data': {'ok': True, 'blob': 'x' * 50000}, 'code': None, 'error': None,
            'warning': "robot_url='fake': benzetim", 'duration_s': 1.0}
    ex, _, _ = make(rig, FakeRunner(huge))
    result = plugin_call(ex, 'find_object', 'find_object')
    assert result.ok and result.data['warning'].startswith("robot_url='fake'")
    assert result.data['plugin_data']['truncated'] is True and len(result.to_llm()) < 9000


def test_request_stop_stops_a_running_plugin_and_the_robot(rig):
    runner = FakeRunner(block=True)
    ex, _, _ = make(rig, runner)
    box = []
    worker = threading.Thread(target=lambda: box.append(
        plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red', 'duration_s': 60})))
    worker.start()
    deadline = time.monotonic() + 5
    while not runner.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    stop = ex.request_stop('Ctrl-C')
    worker.join(10)
    assert stop.status == 'estopped' and rig.estop.is_set                 # the robot is stopped ...
    assert box and box[0].status == 'estopped' and box[0].halts           # ... and so is the run


def test_the_emergency_stop_tool_is_unaffected_by_plugins(rig):
    ex, _, _ = make(rig, policy=Policy(max_motion_calls=0), confirm=None)
    assert ex.execute('emergency_stop', {}).ok and rig.estop.is_set


def test_there_is_still_no_reset_and_no_plugin_can_ask_for_one(rig):
    ex, _, _ = make(rig)
    assert 'reset' not in [s['name'] for s in ex.specs()]
    assert ex.execute('reset', {}).status == 'rejected'
    assert plugin_call(ex, 'find_object', 'reset').status == 'rejected'


# ---- the agent loop and the offline parser ---------------------------------------------
def test_a_looking_request_through_the_loop(rig):
    ex, runner, _ = make(rig)
    llm = ScriptedLLM(LLMResponse('Bakıyorum.', [call('run_plugin', {
        'plugin': 'find_object', 'tool': 'find_object', 'arguments': {'color': 'red'}})]),
        LLMResponse('Kırmızı nesneyi gördüm.'))
    result = AgentRunner(llm, ex).run('kırmızıyı bul')
    assert result.all_ok and result.calls[0][2].risk == 'read' and len(runner.calls) == 1


def test_an_estop_during_a_plugin_ends_the_whole_run(rig):
    ex, _, _ = make(rig, FakeRunner(result_of('estop')))
    llm = ScriptedLLM(LLMResponse('Takip ediyorum.', [call('run_plugin', {
        'plugin': 'follow_color', 'tool': 'follow_color', 'arguments': {'color': 'red'}})]),
        LLMResponse('(buraya gelinmemeli)'))
    result = AgentRunner(llm, ex).run('kırmızıyı takip et')
    assert result.halted == 'acil dur' and len(llm.calls) == 1 and 'Acil dur etkin' in result.text


@pytest.mark.parametrize('text, plugin, arguments', [
    ('yeşil nesneyi bul', 'find_object', {'color': 'green'}),
    ('kırmızı kutuyu ara', 'find_object', {'color': 'red'}),
    ('etrafı tara', 'scan_extent', {}),
    ('mavi topu takip et', 'follow_color', {'color': 'blue'}),
    ('kırmızıyı 10 saniye takip et', 'follow_color', {'color': 'red', 'duration_s': 10}),
])
def test_offline_parser_plugin_phrases(text, plugin, arguments):
    response = RuleLLM().complete('', [{'role': 'user', 'content': text}], [])
    (tc,) = response.tool_calls
    assert tc.name == 'run_plugin' and tc.arguments['plugin'] == plugin
    assert tc.arguments.get('arguments', {}) == arguments


def test_offline_parser_stop_still_wins_over_following():
    assert RuleLLM().complete('', [{'role': 'user', 'content': 'kırmızıyı takip et ve dur'}],
                              []).tool_calls[0].name == 'emergency_stop'


# ---- REAL plugin processes (synthetic frames) ------------------------------------------
def real_executor(rig, confirm=lambda req: True):
    return ToolExecutor(rig.robot, Policy(), RecordingSink(), confirm, plugin_frames='synthetic')


def test_real_find_object_runs_in_its_own_process(rig):
    result = plugin_call(real_executor(rig, confirm=None), 'find_object', 'find_object',
                         {'color': 'any'})
    assert result.ok, result.message
    objects = result.data['plugin_data']['objects']
    assert objects and {'center', 'size'} <= set(objects[0])
    assert 'warning' not in result.data                       # a real robot URL, not 'fake'


def test_real_follow_color_moves_only_through_the_robot_api(rig):
    result = plugin_call(real_executor(rig), 'follow_color', 'follow_color',
                         {'color': 'red', 'duration_s': 1})
    assert result.ok, (result.status, result.message, result.data)
    assert result.data['plugin_data']['reason'] == 'duration'
    above_jobs = [j for j in rig.jobs.recent() if j['command'].startswith('above')]
    assert above_jobs and all(j['command'].startswith('above red') for j in above_jobs)


def test_real_follow_color_ends_as_estopped_when_a_human_stops_the_robot():
    rig = Rig(auto_release=False)
    try:
        ex = real_executor(rig)
        box = []
        worker = threading.Thread(target=lambda: box.append(
            plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red', 'duration_s': 60})))
        worker.start()
        assert rig.skills.entered.wait(30)                 # the plugin's first job is running
        rig.supervisor.stop('insan butonu')
        worker.join(60)
        assert box and box[0].status == 'estopped' and box[0].halts, box
        assert plugin_call(ex, 'find_object', 'find_object').status == 'estopped'
    finally:
        rig.close()


def test_real_request_stop_kills_the_plugin_and_stops_the_robot():
    rig = Rig(auto_release=False)
    try:
        ex = real_executor(rig)
        box = []
        worker = threading.Thread(target=lambda: box.append(
            plugin_call(ex, 'follow_color', 'follow_color', {'color': 'red', 'duration_s': 60})))
        worker.start()
        assert rig.skills.entered.wait(30)
        ex.request_stop('Ctrl-C')
        worker.join(60)
        assert rig.estop.is_set and box and box[0].status == 'estopped' and box[0].halts
    finally:
        rig.close()


def test_the_default_allow_list_is_the_three_shipped_tools():
    assert DEFAULT_ALLOWED == {'find_object.find_object', 'scan_extent.scan_extent',
                               'follow_color.follow_color'}
    assert 'run_plugin' in build_tools(PluginBridge()) and 'run_plugin' not in build_tools()
