"""LLM back-ends: ``ScriptedLLM`` (tests/CI, no key), ``RuleLLM`` (offline Turkish phrase parser,
not an LLM), ``AnthropicLLM`` (optional; ``ANTHROPIC_API_KEY``) and ``OpenAILLM`` (optional;
``OPENAI_API_KEY`` + model + endpoint from configuration). Stdlib HTTP only; keys are registered
with ``robot_agent.redact`` so they never appear in an error text, log line or event."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Union
from urllib.parse import urlsplit

from robot_agent.redact import install_log_scrubbing, register_secret, scrub

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MODEL = 'claude-sonnet-5-5'
ENV_KEY = 'ANTHROPIC_API_KEY'
ENV_MODEL = 'ROBOT_AGENT_MODEL'
# OpenAI: there is deliberately NO built-in model name or endpoint - both are configuration.
OPENAI_ENV_KEY = 'OPENAI_API_KEY'
OPENAI_ENV_MODEL = 'ROBOT_AGENT_OPENAI_MODEL'
OPENAI_ENV_URL = 'ROBOT_AGENT_OPENAI_BASE_URL'      # e.g. the provider's .../v1 base address


class LLMError(RuntimeError):
    """The model could not be reached or answered something unusable (never contains the key)."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any


@dataclass(frozen=True)
class LLMResponse:
    text: str = ''
    tool_calls: List[ToolCall] = field(default_factory=list)


class LLM(Protocol):
    def complete(self, system: str, messages: List[Dict[str, Any]],
                 tools: List[Dict[str, Any]]) -> LLMResponse: ...


# ---- scripted (tests) ----------------------------------------------------------------------
Step = Union[LLMResponse, Callable[[List[Dict[str, Any]]], LLMResponse]]


class ScriptedLLM:
    """Plays a fixed script, one step per ``complete`` call; a step may be a function of the
    conversation so far. After the script it just says it is done. Records what it was shown."""

    def __init__(self, *steps: Step):
        self._steps = list(steps)
        self.calls: List[Dict[str, Any]] = []

    def complete(self, system, messages, tools) -> LLMResponse:
        self.calls.append({'system': system, 'messages': json.loads(json.dumps(messages)),
                           'tools': [t['name'] for t in tools]})
        if not self._steps:
            return LLMResponse('(betik bitti)')
        step = self._steps.pop(0)
        return step(messages) if callable(step) else step


def call(name: str, arguments: Any = None, id: str = 't1') -> ToolCall:     # noqa: A002
    return ToolCall(id, name, {} if arguments is None else arguments)


# ---- offline phrase parser -----------------------------------------------------------------
_COLORS = {'kırmızı': 'red', 'kirmizi': 'red', 'yeşil': 'green', 'yesil': 'green', 'mavi': 'blue'}


def _norm(text: str) -> str:
    return text.replace('İ', 'i').replace('I', 'ı').lower()


class RuleLLM:
    """Understands a handful of Turkish phrases and answers with tool calls; after the result it
    reports it. **Not a language model**: it exists so the CLI works without an API key."""

    def complete(self, system, messages, tools) -> LLMResponse:
        last = messages[-1]
        if last['role'] == 'user' and isinstance(last['content'], list):    # tool results came back
            results = [json.loads(b['content']) for b in last['content']
                       if b.get('type') == 'tool_result']
            lines = [f"{'Tamam' if r['ok'] else 'Olmadı'}: {r['message']}" for r in results]
            return LLMResponse(' '.join(lines) or 'Bitti.')
        text = _norm(last['content'] if isinstance(last['content'], str) else '')
        words = set(re.findall(r'[\wçğıöşü]+', text))
        if words & {'acil', 'dur', 'durdur', 'stop'}:
            return LLMResponse('Acil durdur.',
                               [call('emergency_stop', {'reason': 'kullanıcı isteği'})])
        color_found = next((c for w, c in _COLORS.items()
                            if w in words or any(x.startswith(w) for x in words)), None)
        if color_found and any(x.startswith('takip') or x.startswith('takib') for x in words):
            args = {'color': color_found}
            seconds = re.search(r'(\d+(?:[.,]\d+)?)\s*(saniye|sn|s)\b', text)
            if seconds:
                args['duration_s'] = float(seconds.group(1).replace(',', '.'))
            return LLMResponse(f'{color_found} nesneyi takip ediyorum.',
                               [call('run_plugin', {'plugin': 'follow_color',
                                                    'tool': 'follow_color', 'arguments': args})])
        if color_found and words & {'bul', 'ara', 'bak', 'görüyor', 'görüyormusun'}:
            return LLMResponse(f'{color_found} nesneyi arıyorum.',
                               [call('run_plugin', {'plugin': 'find_object', 'tool': 'find_object',
                                                    'arguments': {'color': color_found}})])
        if 'tara' in words or 'tarama' in words:
            return LLMResponse('Etrafı tarıyorum.', [call('run_plugin', {
                'plugin': 'scan_extent', 'tool': 'scan_extent', 'arguments': {}})])
        for word, color in _COLORS.items():
            if word in words or any(w.startswith(word) for w in words):
                args: Dict[str, Any] = {'color': color}
                match = re.search(r'(\d+(?:[.,]\d+)?)\s*(cm|santim|m)\b', text)
                if match:
                    value = float(match.group(1).replace(',', '.'))
                    args['height_m'] = value / 100 if match.group(2) != 'm' else value
                return LLMResponse(f'{color} nesnenin üstüne gidiyorum.',
                                   [call('go_above_object', args)])
        if words & {'demo', 'senaryo', 'senaryoyu'}:
            return LLMResponse('Demo senaryosunu başlatıyorum.', [call('run_demo_scenario')])
        if words & {'home', 'eve', 'başlangıç', 'ev'}:
            return LLMResponse('Home pozuna gidiyorum.', [call('go_home')])
        if words & {'nesne', 'nesneler', 'listele', 'ne'}:
            return LLMResponse('Nesnelere bakıyorum.', [call('list_objects')])
        return LLMResponse('Bu isteği anlayamadım; robotu hareket ettirmedim. Örnek: "yeşil '
                           'nesnenin üstüne git", "home", "nesneleri listele", "acil dur".')


# ---- Anthropic Messages API ----------------------------------------------------------------
class AnthropicLLM:
    """Optional provider adapter. ``from_env`` returns None when ``ANTHROPIC_API_KEY`` is unset, so
    the layer is simply *off* without a key. ``post`` is injectable for tests (no network in CI)."""

    def __init__(self, api_key: str, model: Optional[str] = None, *, max_tokens: int = 1024,
                 timeout: float = 60.0, post: Optional[Callable[..., Dict[str, Any]]] = None):
        if not api_key:
            raise ValueError('API anahtarı boş')
        self._key = api_key
        register_secret(api_key)
        install_log_scrubbing()
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        self._max_tokens, self._timeout = max_tokens, timeout
        self._post = post or self._http_post

    def __repr__(self) -> str:
        return f'AnthropicLLM(model={self.model!r})'

    @classmethod
    def from_env(cls, environ=os.environ, **kwargs) -> Optional['AnthropicLLM']:
        key = environ.get(ENV_KEY, '').strip()
        return cls(key, **kwargs) if key else None

    def _http_post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            API_URL, data=json.dumps(body).encode(), method='POST',
            headers={'content-type': 'application/json', 'x-api-key': self._key,
                     'anthropic-version': API_VERSION})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = scrub(exc.read()[:300].decode('utf-8', 'replace'))
            raise LLMError(f'Anthropic API HTTP {exc.code}: {detail}') from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LLMError(f'Anthropic API yanıt vermedi: {type(exc).__name__}') from None

    def complete(self, system, messages, tools) -> LLMResponse:
        body = {'model': self.model, 'max_tokens': self._max_tokens, 'system': system,
                'messages': messages, 'tools': tools, 'tool_choice': {'type': 'auto'}}
        data = self._post(body)
        blocks = data.get('content')
        if not isinstance(blocks, list):
            raise LLMError('Anthropic yanıtında content yok')
        text = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text')
        calls = [ToolCall(str(b.get('id')), str(b.get('name')), b.get('input'))
                 for b in blocks if b.get('type') == 'tool_use']
        return LLMResponse(text, calls)


# ---- OpenAI Chat Completions ---------------------------------------------------------------
def _strict_json(text: str) -> Any:
    """json.loads that refuses NaN / Infinity / 1e999 (model-written arguments become motion)."""
    def bad(token):
        raise ValueError(f'sonlu olmayan sayı: {token}')

    def real(token):
        value = float(token)
        if value in (float('inf'), float('-inf')):
            raise ValueError(f'sonlu olmayan sayı: {token}')
        return value
    return json.loads(text, parse_constant=bad, parse_float=real)


def _to_openai_messages(system: str, messages: List[Dict[str, Any]],
                        system_role: str) -> List[Dict[str, Any]]:
    """Our conversation (text / tool_use / tool_result blocks) -> Chat Completions messages."""
    out: List[Dict[str, Any]] = [{'role': system_role, 'content': system}]
    for message in messages:
        content = message.get('content')
        if message['role'] == 'user':
            if isinstance(content, str):
                out.append({'role': 'user', 'content': content})
                continue
            for block in content or []:
                if block.get('type') == 'tool_result':
                    out.append({'role': 'tool', 'tool_call_id': str(block.get('tool_use_id')),
                                'content': str(block.get('content', ''))})
                elif block.get('type') == 'text':
                    out.append({'role': 'user', 'content': block.get('text', '')})
            continue
        if isinstance(content, str):
            out.append({'role': 'assistant', 'content': content})
            continue
        text = ''.join(b.get('text', '') for b in content or [] if b.get('type') == 'text')
        calls = [{'id': str(b.get('id')), 'type': 'function',
                  'function': {'name': str(b.get('name')),
                               'arguments': json.dumps(b.get('input') or {}, ensure_ascii=False)}}
                 for b in content or [] if b.get('type') == 'tool_use']
        entry: Dict[str, Any] = {'role': 'assistant', 'content': text or None}
        if calls:
            entry['tool_calls'] = calls
        out.append(entry)
    return out


def _to_openai_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{'type': 'function', 'function': {
        'name': t['name'], 'description': t.get('description', ''),
        'parameters': t.get('input_schema') or {'type': 'object', 'properties': {}}}}
        for t in tools]


class OpenAILLM:
    """Optional provider adapter for the Chat Completions API (``POST {base_url}/chat/completions``,
    ``Authorization: Bearer``). The model name and the base address are *configuration* - there is
    no default for either. A non-HTTPS address is accepted only on loopback (a key must not cross
    a network in clear text). ``post`` is injectable for tests."""

    def __init__(self, api_key: str, model: str, base_url: str, *, max_tokens: int = 1024,
                 timeout: float = 60.0, system_role: str = 'system',
                 token_field: str = 'max_completion_tokens',
                 post: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None):
        if not api_key:
            raise ValueError('API anahtarı boş')
        if not model:
            raise ValueError(f'model adı yapılandırılmamış ({OPENAI_ENV_MODEL} ya da --model)')
        parts = urlsplit(base_url or '')
        loopback = parts.hostname in ('127.0.0.1', 'localhost', '::1')
        if not base_url or not parts.hostname or parts.scheme not in ('https', 'http'):
            raise ValueError(f'uç adresi yapılandırılmamış ({OPENAI_ENV_URL} ya da --base-url)')
        if parts.scheme != 'https' and not loopback:
            raise ValueError('uç adresi https olmalı (http yalnızca yerel makinede)')
        if parts.username or parts.password:
            raise ValueError('uç adresinde kullanıcı/parola olmamalı')
        self._key = api_key
        register_secret(api_key)
        install_log_scrubbing()
        self.model, self.base_url = model, base_url.rstrip('/')
        self._max_tokens, self._timeout = max_tokens, timeout
        self._system_role, self._token_field = system_role, token_field
        self._post = post or self._http_post

    def __repr__(self) -> str:
        return f'OpenAILLM(model={self.model!r}, base_url={self.base_url!r})'

    @classmethod
    def from_env(cls, environ=os.environ, *, model: Optional[str] = None,
                 base_url: Optional[str] = None, **kwargs) -> Optional['OpenAILLM']:
        """None when ``OPENAI_API_KEY`` is unset (layer off); ValueError when it is set but the
        model or the address is missing - a half-configured provider must not guess."""
        key = environ.get(OPENAI_ENV_KEY, '').strip()
        if not key:
            return None
        return cls(key, model or environ.get(OPENAI_ENV_MODEL, '').strip(),
                   base_url or environ.get(OPENAI_ENV_URL, '').strip(), **kwargs)

    def _http_post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            f'{self.base_url}/chat/completions', data=json.dumps(body).encode(), method='POST',
            headers={'content-type': 'application/json',
                     'authorization': f'Bearer {self._key}'})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return _strict_json(response.read())
        except urllib.error.HTTPError as exc:
            raw = exc.read()[:600].decode('utf-8', 'replace')
            try:
                detail = _strict_json(raw)['error']['message']
            except (ValueError, KeyError, TypeError):
                detail = raw
            raise LLMError(f'OpenAI API HTTP {exc.code}: {scrub(str(detail))[:300]}') from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LLMError(f'OpenAI API yanıt vermedi: {type(exc).__name__}') from None

    def complete(self, system, messages, tools) -> LLMResponse:
        body: Dict[str, Any] = {
            'model': self.model, self._token_field: self._max_tokens,
            'messages': _to_openai_messages(system, messages, self._system_role)}
        if tools:
            body['tools'] = _to_openai_tools(tools)
            body['tool_choice'] = 'auto'
        data = self._post(body)
        try:
            message = data['choices'][0]['message']
        except (KeyError, IndexError, TypeError):
            raise LLMError('OpenAI yanıtında choices[0].message yok') from None
        text = message.get('content') or message.get('refusal') or ''
        calls: List[ToolCall] = []
        for index, item in enumerate(message.get('tool_calls') or []):
            if not isinstance(item, dict) or item.get('type', 'function') != 'function':
                continue
            function = item.get('function') or {}
            try:
                arguments = _strict_json(function.get('arguments') or '{}')
            except ValueError:
                arguments = None            # the executor rejects a non-object argument
            calls.append(ToolCall(str(item.get('id') or f'call_{index}'),
                                  str(function.get('name')), arguments))
        return LLMResponse(text if isinstance(text, str) else '', calls)
