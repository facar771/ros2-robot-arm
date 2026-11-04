"""Onay kuyruğu: hareket eden araçlar Hub arayüzünde insan onayı ister.

* Zaman aşımında **REDDEDİLİR** (sessizlik onay değildir).
* ACİL DUR, iptal ya da kapanış: bekleyen her onay hemen reddedilir; yeni onay istenemez (``close``).
* Bu kuyruk hiçbir Hub kilidini tutmaz; ``request`` yalnızca ajan iş parçacığını bekletir, ACİL DUR yolu
  buradan geçmez.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

PENDING, APPROVED, DENIED, EXPIRED, CANCELLED = 'pending', 'approved', 'denied', 'expired', 'cancelled'


@dataclass
class Approval:
    id: str
    tool: str
    description: str
    created: float
    expires: float
    status: str = PENDING
    arguments: Dict[str, Any] = field(default_factory=dict)
    event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self, now: float) -> Dict[str, Any]:
        return {'id': self.id, 'tool': self.tool, 'description': self.description,
                'status': self.status, 'arguments': self.arguments,
                'expires_in_s': max(0.0, round(self.expires - now, 1))}


class ApprovalQueue:
    def __init__(self, timeout_s: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 on_event: Optional[Callable[[str, str, str], None]] = None):
        self.timeout_s = timeout_s
        self._clock = clock
        self._on_event = on_event or (lambda kind, msg, level: None)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending: Dict[str, Approval] = {}
        self._recent: List[Approval] = []
        self._open = True

    def _emit(self, kind: str, message: str, level: str = 'info') -> None:
        try:
            self._on_event(kind, message, level)
        except Exception:    # noqa: BLE001 - günlük asla onayı bozmaz
            pass

    def open(self) -> None:
        with self._lock:
            self._open = True

    def request(self, tool: str, description: str, arguments: Optional[Dict[str, Any]] = None) -> bool:
        """Karar gelene dek (en çok ``timeout_s``) bekler. True yalnızca açık onayda."""
        now = self._clock()
        ap = Approval(f'a{next(self._ids)}', tool, description, now, now + self.timeout_s,
                      arguments=dict(arguments or {}))
        with self._lock:
            if not self._open:
                return False
            self._pending[ap.id] = ap
        self._emit('agent_approval_requested', f'Onay bekleniyor: {description}', 'warning')
        ap.event.wait(self.timeout_s)
        with self._lock:
            if ap.status == PENDING:                       # süre doldu: REDDEDİLDİ
                ap.status = EXPIRED
            self._pending.pop(ap.id, None)
            self._recent = (self._recent + [ap])[-20:]
            final = ap.status
        if final == EXPIRED:
            self._emit('agent_approval_expired', f'Onay zaman aşımı, REDDEDİLDİ: {description}',
                       'warning')
        return final == APPROVED

    def decide(self, approval_id: str, approve: bool) -> bool:
        """Bekleyen onaya karar verir; yoksa/bitmişse False."""
        with self._lock:
            ap = self._pending.get(approval_id)
            if ap is None or ap.status != PENDING or self._clock() >= ap.expires:
                return False
            ap.status = APPROVED if approve else DENIED
        ap.event.set()
        self._emit('agent_approval_decided',
                   f"Onay {'verildi' if approve else 'reddedildi'}: {ap.description}",
                   'info' if approve else 'warning')
        return True

    def deny_all(self, why: str = 'iptal') -> int:
        """ACİL DUR/iptal: bekleyen her onayı reddeder."""
        with self._lock:
            waiting = [a for a in self._pending.values() if a.status == PENDING]
            for ap in waiting:
                ap.status = CANCELLED
        for ap in waiting:
            ap.event.set()
        if waiting:
            self._emit('agent_approval_cancelled', f'{len(waiting)} onay reddedildi ({why})', 'warning')
        return len(waiting)

    def close(self, why: str = 'kapanış') -> None:
        with self._lock:
            self._open = False
        self.deny_all(why)

    def pending(self) -> List[Dict[str, Any]]:
        now = self._clock()
        with self._lock:
            return [a.public(now) for a in self._pending.values() if a.status == PENDING]
