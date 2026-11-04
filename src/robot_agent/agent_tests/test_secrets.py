"""Canary: a fake key is pushed through the whole stack over real HTTP (fake provider server +
the real Robot API rig); then every place a key could leak is searched - CLI output, log records,
the event log, robot-side events, the model-bound tool results and every Robot API response."""
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from robot_agent.executor import ToolExecutor
from robot_agent.llm import AnthropicLLM, LLMError, OpenAILLM
from robot_agent.redact import MASK, forget_secrets, scrub
from tests.fakes import RecordingSink

from agent_tests.rig import Rig
from robot_agent import cli

CANARY = 'sk-CANARY-7f3a9c2e5b1d4068aa'
ANTHROPIC_CANARY = 'sk-ant-CANARY-91c0d2e8f7a64b35'


class Provider:
    """Fake OpenAI-compatible server. ``script`` = responses in order; ``fail`` = (status, body
    builder) that *echoes the bearer token* the way real providers do for a bad key."""

    def __init__(self, script=(), fail=None):
        self.script, self.fail, self.requests = list(script), fail, []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):    # noqa: N802
                body = self.rfile.read(int(self.headers['Content-Length']))
                auth = self.headers.get('Authorization', '')
                provider.requests.append((self.path, auth, json.loads(body)))
                if provider.fail:
                    status, text = provider.fail(auth)
                else:
                    status, text = 200, json.dumps(provider.script.pop(0))
                data = text.encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def tool_call_reply(*calls):
    return {'choices': [{'message': {'role': 'assistant', 'content': 'tamam', 'tool_calls': [
        {'id': f'c{i}', 'type': 'function', 'function': {'name': n, 'arguments': json.dumps(a)}}
        for i, (n, a) in enumerate(calls)]}}]}


def text_reply(text):
    return {'choices': [{'message': {'role': 'assistant', 'content': text}}]}


class LogCatcher(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


@pytest.fixture
def logs():
    handler = LogCatcher()
    root = logging.getLogger()
    old = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(old)


@pytest.fixture(autouse=True)
def _clean_registry():
    forget_secrets()
    yield
    forget_secrets()


def robot_api_dump(rig):
    """Every readable Robot API response, plus the robot-side event log."""
    pages = [rig.request_text('GET', p) for p in ('/api/state', '/api/jobs')]
    return '\n'.join(pages) + json.dumps(rig.sink.events, default=str)


def run_cli(monkeypatch, rig, provider, *request, extra=()):
    events = RecordingSink()
    monkeypatch.setenv('OPENAI_API_KEY', CANARY)
    monkeypatch.setenv('ROBOT_AGENT_OPENAI_MODEL', 'fake-model')
    monkeypatch.setenv('ROBOT_AGENT_OPENAI_BASE_URL', provider.url)
    monkeypatch.setattr(cli, '_events', lambda: events)
    out = io.StringIO()
    code = cli.main(['--provider', 'openai', '--yes', '--no-plugins', '--url', rig.robot.base_url,
                     *extra, *request], out=out)
    return code, out.getvalue(), events


def assert_no_leak(*surfaces):
    for text in surfaces:
        assert CANARY not in str(text), 'anahtar sızdı'
        assert 'CANARY' not in str(text), 'anahtar parçası sızdı'


def test_happy_path_sends_the_key_only_in_the_auth_header(monkeypatch, rig, logs):
    provider = Provider([tool_call_reply(('list_objects', {}), ('go_above_object',
                                                                {'color': 'green'})),
                         text_reply('Yeşilin üstündeyim.')])
    try:
        code, out, events = run_cli(monkeypatch, rig, provider, 'yeşilin üstüne git')
    finally:
        provider.close()
    assert code == 0 and 'Yeşilin üstündeyim.' in out
    assert [r[1] for r in provider.requests] == [f'Bearer {CANARY}'] * 2     # it WAS used
    bodies = json.dumps([r[2] for r in provider.requests])
    assert_no_leak(bodies, out, '\n'.join(logs.lines), events.events, robot_api_dump(rig))


@pytest.mark.parametrize('status,build', [
    (401, lambda auth: json.dumps({'error': {'message': f'Incorrect API key provided: '
                                             f'{auth[7:]}. You can find your key...'}})),
    (401, lambda auth: json.dumps({'error': {'message': f'bad key {auth[7:13]}****{auth[-4:]}'}})),
    (500, lambda auth: f'upstream failure, request header was "Authorization: {auth}"'),
    (429, lambda auth: f'x-api-key: {auth[7:]}'),
])
def test_provider_errors_that_echo_the_key_are_scrubbed(monkeypatch, rig, logs, status, build):
    provider = Provider(fail=lambda auth: (status, build(auth)))
    try:
        code, out, events = run_cli(monkeypatch, rig, provider, 'home')
    finally:
        provider.close()
    assert code == 2 and 'LLM hatası' in out and f'HTTP {status}' in out
    assert_no_leak(out, '\n'.join(logs.lines), events.events, robot_api_dump(rig))
    assert provider.requests[0][1] == f'Bearer {CANARY}'


def test_exception_text_of_the_llm_error_is_clean(rig):
    provider = Provider(fail=lambda auth: (401, f'key {auth[7:]} is wrong'))
    llm = OpenAILLM(CANARY, 'm', provider.url)
    try:
        with pytest.raises(LLMError) as info:
            llm.complete('s', [{'role': 'user', 'content': 'x'}], [])
    finally:
        provider.close()
    assert CANARY not in str(info.value) and MASK in str(info.value)
    assert CANARY not in repr(llm) and CANARY not in repr(info.value)


def test_unreachable_provider_error_has_no_key(monkeypatch, rig, logs):
    provider = Provider()
    provider.close()
    code, out, events = run_cli(monkeypatch, rig, provider, 'home')
    assert code == 2 and 'yanıt vermedi' in out
    assert_no_leak(out, '\n'.join(logs.lines))


def test_anthropic_errors_are_scrubbed_too():
    def post(body):
        raise LLMError('x')
    llm = AnthropicLLM(ANTHROPIC_CANARY)
    import urllib.error

    class Boom(urllib.error.HTTPError):
        def __init__(self):
            super().__init__('u', 401, 'm', {}, io.BytesIO(
                json.dumps({'error': f'invalid x-api-key {ANTHROPIC_CANARY}'}).encode()))
    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(Boom())
    try:
        with pytest.raises(LLMError) as info:
            llm.complete('s', [{'role': 'user', 'content': 'x'}], [])
    finally:
        urllib.request.urlopen = original
    assert 'CANARY' not in str(info.value) and 'HTTP 401' in str(info.value)


def test_a_key_the_model_echoes_into_a_tool_argument_goes_nowhere(monkeypatch, rig, logs):
    """Prompt text / model output containing the key: it must not reach events or the robot."""
    provider = Provider([tool_call_reply(('emergency_stop', {'reason': f'anahtar {CANARY}'})),
                         text_reply('bitti')])
    try:
        code, out, events = run_cli(monkeypatch, rig, provider, f'dur, anahtarım {CANARY}')
    finally:
        provider.close()
    assert code == 3
    assert_no_leak(out, '\n'.join(logs.lines), events.events, robot_api_dump(rig))
    assert MASK in json.dumps(events.events, ensure_ascii=False)


def test_tool_results_shown_to_the_model_are_scrubbed(rig):
    executor = ToolExecutor(rig.robot, events=RecordingSink())
    OpenAILLM(CANARY, 'm', 'https://x.example.test/v1')          # registers the key
    result = executor.execute('emergency_stop', {'reason': CANARY})
    assert CANARY not in result.to_llm() and CANARY not in result.message


def test_log_records_are_scrubbed_everywhere(logs):
    OpenAILLM(CANARY, 'm', 'https://x.example.test/v1')
    logging.getLogger('some.other.module').error('boom %s and %s', CANARY, {'k': CANARY})
    logging.getLogger('robot_core.x').warning('Authorization: Bearer %s', 'abc123def456')
    assert logs.lines and all('CANARY' not in line and 'abc123def456' not in line
                              for line in logs.lines)


def test_scrub_generic_shapes_and_structures():
    assert scrub('Bearer abcdef123456') == f'Bearer {MASK}'
    assert scrub('x-api-key: hunter2hunter2') == f'x-api-key: {MASK}'
    assert scrub('sk-proj-AbC_123-xyz') == MASK
    assert scrub({'a': ['sk-live-abcdef12'], 'sk-zzzzzzzz': 1, 'n': 5}) == {
        'a': [MASK], MASK: 1, 'n': 5}
    assert scrub('normal text, risk-averse sky-high') == 'normal text, risk-averse sky-high'
    assert scrub(None) is None and scrub(3.5) == 3.5
