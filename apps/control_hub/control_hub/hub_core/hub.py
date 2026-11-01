"""Hub: çekirdek parçaları tek yerde birleştirir (backend, koşucu, erişim, günlük, ACİL DUR kilidi)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from control_hub.hub_core.access import AccessControl
from control_hub.hub_core.backend import RobotBackend
from control_hub.hub_core.events import EventLog
from control_hub.hub_core.runner import BusyError, ScenarioRunner
from control_hub.hub_core.scenarios import Scenario


class UnknownScenarioError(KeyError):
    """Tanımsız senaryo id'si."""


class Hub:
    def __init__(self, backend: RobotBackend, scenarios: Dict[str, Scenario], ui: Dict[str, Any],
                 access: AccessControl, events: Optional[EventLog] = None,
                 clock: Callable[[], float] = time.time):
        self.backend = backend
        self.scenarios = scenarios
        self.ui = ui
        self.access = access
        self.events = events or EventLog()
        self._clock = clock
        self._estop_lock = threading.Lock()
        self._estop_at: Optional[float] = None
        self._estop_listeners: List[Callable[[], None]] = []
        self.runner = ScenarioRunner(backend, self.events, is_estopped=lambda: self.estopped,
                                     clock=clock)

    # --- ACİL DUR ------------------------------------------------------------------------------
    @property
    def estopped(self) -> bool:
        return self._estop_at is not None

    def add_estop_listener(self, fn: Callable[[], None]) -> None:
        """PR-H4: kural motoru gibi bileşenler ACİL DUR'da kendilerini kesmek için bağlanır."""
        self._estop_listeners.append(fn)

    def estop(self, source: str = 'api') -> None:
        """Her şeyi keser ve kilitler. Idempotent; bir adım hata verse de diğerleri yapılır."""
        with self._estop_lock:
            first = self._estop_at is None
            if first:
                self._estop_at = self._clock()
        self.events.emit('estop', f'ACİL DUR ({source})', level='critical', source=source,
                         repeated=not first)
        self.runner.cancel('ACİL DUR')
        for step in (self.backend.emergency_stop, *self._estop_listeners):
            try:
                step()
            except Exception as exc:                  # bir kesme adımı hata verse de ötekiler çalışır
                self.events.emit('estop_step_failed', f'ACİL DUR adımı hata verdi: {exc!r}',
                                 level='error')

    def reset_estop(self, source: str = 'api') -> None:
        """Kilidi açar. Koşucu hâlâ bitmediyse ``BusyError`` (birkaç yüz ms sonra tekrar dene)."""
        if self.runner.running:
            raise BusyError('senaryo henüz durmadı')
        with self._estop_lock:
            self._estop_at = None
        self.backend.reset_emergency_stop()
        self.events.emit('estop_reset', f'ACİL DUR kilidi açıldı ({source})', level='warning',
                         source=source)

    # --- senaryolar ----------------------------------------------------------------------------
    def run_scenario(self, scenario_id: str) -> None:
        sc = self.scenarios.get(scenario_id)
        if sc is None:
            raise UnknownScenarioError(scenario_id)
        self.runner.start(sc)

    def cancel_scenario(self) -> bool:
        return self.runner.cancel('kullanıcı iptali')

    # --- durum ---------------------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """JSON'a uygun canlı durum. PIN ve oturum bilgisi içermez."""
        robot = self.backend.snapshot()
        return {
            'time': self._clock(),
            'access': {'mode': self.access.mode},
            'estop': {'latched': self.estopped, 'at': self._estop_at},
            'runner': self.runner.status(),
            'robot': robot,
            'last_event_seq': self.events.last_seq,
        }

    def close(self) -> None:
        self.runner.cancel('kapanıyor')
        self.runner.wait_idle(3)
        self.backend.close()
