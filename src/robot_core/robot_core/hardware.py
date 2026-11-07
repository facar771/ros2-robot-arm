"""Hardware profile: motors, drivers, encoders, homing (pure Python, no ROS).

Design: docs/ROBOT_PROFILE.md.

``hardware.profile.yaml`` describes the REAL robot's drives. It is used only by the bridge and the
firmware: nothing here enters the URDF or the MoveIt files, so reversing a motor direction can
never change the simulation. All numbers in the YAML are JOINT units (rad, rad/s, rad/s^2); this
module converts them.

    motor_rad = direction * gear_ratio * (joint_rad - zero_offset)
    steps     = motor_rad / (2*pi) * steps_per_rev * microstep        (microsteps from motor zero)

Cross-checks against the robot profile (``<robot>.yaml``): same joints in the same order, joint
(URDF) limits inside ``motor limits - margin``, joint speed/acceleration inside the motor's, home
position inside the joint limits. Rules from docs/HARDWARE.md §7: an axis without encoder (open
loop) must have a home switch; it can never be referenced otherwise. Defaults are the narrower
choice. Every error names the place in the YAML.

K6a: actuator type ``hobby_servo`` (data model + validation only; no protocol, no firmware, no
simulator). A hobby servo has NO feedback: its position is the last COMMAND, always labelled
"kestirim" (estimate). It has no home switch and no encoder either, so the HARDWARE.md §7 rule
"an axis that cannot be referenced is never HOMED (NO_REFERENCE)" is EXTENDED here, for exactly one
case: ``reference.mode: manual_zero``, valid on ``hobby_servo`` axes only. The operator puts the
arm in the known mechanical pose and confirms "burası sıfır"; the confirmation is required again at
EVERY power-up and after EVERY emergency stop (it is never remembered), and the position stays an
estimate afterwards. ``manual_zero`` on an open-loop ``step`` axis is NOT allowed (step loss
accumulates unseen, a servo's error does not): that stays ``NO_REFERENCE`` until the owner of the
safety rules decides otherwise. A profile with servo axes is refused by every step-only consumer
(``joints_to_steps``, the firmware header generator): fail closed until the protocol (K6b) knows
servos.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import yaml

from robot_core.profile import (
    DEFAULT_MARGIN_POSITION,
    DEFAULT_MARGIN_VELOCITY,
    MAX_ACCEL,
    MAX_ANGLE,
    MAX_SPEED,
    Profile,
    ProfileError,
    _fail,
    _integer,
    _mapping,
    _name,
    _number,
    load_profile,
)

HARDWARE_VERSION = 1
STEP = 'step'
SERVO = 'hobby_servo'
MOTOR_TYPES = (STEP, SERVO)
ENCODERS = ('none', 'incremental', 'absolute')
# K6a hobby servo
REFERENCE_MODES = ('manual_zero',)        # the only way a servo (no switch/encoder) is referenced
MANUAL_ZERO_RECONFIRM = ('açılış', 'ACİL DUR')   # fixed rule: confirm again at each of these
ESTIMATE_LABEL = 'kestirim'                # servo position = last command, never a measurement
PULSE_US_BOUNDS = (300.0, 3000.0)         # outside: ms typed as us (1.5?) or a different device
MIN_PULSE_SPAN_US = 200.0                 # a narrower range cannot resolve an angle
MAX_SERVO_SPAN = 2 * math.pi              # a hobby servo does not turn more than once (degrees?)
DEFAULT_REST_SPEED = 0.2                  # rad/s, slow approach to the rest angle (narrow default)
HOMING_METHODS = ('switch', 'index', 'absolute')
DEFAULT_HOMING_SPEED = 0.1        # rad/s (joint)
DEFAULT_HOMING_BACKOFF = 0.05     # rad (joint)
INT32_MAX = 2 ** 31 - 1
UINT32_MAX = 2 ** 32 - 1

OPEN_LOOP_WARNING = (
    'AÇIK DÖNGÜ: encoder yok, adım kaybı ALGILANAMAZ; konum bir kestirimdir (komut sayacı). '
    'Her oturumda homing ve mekanik uç limit anahtarı şart; tork/hız/ivme marjı geniş tutulmalı.')
SERVO_ESTIMATE_WARNING = (
    'GERİ BESLEME YOK (hobby servo): konum = son komut, ölçüm DEĞİL (kestirim). Yük altında '
    'servo komuta ulaşamayabilir ve bu görülmez. Referans: operatör "burası sıfır" onayı, her '
    'açılışta ve her ACİL DUR sonrası yeniden; onaydan sonra da konum kestirim kalır.')


HardwareError = ProfileError   # missing, malformed, or contradicting the robot profile (same type)


# ---- data -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Homing:
    method: str                      # switch | index | absolute
    direction: int                   # joint-space search direction, +1 / -1 (0: not used)
    speed: float                     # rad/s (joint)
    position: float                  # joint angle (rad) the axis reads once referenced
    backoff: float                   # rad (joint) driven away from the switch afterwards


@dataclass(frozen=True)
class Axis:
    joint: str
    motor_type: str                  # 'step'
    motor_model: str
    driver_model: str
    steps_per_rev: int               # full steps per motor-shaft revolution
    microstep: int
    direction: int                   # +1 / -1
    zero_offset: float               # joint angle (rad) at which the motor counter reads 0
    gear_ratio: float                # motor turns per joint turn
    encoder: str                     # none | incremental | absolute
    position_limits: Tuple[float, float]   # joint rad the drive can reach
    velocity_limit: float                  # joint rad/s
    acceleration_limit: float              # joint rad/s^2
    margin_position: float
    margin_velocity: float
    homing: Homing

    @property
    def open_loop(self) -> bool:
        return self.encoder == 'none'

    @property
    def steps_per_motor_rev(self) -> int:
        return self.steps_per_rev * self.microstep

    @property
    def steps_per_joint_rad(self) -> float:
        """Microsteps per radian of JOINT travel, unsigned (the sign is ``direction``)."""
        return self.gear_ratio * self.steps_per_motor_rev / (2 * math.pi)

    def to_motor(self, joint_position: float) -> float:
        """Motor-shaft angle (rad) for a joint position (rad)."""
        return self.direction * self.gear_ratio * (joint_position - self.zero_offset)

    def to_joint(self, motor_position: float) -> float:
        return self.zero_offset + motor_position / (self.direction * self.gear_ratio)

    def to_steps(self, joint_position: float) -> float:
        """Microsteps (fractional) from the motor zero for a joint position."""
        return self.to_motor(joint_position) / (2 * math.pi) * self.steps_per_motor_rev

    def to_steps_int(self, joint_position: float) -> int:
        return round(self.to_steps(joint_position))

    def from_steps(self, steps: float) -> float:
        return self.to_joint(steps / self.steps_per_motor_rev * 2 * math.pi)


@dataclass(frozen=True)
class PulseRange:
    """Linear map between servo pulse width (us) and joint angle (rad): ``min_us`` <->
    ``angle_at_min``, ``max_us`` <-> ``angle_at_max`` (the angles may descend: mirrored mount)."""
    min_us: float
    max_us: float
    angle_at_min: float
    angle_at_max: float

    @property
    def angle_span(self) -> Tuple[float, float]:
        """(lowest, highest) angle the calibrated pulse range can reach."""
        return (min(self.angle_at_min, self.angle_at_max),
                max(self.angle_at_min, self.angle_at_max))

    def angle_to_us(self, angle: float) -> float:
        frac = (angle - self.angle_at_min) / (self.angle_at_max - self.angle_at_min)
        return self.min_us + frac * (self.max_us - self.min_us)

    def us_to_angle(self, pulse_us: float) -> float:
        frac = (pulse_us - self.min_us) / (self.max_us - self.min_us)
        return self.angle_at_min + frac * (self.angle_at_max - self.angle_at_min)


@dataclass(frozen=True)
class Rest:
    """Where a servo goes after PWM is switched on, and how slowly (no jump at power-up)."""
    angle: float                     # joint rad
    speed: float                     # rad/s, <= the axis slew limit


@dataclass(frozen=True)
class ManualZero:
    """``reference.mode: manual_zero``: the operator puts the arm at ``pose`` and confirms.

    The confirmation is NOT stored in the profile and is never remembered: it is required again at
    every ``reconfirm_on`` event, and the position stays an estimate (``ESTIMATE_LABEL``)."""
    pose: float                      # joint rad the axis reads once the operator has confirmed
    mode: str = 'manual_zero'
    reconfirm_on: Tuple[str, ...] = MANUAL_ZERO_RECONFIRM


@dataclass(frozen=True)
class ServoAxis:
    """A hobby servo (SG90 class) axis: no feedback, no home switch, software slew limit."""
    joint: str
    motor_type: str                  # 'hobby_servo'
    motor_model: str
    pulse_range: PulseRange
    position_limits: Tuple[float, float]   # joint rad the servo may be commanded to
    slew_limit: float                # joint rad/s, software speed cap
    rest: Rest
    reference: ManualZero
    margin_position: float
    margin_velocity: float

    @property
    def open_loop(self) -> bool:
        return True                  # no feedback at all

    @property
    def encoder(self) -> str:
        return 'none'

    @property
    def position_source(self) -> str:
        """Always the estimate label: position = last command, never a measurement."""
        return ESTIMATE_LABEL

    @property
    def servo_pulse_range(self) -> Tuple[float, float, float, float]:
        """Value of the quality-tag field ``servo.pulse_range`` (maturity.py)."""
        p = self.pulse_range
        return (p.min_us, p.max_us, p.angle_at_min, p.angle_at_max)

    def _check_angle(self, angle: float) -> None:
        lo, hi = self.position_limits
        if not (math.isfinite(angle) and lo - 1e-12 <= angle <= hi + 1e-12):
            raise HardwareError(f'{self.joint}: {angle:g} rad servo açı sınırı [{lo:g}, {hi:g}] '
                                'dışında (kırpılmaz, reddedilir)')

    def angle_to_pulse_us(self, angle: float) -> float:
        """Pulse width (us) for a joint angle; an angle outside the limits is refused."""
        self._check_angle(angle)
        return self.pulse_range.angle_to_us(angle)

    def angle_to_pulse_us_int(self, angle: float) -> int:
        return round(self.angle_to_pulse_us(angle))

    def pulse_us_to_angle(self, pulse_us: float) -> float:
        p = self.pulse_range
        if not (math.isfinite(pulse_us) and p.min_us - 1e-9 <= pulse_us <= p.max_us + 1e-9):
            raise HardwareError(f'{self.joint}: {pulse_us:g} us darbe aralığı '
                                f'[{p.min_us:g}, {p.max_us:g}] dışında')
        return p.us_to_angle(pulse_us)

    def slew_step(self, current: float, target: float, dt: float,
                  speed: Optional[float] = None) -> float:
        """Next commanded angle when moving from ``current`` toward ``target`` for ``dt`` seconds,
        never faster than the slew limit (and never than ``speed`` if narrower). The target must be
        inside the limits; the result never overshoots it."""
        if not (math.isfinite(dt) and dt >= 0):
            raise HardwareError(f'{self.joint}: dt sonlu ve >= 0 olmalı, bulunan: {dt!r}')
        cap = self.slew_limit if speed is None else min(speed, self.slew_limit)
        if not cap > 0:
            raise HardwareError(f'{self.joint}: hız sınırı pozitif olmalı, bulunan: {cap!r}')
        self._check_angle(target)
        self._check_angle(current)
        reach = cap * dt
        if abs(target - current) <= reach:
            return target
        return current + math.copysign(reach, target - current)


AnyAxis = Union[Axis, ServoAxis]


@dataclass(frozen=True)
class HardwareProfile:
    robot: str
    axes: Tuple[AnyAxis, ...]

    @property
    def joint_names(self) -> Tuple[str, ...]:
        return tuple(a.joint for a in self.axes)

    def axis(self, joint: str) -> AnyAxis:
        return next(a for a in self.axes if a.joint == joint)

    @property
    def servo_axes(self) -> Tuple[str, ...]:
        return tuple(a.joint for a in self.axes if isinstance(a, ServoAxis))

    def _no_servo(self, what: str) -> None:
        if self.servo_axes:
            raise HardwareError(
                f"{what}: profilde hobby_servo ekseni var ({', '.join(self.servo_axes)}); bu yol "
                'yalnız step eksenleri bilir (servo protokolü K6b), güvenli tarafta reddedildi')

    def joints_to_steps(self, positions: Sequence[float]) -> List[int]:
        """Joint vector (chain order, rad) -> motor step targets."""
        self._no_servo('joints_to_steps')
        self._same_length(positions)
        return [a.to_steps_int(q) for a, q in zip(self.axes, positions)]

    def steps_to_joints(self, steps: Sequence[float]) -> List[float]:
        self._no_servo('steps_to_joints')
        self._same_length(steps)
        return [a.from_steps(s) for a, s in zip(self.axes, steps)]

    def servo_pulses_us(self, positions: Sequence[float]) -> Dict[str, int]:
        """Servo axes only: joint vector (chain order, rad) -> pulse width (us) per servo joint.
        An angle outside a servo's limits raises ``HardwareError``."""
        self._same_length(positions)
        return {a.joint: a.angle_to_pulse_us_int(q)
                for a, q in zip(self.axes, positions) if isinstance(a, ServoAxis)}

    def _same_length(self, values: Sequence[float]) -> None:
        if len(values) != len(self.axes):
            raise ValueError(f'{len(self.axes)} değer bekleniyordu, bulunan: {len(values)}')

    @property
    def open_loop_axes(self) -> Tuple[str, ...]:
        return tuple(a.joint for a in self.axes if a.open_loop)

    @property
    def warnings(self) -> Tuple[str, ...]:
        step_open = tuple(a.joint for a in self.axes if isinstance(a, Axis) and a.open_loop)
        out: List[str] = []
        if step_open:
            out.append(f"{OPEN_LOOP_WARNING} Eksenler: {', '.join(step_open)}.")
        if self.servo_axes:
            out.append(f"{SERVO_ESTIMATE_WARNING} Eksenler: {', '.join(self.servo_axes)}.")
        return tuple(out)


# ---- limits in steps (firmware) ---------------------------------------------------------------
def steps_range(axis: Axis, lower: float, upper: float) -> Tuple[int, int]:
    """Step interval for a joint interval, rounded INWARD (narrower); direction -1 swaps ends."""
    _step_axis(axis, 'steps_range')
    a, b = axis.to_steps(lower), axis.to_steps(upper)
    return math.ceil(min(a, b) - 1e-9), math.floor(max(a, b) + 1e-9)


def _step_axis(axis: Any, what: str) -> None:
    """Step-only helpers refuse a servo axis with a clear error, never an AttributeError."""
    if isinstance(axis, ServoAxis):
        raise HardwareError(f'{what}: {axis.joint} bir hobby_servo ekseni; bu yol yalnız step '
                            'eksenleri bilir (servo protokolü K6b), güvenli tarafta reddedildi')


def soft_limits(axis: Axis, profile: Profile) -> Tuple[int, int]:
    _step_axis(axis, 'soft_limits')
    joint = next(j for j in profile.joints if j.name == axis.joint)
    return steps_range(axis, joint.lower, joint.upper)


def hard_limits(axis: Axis) -> Tuple[int, int]:
    _step_axis(axis, 'hard_limits')
    return steps_range(axis, *axis.position_limits)


def rate_steps(axis: Axis, joint_rate: float) -> int:
    """Joint rate (rad/s or rad/s^2) -> steps/s (or steps/s^2), rounded down (narrower)."""
    _step_axis(axis, 'rate_steps')
    return math.floor(joint_rate * axis.steps_per_joint_rad + 1e-9)


def _is_sign(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in (1, -1)


# ---- parsing ----------------------------------------------------------------------------------
def _parse_homing(where: str, data: Any, encoder: str, joint_lower: float, joint_upper: float,
                  joint_speed: float, motor_speed: float) -> Homing:
    d = _mapping(data, where, ('method', 'direction', 'speed', 'position', 'backoff'),
                 ('method', 'position'))
    method = d['method']
    if method == 'manual_zero':
        raise _fail(f'{where}.method',
                    "'manual_zero' step ekseninde İZİNLİ DEĞİL: açık döngü step'te adım kaybı "
                    'birikir ve görünmez, operatör onayı bunu yakalayamaz; yalnız hobby_servo '
                    'ekseninde (reference.mode). Step için kural değişmedi: home/limit anahtarı '
                    'ŞART (HARDWARE.md §7, NO_REFERENCE)')
    if method not in HOMING_METHODS:
        raise _fail(f'{where}.method', f"{' | '.join(HOMING_METHODS)} olmalı, bulunan: {method!r}")
    allowed = {'none': ('switch',), 'incremental': ('switch', 'index'),
               'absolute': HOMING_METHODS}[encoder]
    if method not in allowed:
        hint = ('encoder yok (açık döngü): referans için home/limit anahtarı ŞART'
                if encoder == 'none' else 'artımlı encoder: home anahtarı ya da indeks gerekir')
        raise _fail(f'{where}.method', f'{method!r}, encoder: {encoder} ile olmaz ({hint}); '
                                       f"izinli: {' | '.join(allowed)}")
    direction = 0
    if method != 'absolute':
        if 'direction' not in d:
            raise _fail(where, 'eksik anahtar(lar): direction (arama yönü)')
        direction = d['direction']
        if not _is_sign(direction):
            raise _fail(f'{where}.direction', f'+1 ya da -1 (tam sayı) olmalı, bulunan: '
                                              f'{direction!r}')
    speed = _number(d.get('speed', DEFAULT_HOMING_SPEED), f'{where}.speed', positive=True,
                    hi=MAX_SPEED)
    if speed > min(joint_speed, motor_speed):
        raise _fail(f'{where}.speed', f'homing hızı {speed:g} rad/s, eklem/motor hızını '
                                      f'({min(joint_speed, motor_speed):g}) aşıyor')
    position = _number(d['position'], f'{where}.position', lo=-MAX_ANGLE, hi=MAX_ANGLE)
    if not joint_lower <= position <= joint_upper:
        raise _fail(f'{where}.position', f'{position:g}, eklem limiti [{joint_lower:g}, '
                                         f'{joint_upper:g}] dışında')
    backoff = _number(d.get('backoff', DEFAULT_HOMING_BACKOFF), f'{where}.backoff', lo=0.0,
                      hi=MAX_ANGLE)
    return Homing(method=method, direction=direction, speed=speed, position=position,
                  backoff=backoff)


def _parse_axis(name: str, data: Any, joint) -> Axis:
    where = f'axes.{name}'
    d = _mapping(data, where, ('motor', 'driver', 'direction', 'zero_offset', 'gear_ratio',
                               'encoder', 'limits', 'margin', 'homing'),
                 ('motor', 'driver', 'direction', 'zero_offset', 'gear_ratio', 'limits', 'homing'))
    motor = _mapping(d['motor'], f'{where}.motor', ('type', 'steps_per_rev', 'model'),
                     ('type', 'steps_per_rev'))
    if motor['type'] != STEP:
        raise _fail(f'{where}.motor.type', f"yalnızca 'step' ve 'hobby_servo' destekleniyor, "
                                           f"bulunan: {motor['type']!r}")
    driver = _mapping(d['driver'], f'{where}.driver', ('microstep', 'model'), ('microstep',))
    direction = d['direction']
    if not _is_sign(direction):
        raise _fail(f'{where}.direction', f'+1 ya da -1 (tam sayı) olmalı, bulunan: {direction!r}')
    encoder = d.get('encoder', 'none')
    if encoder not in ENCODERS:
        raise _fail(f'{where}.encoder', f"{' | '.join(ENCODERS)} olmalı, bulunan: {encoder!r}")
    limits = _mapping(d['limits'], f'{where}.limits', ('position', 'velocity', 'acceleration'),
                      ('position', 'velocity', 'acceleration'))
    pos = limits['position']
    if not isinstance(pos, (list, tuple)) or len(pos) != 2:
        raise _fail(f'{where}.limits.position', f'[alt, üst] olmalı, bulunan: {pos!r}')
    pos = tuple(_number(v, f'{where}.limits.position[{i}]', lo=-MAX_ANGLE, hi=MAX_ANGLE)
                for i, v in enumerate(pos))
    if not pos[0] < pos[1]:
        raise _fail(f'{where}.limits.position', f'alt sınır üstten küçük olmalı: {list(pos)}')
    velocity = _number(limits['velocity'], f'{where}.limits.velocity', positive=True, hi=MAX_SPEED)
    acceleration = _number(limits['acceleration'], f'{where}.limits.acceleration', positive=True,
                           hi=MAX_ACCEL)
    margin = _mapping(d.get('margin', {}), f'{where}.margin', ('position', 'velocity'))
    margin_pos = _number(margin.get('position', DEFAULT_MARGIN_POSITION),
                         f'{where}.margin.position', lo=0.0, hi=MAX_ANGLE)
    margin_vel = _number(margin.get('velocity', DEFAULT_MARGIN_VELOCITY),
                         f'{where}.margin.velocity', lo=0.0, hi=MAX_SPEED)

    # the joint's (URDF) limits must lie inside the motor's limits minus the margin
    lo, hi = pos[0] + margin_pos, pos[1] - margin_pos
    if not (joint.lower >= lo - 1e-12 and joint.upper <= hi + 1e-12):
        raise _fail(where, f'eklem limiti [{joint.lower:g}, {joint.upper:g}] motor limiti − marj '
                           f'[{lo:g}, {hi:g}] içinde değil (motor [{pos[0]:g}, {pos[1]:g}], '
                           f'marj {margin_pos:g} rad)')
    if joint.velocity > velocity - margin_vel + 1e-12:
        raise _fail(where, f'eklem hız limiti {joint.velocity:g} rad/s motor hızı − marj '
                           f'({velocity:g} − {margin_vel:g}) değerini aşıyor')
    if joint.acceleration > acceleration + 1e-12:
        raise _fail(where, f'eklem ivme limiti {joint.acceleration:g} rad/s² motor ivmesini '
                           f'({acceleration:g}) aşıyor')
    homing = _parse_homing(f'{where}.homing', d['homing'], encoder, joint.lower, joint.upper,
                           joint.velocity, velocity - margin_vel)
    axis = Axis(
        joint=name, motor_type='step', motor_model=str(motor.get('model', '')),
        driver_model=str(driver.get('model', '')),
        steps_per_rev=_integer(motor['steps_per_rev'], f'{where}.motor.steps_per_rev', 1, 100000),
        microstep=_integer(driver['microstep'], f'{where}.driver.microstep', 1, 256),
        direction=direction,
        zero_offset=_number(d['zero_offset'], f'{where}.zero_offset', lo=-MAX_ANGLE, hi=MAX_ANGLE),
        gear_ratio=_number(d['gear_ratio'], f'{where}.gear_ratio', positive=True, hi=10000.0),
        encoder=encoder, position_limits=(pos[0], pos[1]), velocity_limit=velocity,
        acceleration_limit=acceleration, margin_position=margin_pos, margin_velocity=margin_vel,
        homing=homing)
    _check_step_ranges(axis, joint, where)
    return axis


_STEP_ONLY_KEYS = ('driver', 'direction', 'zero_offset', 'gear_ratio', 'homing')
_RECONFIRM_KEYS = ('reconfirm', 'reconfirm_on', 'remember', 'persist', 'confirmed')


def _parse_pulse_range(where: str, data: Any) -> PulseRange:
    keys = ('min_us', 'max_us', 'angle_at_min_us', 'angle_at_max_us')
    d = _mapping(data, where, keys, keys)
    min_us = _number(d['min_us'], f'{where}.min_us', lo=PULSE_US_BOUNDS[0], hi=PULSE_US_BOUNDS[1])
    max_us = _number(d['max_us'], f'{where}.max_us', lo=PULSE_US_BOUNDS[0], hi=PULSE_US_BOUNDS[1])
    if max_us - min_us < MIN_PULSE_SPAN_US:
        raise _fail(where, f'min_us < max_us olmalı ve en az {MIN_PULSE_SPAN_US:g} us açık olmalı: '
                           f'[{min_us:g}, {max_us:g}]')
    a_min = _number(d['angle_at_min_us'], f'{where}.angle_at_min_us', lo=-MAX_ANGLE, hi=MAX_ANGLE)
    a_max = _number(d['angle_at_max_us'], f'{where}.angle_at_max_us', lo=-MAX_ANGLE, hi=MAX_ANGLE)
    span = abs(a_max - a_min)
    if span <= 0:
        raise _fail(where, 'angle_at_min_us ve angle_at_max_us aynı: açı aralığı sıfır')
    if span > MAX_SERVO_SPAN:
        raise _fail(where, f'açı aralığı {span:g} rad, bir turdan ({MAX_SERVO_SPAN:g}) fazla '
                           '(derece mi yazıldı?)')
    return PulseRange(min_us, max_us, a_min, a_max)


def _parse_servo_axis(name: str, data: Any, joint) -> ServoAxis:
    where = f'axes.{name}'
    if isinstance(data, Mapping):
        stepish = [k for k in _STEP_ONLY_KEYS if k in data]
        if stepish:
            raise _fail(where, f"hobby_servo ekseninde step alanı var: {', '.join(stepish)} "
                               '(servo: motor, servo, reference, limits, margin; homing yerine '
                               'reference.mode: manual_zero)')
    d = _mapping(data, where, ('motor', 'servo', 'reference', 'encoder', 'limits', 'margin'),
                 ('motor', 'servo', 'reference', 'limits'))
    motor = _mapping(d['motor'], f'{where}.motor', ('type', 'model'), ('type',))
    encoder = d.get('encoder', 'none')
    if encoder != 'none':
        raise _fail(f'{where}.encoder', "hobby_servo geri besleme taşımaz: 'none' olmalı (konum = "
                                        f'komut, {ESTIMATE_LABEL}), bulunan: {encoder!r}')

    skeys = ('pulse_range', 'slew_limit', 'rest')
    servo = _mapping(d['servo'], f'{where}.servo', skeys, ('pulse_range', 'slew_limit', 'rest'))
    pulse = _parse_pulse_range(f'{where}.servo.pulse_range', servo['pulse_range'])
    slew = _number(servo['slew_limit'], f'{where}.servo.slew_limit', positive=True, hi=MAX_SPEED)

    limits_in = d['limits']
    if isinstance(limits_in, Mapping) and ('velocity' in limits_in or 'acceleration' in limits_in):
        raise _fail(f'{where}.limits', 'servoda hız servo.slew_limit ile verilir; velocity/'
                                       'acceleration anahtarı yok (servo ivmesi tanımsız)')
    limits = _mapping(limits_in, f'{where}.limits', ('position',), ('position',))
    pos = limits['position']
    if not isinstance(pos, (list, tuple)) or len(pos) != 2:
        raise _fail(f'{where}.limits.position', f'[alt, üst] olmalı, bulunan: {pos!r}')
    pos = tuple(_number(v, f'{where}.limits.position[{i}]', lo=-MAX_ANGLE, hi=MAX_ANGLE)
                for i, v in enumerate(pos))
    if not pos[0] < pos[1]:
        raise _fail(f'{where}.limits.position', f'alt sınır üstten küçük olmalı: {list(pos)}')
    span_lo, span_hi = pulse.angle_span
    if pos[0] < span_lo - 1e-12 or pos[1] > span_hi + 1e-12:
        raise _fail(f'{where}.limits.position',
                    f'[{pos[0]:g}, {pos[1]:g}] kalibre darbe aralığının açılarının '
                    f'[{span_lo:g}, {span_hi:g}] dışında: o açılar için darbe tanımlı değil')

    margin = _mapping(d.get('margin', {}), f'{where}.margin', ('position', 'velocity'))
    margin_pos = _number(margin.get('position', DEFAULT_MARGIN_POSITION),
                         f'{where}.margin.position', lo=0.0, hi=MAX_ANGLE)
    margin_vel = _number(margin.get('velocity', DEFAULT_MARGIN_VELOCITY),
                         f'{where}.margin.velocity', lo=0.0, hi=MAX_SPEED)
    lo, hi = pos[0] + margin_pos, pos[1] - margin_pos
    if not (joint.lower >= lo - 1e-12 and joint.upper <= hi + 1e-12):
        raise _fail(where, f'eklem limiti [{joint.lower:g}, {joint.upper:g}] servo açı limiti − '
                           f'marj [{lo:g}, {hi:g}] içinde değil (servo [{pos[0]:g}, {pos[1]:g}], '
                           f'marj {margin_pos:g} rad)')
    if joint.velocity > slew - margin_vel + 1e-12:
        raise _fail(where, f'eklem hız limiti {joint.velocity:g} rad/s servo yazılım hız sınırı − '
                           f'marj ({slew:g} − {margin_vel:g}) değerini aşıyor')

    rest_in = _mapping(servo['rest'], f'{where}.servo.rest', ('angle', 'speed'), ('angle',))
    rest_angle = _number(rest_in['angle'], f'{where}.servo.rest.angle', lo=-MAX_ANGLE, hi=MAX_ANGLE)
    if not (joint.lower - 1e-12 <= rest_angle <= joint.upper + 1e-12):
        raise _fail(f'{where}.servo.rest.angle', f'{rest_angle:g}, eklem limiti [{joint.lower:g}, '
                                                 f'{joint.upper:g}] dışında')
    rest_speed = _number(rest_in.get('speed', min(DEFAULT_REST_SPEED, slew)),
                         f'{where}.servo.rest.speed', positive=True, hi=MAX_SPEED)
    if rest_speed > slew + 1e-12:
        raise _fail(f'{where}.servo.rest.speed', f'rest hızı {rest_speed:g} rad/s servo yazılım '
                                                 f'hız sınırını ({slew:g}) aşıyor')

    ref_in = d['reference']
    if isinstance(ref_in, Mapping):
        fixed = [k for k in _RECONFIRM_KEYS if k in ref_in]
        if fixed:
            raise _fail(f'{where}.reference',
                        f"{', '.join(fixed)}: yeniden onay ({' + '.join(MANUAL_ZERO_RECONFIRM)}) "
                        'sabit kuraldır, profilden kapatılamaz/saklanamaz')
    ref = _mapping(ref_in, f'{where}.reference', ('mode', 'pose'), ('mode', 'pose'))
    if ref['mode'] not in REFERENCE_MODES:
        raise _fail(f'{where}.reference.mode',
                    f"{' | '.join(REFERENCE_MODES)} olmalı, bulunan: {ref['mode']!r} (servoda "
                    "limit anahtarı/encoder yok; tek referans: operatör onayı 'manual_zero')")
    pose = _number(ref['pose'], f'{where}.reference.pose', lo=-MAX_ANGLE, hi=MAX_ANGLE)
    if not (joint.lower - 1e-12 <= pose <= joint.upper + 1e-12):
        raise _fail(f'{where}.reference.pose', f'{pose:g}, eklem limiti [{joint.lower:g}, '
                                               f'{joint.upper:g}] dışında')
    if not (pos[0] - 1e-12 <= pose <= pos[1] + 1e-12):
        raise _fail(f'{where}.reference.pose',
                    f'{pose:g}, servo açı limiti [{pos[0]:g}, {pos[1]:g}] dışında')

    return ServoAxis(
        joint=name, motor_type=SERVO, motor_model=str(motor.get('model', '')), pulse_range=pulse,
        position_limits=(pos[0], pos[1]), slew_limit=slew, rest=Rest(rest_angle, rest_speed),
        reference=ManualZero(pose=pose), margin_position=margin_pos, margin_velocity=margin_vel)


def _parse_any_axis(name: str, data: Any, joint) -> AnyAxis:
    """Each axis keeps its own type (mixed robot): dispatch on ``motor.type``."""
    motor = data.get('motor') if isinstance(data, Mapping) else None
    if isinstance(motor, Mapping) and motor.get('type') == SERVO:
        return _parse_servo_axis(name, data, joint)
    return _parse_axis(name, data, joint)


def _check_step_ranges(axis: Axis, joint, where: str) -> None:
    """Everything the wire format (HARDWARE.md §3: i32 targets, u32 speeds) must carry."""
    for label, (lo, hi) in (('eklem limiti', steps_range(axis, joint.lower, joint.upper)),
                            ('motor limiti', hard_limits(axis))):
        if lo < -INT32_MAX or hi > INT32_MAX:
            raise _fail(where, f'{label} adım olarak int32 aralığını aşıyor ([{lo}, {hi}]); '
                               'dişli oranı/mikroadım çok büyük ya da birim yanlış')
    for label, value in (('hız', rate_steps(axis, joint.velocity)),
                         ('ivme', rate_steps(axis, joint.acceleration)),
                         ('homing hızı', rate_steps(axis, axis.homing.speed))):
        if not 1 <= value <= UINT32_MAX:
            raise _fail(where, f'{label} {value} adım/sn: 1..{UINT32_MAX} dışında (step üretilemez '
                               'da u32 taşar)')
    if abs(axis.to_steps(axis.homing.position)) > INT32_MAX:
        raise _fail(f'{where}.homing.position', 'adım olarak int32 aralığını aşıyor')


def parse_hardware(data: Any, robot: Profile) -> HardwareProfile:
    top = ('hardware_version', 'robot', 'axes')
    d = _mapping(data, 'donanım profili', top, top)
    if d['hardware_version'] != HARDWARE_VERSION:
        raise _fail('hardware_version', f"{HARDWARE_VERSION} olmalı, bulunan: "
                                        f"{d['hardware_version']!r}")
    name = _name(d['robot'], 'robot')
    if name != robot.robot:
        raise _fail('robot', f'robot profili {robot.robot!r}, donanım profili {name!r} diyor')
    axes_in = d['axes']
    names = list(robot.joint_names)
    if not isinstance(axes_in, Mapping) or list(axes_in) != names:
        raise HardwareError(f'axes: robot profilindeki eklemlerle aynı adlar ve sırada olmalı '
                            f'({names}), bulunan: '
                            f'{list(axes_in) if isinstance(axes_in, Mapping) else axes_in!r}')
    joints = {j.name: j for j in robot.joints}
    return HardwareProfile(robot=name,
                           axes=tuple(_parse_any_axis(n, axes_in[n], joints[n]) for n in names))


def load_hardware(path: Union[str, Path], robot: Optional[Profile] = None) -> HardwareProfile:
    """Read ``hardware.profile.yaml``; the robot profile is ``<robot>.yaml`` beside it."""
    path = Path(path)
    try:
        with open(path, encoding='utf-8') as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise HardwareError(f'{path}: okunamadı ({exc.strerror or exc})') from exc
    except yaml.YAMLError as exc:
        raise HardwareError(f'{path}: geçerli YAML değil ({exc})') from exc
    try:
        if robot is None:
            robot_name = data.get('robot') if isinstance(data, Mapping) else None
            if not isinstance(robot_name, str):
                raise HardwareError('robot: eksik ya da metin değil')
            robot = load_profile(path.with_name(f'{_name(robot_name, "robot")}.yaml'))
        return parse_hardware(data, robot)
    except ProfileError as exc:
        if str(exc).startswith(str(path)):
            raise
        raise HardwareError(f'{path}: {exc}') from exc


def _describe_servo(a: ServoAxis) -> Dict[str, Any]:
    p = a.pulse_range
    return {
        'joint': a.joint, 'motor': {'type': a.motor_type, 'model': a.motor_model},
        'encoder': a.encoder, 'loop': 'open', 'position_source': a.position_source,
        'servo': {'pulse_range': {'min_us': p.min_us, 'max_us': p.max_us,
                                  'angle_at_min_us': p.angle_at_min,
                                  'angle_at_max_us': p.angle_at_max},
                  'slew_limit': a.slew_limit,
                  'rest': {'angle': a.rest.angle, 'speed': a.rest.speed}},
        'motor_limits': {'position': list(a.position_limits),
                         'margin_position': a.margin_position,
                         'margin_velocity': a.margin_velocity},
        'reference': {'mode': a.reference.mode, 'pose': a.reference.pose,
                      'reconfirm_on': list(a.reference.reconfirm_on),
                      'position': ESTIMATE_LABEL},
    }


# ---- read-only view for the Hub (no ROS, never raises) -----------------------------------------
def describe_hardware(path: Union[str, Path, None] = None) -> Dict[str, Any]:
    """JSON-friendly, read-only summary for display (Hub). Returns ``{'ok': False, 'error': ...}``
    instead of raising. Contains the open-loop warning whenever an axis has no encoder."""
    from robot_core.hardware_gen import HARDWARE_PATH  # late: hardware_gen imports this module
    path = Path(path) if path is not None else HARDWARE_PATH
    try:
        robot_name = yaml.safe_load(path.read_text(encoding='utf-8')).get('robot')
        robot = load_profile(path.with_name(f'{robot_name}.yaml'))
        hw = load_hardware(path, robot)
    except (ProfileError, OSError, yaml.YAMLError, AttributeError, TypeError) as exc:
        return {'ok': False, 'error': str(exc)}
    axes = []
    for a in hw.axes:
        if isinstance(a, ServoAxis):
            axes.append(_describe_servo(a))
            continue
        soft, hard = soft_limits(a, robot), hard_limits(a)
        axes.append({
            'joint': a.joint, 'motor': {'type': a.motor_type, 'model': a.motor_model,
                                         'steps_per_rev': a.steps_per_rev},
            'driver': {'model': a.driver_model, 'microstep': a.microstep},
            'direction': a.direction, 'gear_ratio': a.gear_ratio, 'zero_offset': a.zero_offset,
            'encoder': a.encoder, 'loop': 'open' if a.open_loop else 'closed',
            'steps_per_joint_rad': a.steps_per_joint_rad,
            'motor_limits': {'position': list(a.position_limits), 'velocity': a.velocity_limit,
                             'acceleration': a.acceleration_limit,
                             'margin_position': a.margin_position,
                             'margin_velocity': a.margin_velocity},
            'soft_limit_steps': list(soft), 'hard_limit_steps': list(hard),
            'homing': {'method': a.homing.method, 'direction': a.homing.direction,
                       'speed': a.homing.speed, 'position': a.homing.position,
                       'backoff': a.homing.backoff},
        })
    return {'ok': True, 'robot': hw.robot, 'open_loop': bool(hw.open_loop_axes),
            'open_loop_axes': list(hw.open_loop_axes), 'servo_axes': list(hw.servo_axes),
            'warnings': list(hw.warnings), 'axes': axes}
