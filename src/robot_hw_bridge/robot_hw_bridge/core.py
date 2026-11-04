"""B2 çekirdeği (ROS'suz): FollowJointTrajectory hedefini ESP32 komutlarına çevirir, iki yönlü
watchdog'u ve Robot API acil dur bağını yönetir. ROS düğümü (``node.py``) bunu sarar.

Kullanır, yeniden yazmaz: hat ve kodek ``host.HwLink``, eklem↔adım dönüşümü ve limitler
``robot_core.hardware`` (``HardwareProfile``, ``soft_limits``, ``rate_steps``).

Güvenlik kararları (hepsi "daha dar" yönde; ayrıntı: docs/HARDWARE.md §11):

* Açılış durumu **UNHOMED**: ``home()`` başarıyla bitmeden hiçbir yörünge çalışmaz.
* Her bağlantı kesintisi, MCU acil duruşu, FAULT ya da düğüm döngüsü takılması "homed" bilgisini siler.
* Yörünge hedefi yürütülmeden önce tümüyle doğrulanır (eklem adları, sonlu sayılar, limitler, hız,
  başlangıç konumu); biri bozuksa **hiçbir komut gönderilmez**.
* Hareket sırasında ölçülen konum planlanandan ``path_tol`` fazla sapar, hat kopar, MCU acil dura geçer,
  iptal gelir ya da süre aşılırsa eksenler durdurulur.
* **Bu yazılım katmanı donanım acil durdurmanın yerine geçmez** (NC buton + sürücü EN hattı, §6).
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from robot_core.hardware import HardwareProfile, rate_steps, soft_limits
from robot_core.profile import Profile

from robot_hw_bridge.host import HwLink
from robot_hw_bridge.protocol import (
    WATCHDOG_MAX_MS,
    WATCHDOG_MIN_MS,
    AxisFlag,
    ConfigKey,
    EncoderMode,
    Fault,
    Result,
    State,
    Status,
)

# control_msgs/FollowJointTrajectory.Result error codes (ROS'suz kopya; node.py sabitleri eşler)
SUCCESSFUL, INVALID_GOAL, INVALID_JOINTS, OLD_HEADER_TIMESTAMP = 0, -1, -2, -3
PATH_TOLERANCE_VIOLATED, GOAL_TOLERANCE_VIOLATED = -4, -5

_ENCODER = {'none': EncoderMode.NONE, 'incremental': EncoderMode.INCREMENTAL,
            'absolute': EncoderMode.ABSOLUTE}


@dataclass(frozen=True)
class BridgeConfig:
    watchdog_ms: int = 300            # MCU ve host tarafı hat zaman aşımı (200-500)
    start_tol: float = 0.10           # rad: yörüngenin ilk noktası ölçülen konumdan en fazla bu kadar uzak
    goal_tol: float = 0.03            # rad: bitişte hedefe izin verilen sapma
    path_tol: float = 0.15            # rad: yürütme sırasında planlanan ile ölçülen arası
    lookahead_s: float = 0.25         # hareket eden hedef noktası: şimdi + bu süre
    command_period_s: float = 0.10    # MOVE güncelleme aralığı
    settle_s: float = 3.0             # yörünge bitince hedefe oturma için en çok bekleme
    overrun_s: float = 2.0            # yörünge süresine eklenen pay; aşılırsa durdurulur
    home_timeout_s: float = 60.0
    home_tol_steps: int = 3           # homing sonrası sayaç ile beklenen home adımı arasındaki fark
    loop_stall_s: float = 0.25        # düğüm döngüsü bu kadar sessiz kalırsa kalp atışı kesilir
    max_points: int = 20000

    def __post_init__(self) -> None:
        if not WATCHDOG_MIN_MS <= self.watchdog_ms <= WATCHDOG_MAX_MS:
            raise ValueError(f'watchdog_ms {WATCHDOG_MIN_MS}-{WATCHDOG_MAX_MS} olmalı')
        if not 0.05 <= self.loop_stall_s <= self.watchdog_ms / 1000:
            raise ValueError('loop_stall_s 0.05 ile watchdog süresi arasında olmalı')


@dataclass
class ExecResult:
    code: int
    message: str

    @property
    def ok(self) -> bool:
        return self.code == SUCCESSFUL


@dataclass
class Point:
    t: float
    q: Tuple[float, ...]


def _finite(values: Sequence[float]) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


class HwBridge:
    def __init__(self, hw: HardwareProfile, robot: Profile, config: Optional[BridgeConfig] = None,
                 on_estop: Optional[Callable[[str], None]] = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.hw, self.robot = hw, robot
        self.cfg = config or BridgeConfig()
        self._on_estop = on_estop
        self._clock, self._sleep = clock, sleep
        self._link: Optional[HwLink] = None
        self._lock = threading.RLock()
        self._exec_lock = threading.Lock()
        self._abort = threading.Event()
        self._abort_reason = ''
        self._status: Optional[Status] = None
        self._prev: Optional[Tuple[float, List[float]]] = None
        self._vel: List[float] = [0.0] * len(hw.axes)
        self._configured = False
        self._homed_ok = False
        self._estop_seen = False
        self._link_lost = False
        self.fault: Optional[str] = None
        self.reports: List[str] = []              # acil dur nedenleri (Robot API'ye iletilenler)
        self._tick: Optional[float] = None
        self._stalled = False
        self._guard_stop = threading.Event()
        self._guard: Optional[threading.Thread] = None
        self.executing = False

    # ------------------------------------------------------------------ bağlantı
    def attach(self, link: HwLink) -> None:
        """Yeni (ya da yeniden kurulan) hat: önceki "homed/yapılandırıldı" bilgisi geçersizdir."""
        with self._lock:
            self._link = link
            self._configured = self._homed_ok = False
            self._estop_seen = self._link_lost = self._stalled = False
            self._status = self._prev = None
            self._abort.clear()
            self.fault = None

    # HwLink geri çağrıları ------------------------------------------------------
    def on_status(self, st: Status) -> None:
        now = self._clock()
        q = self._joints_from(st)
        with self._lock:
            if self._prev is not None and q is not None:
                dt = now - self._prev[0]
                if dt > 0.005:
                    self._vel = [((a - b) / dt if (f.flags & AxisFlag.MOVING) else 0.0)
                                 for a, b, f in zip(q, self._prev[1], st.axes)]
            if q is not None:
                self._prev = (now, q)
            self._status = st
            if st.state == State.ESTOPPED and not self._estop_seen:
                self._estop_seen = True
                self._invalidate(f'MCU ACİL DUR ({st.reason.name})')
            elif st.state != State.ESTOPPED:
                self._estop_seen = False
            if st.state == State.UNHOMED:
                self._homed_ok = False

    def on_fault(self, fault: Fault) -> None:
        self._invalidate(f'MCU FAULT {fault.code.name} eksen={fault.axis}', report=False)

    def on_link_lost(self) -> None:
        self._link_lost = True
        self._invalidate('ESP32 telemetrisi kesildi (hat zaman aşımı)')

    def on_link_up(self) -> None:
        self._link_lost = False

    def _invalidate(self, reason: str, report: bool = True) -> None:
        """Homed bilgisini sil, çalışan yörüngeyi kes, (isteniyorsa) Robot API'ye acil dur bildir."""
        with self._lock:
            self._homed_ok = False
            self.fault = reason
            self._abort_reason = reason
            self._abort.set()
            if report:
                self.reports.append(reason)
        if report and self._on_estop is not None:
            threading.Thread(target=self._on_estop, args=(reason,), name='hw-estop-report',
                             daemon=True).start()

    # ------------------------------------------------------------------ düğüm döngüsü bekçisi
    def tick(self) -> None:
        """Düğümün asıl döngüsü (zamanlayıcı) bunu sık sık çağırır. Susarsa kalp atışı kesilir ve MCU
        watchdog'u robotu durdurur: "ROS düğümü takıldı ama iş parçacıkları kalp atışı yolluyor" olmaz."""
        self._tick = self._clock()
        if self._guard is None:
            self._guard = threading.Thread(target=self._guard_loop, name='hw-guard', daemon=True)
            self._guard.start()

    def _guard_loop(self) -> None:
        while not self._guard_stop.wait(0.05):
            last = self._tick
            if last is not None and not self._stalled and self._clock() - last > self.cfg.loop_stall_s:
                self._stalled = True
                self._invalidate('düğüm döngüsü takıldı: kalp atışı kesildi', report=False)
                if self._link is not None:
                    self._link.stop(close_transport=False)

    def close(self) -> None:
        self._guard_stop.set()
        if self._link is not None:
            self._link.stop()

    # ------------------------------------------------------------------ durum
    def _joints_from(self, st: Status) -> Optional[List[float]]:
        link = self._link
        hello = link.hello if link else None
        if len(st.axes) != len(self.hw.axes):
            return None
        steps = []
        for i, a in enumerate(st.axes):
            measured = hello is not None and i < len(hello.axes) and \
                hello.axes[i].encoder != EncoderMode.NONE
            steps.append(a.enc if measured else a.pos)
        return self.hw.steps_to_joints(steps)

    def positions(self) -> Optional[List[float]]:
        st = self._status
        return None if st is None else self._joints_from(st)

    def velocities(self) -> List[float]:
        with self._lock:
            return list(self._vel)

    @property
    def homed(self) -> bool:
        return self._homed_ok

    def state_doc(self) -> Dict[str, Any]:
        st = self._status
        link = self._link
        return {'link_ok': bool(link and link.link_ok), 'link_lost': self._link_lost,
                'configured': self._configured, 'homed': self._homed_ok,
                'mcu_state': st.state.name if st else None,
                'estop_reason': st.reason.name if st else None,
                'fault': self.fault, 'executing': self.executing,
                'open_loop_axes': list(self.hw.open_loop_axes), 'warnings': list(self.hw.warnings),
                'watchdog_ms': self.cfg.watchdog_ms,
                'note': 'yazılım acil durdurması donanım acil durdurmanın yerine geçmez'}

    # ------------------------------------------------------------------ yapılandırma, homing
    def _ack(self, what: str, ack) -> Optional[str]:
        return None if ack.result == Result.OK else f'{what}: {ack.result.name}'

    def configure(self, wait_s: float = 3.0) -> Tuple[bool, str]:
        """HELLO'yu doğrular; watchdog ve yazılım limitlerini MCU'ya yazar (limitler robot profilinden)."""
        link = self._link
        if link is None or not link.wait_link(wait_s) or link.hello is None:
            return False, 'ESP32 yanıt vermiyor (HELLO/STATUS yok)'
        hello = link.hello
        if len(hello.axes) != len(self.hw.axes):
            return False, f'eksen sayısı uyuşmuyor: ESP32 {len(hello.axes)}, profil {len(self.hw.axes)}'
        for ax, info in zip(self.hw.axes, hello.axes):
            if info.encoder != _ENCODER[ax.encoder]:
                return False, (f'{ax.joint}: encoder uyuşmuyor (profil {ax.encoder}, '
                               f'ESP32 {info.encoder.name})')
            if not info.referenceable:
                return False, (f'{ax.joint}: ESP32 bu ekseni referanslayamıyor '
                               '(home anahtarı/mutlak encoder yok)')
        if link.state == State.ESTOPPED:
            return False, 'ESP32 ACİL DUR durumunda: NC döngüsü kapalıyken clear() gerekir'
        problems = []
        for text, ack in [('WATCHDOG_MS', link.config(ConfigKey.WATCHDOG_MS, self.cfg.watchdog_ms))]:
            problems.append(self._ack(text, ack))
        for i, ax in enumerate(self.hw.axes):
            lo, hi = soft_limits(ax, self.robot)
            problems.append(self._ack(f'{ax.joint} SOFT_MIN', link.config(ConfigKey.SOFT_MIN, lo, i)))
            problems.append(self._ack(f'{ax.joint} SOFT_MAX', link.config(ConfigKey.SOFT_MAX, hi, i)))
        bad = [p for p in problems if p]
        if bad:
            return False, 'yapılandırma reddedildi: ' + '; '.join(bad)
        self._configured = True
        return True, 'yapılandırıldı'

    def clear(self) -> Tuple[bool, str]:
        """MCU acil dur kilidini aç (yalnızca NC döngüsü kapalıyken işe yarar); homing yine şarttır."""
        link = self._link
        if link is None:
            return False, 'hat yok'
        ack = link.clear()
        if ack.result != Result.OK:
            return False, f'clear reddedildi: {ack.result.name}'
        self._homed_ok = self._configured = False
        self._abort.clear()
        self.fault = None
        return self.configure()

    def home(self) -> Tuple[bool, str]:
        link = self._link
        if link is None or not link.link_ok:
            return False, 'hat yok'
        if self.executing:
            return False, 'yörünge çalışırken homing yapılmaz'
        if not self._configured:
            ok, msg = self.configure()
            if not ok:
                return False, msg
        self._homed_ok = False
        self._abort.clear()
        ack = link.home(0x3F)
        if ack.result != Result.OK:
            return False, f'home reddedildi: {ack.result.name}'
        deadline = self._clock() + self.cfg.home_timeout_s
        while self._clock() < deadline:
            st = self._status
            if self._abort.is_set():
                return False, f'homing kesildi: {self._abort_reason}'
            if st is not None and st.state == State.HOMED:
                break
            if st is not None and st.state == State.UNHOMED and not any(
                    a.flags & AxisFlag.MOVING for a in st.axes):
                return False, 'homing bitti ama tüm eksenler referanslanmadı'
            self._sleep(0.02)
        else:
            link.stop_axes()
            return False, 'homing zaman aşımı'
        # sayaç, profildeki home konumunu göstermeli (hardware.py kuralı); aksi halde güvenli değil
        st = self._status
        wrong = []
        for i, ax in enumerate(self.hw.axes):
            expect = ax.to_steps_int(ax.homing.position)
            have = st.axes[i].pos
            if abs(have - expect) > self.cfg.home_tol_steps:
                wrong.append(f'{ax.joint}: sayaç {have}, beklenen {expect}')
        if wrong:
            self.fault = 'home sayacı profille uyuşmuyor: ' + '; '.join(wrong)
            return False, self.fault
        self.fault = None
        self._homed_ok = True
        return True, 'homing tamam; konumlar güvenilir'

    # ------------------------------------------------------------------ durdurma
    def stop(self, reason: str = 'durdur') -> None:
        """Kontrollü durdurma (iptal): yörünge kesilir, eksenler yavaşlayarak durur; MCU ACİL DURA girmez."""
        self._abort_reason = reason
        self._abort.set()
        if self._link is not None:
            self._link.stop_axes()

    def estop(self, reason: str = 'robot API acil dur') -> None:
        """Sürücüleri keser (MCU ESTOPPED, kilitli): sonrasında clear() + homing gerekir."""
        self._abort_reason = reason
        self._abort.set()
        self._homed_ok = False
        self.fault = reason
        if self._link is not None:
            self._link.estop()

    # ------------------------------------------------------------------ yörünge doğrulama / yürütme
    def validate(self, names: Sequence[str], points: Sequence[Point]) -> Optional[ExecResult]:
        """Hiçbir şey göndermeden hedefi denetler; sorun varsa reddeden sonuç, yoksa None."""
        want = list(self.hw.joint_names)
        if sorted(names) != sorted(want):
            return ExecResult(INVALID_JOINTS, f'eklemler {want} olmalı, gelen: {list(names)}')
        if not points or len(points) > self.cfg.max_points:
            return ExecResult(INVALID_GOAL, 'yörünge boş ya da çok uzun')
        last_t = -1.0
        for p in points:
            if not _finite(list(p.q) + [p.t]) or len(p.q) != len(want):
                return ExecResult(INVALID_GOAL, 'yörüngede sonlu olmayan/eksik değer var')
            if p.t < last_t or (p.t == last_t and last_t >= 0):
                return ExecResult(INVALID_GOAL, 'zaman damgaları artmıyor')
            last_t = p.t
        if points[0].t < 0:
            return ExecResult(INVALID_GOAL, 'zaman negatif')
        for ax, i in ((a, k) for k, a in enumerate(self.hw.axes)):
            lo, hi = soft_limits(ax, self.robot)
            for p in points:
                s = ax.to_steps_int(p.q[i])
                if not lo <= s <= hi:
                    return ExecResult(INVALID_GOAL, f'{ax.joint}: hedef limit dışında ({p.q[i]:.3f} rad)')
            vmax = rate_steps(ax, ax.velocity_limit)
            for a, b in zip(points, points[1:]):
                dt = b.t - a.t
                if dt > 1e-6 and abs(ax.to_steps(b.q[i]) - ax.to_steps(a.q[i])) / dt > vmax + 1:
                    return ExecResult(INVALID_GOAL, f'{ax.joint}: istenen hız donanım sınırını aşıyor')
        return None

    def _interp(self, points: Sequence[Point], t: float) -> List[float]:
        if t <= points[0].t:
            return list(points[0].q)
        for a, b in zip(points, points[1:]):
            if t <= b.t:
                f = 0.0 if b.t == a.t else (t - a.t) / (b.t - a.t)
                return [x + (y - x) * f for x, y in zip(a.q, b.q)]
        return list(points[-1].q)

    def execute(self, names: Sequence[str], points: Sequence[Point],
                cancelled: Callable[[], bool] = lambda: False,
                feedback: Optional[Callable[[List[float], List[float], float], None]] = None
                ) -> ExecResult:
        link = self._link
        if link is None or not link.link_ok:
            return ExecResult(INVALID_GOAL, 'ESP32 hattı yok')
        if not self._homed_ok:
            return ExecResult(INVALID_GOAL, 'önce homing gerekli (açılışta ve her acil dur/hat kesintisinden '
                                            'sonra robot "unhomed"dır)')
        bad = self.validate(names, points)
        if bad is not None:
            return bad
        order = [list(names).index(j) for j in self.hw.joint_names]
        pts = [Point(p.t, tuple(p.q[k] for k in order)) for p in points]
        if not self._exec_lock.acquire(blocking=False):
            return ExecResult(INVALID_GOAL, 'başka bir yörünge çalışıyor')
        self.executing = True
        try:
            return self._run(link, pts, cancelled, feedback)
        finally:
            self.executing = False
            self._exec_lock.release()

    def _abort_now(self, link: HwLink, why: str, code: int) -> ExecResult:
        link.stop_axes()
        return ExecResult(code, why)

    def _run(self, link: HwLink, pts: List[Point], cancelled, feedback) -> ExecResult:
        st = self._status
        q0 = self.positions()
        if st is None or q0 is None:
            return ExecResult(INVALID_GOAL, 'konum bilgisi yok')
        if st.state not in (State.HOMED, State.ENABLED):
            return ExecResult(INVALID_GOAL, f'MCU durumu {st.state.name}: hareket edilemez')
        if max(abs(a - b) for a, b in zip(q0, pts[0].q)) > self.cfg.start_tol:
            return ExecResult(INVALID_GOAL, 'yörüngenin başlangıcı robotun şu anki konumundan uzak')
        self._abort.clear()
        if st.state == State.HOMED:
            ack = link.enable()
            if ack.result != Result.OK:
                return ExecResult(INVALID_GOAL, f'ENABLE reddedildi: {ack.result.name}')
        axes = self.hw.axes
        vel = [max(1, rate_steps(a, a.velocity_limit)) for a in axes]
        acc = [max(1, rate_steps(a, a.acceleration_limit)) for a in axes]
        total = pts[-1].t
        t0 = self._clock()
        last = list(q0)
        while True:
            now = self._clock() - t0
            if cancelled():
                return self._abort_now(link, 'iptal edildi', INVALID_GOAL)
            if self._abort.is_set() or not link.link_ok:
                return self._abort_now(link, f'yürütme kesildi: {self._abort_reason or "hat koptu"}',
                                       INVALID_GOAL)
            cur = self.positions()
            sts = self._status
            if cur is None or sts is None or sts.state != State.ENABLED:
                return self._abort_now(link, f'MCU durumu {sts.state.name if sts else "?"}', INVALID_GOAL)
            plan = self._interp(pts, now)
            dev = max(abs(a - b) for a, b in zip(cur, plan))
            if feedback is not None:
                feedback(plan, cur, now)
            if now <= total and dev > self.cfg.path_tol:
                return self._abort_now(link, f'yol toleransı aşıldı ({dev:.3f} rad)', PATH_TOLERANCE_VIOLATED)
            if now > total + self.cfg.overrun_s + self.cfg.settle_s:
                return self._abort_now(link, 'yürütme süresi aşıldı', GOAL_TOLERANCE_VIOLATED)
            tgt_t = min(total, now + self.cfg.lookahead_s)
            tgt = self._interp(pts, tgt_t)
            span = max(tgt_t - now, self.cfg.command_period_s)
            steps = self.hw.joints_to_steps(tgt)
            for i, ax in enumerate(axes):
                if abs(tgt[i] - last[i]) < 1e-9 and now < total:
                    continue
                need = abs(ax.to_steps(tgt[i]) - ax.to_steps(cur[i])) / span
                v = int(min(vel[i], max(1.0, math.ceil(need * 1.25) + 5)))
                ack = link.move(i, steps[i], v, acc[i])
                if ack.result != Result.OK:
                    return self._abort_now(link, f'{ax.joint}: MOVE reddedildi ({ack.result.name})',
                                           INVALID_GOAL)
                last[i] = tgt[i]
            if now >= total:
                break
            self._sleep(self.cfg.command_period_s)
        # hedefe oturma
        deadline = self._clock() + self.cfg.settle_s
        goal = pts[-1].q
        while self._clock() < deadline:
            if cancelled() or self._abort.is_set() or not link.link_ok:
                return self._abort_now(link, 'yürütme kesildi (oturma sırasında)', INVALID_GOAL)
            cur, sts = self.positions(), self._status
            if cur is not None and sts is not None:
                if sts.state != State.ENABLED:
                    return self._abort_now(link, f'MCU durumu {sts.state.name}', INVALID_GOAL)
                still = not any(a.flags & AxisFlag.MOVING for a in sts.axes)
                if still and max(abs(a - b) for a, b in zip(cur, goal)) <= self.cfg.goal_tol:
                    return ExecResult(SUCCESSFUL, 'tamam')
            self._sleep(0.02)
        return self._abort_now(link, 'hedefe oturulamadı (hedef toleransı)', GOAL_TOLERANCE_VIOLATED)
