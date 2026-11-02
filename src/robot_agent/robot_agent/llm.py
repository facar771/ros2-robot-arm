"""LLM back-ends: ``ScriptedLLM`` (tests/CI, no key), ``RuleLLM`` (offline Turkish phrase parser,
not an LLM) and ``AnthropicLLM`` (optional; needs ``ANTHROPIC_API_KEY``, stdlib HTTP only)."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Union

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MODEL = 'claude-sonnet-5-5'
ENV_KEY = 'ANTHROPIC_API_KEY'
ENV_MODEL = 'ROBOT_AGENT_MODEL'


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
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        self._max_tokens, self._timeout = max_tokens, timeout
        self._post = post or self._http_post

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
            detail = exc.read()[:300].decode('utf-8', 'replace')
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
