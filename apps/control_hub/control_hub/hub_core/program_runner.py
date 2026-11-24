"""Program koşucusu (P2): sunucu tarafı, tek seferde bir program, adımlar sırayla hareket arka ucuna gider.

Sözleşme (UI_EMIRLER.md EK-1 B; S1-0 ``UI_CONTRACT.md §9`` kesinleştirince ona uydurulur): ``MoveClient``
yöntemleri ``POST /api/move`` (+ ``/check``, ``/stop``, ``/pause``, ``/resume``, ``GET /api/move/{job}``)
anlamını taşır; gerçeği ``program_move.BackendMoveClient`` (``hub.backend.move_*``), yedeği SAHTE mock.

Güvenlik (K3):
* ACİL DUR'da çalışan program İPTAL edilir (``on_estop``); kilit varken ``start/step/resume`` →
  ``EstopLatchedError`` (rota 423). Reset sonrası program kendiliğinden DEVAM ETMEZ: iptal kalıcıdır.
* Tarayıcı kalp atışı ``heartbeat_timeout_s`` (3 sn) kesilirse program DURAKLAR (iptal değil).
* Koşucu hiçbir koşulda exception sızdırmaz; hata ``failed`` durumu + satır + neden olur.
Zaman ``clock`` ile verilir; ``advance()`` tek bir zamanlayıcı turudur (testlerde sahte saatle elle çağrılır).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Tuple

from control_hub.hub_core.events import EventLog
from control_hub.hub_core.runner import BusyError, EstopLatchedError

log = logging.getLogger('control_hub.program')
HEARTBEAT_TIMEOUT_S = 3.0               # K3(h): tarayıcı bağlantısı bu kadar kesilirse duraklat
TICK_S = 0.05                           # gerçek zamanlı iş parçacığı turu (testte saat sahtedir)
Body = Dict[str, Any]


class MoveRefused(Exception):
    """Hareket arka ucu reddetti (EK-1 B: 409 outside_safe_zone|joint_limit|path_blocked|unreachable|busy)."""

    def __init__(self, status: int, error: str, message: str):
        super().__init__(message)
        self.status, self.error, self.message = status, error, message


class MoveClient(Protocol):
    """Hareket arka ucu (EK-1 B). ``measured=False`` ise robot duruşu ölçüm DEĞİLDİR (sahte)."""
    label: str
    measured: bool
    mock: bool                                       # True: SAHTE (SİM/robot oynamaz), arayüz bandı çıkar

    def check(self, body: Body) -> Body: ...         # {ok, violations:[{kind, at_pct, message}]}; ETMEZ
    def start(self, body: Body) -> str: ...          # job_id; MoveRefused fırlatabilir
    def status(self, job_id: str) -> Body: ...       # {state: running|done|failed|stopped, error?, message?}
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def pose(self) -> Optional[Body]: ...            # {joints_deg, pos_m, rpy_deg, open_pct} ya da None
    def checker(self) -> Callable[[Body], Body]: ...  # bir kuru çalıştırma için denetçi (zincirleyebilir)


# --- yürütme modeli: açılmış adım listesi, waypoint çözümü, kuru çalıştırma ---------------------------
@dataclass(frozen=True)
class Action:
    """Çalışacak tek adım. ``line``: üst düzey satır, ``sub``: gövde sırası, ``iteration``: tur (1+)."""
    line: int
    sub: Optional[int]
    iteration: Optional[int]
    step: Body


def iter_actions(program: Body) -> Iterator[Action]:
    for i, s in enumerate(program['steps']):
        if s['kind'] == 'loop':
            for it in range(1, s['count'] + 1):
                for j, b in enumerate(s['steps']):
                    if b['kind'] != 'comment':
                        yield Action(i, j, it, b)
        elif s['kind'] != 'comment':
            yield Action(i, None, None, s)


def move_body(step: Body, wps: Dict[str, Body],
              speed_pct: float) -> Tuple[Optional[Body], Optional[Dict[str, str]]]:
    """Adım -> EK-1 B gövdesi ``{type: joint|linear|tool, ...}``; sorun varsa ``(None, {kind, message})``."""
    kind = step['kind']
    if kind == 'tool':
        return {'type': 'tool', 'open_pct': step['open_pct'], 'speed_pct': speed_pct}, None
    wp = None
    if 'waypoint' in step:
        wp = wps.get(step['waypoint'])
        if wp is None:
            return None, {'kind': 'missing_waypoint', 'message': f"waypoint yok: '{step['waypoint']}'"}
    if kind == 'move_j':
        joints = wp.get('joints_deg') if wp else step['joints_deg']
        if joints is None:
            return None, {'kind': 'waypoint_no_joints', 'message': f"'{wp['name']}' eklem değeri taşımıyor"}
        return {'type': 'joint', 'joints_deg': joints, 'speed_pct': speed_pct}, None
    free = step.get('orientation') == 'free'
    if wp is None:
        return _linear(step['pos_m'], None if free else step['rpy_deg'], speed_pct), None
    if 'pos_m' not in wp:
        return None, {'kind': 'waypoint_no_pose', 'message': f"'{wp['name']}' TCP konumu taşımıyor"}
    pos = [round(a + b, 6) for a, b in zip(wp['pos_m'], step.get('offset_m') or [0.0, 0.0, 0.0])]
    return _linear(pos, None if free else wp['rpy_deg'], speed_pct), None


def _linear(pos: List[float], rpy: Optional[List[float]], speed_pct: float) -> Body:
    """``rpy`` yoksa yönelim SERBEST (``orientation:'free'``: S1.1, rpy_deg ile birlikte verilemez)."""
    if rpy is None:
        return {'type': 'linear', 'pos_m': pos, 'orientation': 'free', 'speed_pct': speed_pct}
    return {'type': 'linear', 'pos_m': pos, 'rpy_deg': rpy, 'speed_pct': speed_pct}


def dry_run(program: Body, check: Callable[[Body], Body], speed_pct: float) -> Body:
    """Kuru çalıştırma: her hareket/alet adımını ``check(body)`` (hareket ETMEZ) ile doğrular.

    Döngü gövdesi bir kez denetlenir (2. tur başlangıcı denetlenmez). ``check`` zincirleyen bir denetçiyse
    (``MoveClient.checker()``) her adım önceki adımın bitişinden planlanır; düz ``check`` robotun O ANKİ
    duruşundan sınar (yol/ulaşılabilirlik yaklaşık kalır). Çalışırken her adım yine gerçek duruştan sınanır.
    Sonuç:
    ``{ok, violations, rows:[{line, sub, kind, ok, violations:[{kind, at_pct, message}]}]}``."""
    wps = {w['name']: w for w in program['waypoints']}
    rows: List[Body] = []

    def one(line: int, sub: Optional[int], step: Body) -> None:
        if step['kind'] in ('wait', 'comment', 'loop'):
            return
        body, problem = move_body(step, wps, speed_pct)
        found: List[Body] = []
        if problem is not None:
            found.append({**problem, 'at_pct': None})
        else:
            try:
                res = check(body)
                found = [dict(v) for v in (res.get('violations') or []) if isinstance(v, dict)]
                if not found and not res.get('ok', True):
                    found = [{'kind': 'rejected', 'at_pct': None, 'message': 'hareket arka ucu reddetti'}]
            except Exception as exc:                          # noqa: BLE001 - denetim çökerse satır kırmızı olur
                found = [{'kind': 'check_failed', 'at_pct': None,
                          'message': f'denetlenemedi: {type(exc).__name__}'}]
        rows.append({'line': line, 'sub': sub, 'kind': step['kind'], 'ok': not found, 'violations': found})

    for i, s in enumerate(program['steps']):
        for j, b in (enumerate(s['steps']) if s['kind'] == 'loop' else [(None, s)]):
            one(i, j, b)
    n = sum(len(r['violations']) for r in rows)
    return {'ok': n == 0, 'violations': n, 'rows': rows}


# --- koşucu ----------------------------------------------------------------------------------------
class ProgramRunner:
    def __init__(self, mover: MoveClient, events: EventLog, *,
                 is_estopped: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.monotonic,
                 heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S, threaded: bool = True):
        self._m, self._ev, self._estopped, self._clock = mover, events, is_estopped, clock
        self._hb_timeout, self._threaded = heartbeat_timeout_s, threaded
        self._lock = threading.RLock()
        self._run_id = 0
        self._state, self._name = 'idle', None
        self._actions: List[Action] = []
        self._wps: Dict[str, Body] = {}
        self._i, self._cur, self._halt_after, self._step_mode = 0, None, False, False
        self._speed, self._beat = 0.0, 0.0
        self._line: Optional[int] = None
        self._sub: Optional[int] = None
        self._iter: Optional[int] = None
        self._say, self._pause_reason = '', ''
        self._error: Optional[Body] = None
        self._estop_hit = False

    # --- durum ---------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        with self._lock:
            return self._state in ('running', 'paused')

    def status(self) -> Body:
        with self._lock:
            return {'state': self._state, 'running': self._state in ('running', 'paused'),
                    'run_id': self._run_id, 'program': self._name, 'speed_pct': self._speed,
                    'step_mode': self._step_mode, 'done': self._i, 'total': len(self._actions),
                    'line': self._line, 'sub': self._sub,
                    'iteration': self._iter, 'say': self._say, 'pause_reason': self._pause_reason,
                    'error': None if self._error is None else dict(self._error), 'estop': self._estop_hit}

    # --- komutlar ------------------------------------------------------------------------------
    def start(self, program: Body, speed_pct: float, step_mode: bool = False) -> int:
        with self._lock:
            if self._estopped():
                raise EstopLatchedError('ACİL DUR kilidi açık')
            if self.running:
                raise BusyError(f"'{self._name}' çalışıyor")
            actions = list(iter_actions(program))
            if not actions:
                raise ValueError('boş program')
            self._run_id += 1
            self._actions, self._wps = actions, {w['name']: w for w in program['waypoints']}
            self._name, self._speed, self._step_mode = program['name'], float(speed_pct), step_mode
            self._i, self._cur, self._halt_after, self._error, self._estop_hit = 0, None, False, None, False
            self._line = self._sub = self._iter = None
            self._beat, self._pause_reason = self._clock(), 'adım için hazır' if step_mode else ''
            self._state, self._say = ('paused' if step_mode else 'running'), ''
            self._ev.emit('program_started', f"Program başladı: '{self._name}' ({len(actions)} adım"
                          f"{', adım adım' if step_mode else ''}, hız %{self._speed:g})", program=self._name)
            if self._threaded:
                threading.Thread(target=self._loop, args=(self._run_id,), name='program-runner',
                                 daemon=True).start()
            return self._run_id

    def heartbeat(self) -> None:
        with self._lock:
            self._beat = self._clock()

    def pause(self, reason: str = 'kullanıcı duraklattı') -> bool:
        with self._lock:
            return self._pause(reason)

    def resume(self) -> None:
        with self._lock:
            self._need_paused()
            self._halt_after = self._step_mode = False
            self._go()

    def step(self) -> None:
        """Duraklatılmış programda TEK adım çalıştırır, sonra yine duraklar."""
        with self._lock:
            self._need_paused()
            self._step_mode = self._halt_after = True
            self._go()

    def cancel(self, reason: str = 'kullanıcı durdurdu', estop: bool = False) -> bool:
        with self._lock:
            if not self.running:
                return False
            self._end('cancelled', reason, level='warning', estop=estop)
            return True

    def on_estop(self) -> None:
        """``Hub.add_estop_listener``: ACİL DUR programı iptal eder (robotu Hub'ın kendisi de durdurur)."""
        self.cancel('ACİL DUR: program iptal edildi', estop=True)

    # --- zamanlayıcı turu ----------------------------------------------------------------------
    def advance(self) -> None:
        with self._lock:
            try:
                self._advance()
            except Exception:                                     # koşucu hiçbir koşulda ölmez
                log.exception('program koşucusu iç hatası')
                self._fail('iç hata (ayrıntı günlükte)', code='internal')

    def _advance(self) -> None:
        if not self.running:
            return
        if self._estopped():
            self._end('cancelled', 'ACİL DUR: program iptal edildi', level='warning', estop=True)
            return
        now = self._clock()
        if self._state == 'running' and now - self._beat > self._hb_timeout:
            self._pause(f'tarayıcı bağlantısı {self._hb_timeout:g} sn kesildi: program duraklatıldı')
        elif self._state == 'running':
            if self._cur is None:
                self._dispatch(now)
            else:
                self._poll(now)

    def _loop(self, run_id: int) -> None:
        while True:
            with self._lock:
                if self._run_id != run_id or not self.running:
                    return
            self.advance()
            time.sleep(TICK_S)

    # --- iç ------------------------------------------------------------------------------------
    def _need_paused(self) -> None:
        if self._estopped():
            raise EstopLatchedError('ACİL DUR kilidi açık')
        if self._state != 'paused':
            raise BusyError('program duraklatılmış olmalı')

    def _go(self) -> None:
        now = self._clock()
        self._beat = now
        cur = self._cur
        if cur and cur.get('paused'):
            if cur['phase'] == 'move':
                self._m.resume()
            else:
                cur['until'] = now + cur['left']
            cur['paused'] = False
        self._state, self._pause_reason = 'running', ''

    def _pause(self, reason: str) -> bool:
        if self._state != 'running':
            return False
        cur = self._cur
        if cur and not cur.get('paused'):
            if cur['phase'] == 'move':
                try:
                    self._m.pause()
                except Exception:                                  # noqa: BLE001 - iletilemedi: güvenli taraf
                    log.exception('duraklatma robota iletilemedi')
                    self._fail('duraklatma robota iletilemedi: program durduruldu', code='pause_failed')
                    return False
            else:
                cur['left'] = max(0.0, cur['until'] - self._clock())
            cur['paused'] = True
        self._state, self._pause_reason = 'paused', reason
        self._ev.emit('program_paused', f'Program duraklatıldı: {reason}', level='warning',
                      program=self._name)
        return True

    def _dispatch(self, now: float) -> None:
        if self._i >= len(self._actions):
            self._end('finished', f'tamamlandı ({len(self._actions)} adım)')
            return
        a = self._actions[self._i]
        self._line, self._sub, self._iter = a.line, a.sub, a.iteration
        if a.step['kind'] == 'wait':
            self._say = f"{a.step['s']:g} sn bekle"
            self._cur = {'phase': 'wait', 'until': now + a.step['s']}
            return
        body, problem = move_body(a.step, self._wps, self._speed)
        if problem is not None:
            self._fail(problem['message'], code=problem['kind'])
            return
        self._say = {'move_j': 'eklem hareketi', 'move_l': 'doğrusal hareket', 'tool': 'alet'}[a.step['kind']]
        try:
            job = self._m.start(body)
        except MoveRefused as exc:
            self._fail(f'{exc.error}: {exc.message}', code=exc.error)
            return
        except Exception:                                          # noqa: BLE001
            log.exception('hareket başlatılamadı')
            self._fail('hareket arka ucu hata verdi', code='backend_error')
            return
        self._cur = {'phase': 'move', 'job': job}

    def _poll(self, now: float) -> None:
        cur = self._cur
        if cur['phase'] == 'wait':
            if now >= cur['until']:
                self._complete()
            return
        try:
            res = self._m.status(cur['job'])
        except Exception:                                          # noqa: BLE001
            log.exception('hareket durumu okunamadı')
            self._fail('hareket durumu okunamadı', code='backend_error')
            return
        st = res.get('state')
        if st == 'done':
            self._complete()
        elif st != 'running':
            self._fail(res.get('message') or f'hareket {st}', code=res.get('error') or st)

    def _complete(self) -> None:
        self._cur = None
        self._i += 1
        if self._i >= len(self._actions):
            self._end('finished', f'tamamlandı ({len(self._actions)} adım)')
        elif self._halt_after:
            self._halt_after, self._state, self._pause_reason = False, 'paused', 'adım tamamlandı'

    def _fail(self, message: str, code: Optional[str] = None) -> None:
        self._error = {'line': self._line, 'sub': self._sub, 'iteration': self._iter, 'message': message,
                       'code': code}
        self._end('failed', f'satır {(self._line or 0) + 1}: {message}', level='warning')

    def _end(self, state: str, message: str, level: str = 'info', estop: bool = False) -> None:
        if state != 'finished':
            try:
                self._m.stop()                                     # çalışan iş varsa kontrollü durdur
            except Exception:                                      # noqa: BLE001
                log.exception('durdurma robota iletilemedi')
        self._state, self._cur, self._halt_after, self._estop_hit = state, None, False, estop
        self._say, self._pause_reason = message, ''
        self._ev.emit(f'program_{state}', f"Program '{self._name}': {message}", level=level,
                      program=self._name)
