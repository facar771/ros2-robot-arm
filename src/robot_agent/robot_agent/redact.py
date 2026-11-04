"""Key hygiene: API keys must never reach a log line, an event, an error text or the robot API.

Two layers, both cheap: (1) every key the process has *registered* (the LLM adapters do it) is
replaced by ``[GİZLİ]`` wherever it appears; (2) generic key shapes (``sk-...``, ``Bearer ...``,
``x-api-key: ...``) are masked even if they were never registered - providers echo a *masked*
copy of a bad key in their 401 bodies, and a person may paste a key into a request.
``install_log_scrubbing`` makes this hold for every ``logging`` record in the process."""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Set

MASK = '[GİZLİ]'
MIN_SECRET = 6          # shorter "keys" would mask ordinary words
_SECRETS: Set[str] = set()
_LOCK = threading.Lock()
_PATTERNS = (
    re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-*]{4,}'),
    re.compile(r'(?i)\b(x-api-key|api[-_ ]?key|authorization)(["\']?\s*[:=]\s*["\']?)[^\s"\',;}]+'),
    re.compile(r'\bsk-[A-Za-z0-9_\-*]{4,}'),
)
_installed = False


def register_secret(secret: str) -> None:
    secret = (secret or '').strip()
    if len(secret) >= MIN_SECRET:
        with _LOCK:
            _SECRETS.add(secret)


def forget_secrets() -> None:        # tests
    with _LOCK:
        _SECRETS.clear()


def scrub(value: Any) -> Any:
    """Mask keys in a string, or recursively in dict/list/tuple values (keys of dicts too)."""
    if isinstance(value, str):
        with _LOCK:
            known = sorted(_SECRETS, key=len, reverse=True)
        for secret in known:
            value = value.replace(secret, MASK)
        value = _PATTERNS[0].sub(lambda m: f'{m.group(1)} {MASK}', value)
        value = _PATTERNS[1].sub(lambda m: f'{m.group(1)}{m.group(2)}{MASK}', value)
        return _PATTERNS[2].sub(MASK, value)
    if isinstance(value, dict):
        return {scrub(k) if isinstance(k, str) else k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def install_log_scrubbing() -> None:
    """Wrap the record factory once: every record's message is scrubbed when it is created."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.msg = scrub(record.getMessage())
            record.args = ()
        except Exception:    # noqa: BLE001 - never break logging
            pass
        return record
    logging.setLogRecordFactory(factory)
    _installed = True


class ScrubbingSink:
    """EventSink wrapper: payloads are scrubbed before they are stored."""

    def __init__(self, inner):
        self._inner = inner

    def emit(self, kind: str, payload: Any = None, *args, **kwargs):
        return self._inner.emit(kind, scrub(payload), *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)
