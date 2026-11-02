"""Scripted-LLM scenarios (no API key, no network): valid, invalid, unsafe, and e-stop requests."""
import json
import threading

import pytest
from robot_agent.agent import AgentRunner
from robot_agent.executor import Policy, ToolExecutor
from robot_agent.llm import LLMResponse, ScriptedLLM, call
from robot_core.safezone import SafeZone
from tests.fakes import RecordingSink

from agent_tests.rig import Rig


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def runner(rig, llm, confirm=lambda req: True, **kwargs):
    events = RecordingSink()
    executor = ToolExecutor(rig.robot, kwargs.pop('policy', Policy()), events, confirm)
    return AgentRunner(llm, executor, **kwargs), executor, events


def above(rig):
    return [c for c in rig.skills.calls if c[0] == 'go_above']


def tool_results(messages):
    return [json.loads(b['content']) for m in messages if m['role'] == 'user'
            and isinstance(m['content'], list) for b in m['content']]


# ---- valid ------------------------------------------------------------------------------------
def test_valid_request_runs_and_the_conversation_is_well_formed(rig):
    llm = ScriptedLLM(
        LLMResponse('Nesnelere bakıyorum.', [call('list_objects', id='a')]),
        LLMResponse('Yeşilin üstüne gidiyorum.',
                    [call('go_above_object', {'color': 'green'}, 'b')]),
        LLMResponse('Yeşil nesnenin üstündeyim.'))
    agent, _, events = runner(rig, llm)
    result = agent.run('yeşil nesnenin üstüne git')
    assert result.all_ok and result.halted is None and result.text == 'Yeşil nesnenin üstündeyim.'
    assert [(n, r.status) for n, _, r in result.calls] == [('list_objects', 'ok'),
                                                           ('go_above_object', 'ok')]
    assert above(rig) == [('go_above', 'green', 0.35)]
    # tool_use / tool_result pairing: every result answers the id the model used
    uses = [b['id'] for m in result.messages if m['role'] == 'assistant' for b in m['content']
            if isinstance(b, dict) and b['type'] == 'tool_use']
    answered = [b['tool_use_id'] for m in result.messages if m['role'] == 'user'
                and isinstance(m['content'], list) for b in m['content']]
    assert uses == answered == ['a', 'b']
    assert [e[1]['tool'] for e in events.events] == ['list_objects', 'go_above_object']
    assert llm.calls[0]['tools'][-1] == 'emergency_stop' and 'YALNIZCA' in llm.calls[0]['system']


# ---- invalid ----------------------------------------------------------------------------------
def test_invalid_request_is_rejected_and_the_model_can_correct_itself(rig):
    def corrected(messages):
        last = tool_results(messages)[-1]
        assert last['status'] == 'rejected' and 'purple' in last['message']
        return LLMResponse('Düzeltiyorum.', [call('go_above_object', {'color': 'red'}, 't2')])

    llm = ScriptedLLM(LLMResponse('', [call('go_above_object', {'color': 'purple'}, 't1')]),
                      corrected, LLMResponse('Kırmızıdayım.'))
    agent, _, _ = runner(rig, llm)
    result = agent.run('mor nesneye git')
    assert [r.status for _, _, r in result.calls] == ['rejected', 'ok']
    assert above(rig) == [('go_above', 'red', 0.35)]           # only the corrected call moved it
    assert not result.all_ok                                   # but the run did include a refusal


@pytest.mark.parametrize('name, arguments', [
    ('os_system', {'cmd': 'rm -rf /'}), ('reset', {}), ('go_above_object', {'color': 'red',
                                                                            'height_m': 'çok'}),
    ('go_above_object', {'color': 'red', 'height_m': 99}), ('run_demo_scenario', {'fast': True}),
])
def test_dangerous_or_malformed_tool_calls_never_reach_the_robot(rig, name, arguments):
    llm = ScriptedLLM(LLMResponse('', [call(name, arguments)]), LLMResponse('Olmadı.'))
    agent, _, _ = runner(rig, llm)
    result = agent.run('bir şey yap')
    assert result.calls[0][2].status == 'rejected'
    assert rig.skills.calls == [] and rig.jobs.recent() == [] and not rig.estop.is_set


# ---- unsafe -----------------------------------------------------------------------------------
def test_unsafe_target_is_refused_and_the_model_is_told_why():
    rig = Rig(zone=SafeZone((0.05, -0.5, 0.02), (0.75, 0.5, 0.2)))
    try:
        def gives_up(messages):
            last = tool_results(messages)[-1]
            assert last['status'] == 'refused'
            assert 'güvenli çalışma kutusunun dışında' in last['message']
            return LLMResponse('Güvenlik sınırı nedeniyle gidemiyorum.')

        agent, _, _ = runner(rig, ScriptedLLM(
            LLMResponse('', [call('go_above_object', {'color': 'green', 'height_m': 0.4})]),
            gives_up))
        result = agent.run('yeşilin çok yukarısına git')
        assert result.calls[0][2].status == 'refused' and above(rig) == []
        assert result.text == 'Güvenlik sınırı nedeniyle gidemiyorum.'
    finally:
        rig.close()


def test_a_refusal_is_not_retried_by_the_loop_itself(rig):
    llm = ScriptedLLM(LLMResponse('', [call('go_home')]), LLMResponse('Onay yok, yapmadım.'))
    agent, _, _ = runner(rig, llm, confirm=None)
    result = agent.run('home')
    assert [r.status for _, _, r in result.calls] == ['refused'] and rig.skills.calls == []
    assert len(llm.calls) == 2


def test_too_many_calls_in_one_reply_are_cut_off(rig):
    calls = [call('get_robot_state', id=f't{i}') for i in range(5)]
    agent, _, _ = runner(rig, ScriptedLLM(LLMResponse('', calls), LLMResponse('bitti')),
                         max_calls_per_step=3)
    result = agent.run('durumu 5 kez oku')
    assert [r.status for _, _, r in result.calls] == ['ok'] * 3 + ['refused'] * 2


def test_the_step_limit_ends_a_looping_model(rig):
    llm = ScriptedLLM(*[LLMResponse('', [call('get_robot_state', id=f't{i}')])
                        for i in range(20)])
    agent, _, _ = runner(rig, llm, max_steps=4)
    result = agent.run('dön dur')
    assert result.halted == 'adım sınırı' and len(llm.calls) == 4 and len(result.calls) == 4


# ---- emergency stop ------------------------------------------------------------------------------
def test_request_during_an_emergency_stop_is_refused_and_the_run_ends(rig):
    rig.supervisor.stop('insan')
    llm = ScriptedLLM(
        LLMResponse('Gidiyorum.', [call('go_home', id='t1'), call('go_above_object',
                                                                  {'color': 'red'}, 't2')]),
        LLMResponse('(buraya gelinmemeli)'))
    agent, _, _ = runner(rig, llm)
    result = agent.run('home sonra kırmızıya git')
    assert result.halted == 'acil dur' and len(llm.calls) == 1        # the model got no 2nd turn
    assert [r.status for _, _, r in result.calls] == ['estopped', 'estopped']
    assert 'yürütülmedi' in result.calls[1][2].message
    assert rig.skills.calls == [] and rig.estop.is_set
    assert 'Acil dur etkin' in result.text and 'Gidiyorum' not in result.text


def test_a_human_pressing_the_button_during_a_tool_call_ends_the_whole_run():
    rig = Rig(auto_release=False)
    try:
        llm = ScriptedLLM(
            LLMResponse('', [call('go_above_object', {'color': 'green'}, 't1')]),
            LLMResponse('(buraya gelinmemeli)'))
        agent, _, events = runner(rig, llm)
        box = []
        worker = threading.Thread(target=lambda: box.append(agent.run('yeşile git')))
        worker.start()
        assert rig.skills.entered.wait(5)
        rig.supervisor.stop('insan butonu')                           # the human
        worker.join(10)
        result = box[0]
        assert result.halted == 'acil dur' and result.calls[0][2].status == 'estopped'
        assert len(llm.calls) == 1 and rig.estop.is_set
        assert events.events[-1][1]['status'] == 'estopped'
    finally:
        rig.close()


def test_the_model_can_stop_the_robot_and_nothing_follows(rig):
    llm = ScriptedLLM(
        LLMResponse('Tehlikeli görünüyor, durduruyorum.', [call('emergency_stop',
                                                               {'reason': 'çarpışma riski'})]),
        LLMResponse('(buraya gelinmemeli)'))
    agent, _, _ = runner(rig, llm, confirm=None)
    result = agent.run('hızlıca her yere git')
    assert result.halted == 'acil dur' and result.calls[0][2].ok
    assert rig.estop.is_set and 'çarpışma riski' in rig.estop.info()['reason']
    assert len(llm.calls) == 1


def test_after_an_agent_stop_the_same_executor_never_moves_again(rig):
    agent, executor, _ = runner(rig, ScriptedLLM())
    executor.request_stop('test')
    rig.supervisor.reset()                       # even if a human unlocks the robot ...
    assert executor.execute('go_home', {}).status == 'estopped'    # ... this executor stays halted
    fresh = ToolExecutor(rig.robot, events=RecordingSink(), confirm=lambda r: True)
    assert fresh.execute('go_home', {}).ok       # a new run (new executor) starts clean


def test_events_record_every_call_in_order(rig):
    llm = ScriptedLLM(LLMResponse('', [call('go_home', id='1')]),
                      LLMResponse('', [call('exec', {}, '2')]), LLMResponse('bitti'))
    agent, _, events = runner(rig, llm)
    agent.run('x')
    assert [(e[1]['tool'], e[1]['status']) for e in events.events] == [('go_home', 'ok'),
                                                                        ('exec', 'rejected')]
