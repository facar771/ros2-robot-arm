"""Profile maturity: quality tags, three maturity levels, the real-hardware gate (pure Python).

Design: docs/ROBOT_PROFILE.md ("Eksik bilgiyle çalışan profil", roadmap J5). The robot is described
from day one with stand-in numbers; this module says HOW MUCH of the profile is real.

* **Quality tag** per physical value: ``measured`` | ``estimated`` | ``placeholder``. Tags live in a
  SIDECAR file (``quality.profile.yaml``), not in the robot/hardware YAML, so those two files and
  everything generated from them stay byte-identical. A value with no tag is ``placeholder``
  (backward compatible). ``null`` = no value known at all (status ``unknown``, worse than a
  placeholder). A tag may pin the value it was measured at (``{tag: measured, value: 0.35}``): when
  the profile number later differs, the tag is ``stale`` and does NOT count as measured.
* **Maturity**: 1 taslak (schema valid: the profile parsed) -> 2 simülasyona hazır (poses/limits
  consistent) -> 3 donanıma hazır (every critical field ``measured`` and the static torque check
  passed). ``maturity()`` returns the level AND the gaps by field name.
* **Gate**: ``gate('sim'|'real', maturity)``. Real needs level 3 AND the path check on the executor
  (F4b); until ``F4B_DONE`` is flipped by that PR, real never opens. Wiring the gate into the API
  is NOT done here.

Lowering a tag (measured -> estimated) lowers the maturity again, because maturity is always
recomputed from the tags.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import yaml

from robot_core.hardware import HardwareError, HardwareProfile, ServoAxis, load_hardware
from robot_core.profile import Profile, ProfileError, _fail, _mapping, _name, load_profile

QUALITY_VERSION = 1
TAGS = ('measured', 'estimated', 'placeholder')
DEFAULT_TAG = 'placeholder'          # untagged values (backward compatible, the narrow choice)

DRAFT, SIM_READY, HARDWARE_READY = 1, 2, 3
LEVEL_NAMES = {DRAFT: 'taslak', SIM_READY: 'simülasyona hazır', HARDWARE_READY: 'donanıma hazır'}

# Flipped to True only by the PR that makes the executor enforce the path check (F4b).
F4B_DONE = False

GRAVITY = 9.81
SERVO = 'hobby_servo'

# Fields that must be `measured` for hardware readiness. {link}/{joint} expand per link/axis.
LINK_CRITICAL = ('length', 'mass')                       # mass feeds the torque check
JOINT_CRITICAL = ('limits.position', 'limits.effort')    # physical limit; effort = torque rating
AXIS_CRITICAL = ('motor.type', 'motor.steps_per_rev', 'driver.microstep', 'gear_ratio',
                 'limits.position', 'homing.position')   # homing.position = home/limit switch
SERVO_CRITICAL = ('servo.pulse_range',)                  # instead of steps/gear/homing (K6)
# K6a: what a REAL servo axis (ServoAxis) must also have measured: its software speed cap, the rest
# angle it slowly approaches after PWM-on, and the pose the operator confirms for manual_zero.
# (limits.position, the angle limit, is already in AXIS_CRITICAL.) Only enforced on a ServoAxis, so
# the older J5 stand-in (a step Axis relabelled hobby_servo) still reports just pulse_range.
SERVO_REAL_CRITICAL = ('servo.slew_limit', 'servo.rest_angle', 'reference.pose')
STEP_ONLY = ('motor.steps_per_rev', 'driver.microstep', 'gear_ratio', 'homing.position')


# ---- quality map ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Quality:
    """``tags``: field path -> tag, or ``None`` (no value known). ``pins``: path -> pinned value."""
    tags: Mapping[str, Optional[str]] = field(default_factory=dict)
    pins: Mapping[str, Any] = field(default_factory=dict)

    def tag(self, path: str) -> Optional[str]:
        return self.tags.get(path, DEFAULT_TAG)

    def with_tag(self, path: str, tag: Optional[str], pin: Any = None) -> 'Quality':
        """New map with ``path`` re-tagged (the old pin is dropped unless ``pin`` is given)."""
        if tag is not None and tag not in TAGS:
            raise ProfileError(f'quality.{path}: {" | ".join(TAGS)} ya da null olmalı, bulunan: '
                               f'{tag!r}')
        pins = {k: v for k, v in self.pins.items() if k != path}
        if pin is not None:
            pins[path] = pin
        return Quality({**self.tags, path: tag}, pins)


def known_paths(profile: Profile) -> List[str]:
    """Every field path a tag may name (hardware axes follow the robot's joint names)."""
    paths = [f'links.{n.name}.{f}' for n in profile.links for f in LINK_CRITICAL]
    for j in profile.joints:
        paths += [f'joints.{j.name}.{f}' for f in JOINT_CRITICAL]
        paths += [f'axes.{j.name}.{f}'
                  for f in AXIS_CRITICAL + SERVO_CRITICAL + SERVO_REAL_CRITICAL]
    return paths


def parse_quality(data: Any, profile: Profile) -> Quality:
    top = ('quality_version', 'robot', 'fields')
    d = _mapping(data, 'kalite profili', top, top)
    if d['quality_version'] != QUALITY_VERSION:
        raise _fail('quality_version',
                    f"{QUALITY_VERSION} olmalı, bulunan: {d['quality_version']!r}")
    if _name(d['robot'], 'robot') != profile.robot:
        raise _fail('robot',
                    f"robot profili {profile.robot!r}, kalite profili {d['robot']!r} diyor")
    fields = d['fields'] if d['fields'] is not None else {}
    if not isinstance(fields, Mapping):
        raise _fail('fields', f'eşleme (alan yolu: etiket) olmalı, bulunan: {fields!r}')
    known = set(known_paths(profile))
    tags: Dict[str, Optional[str]] = {}
    pins: Dict[str, Any] = {}
    for path, entry in fields.items():
        if path not in known:
            raise _fail(f'fields.{path}', 'bilinmeyen alan yolu (yazım hatası mı? örnek: '
                                          'links.link_2.length, axes.joint_1.gear_ratio)')
        if isinstance(entry, Mapping):
            entry = _mapping(entry, f'fields.{path}', ('tag', 'value'), ('tag', 'value'))
            pins[path] = entry['value']
            entry = entry['tag']
        if entry is not None and entry not in TAGS:
            raise _fail(f'fields.{path}',
                        f"{' | '.join(TAGS)} ya da null olmalı, bulunan: {entry!r}")
        tags[path] = entry
    return Quality(tags, pins)


def load_quality(path: Union[str, Path, None], profile: Profile) -> Quality:
    """Read the sidecar; no file (``None`` or missing) = every value ``placeholder``."""
    if path is None or not Path(path).exists():
        return Quality()
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f'{path}: okunamadı/geçerli YAML değil ({exc})') from exc
    try:
        return parse_quality(data, profile)
    except ProfileError as exc:
        raise ProfileError(f'{path}: {exc}') from exc


# ---- field values -----------------------------------------------------------------------------
def _axis_value(axis: Any, suffix: str) -> Any:
    getters = {
        'motor.type': lambda a: a.motor_type,
        'motor.steps_per_rev': lambda a: a.steps_per_rev,
        'driver.microstep': lambda a: a.microstep,
        'gear_ratio': lambda a: a.gear_ratio,
        'limits.position': lambda a: list(a.position_limits),
        'homing.position': lambda a: a.homing.position,
        'servo.pulse_range': lambda a: getattr(a, 'servo_pulse_range', None),
        'servo.slew_limit': lambda a: getattr(a, 'slew_limit', None),
        'servo.rest_angle': lambda a: getattr(getattr(a, 'rest', None), 'angle', None),
        'reference.pose': lambda a: getattr(getattr(a, 'reference', None), 'pose', None),
    }
    return getters[suffix](axis)


def field_value(profile: Profile, hardware: Optional[HardwareProfile], path: str) -> Any:
    """Current value of ``path`` (``None`` = not available, e.g. no hardware profile)."""
    kind, name, suffix = path.split('.', 2)
    if kind == 'links':
        link = profile.link(name)
        return {'length': link.length, 'mass': link.mass}[suffix]
    if kind == 'joints':
        joint = next(j for j in profile.joints if j.name == name)
        return {'limits.position': [joint.lower, joint.upper],
                'limits.effort': joint.effort}[suffix]
    if hardware is None:
        return None
    return _axis_value(hardware.axis(name), suffix)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def status(quality: Quality, profile: Profile, hardware: Optional[HardwareProfile],
           path: str) -> str:
    """measured | estimated | placeholder | unknown (null) | stale (pinned value changed)."""
    tag = quality.tag(path)
    if tag is None:
        return 'unknown'
    if path in quality.pins and not _same(field_value(profile, hardware, path),
                                          quality.pins[path]):
        return 'stale'
    return tag


# ---- static torque check ----------------------------------------------------------------------
def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


def static_torques(profile: Profile) -> Dict[str, float]:
    """Worst-case holding torque per joint (N*m): every mass beyond the joint at the farthest
    distance the geometry allows (arm stretched horizontally), whatever the joint's axis is.
    Conservative on purpose: it can only over-estimate."""
    n = len(profile.joints)
    out: Dict[str, float] = {}
    for k, joint in enumerate(profile.joints):
        total = 0.0
        for m in range(k + 1, n + 1):
            reach = _norm(profile.links[m].origin) + sum(
                _norm(profile.joints[j].origin) for j in range(k + 1, m))
            total += profile.links[m].mass * reach
        if profile.tool is not None:
            reach = _norm(profile.tool.com) + sum(
                _norm(profile.joints[j].origin) for j in range(k + 1, n))
            total += profile.tool.mass * reach
        out[joint.name] = GRAVITY * total
    return out


def torque_failures(profile: Profile) -> List[str]:
    return [f'{name}: gereken {need:.3g} N*m > tork sınırı {joint.effort:g} N*m'
            for (name, need), joint in zip(static_torques(profile).items(), profile.joints)
            if need > joint.effort]


# ---- maturity ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gap:
    field: str                       # field path, or 'tork denetimi' / 'donanım profili'
    status: str                      # placeholder | estimated | unknown | stale | eksik | başarısız
    detail: str = ''

    def __str__(self) -> str:
        return f'{self.field} ({self.status}{": " + self.detail if self.detail else ""})'


@dataclass(frozen=True)
class Maturity:
    level: int
    blockers: Tuple[str, ...]        # why the profile is not simulation-ready (level 1)
    gaps: Tuple[Gap, ...]            # why it is not hardware-ready (level 2)

    @property
    def name(self) -> str:
        return LEVEL_NAMES[self.level]

    @property
    def missing_fields(self) -> Tuple[str, ...]:
        """Names of the fields that keep the profile from being hardware-ready."""
        return tuple(g.field for g in self.gaps)


def _critical_paths(profile: Profile, hardware: Optional[HardwareProfile]) -> List[str]:
    paths = [f'links.{link.name}.{f}' for link in profile.links for f in LINK_CRITICAL]
    for joint in profile.joints:
        paths += [f'joints.{joint.name}.{f}' for f in JOINT_CRITICAL]
        axis = hardware.axis(joint.name) if hardware is not None else None
        servo = axis is not None and axis.motor_type == SERVO
        for f in AXIS_CRITICAL:
            if not (servo and f in STEP_ONLY):
                paths.append(f'axes.{joint.name}.{f}')
        if servo:
            extra = SERVO_REAL_CRITICAL if isinstance(axis, ServoAxis) else ()
            paths += [f'axes.{joint.name}.{f}' for f in SERVO_CRITICAL + extra]
    return paths


def _blockers(profile: Profile) -> List[str]:
    """Consistency a simulation needs beyond the schema (the parser already enforces most)."""
    out = []
    limits = {j.name: (j.lower, j.upper) for j in profile.joints}
    for label, values in [('moveit.initial_positions', profile.initial_positions),
                          *[(f'moveit.named_poses.{p}', dict(zip(profile.joint_names, v)))
                            for p, v in profile.named_poses.items()]]:
        for name, value in values.items():
            lo, hi = limits[name]
            if not lo <= value <= hi:
                out.append(f'{label}.{name}: {value:g} eklem limiti [{lo:g}, {hi:g}] dışında')
    if 'home' not in profile.named_poses:
        out.append("moveit.named_poses.home: 'home' pozu yok")
    return out


def maturity(profile: Profile, hardware: Optional[HardwareProfile] = None,
             quality: Optional[Quality] = None, *, hardware_error: str = '') -> Maturity:
    """Maturity of an already PARSED profile (a profile that does not parse is not even a draft:
    the loaders raise ``ProfileError``). ``hardware_error``: why the hardware profile could not be
    used (reported as a gap; it never blocks the simulation, which does not read it)."""
    quality = quality or Quality()
    blockers = tuple(_blockers(profile))
    if blockers:
        return Maturity(DRAFT, blockers, ())
    gaps: List[Gap] = []
    if hardware is None:
        gaps.append(Gap('donanım profili', 'eksik', hardware_error or 'hardware.profile.yaml yok'))
    for path in _critical_paths(profile, hardware):
        if hardware is None and path.startswith('axes.'):
            continue                                   # already one gap for the whole file
        state = status(quality, profile, hardware, path)
        if state == 'measured' and field_value(profile, hardware, path) is None:
            state = 'eksik'
        if state != 'measured':
            gaps.append(Gap(path, state))
    for text in torque_failures(profile):
        gaps.append(Gap('tork denetimi', 'başarısız', text))
    return Maturity(HARDWARE_READY if not gaps else SIM_READY, (), tuple(gaps))


def load_maturity(profile_path: Union[str, Path], hardware_path: Union[str, Path, None] = None,
                  quality_path: Union[str, Path, None] = None,
                  tool: Optional[str] = None) -> Maturity:
    """Files -> maturity. A robot profile that does not parse raises ``ProfileError``; a hardware
    profile that does not parse is a hardware gap only; a bad quality file raises."""
    profile = load_profile(profile_path, tool)
    hardware, error = None, ''
    if hardware_path is not None and Path(hardware_path).exists():
        try:
            hardware = load_hardware(hardware_path, profile)
        except HardwareError as exc:
            error = str(exc)
    return maturity(profile, hardware, load_quality(quality_path, profile), hardware_error=error)


# ---- gate -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class GateResult:
    open: bool
    mode: str
    reasons: Tuple[str, ...]         # why closed (human readable, Turkish)
    missing: Tuple[str, ...]         # field names to fill (real gate)


def gate(mode: str, m: Maturity) -> GateResult:
    """May the robot run in ``mode`` ('sim' | 'real')? Pure decision; nothing is wired to it yet."""
    if mode == 'sim':
        if m.level >= SIM_READY:
            return GateResult(True, mode, (), ())
        return GateResult(False, mode, ('simülasyona hazır değil: ' + '; '.join(m.blockers),), ())
    if mode != 'real':
        raise ValueError(f"mode 'sim' ya da 'real' olmalı, bulunan: {mode!r}")
    reasons: List[str] = []
    if m.level < HARDWARE_READY:
        shown = ', '.join(str(g) for g in m.gaps[:8]) or '; '.join(m.blockers)
        more = f' … (+{len(m.gaps) - 8} alan daha, hepsi `missing`te)' if len(m.gaps) > 8 else ''
        reasons.append(f'donanıma hazır değil (seviye: {m.name}); {len(m.gaps)} eksik/ölçülmemiş '
                       f'alan: {shown}{more}')
    if not F4B_DONE:
        reasons.append('yol boyunca kutu denetimi (F4b) yürütmede henüz uygulanmıyor')
    return GateResult(not reasons, mode, tuple(reasons), m.missing_fields)


__all__ = ['DRAFT', 'F4B_DONE', 'Gap', 'GateResult', 'HARDWARE_READY', 'Maturity', 'Quality',
           'SIM_READY', 'TAGS', 'field_value', 'gate', 'known_paths', 'load_maturity',
           'load_quality', 'maturity', 'parse_quality', 'static_torques', 'status',
           'torque_failures']
