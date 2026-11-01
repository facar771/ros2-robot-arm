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
