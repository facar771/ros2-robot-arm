"""Safe working box and speed cap for tool goals (ROS-free, configured in YAML).

A Cartesian goal (``go_to_position``, ``go_above``) whose *target point* lies outside the box is
refused with an explicit message and the robot does not move. Only the target is checked; the path
between two points is MoveIt's job (collision checking). Named joint poses (``home``) are trusted
configuration and not checked. ``max_velocity_scale`` caps the velocity/acceleration scaling the
motion adapter may ask MoveIt for.

``config/safe_zone.yaml`` (robot_moveit_config)::

    frame: world
    box: {x: [0.05, 0.75], y: [-0.5, 0.5], z: [0.02, 0.70]}   # metres, tool-tip targets
    max_velocity_scale: 0.5                                  # 0 < scale <= 1
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

import yaml

Vec = Tuple[float, float, float]
AXES = ('x', 'y', 'z')


class SafetyConfigError(ValueError):
    """The safety YAML is missing, malformed or inconsistent (message: what and where)."""


@dataclass(frozen=True)
class SafeZone:
    lower: Vec
    upper: Vec
    frame: str = 'world'

    def check(self, point: Vec) -> Optional[str]:
        """None when ``point`` (world frame, metres) is inside; otherwise why it is not."""
        problems = []
        for axis, value, lo, hi in zip(AXES, point, self.lower, self.upper):
            if not math.isfinite(value):
                problems.append(f'{axis}={value!r} sonlu değil')
            elif not lo <= value <= hi:
                problems.append(f'{axis}={value:.3f} m, izin verilen aralık [{lo:g}, {hi:g}]')
        if not problems:
            return None
        return ('hedef güvenli çalışma kutusunun dışında (' + '; '.join(problems) + '), '
                'robot hareket etmedi')

    def as_dict(self) -> Dict[str, Any]:
        return {'frame': self.frame,
                'box': {a: [lo, hi] for a, lo, hi in zip(AXES, self.lower, self.upper)}}


@dataclass(frozen=True)
class SafetyConfig:
    zone: SafeZone
    max_velocity_scale: float = 0.5

    def __post_init__(self) -> None:
        # the jog / waypoint / go_to_position box must be a SafeZone: the park envelope
        # (ParkZone) is deliberately a different type and can never stand in for it
        if not isinstance(self.zone, SafeZone):
            raise SafetyConfigError(f'zone SafeZone olmalı, bulunan: {type(self.zone).__name__}')

    def as_dict(self, velocity_scale: Optional[float] = None) -> Dict[str, Any]:
        return {'zone': self.zone.as_dict(), 'max_velocity_scale': self.max_velocity_scale,
                'velocity_scale': velocity_scale}


def effective_velocity_scale(requested: float, cap: Optional[float]) -> float:
    """The scaling actually used: ``requested`` limited to ``cap`` (and to (0, 1])."""
    if not math.isfinite(requested) or requested <= 0.0:
        raise ValueError(f'hız ölçeği 0 ile 1 arasında olmalı, verilen: {requested!r}')
    scale = min(float(requested), 1.0)
    return scale if cap is None else min(scale, float(cap))


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SafetyConfigError(f'{where}: sonlu bir sayı olmalı, bulunan: {value!r}')
    return float(value)


def parse_safety(data: Any) -> SafetyConfig:
    if not isinstance(data, Mapping):
        raise SafetyConfigError('güvenlik YAML kökü bir eşleme (anahtar: değer) olmalı')
    unknown = set(data) - {'frame', 'box', 'max_velocity_scale'}
    if unknown:
        raise SafetyConfigError(f'bilinmeyen anahtar(lar): {", ".join(sorted(map(str, unknown)))}')
    box = data.get('box')
    if not isinstance(box, Mapping) or set(box) != set(AXES):
        raise SafetyConfigError("'box' tam olarak x, y, z aralıklarını içermeli")
    lower, upper = [], []
    for axis in AXES:
        pair = box[axis]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise SafetyConfigError(f"box.{axis}: [alt, üst] olmalı, bulunan: {pair!r}")
        lo, hi = _number(pair[0], f'box.{axis}[0]'), _number(pair[1], f'box.{axis}[1]')
        if not lo < hi:
            raise SafetyConfigError(f'box.{axis}: alt sınır üstten küçük olmalı ({lo:g} !< {hi:g})')
        lower.append(lo)
        upper.append(hi)
    frame = data.get('frame', 'world')
    if not isinstance(frame, str) or not frame:
        raise SafetyConfigError("'frame' boş olmayan bir metin olmalı")
    cap = _number(data.get('max_velocity_scale', 0.5), 'max_velocity_scale')
    if not 0.0 < cap <= 1.0:
        raise SafetyConfigError(f'max_velocity_scale 0 ile 1 arasında olmalı, bulunan: {cap:g}')
    return SafetyConfig(SafeZone(tuple(lower), tuple(upper), frame), cap)


def load_safety(path: Union[str, Path]) -> SafetyConfig:
    try:
        with open(path, encoding='utf-8') as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise SafetyConfigError(f'{path}: okunamadı ({exc.strerror or exc})') from exc
    except yaml.YAMLError as exc:
        raise SafetyConfigError(f'{path}: geçerli YAML değil ({exc})') from exc
    try:
        return parse_safety(data)
    except SafetyConfigError as exc:
        raise SafetyConfigError(f'{path}: {exc}') from exc


# ---- park exemption (F4a-2) -------------------------------------------------------------------
# NOT generated and NOT part of the robot profile: `config/safe_zone.park.yaml` is a hand-reviewed
# safety file. It lets exactly ONE move leave the inner box: ready <-> home, validated by
# robot_core.pathcheck.check_park_move against the park envelope below. Jog, waypoints,
# go_to_position and the agent only ever see the inner SafeZone (ParkZone is a different type).
PARK_FILE = 'safe_zone.park.yaml'
PARK_MARGIN = 0.05            # m added to the measured transit extent; no tolerance on top
PARK_POSES = ('home',)        # the ONLY pose that may be parked at (adding one = code review)
PARK_MAX_READY_TOLERANCE = 0.02   # rad: how close to `ready` a park move must start
PARK_MAX_VELOCITY_FRACTION = 0.3
_PARK_KEYS = ('park_version', 'frame', 'reference_pose', 'ready_joints', 'ready_tolerance',
              'poses', 'transit', 'max_velocity_fraction')


class ParkConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ParkZone:
    """Park envelope: measured transit extent + 5 cm. Deliberately NOT a SafeZone."""
    lower: Vec
    upper: Vec
    frame: str

    def as_safe_zone(self) -> SafeZone:      # only pathcheck.check_park_move calls this
        return SafeZone(self.lower, self.upper, self.frame)


@dataclass(frozen=True)
class ParkConfig:
    ready_joints: Tuple[float, ...]
    ready_tolerance: float
    poses: Mapping[str, Tuple[float, ...]]          # verified joint values, `home` only
    measured: Tuple[Tuple[float, float], ...]       # per axis: verified transit extent (m)
    zone: ParkZone                                  # = measured +- PARK_MARGIN
    max_velocity_fraction: float


def _park_joints(value: Any, where: str) -> Tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ParkConfigError(f'{where}: eklem listesi olmalı: {value!r}')
    return tuple(_park_number(v, f'{where}[{i}]') for i, v in enumerate(value))


def _park_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ParkConfigError(f'{where}: sonlu sayı olmalı: {value!r}')
    return float(value)


def parse_park(data: Any) -> ParkConfig:
    """Strict: unknown keys, a pose other than ``home``, a margin other than 5 cm, a ready
    tolerance above 0.02 rad or a speed above 30 % are all errors."""
    if not isinstance(data, Mapping):
        raise ParkConfigError('park dosyası bir eşleme olmalı')
    extra = set(data) - set(_PARK_KEYS)
    missing = [k for k in _PARK_KEYS if k not in data]
    if extra or missing:
        raise ParkConfigError(f'park anahtarları: bilinmeyen {sorted(extra)}, eksik {missing}')
    if data['park_version'] != 1:
        raise ParkConfigError(f"park_version 1 olmalı: {data['park_version']!r}")
    if data['reference_pose'] != 'ready':
        raise ParkConfigError("reference_pose 'ready' olmalı")
    ready = _park_joints(data['ready_joints'], 'ready_joints')
    tol = _park_number(data['ready_tolerance'], 'ready_tolerance')
    if not 0.0 < tol <= PARK_MAX_READY_TOLERANCE:
        raise ParkConfigError(f'ready_tolerance (0, {PARK_MAX_READY_TOLERANCE}] rad olmalı: {tol}')
    poses_in = data['poses']
    if not isinstance(poses_in, Mapping) or set(poses_in) != set(PARK_POSES):
        got = sorted(poses_in) if isinstance(poses_in, Mapping) else poses_in
        raise ParkConfigError(f'poses yalnız {list(PARK_POSES)} içermeli: {got!r}')
    poses = {}
    for name, body in poses_in.items():
        if not isinstance(body, Mapping) or set(body) != {'joints'}:
            raise ParkConfigError(f'poses.{name}: yalnız `joints` olmalı')
        joints = _park_joints(body['joints'], f'poses.{name}.joints')
        if len(joints) != len(ready):
            raise ParkConfigError(f'poses.{name}.joints {len(ready)} eklem olmalı')
        poses[name] = joints
    transit = data['transit']
    if not isinstance(transit, Mapping) or set(transit) != {'measured', 'margin'}:
        raise ParkConfigError('transit: yalnız measured ve margin olmalı')
    if _park_number(transit['margin'], 'transit.margin') != PARK_MARGIN:
        raise ParkConfigError(f'transit.margin tam {PARK_MARGIN} m olmalı (tolerans eklenmez)')
    meas = transit['measured']
    if not isinstance(meas, Mapping) or set(meas) != set(AXES):
        raise ParkConfigError('transit.measured x, y, z içermeli')
    measured = []
    for axis in AXES:
        pair = meas[axis]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ParkConfigError(f'transit.measured.{axis}: [alt, üst] olmalı')
        lo, hi = _park_number(pair[0], f'{axis}[0]'), _park_number(pair[1], f'{axis}[1]')
        if lo > hi:
            raise ParkConfigError(f'transit.measured.{axis}: alt <= üst olmalı')
        measured.append((lo, hi))
    frac = _park_number(data['max_velocity_fraction'], 'max_velocity_fraction')
    if not 0.0 < frac <= PARK_MAX_VELOCITY_FRACTION:
        raise ParkConfigError(f'max_velocity_fraction (0, {PARK_MAX_VELOCITY_FRACTION}] olmalı')
    if not isinstance(data['frame'], str) or not data['frame']:
        raise ParkConfigError("'frame' boş olmayan bir metin olmalı")
    zone = ParkZone(tuple(round(lo - PARK_MARGIN, 4) for lo, _ in measured),
                    tuple(round(hi + PARK_MARGIN, 4) for _, hi in measured), data['frame'])
    return ParkConfig(ready, tol, poses, tuple(measured), zone, frac)


def load_park(path: Union[str, Path]) -> ParkConfig:
    try:
        with open(path, encoding='utf-8') as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ParkConfigError(f'{path}: okunamadı ({exc.strerror or exc})') from exc
    except yaml.YAMLError as exc:
        raise ParkConfigError(f'{path}: geçerli YAML değil ({exc})') from exc
    try:
        return parse_park(data)
    except ParkConfigError as exc:
        raise ParkConfigError(f'{path}: {exc}') from exc
