"""Ajanın önüne konan korumalar: günlük LLM sayacı, iptal denetimli LLM, ACİL DUR'u Hub'a bağlayan port
(R10: service.py'den ayrıldı; davranış aynı)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from control_hub.agent.errors import Cancelled, LimitReached
from control_hub.hub_core import Hub


class UsageCounter:
    """Günlük LLM isteği sayacı (bellekte; süreç yeniden başlayınca sıfırlanır — bir maliyet freni,
    güvenlik sınırı değil)."""

    def __init__(self, limit: int, today: Callable[[], str] = lambda: time.strftime('%Y-%m-%d')):
        self.limit, self._today = limit, today
        self._day, self.used = today(), 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            day = self._today()
            if day != self._day:
                self._day, self.used = day, 0
            if self.used >= self.limit:
                raise LimitReached
            self.used += 1

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            if self._today() != self._day:
                self._day, self.used = self._today(), 0
            return {'daily_limit': self.limit, 'used_today': self.used}


class GuardedLLM:
    """Her LLM isteği: önce iptal bayrağı, sonra günlük sayaç."""

    def __init__(self, inner: Any, cancelled: threading.Event, counter: Optional[UsageCounter]):
        self._inner, self._cancelled, self._counter = inner, cancelled, counter

    def complete(self, system, messages, tools):
        if self._cancelled.is_set():
            raise Cancelled
        if self._counter is not None:
            self._counter.take()
        return self._inner.complete(system, messages, tools)


class GuardedPort:
    """Ajanın robota giden portu: ACİL DUR çağrısı önce Hub kilidini de koyar (tek gerçek, tek kilit)."""

    def __init__(self, inner: Any, hub: Hub):
        self._inner, self._hub = inner, hub
        self.base_url = getattr(inner, 'base_url', None)

    def get(self, path: str):
        return self._inner.get(path)

    def post(self, path: str, payload: Optional[Dict[str, Any]] = None):
        if path == '/api/stop':
            self._hub.estop(source='agent')
        return self._inner.post(path, payload)
