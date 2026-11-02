"""Robot arka ucu sözleşmesi. Hub robotu yalnızca bu arayüz üzerinden tanır.

* ``FakeBackend``: ROS'suz, donanımsız simülasyon (geliştirme, testler, telefondan ilk deneme).
* ``RobotApiBackend``: robotun HTTP API v1'i (``/api/jobs``, ``/api/stop``, ``/api/reset``).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class BackendResult:
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StopReport:
    """ACİL DUR'un sonucu. ``stopped=True`` yalnızca robot kolun durduğunu **doğruladıysa**."""
    stopped: Optional[bool]          # True doğrulandı · False doğrulanamadı/ulaşılamadı
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResetResult:
    ok: bool
    message: str


@runtime_checkable
class RobotBackend(Protocol):
    name: str
    # Çalışan bir hareketi ortasında kesebiliyor mu? False ise ACİL DUR yalnızca sonraki adımı
    # engeller; arayüz bunu kullanıcıya açıkça söyler.
    can_interrupt: bool

    def execute(self, command: str, cancel: threading.Event) -> BackendResult:
        """Tek robot komutunu (``commands.parse_robot_command`` beyaz listesi) bitene dek çalıştırır.

        ``cancel`` kurulursa hareketi mümkünse keser. Exception fırlatmaz; hata ``ok=False``."""

    def emergency_stop(self) -> StopReport:
        """Robotu durdur (kilitli kalır: ``reset_emergency_stop`` çağrılana dek). Exception fırlatmaz;
        sonucu ``StopReport`` bildirir (``stopped=False``: durduğu doğrulanamadı)."""

    def reset_emergency_stop(self) -> ResetResult:
        """Robot kilidini açmayı dener; robot reddedebilir (kol hâlâ hareket ediyor olabilir)."""

    def snapshot(self) -> Dict[str, Any]:
        """Canlı durum: ``connected``, ``joints``, ``tool``, ``objects``... (JSON'a uygun)."""

    def close(self) -> None: ...
