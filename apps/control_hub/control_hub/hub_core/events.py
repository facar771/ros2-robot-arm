"""Olay günlüğü: bellekte halka tampon + dış alıcılar (PR-H4'te veri katmanına yazan alıcı)."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List

log = logging.getLogger('control_hub')
LEVELS = ('info', 'warning', 'error', 'critical')


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    kind: str
    level: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {'seq': self.seq, 'ts': self.ts, 'kind': self.kind, 'level': self.level,
                'message': self.message, 'data': self.data}


class EventLog:
    def __init__(self, capacity: int = 500, clock: Callable[[], float] = time.time):
        self._buf: Deque[Event] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._seq = 0
        self._clock = clock
        self._sinks: List[Callable[[Event], None]] = []

    def add_sink(self, sink: Callable[[Event], None]) -> None:
        self._sinks.append(sink)

    def emit(self, kind: str, message: str, level: str = 'info', **data: Any) -> Event:
        if level not in LEVELS:
            level = 'info'
        with self._lock:
            self._seq += 1
            ev = Event(self._seq, self._clock(), kind, level, message, data)
            self._buf.append(ev)
        log.log({'info': 20, 'warning': 30, 'error': 40, 'critical': 50}[level], '%s: %s', kind, message)
        for sink in list(self._sinks):
            try:
                sink(ev)
            except Exception:                       # günlük asla robotu/koşucuyu düşürmez
                log.exception('olay alıcısı hata verdi')
        return ev

    def since(self, seq: int = 0, limit: int = 200) -> List[Event]:
        with self._lock:
            out = [e for e in self._buf if e.seq > seq]
        return out[-limit:]

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq
