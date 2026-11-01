"""Senaryo koşucusu: tek anda tek senaryo, iptal edilebilir, asla exception sızdırmaz."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from control_hub.hub_core.backend import RobotBackend
from control_hub.hub_core.events import EventLog
from control_hub.hub_core.scenarios import Scenario


class BusyError(RuntimeError):
    """Başka bir senaryo çalışıyor."""


class EstopLatchedError(RuntimeError):
    """ACİL DUR kilidi açık; önce sıfırlanmalı."""


class ScenarioRunner:
    def __init__(self, backend: RobotBackend, events: EventLog, *,
                 is_estopped: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.time):
        self._backend = backend
        self._events = events
        self._is_estopped = is_estopped
        self._clock = clock
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._thread: Optional[threading.Thread] = None
        self._st: Dict[str, Any] = {'running': False, 'scenario': None, 'label': None, 'step': None,
                                    'steps_total': 0, 'say': '', 'started_at': None, 'last': None}

    # --- API -----------------------------------------------------------------------------------
    def start(self, scenario: Scenario) -> None:
        with self._lock:
            if self._is_estopped():
                raise EstopLatchedError('ACİL DUR kilidi açık')
            if self._st['running']:
                raise BusyError(f"'{self._st['label']}' çalışıyor")
            self._cancel = threading.Event()
            self._idle.clear()
            self._st.update(running=True, scenario=scenario.id, label=scenario.label, step=0,
                            steps_total=len(scenario.steps), say='', started_at=self._clock())
            self._thread = threading.Thread(target=self._run, args=(scenario, self._cancel),
                                            name=f'scenario-{scenario.id}', daemon=True)
            self._thread.start()

    def cancel(self, reason: str = 'iptal') -> bool:
        with self._lock:
            if not self._st['running']:
                return False
            self._cancel.set()
        self._events.emit('scenario_cancel_requested', f'Senaryo iptal isteği: {reason}',
                          level='warning', reason=reason)
        return True

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._st, last=None if self._st['last'] is None else dict(self._st['last']))

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._st['running'])

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        return self._idle.wait(timeout)

    # --- iç ------------------------------------------------------------------------------------
    def _run(self, sc: Scenario, cancel: threading.Event) -> None:
        ok, msg = False, 'bilinmeyen hata'
        self._events.emit('scenario_started', f"'{sc.label}' başladı", scenario=sc.id)
        try:
            ok, msg = self._steps(sc, cancel)
        except Exception as exc:                                  # koşucu hiçbir koşulda ölmez
            self._events.emit('internal_error', f'Koşucu iç hatası: {exc!r}', level='error')
            ok, msg = False, 'iç hata (ayrıntı günlükte)'
        finally:
            cancelled = cancel.is_set()
            with self._lock:
                self._st.update(running=False, step=None, say='', last={
                    'scenario': sc.id, 'label': sc.label, 'ok': ok, 'cancelled': cancelled,
                    'message': msg, 'finished_at': self._clock()})
            self._events.emit('scenario_finished', f"'{sc.label}': {msg}",
                              level='info' if ok else 'warning', scenario=sc.id, ok=ok,
                              cancelled=cancelled)
            self._idle.set()

    def _steps(self, sc: Scenario, cancel: threading.Event):
        total = len(sc.steps)
        for i, step in enumerate(sc.steps, 1):
            if cancel.is_set():
                return False, f'iptal edildi (adım {i}/{total} başlamadı)'
            with self._lock:
                self._st.update(step=i, say=step.say or (step.command or f'{step.seconds:g} sn bekle'))
            self._events.emit('step_started', step.say or step.command or f'{step.seconds:g} sn bekle',
                              scenario=sc.id, step=i, of=total)
            if step.kind == 'wait':
                if cancel.wait(step.seconds):
                    return False, f'iptal edildi (adım {i}/{total}: bekleme)'
                continue
            res = self._backend.execute(step.command, cancel)
            self._events.emit('step_finished', f'{step.command}: {res.message}',
                              level='info' if res.ok else 'warning', scenario=sc.id, step=i,
                              ok=res.ok, command=step.command)
            if cancel.is_set():
                return False, f'iptal edildi (adım {i}/{total}: {step.command})'
            if not res.ok:
                return False, f'adım {i}/{total} başarısız ({step.command}): {res.message}'
        return True, f'tamamlandı ({total} adım)'
