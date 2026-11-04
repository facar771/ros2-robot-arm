"""Hub: çekirdek parçaları tek yerde birleştirir (backend, koşucu, erişim, günlük, ACİL DUR kilidi)."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from control_hub.hub_core.access import AccessControl
from control_hub.hub_core.backend import RobotBackend, StopReport
from control_hub.hub_core.events import EventLog
from control_hub.hub_core.runner import BusyError, ScenarioRunner
from control_hub.hub_core.scenarios import Scenario


class UnknownScenarioError(KeyError):
    """Tanımsız senaryo id'si."""


class ResetRefusedError(RuntimeError):
    """Robot kilidin açılmasını reddetti (kol hâlâ hareket ediyor/bilinmiyor, ulaşılamıyor...)."""


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
        self._stop_report: Optional[StopReport] = None
        self._stop_thread: Optional[threading.Thread] = None
        self.bridge: Optional[Any] = None          # hw.SerialTcpBridge (isteğe bağlı, ESP32 köprüsü)
        self.agent: Optional[Any] = None           # agent.AgentService (isteğe bağlı, LLM ajanı)
        self.runner = ScenarioRunner(backend, self.events, is_estopped=lambda: self.estopped,
                                     clock=clock)

    # --- ACİL DUR ------------------------------------------------------------------------------
    @property
    def estopped(self) -> bool:
        """Hub kilidi YA DA robotun kendi kilidi (başka bir istemci/düğme durdurduysa da kilitli say)."""
        return self._estop_at is not None or self._robot_tripped()

    def _robot_tripped(self) -> bool:
        try:
            return bool((self.backend.snapshot().get('estop') or {}).get('tripped'))
        except Exception:
            return False

    def add_estop_listener(self, fn: Callable[[], None]) -> None:
        """PR-H4: kural motoru gibi bileşenler ACİL DUR'da kendilerini kesmek için bağlanır."""
        self._estop_listeners.append(fn)

    def estop(self, source: str = 'api') -> None:
        """Hemen kilitler ve robota durdurma gönderir. Çağıranı **bloklamaz**: robotun kolun
        durduğunu doğrulaması birkaç sn sürebilir; sonuç ``snapshot()['estop']['report']`` ve
        ``estop_confirmed`` / ``estop_unverified`` olaylarıyla gelir. Idempotent."""
        with self._estop_lock:
            first = self._estop_at is None
            if first:
                self._estop_at = self._clock()
            self._stop_report = None
            t = threading.Thread(target=self._stop_backend, name='estop-backend', daemon=True)
            self._stop_thread = t
        self.events.emit('estop', f'ACİL DUR ({source})', level='critical', source=source,
                         repeated=not first)
        # Önce koşucunun iptal bayrağı (bellekte, anında): robot durmadan önce "iptal" işaretlenmiş olsun,
        # yoksa senaryo sonucu "başarısız" görünebilir. Hemen ardından robota durdurma gider.
        self.runner.cancel('ACİL DUR')
        t.start()
        for fn in self._estop_listeners:
            try:
                fn()
            except Exception as exc:                 # bir dinleyici hata verse de ötekiler çalışır
                self.events.emit('estop_step_failed', f'ACİL DUR dinleyicisi hata verdi: {exc!r}',
                                 level='error')

    def _stop_backend(self) -> None:
        try:
            report = self.backend.emergency_stop()
        except Exception as exc:
            report = StopReport(False, f'durdurma çağrısı hata verdi: {exc!r}')
        with self._estop_lock:
            if self._stop_thread is not threading.current_thread():
                return                               # daha yeni bir ACİL DUR bu sonucu geçersiz kıldı
            self._stop_report = report
        if report.stopped:
            self.events.emit('estop_confirmed', f'Robot durdu: {report.message}', level='warning',
                             **report.details)
        else:
            self.events.emit('estop_unverified',
                             f'ROBOTUN DURDUĞU DOĞRULANAMADI: {report.message} — fiziksel acil '
                             'durdurmayı kullan!', level='critical', **report.details)

    def wait_estop_report(self, timeout: float = 5.0) -> Optional[StopReport]:
        t = self._stop_thread
        if t is not None:
            t.join(timeout)
        return self._stop_report

    def reset_estop(self, source: str = 'api') -> None:
        """Önce robot kilidini açtırır; robot reddederse Hub kilidi de **açılmaz**
        (``ResetRefusedError``). Koşucu hâlâ bitmediyse ``BusyError``."""
        if self.runner.running:
            raise BusyError('senaryo henüz durmadı')
        t = self._stop_thread
        if t is not None:
            t.join(8)                                  # robotun "durdu" doğrulaması en çok ~5 sn
            if t.is_alive():
                raise BusyError('robot durdurma doğrulaması sürüyor')
        res = self.backend.reset_emergency_stop()
        if not res.ok:
            self.events.emit('estop_reset_refused', f'Robot kilidi açmayı reddetti: {res.message}',
                             level='warning')
            raise ResetRefusedError(res.message)
        with self._estop_lock:
            self._estop_at = None
            self._stop_report = None
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
        report = self._stop_report
        pending = self._stop_thread is not None and self._stop_thread.is_alive()
        robot_tripped = bool((robot.get('estop') or {}).get('tripped'))
        return {
            'time': self._clock(),
            'access': {'mode': self.access.mode},
            'estop': {'latched': self._estop_at is not None or robot_tripped, 'at': self._estop_at,
                      'by_robot_only': self._estop_at is None and robot_tripped,
                      'pending': pending,
                      'report': None if report is None else {
                          'stopped': report.stopped, 'message': report.message,
                          'details': report.details}},
            'runner': self.runner.status(),
            'robot': robot,
            'bridge': self.bridge.status() if self.bridge is not None else None,
            'agent': self.agent.summary() if self.agent is not None else None,
            'last_event_seq': self.events.last_seq,
        }

    def close(self) -> None:
        self.runner.cancel('kapanıyor')
        if self.agent is not None:
            self.agent.close()
        if self.bridge is not None:
            self.bridge.stop()
        self.runner.wait_idle(3)
        self.backend.close()
