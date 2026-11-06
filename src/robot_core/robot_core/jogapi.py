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

from robot_core.frames import (
    FrameError,
    FrameStore,
    SphereFrame,
    resolve_rotation,
    sphere_frame,
)
from robot_core.jog import (
    SPHERE_MAX_RADIUS,
    SPHERE_MIN_RADIUS,
    DlsJogEngine,
    JogConfig,
    JogEngine,
    JogError,
    JogStatus,
    JogStep,
    SphereJog,
    reachable_region,
)
from robot_core.kinematics import Chain
from robot_core.se3 import display_wpr
from robot_core.spheres import TYPES, SphereStore
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
INC_TOLERANCE = (2e-4, 2e-4, 1e-4)   # sphere increment done: rad, rad, m left (measured)


def replace_status(step: JogStep, status: JogStatus, messages: Any = None) -> JogStep:
    from dataclasses import replace

    return replace(step, status=status,
                   messages=tuple(messages) if messages is not None else step.messages)


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
        spheres: Optional[SphereStore] = None,
        config: Optional[JogApiConfig] = None,
        jog_config: Optional[JogConfig] = None,
        busy: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.engine, self.chain, self._joints, self._sink = engine, chain, joints, sink
        self.estop, self.frames, self._busy, self._clock = estop, frames, busy, clock
        self.spheres = spheres      # L8: sphere frames (None: sphere jog is off)
        self._reach_lock = threading.Lock()
        self.cfg = config or JogApiConfig()
        jc = jog_config or getattr(engine, 'config', None) or JogConfig()
        self._vmax = (
            np.array([jc.max_linear_speed] * 3 + [jc.max_angular_speed] * 3) * jc.speed_scale
        )
        # sphere jog rates: azimuth rad/s, elevation rad/s, r m/s (the 3 entries after are unused)
        self._vmax_sphere = np.array([self._vmax[3], self._vmax[3], self._vmax[0], 0.0, 0.0, 0.0])
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
        self._sphere: Optional[SphereJog] = None     # L8: set while the frame is a sphere
        self._align = False
        self._sph_inc: Optional[Dict[str, float]] = None

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
            allowed = {'lease', 'frame', 'twist', 'increment', 'align', 'ttl_ms'}
            if not isinstance(body, dict) or set(body) - allowed or 'lease' not in body:
                raise JogDenied(
                    400,
                    'gövde {"lease", "frame", "twist" | "increment" | "align", "ttl_ms"} olmalı',
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
            has_twist, has_inc, has_align = 'twist' in body, 'increment' in body, 'align' in body
            if has_twist + has_inc + has_align != 1:
                raise JogDenied(
                    400,
                    'tam olarak biri gerekir: "twist" (basılı-tut), "increment" '
                    'ya da "align" (yalnız küre çerçevesinde)',
                )
            frame = body.get('frame', 'World')
            if not isinstance(frame, str):
                raise JogDenied(400, 'frame bir ad olmalı (World, Tool ya da öğretilmiş çerçeve)')
            sphere = self._sphere_frame(frame)
            rotation = None
            if sphere is None:
                if has_align:
                    raise JogDenied(400, '"align" yalnız küre çerçevesinde kullanılır')
                try:
                    rotation = resolve_rotation(frame, self.frames)
                except FrameError as exc:
                    raise JogDenied(400, str(exc)) from exc
            elif not isinstance(self.engine, DlsJogEngine):
                raise JogDenied(400, 'bu jog motoru küre çerçevesini desteklemiyor')
            new_session = holder != lease
            if new_session or frame != self._frame:
                if not new_session and self._moving:
                    raise JogDenied(409, 'hareket sürerken çerçeve değiştirilemez')
                self.engine.reset()
                if sphere is None:
                    self._sphere = None
                    self.engine.set_frame(rotation)
                else:  # SphereJog sets the World frame itself on its first step
                    self._sphere = SphereJog(self.engine, sphere)
                self._sph_inc = None
            if has_inc:
                if sphere is not None:
                    self._begin_sphere_increment(body['increment'], q, now)
                    self._align = False
                else:
                    self._begin_increment(body['increment'], q, now)
                mode = 'increment'
            else:
                mode = 'align' if has_align else 'hold'
                self._check_hold(remote)
                if has_align:
                    if body['align'] is not True:
                        raise JogDenied(400, '"align" true olmalı')
                    self._req = np.zeros(6)
                else:
                    self._req = self._parse_twist(body['twist'], sphere is not None)
                if self._inc_until is not None:
                    raise JogDenied(409, 'artımlı hareket sürüyor')
                self._align = has_align
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

    def _sphere_frame(self, name: str) -> Optional[SphereFrame]:
        if self.spheres is None or name not in self.spheres.names():
            return None
        return self.spheres.get(name)

    def _parse_twist(self, twist: Any, sphere: bool = False) -> np.ndarray:
        if sphere:
            ok = (
                isinstance(twist, list)
                and len(twist) == 3
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
                    f'küre çerçevesinde twist 3 sonlu sayı olmalı [azimut yükselme r] '
                    f'(rad/s, rad/s, m/s; |değer| <= {self.cfg.max_twist_abs:g})',
                )
            return np.clip(np.array(list(twist) + [0.0] * 3, float), -self._vmax_sphere,
                           self._vmax_sphere)
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

    def _begin_sphere_increment(self, inc: Any, q: np.ndarray, now: float) -> None:
        """Sphere frame: axis 0 = azimuth (deg), 1 = elevation (deg), 2 = r (mm)."""
        if self._inc_until is not None and now <= self._inc_until:
            raise JogDenied(409, 'önceki artım henüz bitmedi')
        ok = (
            isinstance(inc, dict)
            and set(inc) == {'axis', 'amount'}
            and isinstance(inc['axis'], int)
            and not isinstance(inc['axis'], bool)
            and 0 <= inc['axis'] <= 2
            and isinstance(inc['amount'], (int, float))
            and not isinstance(inc['amount'], bool)
            and math.isfinite(inc['amount'])
        )
        if not ok:
            raise JogDenied(
                400,
                'küre çerçevesinde increment {"axis": 0 azimut | 1 yükselme | 2 r, '
                '"amount": ±(0.1|1|10) ° (azimut, yükselme) ya da mm (r)} olmalı',
            )
        if not any(math.isclose(abs(inc['amount']), a, rel_tol=1e-9) for a in AMOUNTS_MM):
            raise JogDenied(
                400,
                f'artım yalnızca {", ".join(f"{a:g}" for a in AMOUNTS_MM)} (° ya da mm) olabilir',
            )
        axis = inc['axis']
        amount = inc['amount'] * (math.pi / 180.0 if axis < 2 else 1e-3)
        try:
            az, el, dist = self._sphere.angles(q)
        except (FrameError, JogError) as exc:
            raise JogDenied(400, str(exc)) from exc
        limit = math.radians(self._sphere.config.max_elevation_deg)
        if axis == 1 and abs(el + amount) > limit:
            raise JogDenied(
                400,
                f'yükselme ±{self._sphere.config.max_elevation_deg:g}° sınırını aşar '
                f'(şimdi {math.degrees(el):.1f}°)',
            )
        if axis == 2 and not SPHERE_MIN_RADIUS <= dist + amount <= SPHERE_MAX_RADIUS:
            raise JogDenied(
                400,
                f'r [{SPHERE_MIN_RADIUS:g}, {SPHERE_MAX_RADIUS:g}] m dışına çıkar '
                f'(şimdi {dist:.3f} m)',
            )
        value = (az, el, dist)[axis]
        self._sph_inc = {'axis': axis, 'target': value + float(amount)}
        # an arc is long at a few cm/s: the time limit follows the path (azimuth 10 deg at r 0.7 m
        # is 0.12 m = 6 s at the default 2 cm/s), twice the estimate plus a second
        lever = dist * math.cos(el) if axis == 0 else (dist if axis == 1 else 1.0)
        estimate = abs(amount) * lever / max(float(self._vmax[0]), 1e-9)
        self._inc_until = now + max(self.cfg.increment_timeout_s, 2.0 * estimate + 1.0)
        # not the previous increment's 'done'
        self._status, self._messages = JogStatus.OK.value, []
        self._req = np.zeros(6)
        self._applied = np.zeros(6)

    def _sphere_step(self, q: np.ndarray, dt: float, inc: bool, align: bool) -> JogStep:
        """One tick of the sphere jog: hold rates, align, or an azimuth/elevation/r increment.

        The increment is closed on the MEASURED angle (``target`` is fixed when it starts): the
        sphere path has no target pose to pull the TCP back like the Cartesian increment has, so
        counting only the commanded progress would stop short by whatever the plant lags."""
        sph = self._sphere
        if not (inc and self._sph_inc is not None):
            return sph.step(q, self._applied[:3], dt, align=align)
        axis, target = int(self._sph_inc['axis']), self._sph_inc['target']
        now_value = sph.angles(q)[axis]
        remaining = target - now_value
        if axis == 0:
            remaining = math.atan2(math.sin(remaining), math.cos(remaining))
        if abs(remaining) <= INC_TOLERANCE[axis]:
            step = sph.step(q, [0.0, 0.0, 0.0], dt)
            return replace_status(step, JogStatus.DONE, ())
        rates = [0.0, 0.0, 0.0]
        rates[axis] = math.copysign(min(self._vmax_sphere[axis], abs(remaining) / dt), remaining)
        step = sph.step(q, rates, dt)
        if not step.moved and step.status in (JogStatus.IDLE, JogStatus.OK, JogStatus.LIMITED):
            return replace_status(step, JogStatus.ABORTED,
                                  step.messages or ('artım ilerleyemedi',))
        return step

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
                'sphere': self._sphere_status(),
            }

    def _sphere_status(self) -> Optional[Dict[str, Any]]:
        """Where the TCP is on the active sphere frame (None: no sphere session)."""
        sph = self._sphere
        q = self._q() if sph is not None and self._lease else None
        if sph is None or q is None:
            return None
        try:
            az, el, dist = sph.angles(q)
        except (FrameError, JogError):
            return None
        return {
            'name': sph.frame.name,
            'azimuth_deg': round(math.degrees(az), 2),
            'elevation_deg': round(math.degrees(el), 2),
            'radius_m': round(dist, 4),
            'on_sphere': abs(dist - sph.radius) <= sph.config.position_tolerance,
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
                self._sph_inc = None
                self._moving = False
                self.engine.reset()
                self._sink.hold(self.chain.joint_names, q)
                return
            want = self._req if (not inc and now <= self._expires) else np.zeros(6)
            align = self._sphere is not None and self._align and not inc and now <= self._expires
            vmax = self._vmax_sphere if self._sphere is not None else self._vmax
            # commanded speed falls linearly to zero within stop_ramp_s (rises at the same rate)
            rate = vmax / self.cfg.stop_ramp_s
            self._applied = self._applied + np.clip(want - self._applied, -rate * dt, rate * dt)
            if abs(self._applied).max() < 1e-9:
                self._applied = np.zeros(6)
            if not inc and not align and not self._applied.any() and not want.any():
                self._moving = False
                if self._status in (JogStatus.OK.value, JogStatus.LIMITED.value):
                    self._status, self._messages = JogStatus.IDLE.value, []
                if now - self._last_cmd > self.cfg.lease_hold_s:
                    self._end('kiralama süresi doldu')
                return
            try:
                if self._sphere is not None:
                    step = self._sphere_step(q, max(dt, 1e-3), inc, align)
                else:
                    step = self.engine.step(
                        q, self._applied if not inc else np.zeros(6), max(dt, 1e-3)
                    )
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
                # a long (sphere) increment outlives the lease hold time: restart it, or the
                # session ends on the very next tick and the result is never visible
                self._last_cmd = now
                self._inc_until = None
                self._sph_inc = None
                self._moving = False
            elif step.status in STOPPED_STATUSES:
                self._applied = np.zeros(6)
                if self._sphere is not None and inc:       # a sphere increment cannot go on
                    self._inc_until = None
                    self._sph_inc = None
                    self._moving = False

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
def sphere_document(frame: SphereFrame) -> Dict[str, Any]:
    return frame.as_dict()


def frames_document(store: FrameStore, spheres: Optional[SphereStore] = None,
                    spheres_error: Optional[str] = None) -> Dict[str, Any]:
    """``GET /api/frames``. Additive (L8): ``types`` (what PUT understands), ``type: "user"`` on
    every user frame, ``spheres`` as a SEPARATE list (an old client that copies ``frames`` back
    with ``PUT`` then keeps working and never meets an entry it cannot read)."""
    from robot_core.frames import frame_to_dict

    out = []
    for name in store.names():
        frame = store.get(name)
        doc = {'type': 'user', **frame_to_dict(frame)}
        wpr = display_wpr(frame.rotation_matrix)
        doc['wpr_deg'] = [round(wpr['w'], 2), round(wpr['p'], 2), round(wpr['r'], 2)]
        doc['warnings'] = list(frame.warnings) + ([wpr['warning']] if wpr['warning'] else [])
        out.append(doc)
    doc = {
        'builtin': ['World', 'Tool'],
        'max_frames': store.limits.max_frames,
        'types': list(TYPES) if spheres is not None else ['user'],
        'frames': out,
        'spheres': [sphere_document(s) for s in spheres.all()] if spheres is not None else [],
        'max_spheres': spheres.max_spheres if spheres is not None else 0,
    }
    if spheres_error:
        doc['spheres_error'] = spheres_error
    return doc


def _unknown_type(kind: Any, where: str) -> FrameError:
    return FrameError(
        f'{where}: bilinmeyen çerçeve türü {kind!r} (desteklenen: {", ".join(TYPES)}); '
        'sessizce atlanmaz'
    )


def replace_frames(
    store: FrameStore,
    body: Any,
    guard: Optional[Callable[[Dict[str, Any]], None]] = None,
    spheres: Optional[SphereStore] = None,
    spheres_error: Optional[str] = None,
) -> Dict[str, Any]:
    """``PUT /api/frames``: replace the WHOLE user-frame list (validated first, written atomically).

    L8: the body may also carry ``"spheres": [...]``. Present: the sphere list is replaced as well;
    ABSENT: the stored spheres are left alone (an old Hub that knows only user frames can neither
    see nor delete them). ``type`` is optional on a user frame (default ``user``); any other type
    in ``frames`` is an error that names it (spheres go in ``spheres``). Raises
    :class:`FrameError` (400) with the reason; nothing is written on any error."""
    from robot_core.frames import check_name, frame_from_three_points

    if (
        not isinstance(body, dict)
        or not {'frames'} <= set(body) <= {'frames', 'spheres'}
        or not isinstance(body['frames'], list)
    ):
        raise FrameError(
            'gövde {"frames": [{"name", "points": {"origin", "x", "xy"}}, ...], '
            '"spheres": [{"type": "sphere", "name", "center", "radius"}, ...] (isteğe bağlı)} '
            'olmalı'
        )
    if len(body['frames']) > store.limits.max_frames:
        raise FrameError(f'en çok {store.limits.max_frames} çerçeve')
    new = {}
    for i, entry in enumerate(body['frames']):
        if isinstance(entry, dict) and entry.get('type', 'user') != 'user':
            kind = entry['type']
            if kind == 'sphere':
                raise FrameError(
                    f'frames[{i}]: küre çerçeveleri "spheres" listesinde gönderilir, '
                    '"frames"te değil'
                )
            raise _unknown_type(kind, f'frames[{i}]')
        if (
            not isinstance(entry, dict)
            or set(entry) - {'type'} != {'name', 'points'}
            or not isinstance(entry['points'], dict)
            or set(entry['points']) != {'origin', 'x', 'xy'}
        ):
            raise FrameError(f'frames[{i}]: {{"name", "points": {{"origin", "x", "xy"}}}} olmalı')
        name = check_name(entry['name'])
        if name.lower() in {n.lower() for n in new}:
            raise FrameError(f'frames[{i}]: ad tekrar ediyor: {name!r}')
        p = entry['points']
        new[name] = frame_from_three_points(name, p['origin'], p['x'], p['xy'], store.limits)
    new_spheres = None
    if 'spheres' in body:
        if spheres is None:
            raise FrameError(
                'küre çerçeveleri bu sunucuda kullanılamıyor'
                + (f': {spheres_error}' if spheres_error else '')
            )
        new_spheres = _parse_spheres(body['spheres'], spheres.max_spheres)
    kept = spheres.all() if spheres is not None and new_spheres is None else (new_spheres or [])
    clash = {n.lower() for n in new} & {sp.name.lower() for sp in kept}
    if clash:
        raise FrameError(f'çerçeve adı hem kullanıcı hem küre olamaz: {", ".join(sorted(clash))}')
    with store._lock:  # J1's FrameStore has no replace-all (Robot_2'den istek)
        if guard is not None:
            guard(new)  # e.g. waypoints expressed in a frame that would vanish (O2)
        store._save(new)
        store._frames = new
    if new_spheres is not None:
        spheres.replace_all(new_spheres)
    return frames_document(store, spheres, spheres_error)


def _parse_spheres(entries: Any, limit: int) -> List[SphereFrame]:
    if not isinstance(entries, list):
        raise FrameError('spheres: liste olmalı')
    if len(entries) > limit:
        raise FrameError(f'en çok {limit} küre çerçevesi')
    out: List[SphereFrame] = []
    for i, entry in enumerate(entries):
        where = f'spheres[{i}]'
        if not isinstance(entry, dict):
            raise FrameError(f'{where}: {{"type": "sphere", "name", "center", "radius"}} olmalı')
        kind = entry.get('type', 'sphere')
        if kind != 'sphere':
            raise _unknown_type(kind, where)
        extra = set(entry) - {'type', 'name', 'center', 'radius', 'tool_axis'}
        missing = {'name', 'center', 'radius'} - set(entry)
        if extra or missing:
            raise FrameError(f'{where}: bilinmeyen {sorted(extra) or "-"}, eksik '
                             f'{sorted(missing) or "-"}')
        try:
            sphere = sphere_frame(entry['name'], entry['center'], entry['radius'],
                                  entry.get('tool_axis', 'z'))
        except FrameError as exc:
            raise FrameError(f'{where}: {exc}') from exc
        if sphere.name.lower() in {s.name.lower() for s in out}:
            raise FrameError(f'{where}: ad tekrar ediyor: {sphere.name!r}')
        out.append(sphere)
    return out


# ---- reachable range of a sphere (C, r) before it is created ------------------------------------
REACH_AZ_STEP_DEG, REACH_EL_STEP_DEG = 15.0, 10.0       # coarse: the API answers in seconds


def sphere_reach(jog: 'JogController', body: Any) -> Dict[str, Any]:
    """``POST /api/frames/reach {"center": [x,y,z], "radius": r, "tool_axis"?}``: which (azimuth,
    elevation) cells of that sphere the jog can reach from the CURRENT pose. Does not move the
    robot, changes nothing. IK + safe box + joint limits + singularity; NO collision model."""
    if (
        not isinstance(body, dict)
        or not {'center', 'radius'} <= set(body) <= {'center', 'radius', 'tool_axis'}
    ):
        raise JogDenied(400, 'gövde {"center": [x,y,z], "radius": r, "tool_axis"?} olmalı')
    try:
        probe = sphere_frame('probe', body['center'], body['radius'], body.get('tool_axis', 'z'))
    except FrameError as exc:
        raise JogDenied(400, str(exc)) from exc
    if not isinstance(jog.engine, DlsJogEngine):
        raise JogDenied(400, 'bu jog motoru erişim aralığı hesaplayamaz')
    if jog.estop.is_set:
        raise JogDenied(423, ESTOP_MESSAGE, state='estopped')
    if jog.active or jog._busy():
        raise JogDenied(409, 'jog ya da iş sürerken erişim aralığı hesaplanmaz', state='busy')
    q = jog._q()
    if q is None:
        raise JogDenied(503, 'eklem durumu okunamıyor: erişim aralığı hesaplanamaz')
    if not jog._reach_lock.acquire(blocking=False):
        raise JogDenied(409, 'başka bir erişim aralığı hesabı sürüyor')
    try:
        reach = reachable_region(jog.engine, probe, q, az_step_deg=REACH_AZ_STEP_DEG,
                                 el_step_deg=REACH_EL_STEP_DEG)
    except (JogError, FrameError) as exc:
        raise JogDenied(400, str(exc)) from exc
    finally:
        jog._reach_lock.release()
    rng = reach.elevation_range
    rows = [
        {'elevation_deg': float(e), 'azimuth_intervals_deg': [
            [round(a, 1), round(b, 1)] for a, b in reach.azimuth_intervals(float(e))]}
        for e in reach.elevations if reach._row(float(e)).any()
    ]
    return {
        'ok': True,
        'center': list(probe.center),
        'radius': probe.radius,
        'tool_axis': probe.tool_axis,
        'reachable': reach.reachable,
        'elevation_range_deg': None if rng is None else [rng[0], rng[1]],
        'rows': rows,
        'grid': {
            'azimuth_deg': [float(a) for a in reach.azimuths],
            'elevation_deg': [float(e) for e in reach.elevations],
            'cells': [''.join('1' if c else '0' for c in row) for row in reach.grid],
        },
        'summary': reach.summary(),
        'note': 'başlangıç pozundan bağlantılı hücreler; IK + güvenli kutu + eklem limiti + '
        'tekillik; çarpışma modeli YOK; ızgara kaba (15° azimut, 10° yükselme)',
    }
