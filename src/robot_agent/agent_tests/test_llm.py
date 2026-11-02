"""RuleLLM (offline parser) and the optional Anthropic adapter (HTTP injected, no network)."""
import io
import json
import urllib.error
import urllib.request

import pytest
from robot_agent.llm import (
    DEFAULT_MODEL,
    AnthropicLLM,
    LLMError,
    LLMResponse,
    RuleLLM,
    ScriptedLLM,
)


def ask(text):
    return RuleLLM().complete('', [{'role': 'user', 'content': text}], [])


@pytest.mark.parametrize('text, tool, arguments', [
    ('yeşil nesnenin üstüne git', 'go_above_object', {'color': 'green'}),
    ('Yeşil nesneye git', 'go_above_object', {'color': 'green'}),
    ('KIRMIZI kutunun üstüne git', 'go_above_object', {'color': 'red'}),
    ('mavi topun 40 cm üstüne git', 'go_above_object', {'color': 'blue', 'height_m': 0.4}),
    ('mavi topun 0,3 m üstüne git', 'go_above_object', {'color': 'blue', 'height_m': 0.3}),
    ('home', 'go_home', {}), ('eve dön', 'go_home', {}),
    ('demo senaryosunu çalıştır', 'run_demo_scenario', {}),
    ('nesneleri listele', 'list_objects', {}),
    ('acil dur', 'emergency_stop', {'reason': 'kullanıcı isteği'}),
    ('DUR', 'emergency_stop', {'reason': 'kullanıcı isteği'}),
])
def test_phrases(text, tool, arguments):
    response = ask(text)
    assert [(c.name, c.arguments) for c in response.tool_calls] == [(tool, arguments)]


def test_stop_wins_over_everything_else():
    assert ask('yeşile git ve dur').tool_calls[0].name == 'emergency_stop'
    assert ask('demo başlat ama acil dur').tool_calls[0].name == 'emergency_stop'


def test_unknown_requests_move_nothing():
    response = ask('bana bir şiir yaz')
    assert response.tool_calls == []
    assert 'anlayamadım' in response.text and 'hareket ettirmedim' in response.text


def test_after_a_tool_result_it_reports_it():
    messages = [{'role': 'user', 'content': 'home'},
                {'role': 'assistant', 'content': []},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't',
                                              'content': json.dumps({'ok': False,
                                                                     'message': 'meşgul'})}]}]
    assert RuleLLM().complete('', messages, []).text == 'Olmadı: meşgul'


def test_scripted_llm_records_and_ends_politely():
    llm = ScriptedLLM(LLMResponse('bir'), lambda messages: LLMResponse(f'{len(messages)} mesaj'))
    assert llm.complete('s', [{'role': 'user', 'content': 'x'}], [{'name': 'a'}]).text == 'bir'
    assert llm.complete('s', [{}, {}], []).text == '2 mesaj'
    assert llm.complete('s', [], []).text == '(betik bitti)' and len(llm.calls) == 3


# ---- the optional provider adapter -------------------------------------------------------------
def test_without_a_key_the_layer_is_off():
    assert AnthropicLLM.from_env({}) is None
    assert AnthropicLLM.from_env({'ANTHROPIC_API_KEY': ' '}) is None
    with pytest.raises(ValueError):
        AnthropicLLM('')


def test_request_and_response_shape():
    seen = {}

    def post(body):
        seen.update(body)
        return {'content': [{'type': 'text', 'text': 'Tamam.'},
                            {'type': 'tool_use', 'id': 'toolu_1', 'name': 'go_home', 'input': {}}]}

    llm = AnthropicLLM.from_env({'ANTHROPIC_API_KEY': 'sk-ant-secret'}, post=post)
    tools = [{'name': 'go_home', 'description': 'd', 'input_schema': {'type': 'object'}}]
    response = llm.complete('sistem', [{'role': 'user', 'content': 'home'}], tools)
    assert seen['model'] == DEFAULT_MODEL and seen['system'] == 'sistem' and seen['tools'] == tools
    assert seen['tool_choice'] == {'type': 'auto'} and seen['max_tokens'] > 0
    assert 'sk-ant-secret' not in json.dumps(seen)                  # the key is a header only
    assert response.text == 'Tamam.'
    calls = [(c.id, c.name, c.arguments) for c in response.tool_calls]
    assert calls == [('toolu_1', 'go_home', {})]


def test_the_model_can_be_chosen():
    assert AnthropicLLM('k', 'claude-x').model == 'claude-x'


def test_a_bad_answer_is_an_error():
    llm = AnthropicLLM('k', post=lambda body: {'error': 'x'})
    with pytest.raises(LLMError):
        llm.complete('', [], [])


def test_http_errors_become_llm_errors_without_leaking_the_key(monkeypatch):
    def fail(request, timeout):
        body = io.BytesIO(b'invalid x-api-key')
        raise urllib.error.HTTPError(request.full_url, 401, 'no', {}, body)

    monkeypatch.setattr(urllib.request, 'urlopen', fail)
    with pytest.raises(LLMError) as err:
        AnthropicLLM('sk-ant-secret').complete('', [{'role': 'user', 'content': 'x'}], [])
    assert '401' in str(err.value) and 'sk-ant-secret' not in str(err.value)

    def down(request, timeout):
        raise urllib.error.URLError('dns')

    monkeypatch.setattr(urllib.request, 'urlopen', down)
    with pytest.raises(LLMError, match='yanıt vermedi'):
        AnthropicLLM('k').complete('', [], [])


def test_the_request_goes_to_the_messages_endpoint_with_the_key_in_a_header(monkeypatch):
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake(request, timeout):
        captured['url'], captured['headers'] = request.full_url, dict(request.header_items())
        captured['body'] = json.loads(request.data)
        return Response(json.dumps({'content': [{'type': 'text', 'text': 'merhaba'}]}).encode())

    monkeypatch.setattr(urllib.request, 'urlopen', fake)
    assert AnthropicLLM('sk-ant-k').complete('s', [], []).text == 'merhaba'
    assert captured['url'] == 'https://api.anthropic.com/v1/messages'
    assert captured['headers']['X-api-key'] == 'sk-ant-k'
    assert 'sk-ant-k' not in json.dumps(captured['body'])
