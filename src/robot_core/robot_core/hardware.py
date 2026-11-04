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
ENCODERS = ('none', 'incremental', 'absolute')
HOMING_METHODS = ('switch', 'index', 'absolute')
DEFAULT_HOMING_SPEED = 0.1        # rad/s (joint)
DEFAULT_HOMING_BACKOFF = 0.05     # rad (joint)
INT32_MAX = 2 ** 31 - 1
UINT32_MAX = 2 ** 32 - 1

OPEN_LOOP_WARNING = (
    'AÇIK DÖNGÜ: encoder yok, adım kaybı ALGILANAMAZ; konum bir kestirimdir (komut sayacı). '
    'Her oturumda homing ve mekanik uç limit anahtarı şart; tork/hız/ivme marjı geniş tutulmalı.')


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
class HardwareProfile:
    robot: str
    axes: Tuple[Axis, ...]

    @property
    def joint_names(self) -> Tuple[str, ...]:
        return tuple(a.joint for a in self.axes)

    def axis(self, joint: str) -> Axis:
        return next(a for a in self.axes if a.joint == joint)

    def joints_to_steps(self, positions: Sequence[float]) -> List[int]:
        """Joint vector (chain order, rad) -> motor step targets."""
        self._same_length(positions)
        return [a.to_steps_int(q) for a, q in zip(self.axes, positions)]

    def steps_to_joints(self, steps: Sequence[float]) -> List[float]:
        self._same_length(steps)
        return [a.from_steps(s) for a, s in zip(self.axes, steps)]

    def _same_length(self, values: Sequence[float]) -> None:
        if len(values) != len(self.axes):
            raise ValueError(f'{len(self.axes)} değer bekleniyordu, bulunan: {len(values)}')

    @property
    def open_loop_axes(self) -> Tuple[str, ...]:
        return tuple(a.joint for a in self.axes if a.open_loop)

    @property
    def warnings(self) -> Tuple[str, ...]:
        if not self.open_loop_axes:
            return ()
        return (f"{OPEN_LOOP_WARNING} Eksenler: {', '.join(self.open_loop_axes)}.",)


# ---- limits in steps (firmware) ---------------------------------------------------------------
def steps_range(axis: Axis, lower: float, upper: float) -> Tuple[int, int]:
    """Step interval for a joint interval, rounded INWARD (narrower); direction -1 swaps ends."""
    a, b = axis.to_steps(lower), axis.to_steps(upper)
    return math.ceil(min(a, b) - 1e-9), math.floor(max(a, b) + 1e-9)


def soft_limits(axis: Axis, profile: Profile) -> Tuple[int, int]:
    joint = next(j for j in profile.joints if j.name == axis.joint)
    return steps_range(axis, joint.lower, joint.upper)


def hard_limits(axis: Axis) -> Tuple[int, int]:
    return steps_range(axis, *axis.position_limits)


def rate_steps(axis: Axis, joint_rate: float) -> int:
    """Joint rate (rad/s or rad/s^2) -> steps/s (or steps/s^2), rounded down (narrower)."""
    return math.floor(joint_rate * axis.steps_per_joint_rad + 1e-9)


def _is_sign(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in (1, -1)


# ---- parsing ----------------------------------------------------------------------------------
def _parse_homing(where: str, data: Any, encoder: str, joint_lower: float, joint_upper: float,
                  joint_speed: float, motor_speed: float) -> Homing:
    d = _mapping(data, where, ('method', 'direction', 'speed', 'position', 'backoff'),
                 ('method', 'position'))
    method = d['method']
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
    if motor['type'] != 'step':
        raise _fail(f'{where}.motor.type', f"yalnızca 'step' destekleniyor, bulunan: "
                                           f"{motor['type']!r}")
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
                           axes=tuple(_parse_axis(n, axes_in[n], joints[n]) for n in names))


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
            'open_loop_axes': list(hw.open_loop_axes), 'warnings': list(hw.warnings),
            'axes': axes}
