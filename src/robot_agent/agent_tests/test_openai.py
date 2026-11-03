"""OpenAILLM: translation of our conversation/tools, configuration (no built-in model/endpoint),
response parsing. HTTP is injected; the real HTTP path is covered in test_secrets.py."""
import json
from pathlib import Path

import pytest
from robot_agent.agent import AgentRunner
from robot_agent.executor import ToolExecutor
from robot_agent.llm import LLMError, LLMResponse, OpenAILLM, ScriptedLLM, _strict_json, call
from tests.fakes import RecordingSink

from agent_tests.rig import Rig

URL = 'https://llm.example.test/v1'


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def make(post, **kw):
    return OpenAILLM('sk-test-123456', 'model-x', URL, post=post, **kw)


def reply(message, finish='stop'):
    return {'choices': [{'index': 0, 'message': message, 'finish_reason': finish}]}


# ---- configuration: nothing is built in ----------------------------------------------------
def test_no_model_name_or_endpoint_is_hard_coded():
    src = Path(__file__).parents[1] / 'robot_agent'
    text = '\n'.join(p.read_text() for p in src.glob('*.py'))
    assert 'api.openai.com' not in text and 'gpt-' not in text


def test_from_env_off_without_key_and_strict_when_half_configured():
    assert OpenAILLM.from_env({}) is None
    assert OpenAILLM.from_env({'OPENAI_API_KEY': '  '}) is None
    with pytest.raises(ValueError, match='model'):
        OpenAILLM.from_env({'OPENAI_API_KEY': 'sk-abcdef', 'ROBOT_AGENT_OPENAI_BASE_URL': URL})
    with pytest.raises(ValueError, match='uç adresi'):
        OpenAILLM.from_env({'OPENAI_API_KEY': 'sk-abcdef', 'ROBOT_AGENT_OPENAI_MODEL': 'm'})
    llm = OpenAILLM.from_env({'OPENAI_API_KEY': 'sk-abcdef', 'ROBOT_AGENT_OPENAI_MODEL': 'm',
                              'ROBOT_AGENT_OPENAI_BASE_URL': URL + '/'})
    assert (llm.model, llm.base_url) == ('m', URL)
    assert OpenAILLM.from_env({'OPENAI_API_KEY': 'sk-abcdef'}, model='a', base_url=URL).model == 'a'


@pytest.mark.parametrize('url', ['http://llm.example.test/v1', 'ftp://x/v1', 'llm.example.test',
                                 'https://user:pw@llm.example.test/v1', ''])
def test_bad_endpoints_are_refused(url):
    with pytest.raises(ValueError):
        OpenAILLM('sk-abcdef', 'm', url)


def test_plain_http_is_allowed_on_loopback_only():
    assert OpenAILLM('sk-abcdef', 'm', 'http://127.0.0.1:9/v1').base_url == 'http://127.0.0.1:9/v1'
    with pytest.raises(ValueError):
        OpenAILLM('', 'm', URL)


def test_repr_never_shows_the_key():
    assert 'sk-test-123456' not in repr(make(lambda body: {}))


# ---- request translation --------------------------------------------------------------------
def test_conversation_and_tools_are_translated(rig):
    seen = []

    def post(body):
        seen.append(body)
        if len(seen) == 1:
            return reply({'role': 'assistant', 'content': 'Bakıyorum.', 'tool_calls': [
                {'id': 'call_1', 'type': 'function',
                 'function': {'name': 'list_objects', 'arguments': '{}'}}]}, 'tool_calls')
        return reply({'role': 'assistant', 'content': 'Üç nesne var.'})
    executor = ToolExecutor(rig.robot, events=RecordingSink())
    result = AgentRunner(make(post), executor).run('nesneleri say')
    assert result.text == 'Üç nesne var.' and result.all_ok
    first, second = seen
    assert first['model'] == 'model-x' and first['tool_choice'] == 'auto'
    assert first['max_completion_tokens'] == 1024 and 'max_tokens' not in first
    assert first['messages'][0]['role'] == 'system'
    assert first['messages'][1] == {'role': 'user', 'content': 'nesneleri say'}
    names = [t['function']['name'] for t in first['tools']]
    assert names == [s['name'] for s in executor.specs()] and 'emergency_stop' in names
    assert all(t['type'] == 'function' and t['function']['parameters']['type'] == 'object'
               for t in first['tools'])
    assert 'reset' not in names
    roles = [m['role'] for m in second['messages']]
    assert roles == ['system', 'user', 'assistant', 'tool']
    assistant, tool = second['messages'][2:]
    assert assistant['tool_calls'][0]['id'] == tool['tool_call_id'] == 'call_1'
    assert assistant['tool_calls'][0]['function']['arguments'] == '{}'
    assert json.loads(tool['content'])['ok'] is True


def test_system_role_and_token_field_are_configurable():
    seen = []
    llm = make(lambda b: (seen.append(b), reply({'content': 'x'}))[1], system_role='developer',
               token_field='max_tokens')
    llm.complete('kural', [{'role': 'user', 'content': 'a'}], [])
    assert seen[0]['messages'][0] == {'role': 'developer', 'content': 'kural'}
    assert seen[0]['max_tokens'] == 1024 and 'tools' not in seen[0]


# ---- response parsing -------------------------------------------------------------------------
def test_tool_calls_are_parsed_and_unusable_arguments_do_not_become_motion():
    msg = {'content': None, 'tool_calls': [
        {'id': 'a', 'type': 'function',
         'function': {'name': 'go_above_object', 'arguments': '{"color": "red"}'}},
        {'id': 'b', 'type': 'function',
         'function': {'name': 'go_above_object', 'arguments': '{"height_m": NaN}'}},
        {'id': 'c', 'type': 'function',
         'function': {'name': 'go_above_object', 'arguments': '{"height_m": 1e999}'}},
        {'id': 'd', 'type': 'function', 'function': {'name': 'go_home', 'arguments': 'not json'}},
        {'id': 'e', 'type': 'custom', 'custom': {'name': 'x', 'input': 'y'}},
        {'type': 'function', 'function': {'name': 'go_home'}}]}
    out = make(lambda b: reply(msg, 'tool_calls')).complete('s', [], [])
    assert [(c.id, c.name, c.arguments) for c in out.tool_calls] == [
        ('a', 'go_above_object', {'color': 'red'}), ('b', 'go_above_object', None),
        ('c', 'go_above_object', None), ('d', 'go_home', None), ('call_5', 'go_home', {})]


def test_refusal_text_and_missing_choices():
    assert make(lambda b: reply({'content': None, 'refusal': 'olmaz'})).complete(
        's', [], []).text == 'olmaz'
    with pytest.raises(LLMError):
        make(lambda b: {'choices': []}).complete('s', [], [])
    with pytest.raises(LLMError):
        make(lambda b: {'error': 'x'}).complete('s', [], [])


def test_strict_json_helper():
    assert _strict_json('{"a": [1, 2.5]}') == {'a': [1, 2.5]}
    for bad in ('NaN', 'Infinity', '-Infinity', '1e999'):
        with pytest.raises(ValueError):
            _strict_json('[%s]' % bad)


def test_scripted_and_openai_see_the_same_tool_list(rig):
    executor = ToolExecutor(rig.robot)
    scripted = ScriptedLLM(LLMResponse('x', [call('list_objects')]))
    AgentRunner(scripted, executor, max_steps=1).run('a')
    assert scripted.calls[0]['tools'] == [s['name'] for s in executor.specs()]
