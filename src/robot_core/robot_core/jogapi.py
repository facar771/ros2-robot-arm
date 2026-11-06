"""Jog over the Robot API (J2), ROS-free: lease + dead-man + tick loop around the J1 engine.

``POST /api/jog`` is both the command and the heartbeat. The SERVER enforces the dead-man: a command
is valid for ``ttl_ms`` (default 250 ms, never more than 500 ms, whatever the client asks); when it
runs out the commanded velocity is ramped to zero within ``stop_ramp_s`` (100 ms). Nothing here
trusts the client to stop. One lease (client-chosen token) at a time; a second client gets ``409``.

The tick loop (50 Hz) reads the MEASURED joints, asks :class:`robot_core.jog.JogEngine` for the next
joint command and streams a SHORT trajectory (``horizon_ticks`` x ``tick_s`` <= 100 ms) to the
controller through a :class:`SetpointPort`. If this process dies, the controller finishes the last
<=100 ms and stands still: there is no long trajectory to run away on.

Stops that do not need the client: latched e-stop (the session is dropped on the next tick, a hold
setpoint is sent, every POST answers 423), a job that starts (jog and jobs both write to the
trajectory controller, so they exclude each other), lost joint states, a tick gap longer than the
engine allows. Hold-to-run (continuous twist) is a separate, explicitly chosen mode that is OFF by
default for ``mode: real`` and for remote (non-loopback) clients; the default is incremental
(0.1 / 1 / 10 mm and degrees).

Every number here is a SUGGESTION to tune in simulation (see docs/JOG.md); defaults are narrow.
A software dead-man/e-stop does NOT replace the hardware emergency stop.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

import numpy as np

from robot_core.frames import FrameError, FrameStore, resolve_rotation
from robot_core.jog import JogConfig, JogEngine, JogError, JogStatus
from robot_core.kinematics import Chain
from robot_core.se3 import display_wpr
from robot_core.supervisor import ESTOP_MESSAGE, EStop

log = logging.getLogger('robot_core.jogapi')

TTL_DEFAULT_MS, TTL_MAX_MS, TTL_MIN_MS = 250, 500, 50
LEASE_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
AMOUNTS_MM = (0.1, 1.0, 10.0)  # incremental steps allowed (also degrees)
STOPPED_STATUSES = (
    JogStatus.SINGULARITY,
    JogStatus.JOINT_LIMIT,
    JogStatus.BOX_STOP,
    JogStatus.TRACKING,
    JogStatus.ABORTED,
)
READY_POSE = (
    0.0,
    -0.1,
    1.65,
    0.0,
    1.2,
    0.0,
)
# SUGGESTION: TCP ~(0.51, 0, 0.43), >=0.2 m from every box wall, sigma_min 0.23, joints >=0.3 rad
# from the limits
READY_NAME = 'ready'


class JogDenied(Exception):
    """The request was refused: ``status`` is the HTTP code, ``message`` says why (Turkish)."""

    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


class JointPort(Protocol):
    def joint_positions(self) -> Optional[Dict[str, float]]: ...


class SetpointPort(Protocol):
    def send(
        self, names: Sequence[str], positions: Sequence[Sequence[float]], times_s: Sequence[float]
    ) -> None:
        """Replace the controller's trajectory with this short one (times from now)."""

    def hold(self, names: Sequence[str], position: Sequence[float]) -> None:
        """Replace whatever is running with "stay here"."""


@dataclass(frozen=True)
class JogApiConfig:
    tick_s: float = 0.02
    horizon_ticks: int = 3  # setpoint horizon = horizon_ticks * tick_s <= 100 ms
    stop_ramp_s: float = 0.10  # commanded speed reaches zero this fast (<= 150 ms)
    lease_hold_s: float = 2.0  # the lease stays reserved this long after the last command
    increment_timeout_s: float = 5.0
    hold_enabled: bool = True  # continuous (hold-to-run) jog; real mode: False
    hold_remote_enabled: bool = False  # ... for non-loopback clients (phone)
    max_twist_abs: float = 1.0  # |component| above this is a malformed request

    def __post_init__(self) -> None:
        if not (
            0 < self.tick_s <= 0.05
            and 1 <= self.horizon_ticks <= 5
            and self.tick_s * self.horizon_ticks <= 0.1 + 1e-9
            and 0 < self.stop_ramp_s <= 0.15
            and self.lease_hold_s >= 0
        ):
            raise ValueError(
                'JogApiConfig: tick<=50 ms, ufuk<=100 ms, durma rampası<=150 ms olmalı'
            )


def clamp_ttl_ms(value: Any) -> int:
    """Server-side TTL: default 250, at least 50, at most 500 (a client cannot ask for more)."""
    if value is None:
        return TTL_DEFAULT_MS
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise JogDenied(400, f'ttl_ms sayı olmalı, verilen: {value!r}')
    return int(min(TTL_MAX_MS, max(TTL_MIN_MS, value)))


class JogController:
    def __init__(
        self,
        engine: JogEngine,
        chain: Chain,
        joints: JointPort,
        sink: SetpointPort,
        estop: EStop,
        frames: FrameStore,
        *,
        config: Optional[JogApiConfig] = None,
        jog_config: Optional[JogConfig] = None,
        busy: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.engine, self.chain, self._joints, self._sink = engine, chain, joints, sink
        self.estop, self.frames, self._busy, self._clock = estop, frames, busy, clock
        self.cfg = config or JogApiConfig()
        jc = jog_config or getattr(engine, 'config', None) or JogConfig()
        self._vmax = (
            np.array([jc.max_linear_speed] * 3 + [jc.max_angular_speed] * 3) * jc.speed_scale
        )
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._reset_session()
        self._status = JogStatus.IDLE.value
        self._messages: List[str] = []

    # ---- session ----------------------------------------------------------------------------
    def _reset_session(self) -> None:
        self._lease: Optional[str] = None
        self._last_cmd = 0.0
        self._expires = 0.0
        self._ttl_ms = TTL_DEFAULT_MS
        self._frame = 'World'
        self._mode = 'increment'
        self._req = np.zeros(6)
        self._applied = np.zeros(6)
        self._inc_until: Optional[float] = None
        self._last_tick: Optional[float] = None
        self._moving = False

    def _end(self, why: str, hold: bool = False) -> None:
        """Drop the session (caller holds the lock); optionally send a hold setpoint."""
        had = self._lease is not None
        if had and hold:
            q = self._q()
            if q is not None:
                try:
                    self._sink.hold(self.chain.joint_names, q)
                except Exception:  # noqa: BLE001
                    log.exception('jog hold setpoint gönderilemedi')
        self._reset_session()
        try:
            self.engine.reset()
        except Exception:  # noqa: BLE001
            pass
        if had:
            self._status, self._messages = JogStatus.IDLE.value, [why]

    def _q(self) -> Optional[np.ndarray]:
        pos = self._joints.joint_positions()
        if not pos or any(n not in pos for n in self.chain.joint_names):
            return None
        q = np.array([pos[n] for n in self.chain.joint_names], float)
        return q if np.all(np.isfinite(q)) else None

    @property
    def active(self) -> bool:
        """True while a lease is held and it is either moving or inside its TTL."""
        with self._lock:
            if self._lease is None:
                return False
            return self._moving or self._clock() <= self._expires or self._inc_until is not None

    # ---- request ----------------------------------------------------------------------------
    def command(self, body: Dict[str, Any], *, remote: bool = False) -> Dict[str, Any]:
        """Validate and apply one ``POST /api/jog`` body; raises :class:`JogDenied`."""
        now = self._clock()
        with self._lock:
            if self.estop.is_set:
                self._end('acil dur', hold=True)
                raise JogDenied(423, ESTOP_MESSAGE, state='estopped')
            allowed = {'lease', 'frame', 'twist', 'increment', 'ttl_ms'}
            if not isinstance(body, dict) or set(body) - allowed or 'lease' not in body:
                raise JogDenied(
                    400, 'gövde {"lease", "frame", "twist" | "increment", "ttl_ms"} olmalı'
                )
            lease = body['lease']
            if not isinstance(lease, str) or not LEASE_RE.fullmatch(lease):
                raise JogDenied(400, 'lease 8-64 karakterlik harf/rakam/_/- bir belirteç olmalı')
            ttl = clamp_ttl_ms(body.get('ttl_ms')) if body.get('ttl_ms') != 0 else 0
            clamped = 'ttl_ms' in body and body['ttl_ms'] not in (None, 0) and ttl != body['ttl_ms']
            holder = self._lease
            if holder is not None and holder != lease:
                if (
                    now <= self._expires
                    or self._inc_until is not None
                    or now - self._last_cmd < self.cfg.lease_hold_s
                ):
                    raise JogDenied(409, 'jog başka bir istemcinin kiralamasında', state='jogging')
                self._end('kiralama devredildi')
            if ttl == 0:  # explicit release
                if holder == lease:
                    self._end('kiralama bırakıldı')
                return self._reply(now, False)
            if self._busy():
                raise JogDenied(
                    409, 'bir iş çalışıyor: jog ve işler birbirini dışlar', state='busy'
                )
            q = self._q()
            if q is None:
                raise JogDenied(503, 'eklem durumu okunamıyor: jog yapılamaz')
            has_twist, has_inc = 'twist' in body, 'increment' in body
            if has_twist == has_inc:
                raise JogDenied(
                    400, 'tam olarak biri gerekir: "twist" (basılı-tut) ya da "increment"'
                )
            frame = body.get('frame', 'World')
            if not isinstance(frame, str):
                raise JogDenied(400, 'frame bir ad olmalı (World, Tool ya da öğretilmiş çerçeve)')
            try:
                rotation = resolve_rotation(frame, self.frames)
            except FrameError as exc:
                raise JogDenied(400, str(exc)) from exc
            new_session = holder != lease
            if new_session or frame != self._frame:
                if not new_session and self._moving:
                    raise JogDenied(409, 'hareket sürerken çerçeve değiştirilemez')
                self.engine.reset()
                self.engine.set_frame(rotation)
            if has_inc:
                self._begin_increment(body['increment'], q, now)
                mode = 'increment'
            else:
                mode = 'hold'
                self._check_hold(remote)
                self._req = self._parse_twist(body['twist'])
                if self._inc_until is not None:
                    raise JogDenied(409, 'artımlı hareket sürüyor')
            self._lease, self._frame, self._mode = lease, frame, mode
            self._last_cmd, self._ttl_ms = now, ttl
            self._expires = now + ttl / 1000.0
            if new_session:
                self._last_tick = None
            return self._reply(now, clamped)

    def _check_hold(self, remote: bool) -> None:
        if not self.cfg.hold_enabled:
            raise JogDenied(
                403,
                'basılı-tut jog bu sunucuda kapalı (yalnızca artımlı); '
                'sunucu yapılandırmasıyla açılır',
            )
        if remote and not self.cfg.hold_remote_enabled:
            raise JogDenied(403, 'basılı-tut jog uzak (loopback dışı) istemcilere kapalı')

    def _parse_twist(self, twist: Any) -> np.ndarray:
        ok = (
            isinstance(twist, list)
            and len(twist) == 6
            and all(
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(v)
                and abs(v) <= self.cfg.max_twist_abs
                for v in twist
            )
        )
        if not ok:
            raise JogDenied(
                400,
                f'twist 6 sonlu sayı olmalı [vx vy vz wx wy wz] '
                f'(m/s, rad/s; |değer| <= {self.cfg.max_twist_abs:g})',
            )
        return np.clip(np.array(twist, float), -self._vmax, self._vmax)  # server clips

    def _begin_increment(self, inc: Any, q: np.ndarray, now: float) -> None:
        if self._inc_until is not None and now <= self._inc_until:
            raise JogDenied(409, 'önceki artım henüz bitmedi')
        ok = (
            isinstance(inc, dict)
            and set(inc) == {'axis', 'amount'}
            and isinstance(inc['axis'], int)
            and not isinstance(inc['axis'], bool)
            and 0 <= inc['axis'] <= 5
            and isinstance(inc['amount'], (int, float))
            and not isinstance(inc['amount'], bool)
            and math.isfinite(inc['amount'])
        )
        if not ok:
            raise JogDenied(
                400,
                'increment {"axis": 0-5 (X Y Z W P R), "amount": ±(0.1|1|10) mm ya da °} olmalı',
            )
        mag = abs(inc['amount'])
        if not any(math.isclose(mag, a, rel_tol=1e-9) for a in AMOUNTS_MM):
            raise JogDenied(
                400,
                f'artım yalnızca {", ".join(f"{a:g}" for a in AMOUNTS_MM)} (mm ya da °) olabilir',
            )
        amount = inc['amount'] * (1e-3 if inc['axis'] < 3 else math.pi / 180.0)
        try:
            self.engine.begin_increment(q, inc['axis'], amount)
        except JogError as exc:
            raise JogDenied(400, str(exc)) from exc
        self._inc_until = now + self.cfg.increment_timeout_s
        self._req = np.zeros(6)
        self._applied = np.zeros(6)

    def _reply(self, now: float, clamped: bool) -> Dict[str, Any]:
        doc = self.status()
        doc.update(
            {
                'ok': True,
                'ttl_ms': self._ttl_ms,
                'ttl_clamped': clamped,
                'stop_ramp_ms': int(self.cfg.stop_ramp_s * 1000),
            }
        )
        return doc

    # ---- status -----------------------------------------------------------------------------
    def tcp_document(self) -> Optional[Dict[str, Any]]:
        q = self._q()
        if q is None:
            return None
        pose = self.chain.fk(q)
        wpr = display_wpr(pose[:3, :3])
        return {
            'frame': 'World',
            'position': [round(float(v), 4) for v in pose[:3, 3]],
            'wpr_deg': [round(wpr['w'], 2), round(wpr['p'], 2), round(wpr['r'], 2)],
            'warning': wpr['warning'],
        }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'enabled': True,
                'active': self.active,
                'mode': self._mode if self._lease else None,
                'frame': self._frame if self._lease else None,
                'status': self._status,
                'messages': list(self._messages),
                'moving': self._moving,
                'hold_enabled': self.cfg.hold_enabled,
                'speed': {
                    'max_linear_m_s': float(self._vmax[0]),
                    'max_angular_rad_s': float(self._vmax[3]),
                },
                'ttl_max_ms': TTL_MAX_MS,
                'increments': list(AMOUNTS_MM),
            }

    # ---- tick -------------------------------------------------------------------------------
    def tick(self) -> None:
        now = self._clock()
        with self._lock:
            if self._lease is None:
                return
            if self.estop.is_set:
                self._end('acil dur: jog iptal', hold=True)
                return
            if self._busy():
                self._end('bir iş başladı: jog iptal', hold=True)
                return
            q = self._q()
            if q is None:
                self._end('eklem durumu kayıp: jog iptal', hold=True)
                return
            dt = self.cfg.tick_s if self._last_tick is None else now - self._last_tick
            self._last_tick = now
            inc = self._inc_until is not None
            if inc and now > self._inc_until:
                self._status, self._messages = (
                    JogStatus.ABORTED.value,
                    ['artım zaman aşımına uğradı'],
                )
                self._inc_until = None
                self._moving = False
                self.engine.reset()
                self._sink.hold(self.chain.joint_names, q)
                return
            want = self._req if (not inc and now <= self._expires) else np.zeros(6)
            # commanded speed falls linearly to zero within stop_ramp_s (rises at the same rate)
            rate = self._vmax / self.cfg.stop_ramp_s
            self._applied = self._applied + np.clip(want - self._applied, -rate * dt, rate * dt)
            if abs(self._applied).max() < 1e-9:
                self._applied = np.zeros(6)
            if not inc and not self._applied.any() and not want.any():
                self._moving = False
                if self._status in (JogStatus.OK.value, JogStatus.LIMITED.value):
                    self._status, self._messages = JogStatus.IDLE.value, []
                if now - self._last_cmd > self.cfg.lease_hold_s:
                    self._end('kiralama süresi doldu')
                return
            try:
                step = self.engine.step(q, self._applied if not inc else np.zeros(6), max(dt, 1e-3))
            except JogError as exc:
                self._end(f'jog durdu: {exc}', hold=True)
                return
            self._status, self._messages = step.status.value, list(step.messages)
            if step.moved:
                k = self.cfg.horizon_ticks
                pts = [step.q_next + step.dq * i for i in range(k)]
                times = [self.cfg.tick_s * (i + 1) for i in range(k)]
                self._sink.send(self.chain.joint_names, [p.tolist() for p in pts], times)
            self._moving = bool(step.moved)
            if step.status in (JogStatus.DONE, JogStatus.ABORTED):
                self._inc_until = None
                self._moving = False
            elif step.status in STOPPED_STATUSES:
                self._applied = np.zeros(6)

    # ---- thread -----------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='jog-tick', daemon=True)
            self._thread.start()

    def _run(self) -> None:
        """Ticks are paced by the controller CLOCK (the node's sim time under Gazebo), not the wall
        clock: a simulation running slower than real time then sees the same setpoint rate per
        simulated second, so the arm keeps up with the commands."""
        last = None
        while not self._stop.is_set():
            now = self._clock()
            if last is None or now - last >= self.cfg.tick_s - 1e-9 or now < last:
                last = now
                try:
                    self.tick()
                except Exception:    # noqa: BLE001 - a bug must stop the jog, not the process
                    log.exception('jog tick hatası: oturum kapatılıyor')
                    with self._lock:
                        self._end('iç hata: jog durdu', hold=True)
            self._stop.wait(0.002)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            self._end('kapatıldı', hold=True)


# ---- frames (single source of truth: the Robot API) ----------------------------------------------
def frames_document(store: FrameStore) -> Dict[str, Any]:
    from robot_core.frames import frame_to_dict

    out = []
    for name in store.names():
        frame = store.get(name)
        doc = frame_to_dict(frame)
        wpr = display_wpr(frame.rotation_matrix)
        doc['wpr_deg'] = [round(wpr['w'], 2), round(wpr['p'], 2), round(wpr['r'], 2)]
        doc['warnings'] = list(frame.warnings) + ([wpr['warning']] if wpr['warning'] else [])
        out.append(doc)
    return {'builtin': ['World', 'Tool'], 'max_frames': store.limits.max_frames, 'frames': out}


def replace_frames(
    store: FrameStore, body: Any, guard: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """``PUT /api/frames``: replace the WHOLE user-frame list (validated first, written atomically).
    Raises :class:`FrameError` (400) with the reason; the file is untouched on any error."""
    from robot_core.frames import check_name, frame_from_three_points

    if (
        not isinstance(body, dict)
        or set(body) != {'frames'}
        or not isinstance(body['frames'], list)
    ):
        raise FrameError(
            'gövde {"frames": [{"name", "points": {"origin", "x", "xy"}}, ...]} olmalı'
        )
    if len(body['frames']) > store.limits.max_frames:
        raise FrameError(f'en çok {store.limits.max_frames} çerçeve')
    new = {}
    for i, entry in enumerate(body['frames']):
        if (
            not isinstance(entry, dict)
            or set(entry) != {'name', 'points'}
            or not isinstance(entry['points'], dict)
            or set(entry['points']) != {'origin', 'x', 'xy'}
        ):
            raise FrameError(f'frames[{i}]: {{"name", "points": {{"origin", "x", "xy"}}}} olmalı')
        name = check_name(entry['name'])
        if name.lower() in {n.lower() for n in new}:
            raise FrameError(f'frames[{i}]: ad tekrar ediyor: {name!r}')
        p = entry['points']
        new[name] = frame_from_three_points(name, p['origin'], p['x'], p['xy'], store.limits)
    with store._lock:  # J1's FrameStore has no replace-all (Robot_2'den istek)
        if guard is not None:
            guard(new)  # e.g. waypoints expressed in a frame that would vanish (O2)
        store._save(new)
        store._frames = new
    return frames_document(store)
