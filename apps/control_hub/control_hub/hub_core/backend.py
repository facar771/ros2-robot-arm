"""Robot arka ucu sözleşmesi. Hub robotu yalnızca bu arayüz üzerinden tanır.

* ``FakeBackend``: ROS'suz, donanımsız simülasyon (geliştirme, testler, telefondan ilk deneme).
* ``RobotApiBackend`` (PR-H2): robot_viz HTTP API'si.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable


@dataclass(frozen=True)
class BackendResult:
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RobotBackend(Protocol):
    name: str
    # Çalışan bir hareketi ortasında kesebiliyor mu? False ise ACİL DUR yalnızca sonraki adımı
    # engeller; arayüz bunu kullanıcıya açıkça söyler.
    can_interrupt: bool

    def execute(self, command: str, cancel: threading.Event) -> BackendResult:
        """Tek robot komutunu (``commands.parse_robot_command`` beyaz listesi) bitene dek çalıştırır.

        ``cancel`` kurulursa hareketi mümkünse keser. Exception fırlatmaz; hata ``ok=False``."""

    def emergency_stop(self) -> None:
        """Donanım düzeyinde durdur (kilitli kalır: ``reset_emergency_stop`` çağrılana dek)."""

    def reset_emergency_stop(self) -> None: ...

    def snapshot(self) -> Dict[str, Any]:
        """Canlı durum: ``connected``, ``joints``, ``tool``, ``objects``... (JSON'a uygun)."""

    def close(self) -> None: ...
