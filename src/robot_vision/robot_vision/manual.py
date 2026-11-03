"""Manual control of the camera platform: command validation and safe rate generation.

ROS-free (the ROS adapter is ``robot_vision_ros.manual_node``). The platform is the 3-DoF chain of
:mod:`robot_vision.platform`; here its axes carry the operator's names:

=========  ===================  =======================================
``pan``    yaw joint (rad)      left / right (positive = counter-clockwise from above)
``tilt``   pitch joint (rad)    up / down (**positive = camera looks DOWN**)
``slide``  slide joint (m)      the platform's forward position
=========  ===================  =======================================

Commands are JSON objects (see :func:`parse_command`)::

    {"op": "goto", "pan": 0.3, "tilt": 0.2, "slide": 1.0, "speed": 0.5}   # any subset of the axes
    {"op": "jog", "pan_rate": 0.2, "tilt_rate": -0.1, "ttl_s": 0.5}       # dead-man: expires
    {"op": "stop"}

Safety rules (every one has a test):

* A command is **refused as a whole** (nothing moves, the running motion is left as it was) if it is
  not valid JSON, has an unknown key/op, a non-number (bool, string), a NaN/Infinity (also as the
  JSON literals ``NaN`` / ``Infinity``), a ``goto`` target outside the joint limits, or a ``jog``
  rate above the manual rate cap. Targets are never silently clamped.
* The manual rate cap is ``rate_scale`` (default 0.5) of the platform's rate limits; velocities also
  change at most ``accel_scale`` x cap per second (no steps), except ``stop``, which is immediate.
* Near a joint limit the outward rate is cut (braking zone) and is zero AT the limit.
* ``jog`` expires after ``ttl_s`` (default 0.5 s, at most 2 s): a vanished operator stops the
  camera.
  ``goto`` gives up after ``goto_timeout_s``. Without a fresh joint state the motion stops.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from robot_vision.platform import PlatformLimits

AXES = ('pan', 'slide', 'tilt')          # same order as the platform's q = (yaw, slide, pitch)
OPS = ('goto', 'jog', 'stop')
MAX_TTL_S = 2.0

Rates = Tuple[float, float, float]       # (yaw rad/s, slide m/s, pitch rad/s)


class CommandError(ValueError):
    """A refused command. ``code``: ``bad_json`` | ``bad_command`` | ``non_finite`` |
    ``out_of_limits`` | ``rate_too_high`` (the ROS node adds ``busy`` / ``no_state``)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ManualConfig:
    limits: PlatformLimits = field(default_factory=PlatformLimits)
    rate_scale: float = 0.5               # manual rate cap = rate_scale x limits.*_rate
    accel_scale: float = 4.0              # max rate change per second = accel_scale x cap
    gain: float = 2.0                     # goto: rate = gain x error (1/s), capped
    tolerance: Tuple[float, float, float] = (0.01, 0.01, 0.01)    # (rad, m, rad) -> goto done
    margin: Tuple[float, float, float] = (0.15, 0.20, 0.15)       # braking zone (rad, m, rad)
    min_brake: float = 0.2                # outward rate never scaled below this inside the zone
    jog_ttl_s: float = 0.5
    goto_timeout_s: float = 30.0

    def bounds(self):
        lim = self.limits
        return (lim.yaw, lim.slide, lim.pitch)

    def caps(self) -> Rates:
        lim = self.limits
        s = self.rate_scale
        return (s * lim.yaw_rate, s * lim.slide_rate, s * lim.pitch_rate)


def _no_constant(name: str):
    raise CommandError('non_finite', f'{name} geçerli bir sayı değil (NaN/Infinity reddedilir)')


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandError('bad_command', f'{what}: sayı olmalı, {type(value).__name__} geldi')
    value = float(value)
    if not math.isfinite(value):
        raise CommandError('non_finite', f'{what}: sonlu bir sayı olmalı ({value})')
    return value


def parse_command(text: str, cfg: Optional[ManualConfig] = None) -> Dict[str, Any]:
    """Parse and validate a command (JSON text or an already decoded dict) and return a clean dict:
    ``{'op': 'goto', 'target': {axis: value}, 'speed': f}`` /
    ``{'op': 'jog', 'rates': {axis: value}, 'ttl_s': s}`` / ``{'op': 'stop'}``.
    Raises :class:`CommandError`; never returns a half-validated command."""
    cfg = cfg or ManualConfig()
    if isinstance(text, dict):
        raw = text
    else:
        try:
            raw = json.loads(text, parse_constant=_no_constant)
        except CommandError:
            raise
        except (ValueError, TypeError) as exc:
            raise CommandError('bad_json', f'geçerli JSON değil: {exc}') from exc
    if not isinstance(raw, dict):
        raise CommandError('bad_command', 'komut bir JSON nesnesi olmalı')
    op = raw.get('op')
    if op not in OPS:
        raise CommandError('bad_command', f"op {'/'.join(OPS)} olmalı, {op!r} geldi")
    if op == 'stop':
        extra = set(raw) - {'op'}
        if extra:
            raise CommandError('bad_command', f'stop için bilinmeyen anahtar: {sorted(extra)}')
        return {'op': 'stop'}
    bounds = dict(zip(AXES, cfg.bounds()))
    caps = dict(zip(AXES, cfg.caps()))
    if op == 'goto':
        allowed = {'op', 'speed', *AXES}
        extra = set(raw) - allowed
        if extra:
            raise CommandError('bad_command', f'bilinmeyen anahtar: {sorted(extra)}')
        target = {a: _number(raw[a], a) for a in AXES if a in raw}
        if not target:
            raise CommandError('bad_command',
                               'goto için en az bir eksen ver (pan/tilt/slide)')
        for a, v in target.items():
            lo, hi = bounds[a]
            if not lo <= v <= hi:
                raise CommandError('out_of_limits', f'{a}={v} sınır dışı [{lo}, {hi}]')
        speed = _number(raw['speed'], 'speed') if 'speed' in raw else 1.0
        if not 0.0 < speed <= 1.0:
            raise CommandError('bad_command',
                               'speed (0, 1] aralığında olmalı (elle hız tavanının oranı)')
        return {'op': 'goto', 'target': target, 'speed': speed}
    allowed = {'op', 'ttl_s', *(f'{a}_rate' for a in AXES)}
    extra = set(raw) - allowed
    if extra:
        raise CommandError('bad_command', f'bilinmeyen anahtar: {sorted(extra)}')
    rates = {a: _number(raw[f'{a}_rate'], f'{a}_rate') for a in AXES if f'{a}_rate' in raw}
    if not rates:
        raise CommandError('bad_command',
                           'jog için en az bir hız ver (pan_rate/tilt_rate/slide_rate)')
    for a, v in rates.items():
        if abs(v) > caps[a] + 1e-12:
            raise CommandError('rate_too_high',
                               f'{a}_rate={v} elle hız tavanını aşıyor (±{caps[a]:.3f})')
    ttl = _number(raw['ttl_s'], 'ttl_s') if 'ttl_s' in raw else cfg.jog_ttl_s
    if not 0.0 < ttl <= MAX_TTL_S:
        raise CommandError('bad_command', f'ttl_s (0, {MAX_TTL_S}] aralığında olmalı')
    return {'op': 'jog', 'rates': rates, 'ttl_s': ttl}


@dataclass
class Tick:
    rates: Rates                          # what to publish now
    active: bool                          # a motion is in progress (False: nothing to publish)
    done: Optional[str] = None            # reached | jog_expired | timeout | stopped | no_state


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class ManualController:
    """Turns validated commands plus the measured joint state into rate commands. Pure and
    deterministic: time is an argument."""

    def __init__(self, cfg: Optional[ManualConfig] = None):
        self.cfg = cfg or ManualConfig()
        self._cmd: Optional[Dict[str, Any]] = None
        self._since = 0.0
        self._expires = 0.0
        self._last_t: Optional[float] = None
        self._v: Rates = (0.0, 0.0, 0.0)          # what we last commanded

    @property
    def active(self) -> bool:
        return self._cmd is not None

    def start(self, cmd: Dict[str, Any], now: float) -> None:
        """Accept a command already checked by :func:`parse_command` (``stop`` handled by
        :meth:`stop`)."""
        if cmd['op'] == 'stop':
            return self.stop()
        self._cmd = cmd
        self._since = now
        self._expires = now + cmd['ttl_s'] if cmd['op'] == 'jog' else float('inf')

    def stop(self) -> None:
        """Immediate: the next tick publishes zero and then the controller is idle."""
        self._cmd = {'op': 'stop'}

    def cancel(self) -> None:
        """Drop the motion without publishing anything (someone else owns the joints now)."""
        self._cmd = None
        self._v = (0.0, 0.0, 0.0)
        self._last_t = None

    def refresh_jog(self, now: float) -> None:
        if self._cmd and self._cmd['op'] == 'jog':
            self._expires = now + self._cmd['ttl_s']

    # ---- the control step ----------------------------------------------------------------------
    def tick(self, q: Optional[Rates], now: float) -> Tick:
        cmd = self._cmd
        if cmd is None:
            return Tick((0.0, 0.0, 0.0), False)
        dt = 0.05 if self._last_t is None else _clamp(now - self._last_t, 0.0, 0.5)
        self._last_t = now
        done: Optional[str] = None
        want = [0.0, 0.0, 0.0]
        caps = self.cfg.caps()
        if cmd['op'] == 'stop':
            done = 'stopped'
        elif q is None or any(not math.isfinite(x) for x in q):
            done = 'no_state'
        elif cmd['op'] == 'jog':
            if now >= self._expires:
                done = 'jog_expired'
            else:
                for i, a in enumerate(AXES):
                    want[i] = cmd['rates'].get(a, 0.0)
        else:
            if now - self._since > self.cfg.goto_timeout_s:
                done = 'timeout'
            else:
                reached = True
                for i, a in enumerate(AXES):
                    if a not in cmd['target']:
                        continue
                    err = cmd['target'][a] - q[i]
                    if abs(err) >= self.cfg.tolerance[i]:
                        reached = False
                        cap = caps[i] * cmd['speed']
                        want[i] = _clamp(self.cfg.gain * err, -cap, cap)
                if reached:
                    done = 'reached'
        if done is not None:
            self._cmd = None
            self._v = (0.0, 0.0, 0.0)             # stop and every end of motion: immediately zero
            self._last_t = None
            return Tick((0.0, 0.0, 0.0), True, done)
        out = []
        for i in range(3):
            w = self._brake(q, i, want[i])
            step = self.cfg.accel_scale * caps[i] * dt
            out.append(self._v[i] + _clamp(w - self._v[i], -step, step))
        self._v = (out[0], out[1], out[2])
        return Tick(self._v, True)

    def _brake(self, q: Rates, i: int, rate: float) -> float:
        lo, hi = self.cfg.bounds()[i]
        margin = self.cfg.margin[i]
        if rate > 0:
            dist = hi - q[i]
        elif rate < 0:
            dist = q[i] - lo
        else:
            return 0.0
        if dist <= 0.0:
            return 0.0
        if dist >= margin:
            return rate
        return rate * max(self.cfg.min_brake, dist / margin)


class ManualSession:
    """What the ROS node does, without ROS (so it is unit-tested): command in -> reply out, and one
    ``tick`` per control period -> the rates to publish (or None = publish nothing).

    ``busy`` means another controller owns the joints (the tracking node is in ``track``/``home``):
    new motions are refused with ``busy`` and a running manual motion is dropped WITHOUT publishing
    (a zero would fight the other controller). ``q`` is None when no fresh joint state is known."""

    def __init__(self, cfg: Optional[ManualConfig] = None):
        self.cfg = cfg or ManualConfig()
        self.ctrl = ManualController(self.cfg)
        self.last: Dict[str, Any] = {}
        self.mode = 'idle'                        # idle | goto | jog

    def handle(self, text: Any, now: float, q: Optional[Rates], busy: bool) -> Dict[str, Any]:
        reply: Dict[str, Any] = {'accepted': False, 'op': None, 'code': None, 'message': ''}
        try:
            cmd = parse_command(text, self.cfg)
            reply['op'] = cmd['op']
            if cmd['op'] == 'stop':
                if busy:
                    raise CommandError(
                        'busy', 'tracking eklemleri yönetiyor; elle stop onu durdurmaz '
                                '(/tracking/mode idle gönder)')
                self.ctrl.stop()
            else:
                if busy:
                    raise CommandError(
                        'busy', "tracking 'idle' modunda değil; elle komut reddedildi")
                if q is None or any(not math.isfinite(x) for x in q):
                    raise CommandError(
                        'no_state', 'taze /platform/joint_states yok; komut reddedildi')
                self.ctrl.start(cmd, now)
                self.mode = cmd['op']
            reply.update(accepted=True, message='kabul edildi')
        except CommandError as exc:
            reply.update(code=exc.code, message=str(exc))
        self.last = reply
        return reply

    def tick(self, q: Optional[Rates], now: float, busy: bool) -> Optional[Rates]:
        if busy:
            if self.ctrl.active:
                self.ctrl.cancel()
                self.mode = 'idle'
                self.last = {'accepted': False, 'op': None, 'code': 'busy',
                             'message': 'tracking devraldı; elle hareket bırakıldı'}
            return None
        tick = self.ctrl.tick(q, now)
        if tick.done is not None:
            self.mode = 'idle'
            self.last = {**self.last, 'done': tick.done}
        return tick.rates if tick.active else None
