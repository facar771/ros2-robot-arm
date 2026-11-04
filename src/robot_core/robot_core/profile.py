"""Robot profile: reader + validator (pure Python, no ROS). Design: docs/ROBOT_PROFILE.md.

One YAML (``robot_description/profile/<robot>.yaml``) describes the robot;
``robot_core.profile_gen`` derives URDF/xacro, joint limits, SRDF, safe zone and controller files
from it.

Motors, drivers, encoders and homing are NOT here: they live in ``hardware.profile.yaml``
(``robot_core.hardware``) and never enter the URDF/MoveIt files.

Scope: a SERIAL chain of REVOLUTE joints. Parallel/closed-loop mechanisms, mimic joints and
prismatic joints are rejected with a clear message rather than half-supported.

Rules for defaults (always the narrower / safer choice) and for required values:
- values with no safe default are REQUIRED (position limits, axes, lengths, mass);
- omitted velocity/acceleration limit = 0.5, omitted effort = 10.0, omitted
  ``max_velocity_scale`` = 0.5;
- omitted ``safety.box`` = the axis-aligned box inside the sphere of half the straight-arm reach
  around the shoulder (a box given explicitly must lie inside the full reach, otherwise: error).

Every error names the place in the YAML (``joints.joint_2.limits.position: ...``).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import yaml

from robot_core.safezone import AXES, SafetyConfig, SafetyConfigError, parse_safety

PROFILE_VERSION = 3
Vec = Tuple[float, float, float]
_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_POSE_NAME = re.compile(r'^[a-z][a-z0-9_]*$')

# unit sanity (mm or degrees typed where metres or radians are expected)
MAX_ANGLE = 4 * math.pi          # rad: more than two turns means degrees
MAX_SPEED = 50.0                 # rad/s
MAX_ACCEL = 1000.0               # rad/s^2
MAX_LENGTH = 5.0                 # m: more means millimetres
MIN_LENGTH = 0.001               # m: less means a typo (or metres typed as km)
MAX_MASS = 1000.0                # kg
MAX_EFFORT = 10000.0             # N*m

DEFAULT_VELOCITY = 0.5
DEFAULT_ACCELERATION = 0.5
DEFAULT_EFFORT = 10.0
DEFAULT_SCALE = 0.5
DEFAULT_MARGIN_POSITION = 0.05
DEFAULT_MARGIN_VELOCITY = 0.05
BOX_FLOOR = 0.02                 # m: derived/explicit safe boxes stay above the ground
BOX_NEAR = 0.05                  # m: derived boxes keep clear of the base column


class ProfileError(ValueError):
    """The profile is missing something, malformed, or inconsistent (message: where and what)."""


# ---- data -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Link:
    name: str
    length: float                    # m along the link z axis: sets the next joint's offset
    shape: str                       # 'cylinder' | 'box'
    dims: Tuple[float, ...]          # cylinder: (radius, length)   box: (x, y, z)   (resolved)
    origin: Vec                      # geometry centre (and centre of mass) in the link frame
    material: str
    mass: float
    inertia: Tuple[float, float, float]   # ixx, iyy, izz (products are zero)


@dataclass(frozen=True)
class Joint:
    name: str
    parent: str
    child: str
    origin: Vec                      # resolved: (0, 0, parent.length) + offset
    axis: Vec
    lower: float
    upper: float
    velocity: float
    acceleration: float
    effort: float


@dataclass(frozen=True)
class Profile:
    robot: str
    materials: Mapping[str, Tuple[float, float, float, float]]
    mount: Tuple[str, str, str]                      # joint name, parent, child
    links: Tuple[Link, ...]
    joints: Tuple[Joint, ...]
    planning_group: str
    default_velocity_scaling: float
    default_acceleration_scaling: float
    initial_positions: Mapping[str, float]
    named_poses: Mapping[str, Tuple[float, ...]]
    allow_extra: Tuple[Tuple[str, str, str], ...]
    safety: SafetyConfig
    reach_center: Vec                                # shoulder: where joint_2's frame sits at zero
    reach_radius: float                              # straight-arm reach from the shoulder
    box_derived: bool

    @property
    def joint_names(self) -> Tuple[str, ...]:
        return tuple(j.name for j in self.joints)

    @property
    def tip_link(self) -> str:
        return self.links[-1].name

    def link(self, name: str) -> Link:
        return next(link for link in self.links if link.name == name)

    def adjacent_pairs(self) -> List[Tuple[str, str]]:
        return [(j.parent, j.child) for j in self.joints]


# ---- small validators -------------------------------------------------------------------------
def _fail(where: str, message: str) -> ProfileError:
    return ProfileError(f'{where}: {message}')


def _number(value: Any, where: str, *, lo: Optional[float] = None, hi: Optional[float] = None,
            positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _fail(where, f'sonlu bir sayı olmalı, bulunan: {value!r}')
    value = float(value)
    if positive and value <= 0:
        raise _fail(where, f'pozitif olmalı, bulunan: {value:g}')
    if lo is not None and value < lo:
        raise _fail(where, f'{value:g}, alt sınır {lo:g} altında')
    if hi is not None and value > hi:
        raise _fail(where, f'{value:g}, üst sınır {hi:g} üstünde')
    return value


def _vector(value: Any, count: int, where: str) -> Tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise _fail(where, f'{count} sayılık liste olmalı, bulunan: {value!r}')
    return tuple(_number(v, f'{where}[{i}]') for i, v in enumerate(value))


def _integer(value: Any, where: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(where, f'tam sayı olmalı, bulunan: {value!r}')
    if not lo <= value <= hi:
        raise _fail(where, f'{value}, izin verilen aralık [{lo}, {hi}] dışında')
    return value


def _mapping(value: Any, where: str, allowed: Sequence[str], required: Sequence[str] = ()) -> Dict:
    if not isinstance(value, Mapping):
        raise _fail(where, f'eşleme (anahtar: değer) olmalı, bulunan: {value!r}')
    unknown = sorted(set(map(str, value)) - set(allowed))
    if unknown:
        raise _fail(where, f"bilinmeyen anahtar(lar): {', '.join(unknown)} "
                           f"(geçerli: {', '.join(allowed)})")
    missing = [k for k in required if k not in value]
    if missing:
        raise _fail(where, f"eksik anahtar(lar): {', '.join(missing)}")
    return dict(value)


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _NAME.match(value):
        raise _fail(where, f'geçerli bir ad olmalı (harf/rakam/_), bulunan: {value!r}')
    return value


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(c * c for c in v))


# ---- derived values ---------------------------------------------------------------------------
def solid_inertia(shape: str, dims: Sequence[float], mass: float) -> Tuple[float, float, float]:
    """Solid-primitive inertia about the centre, rounded to 6 decimals like the URDF always was."""
    if shape == 'cylinder':
        r, length = dims
        ixx = mass * (3 * r * r + length * length) / 12
        values = (ixx, ixx, mass * r * r / 2)
    else:
        x, y, z = dims
        values = (mass * (y * y + z * z) / 12, mass * (x * x + z * z) / 12,
                  mass * (x * x + y * y) / 12)
    return tuple(round(v, 6) for v in values)


def _reach(joints: Sequence[Joint], tip_length: float) -> Tuple[Vec, float]:
    """Shoulder (joint_2's frame at zero) and straight-arm reach from it to the tool tip."""
    if len(joints) < 2:
        raise ProfileError('joints: omuz (2. eklem) için en az 2 eklem gerekli')
    shoulder = tuple(sum(j.origin[i] for j in joints[:2]) for i in range(3))
    reach = sum(_norm(j.origin) for j in joints[2:]) + tip_length
    return shoulder, reach


def derive_box(center: Vec, reach: float) -> Dict[str, List[float]]:
    """Axis-aligned box inside the sphere of radius reach/2 around ``center`` (the narrower
    default), above the floor and clear of the base column."""
    half = (reach / 2) / math.sqrt(3)
    box = {a: [center[i] - half, center[i] + half] for i, a in enumerate(AXES)}
    box['x'][0] = max(box['x'][0], BOX_NEAR)
    box['z'][0] = max(box['z'][0], BOX_FLOOR)
    for axis, (lo, hi) in box.items():
        if not lo < hi:
            raise ProfileError(f'safety.box: türetilen {axis} aralığı boş ({lo:g} !< {hi:g}); '
                               'kutuyu elle yaz')
    return box


# ---- parsing ----------------------------------------------------------------------------------
def _parse_materials(data: Any) -> Dict[str, Tuple[float, ...]]:
    if not isinstance(data, Mapping) or not data:
        raise ProfileError('materials: en az bir malzeme (ad: [r, g, b, a]) olmalı')
    out = {}
    for name, rgba in data.items():
        _name(name, f'materials.{name}')
        out[name] = tuple(_number(v, f'materials.{name}[{i}]', lo=0.0, hi=1.0)
                          for i, v in enumerate(_vector(rgba, 4, f'materials.{name}')))
    return out


def _parse_link(name: str, data: Any, materials: Mapping) -> Link:
    where = f'links.{name}'
    d = _mapping(data, where, ('length', 'shape', 'origin', 'material', 'mass', 'inertia'),
                 ('length', 'shape', 'material', 'mass'))
    length = _number(d['length'], f'{where}.length', lo=MIN_LENGTH, hi=MAX_LENGTH)
    shape_data = d['shape']
    kind = shape_data.get('type') if isinstance(shape_data, Mapping) else None
    if kind == 'cylinder':
        s = _mapping(shape_data, f'{where}.shape', ('type', 'radius', 'length'),
                     ('type', 'radius'))
        radius = _number(s['radius'], f'{where}.shape.radius', lo=MIN_LENGTH, hi=MAX_LENGTH)
        extent = _number(s.get('length', length), f'{where}.shape.length', lo=MIN_LENGTH,
                         hi=MAX_LENGTH)
        dims: Tuple[float, ...] = (radius, extent)
    elif kind == 'box':
        s = _mapping(shape_data, f'{where}.shape', ('type', 'size'), ('type', 'size'))
        size = s['size']
        if not isinstance(size, (list, tuple)) or len(size) not in (2, 3):
            raise _fail(f'{where}.shape.size', f'[x, y] (z = length) ya da [x, y, z]: {size!r}')
        xyz = [_number(v, f'{where}.shape.size[{i}]', lo=MIN_LENGTH, hi=MAX_LENGTH)
               for i, v in enumerate(size)]
        dims = (xyz[0], xyz[1], xyz[2] if len(xyz) == 3 else length)
    else:
        raise _fail(f'{where}.shape.type', f"'cylinder' ya da 'box' olmalı, bulunan: {kind!r}")
    extent_z = dims[-1]
    origin = (_vector(d['origin'], 3, f'{where}.origin') if 'origin' in d
              else (0.0, 0.0, extent_z / 2))
    material = d['material']
    if material not in materials:
        raise _fail(f'{where}.material', f'tanımsız malzeme {material!r}')
    mass = _number(d['mass'], f'{where}.mass', positive=True, hi=MAX_MASS)
    if 'inertia' in d:
        inertia = tuple(_number(v, f'{where}.inertia[{i}]', positive=True)
                        for i, v in enumerate(_vector(d['inertia'], 3, f'{where}.inertia')))
    else:
        inertia = solid_inertia(kind, dims, mass)
    return Link(name, length, kind, dims, origin, material, mass, inertia)


def _parse_joint(name: str, data: Any, parent_link: Link) -> Joint:
    where = f'joints.{name}'
    d = _mapping(data, where, ('type', 'parent', 'child', 'axis', 'offset', 'limits'),
                 ('type', 'parent', 'child', 'axis', 'limits'))
    if d['type'] != 'revolute':
        raise _fail(f'{where}.type', f"yalnızca 'revolute' destekleniyor (bulunan: {d['type']!r}); "
                                     'prizmatik/paralel/mimic eklemler kapsam dışı')
    axis = _vector(d['axis'], 3, f'{where}.axis')
    if abs(_norm(axis) - 1.0) > 1e-9:
        raise _fail(f'{where}.axis', f'birim vektör olmalı (uzunluk {_norm(axis):g}): {list(axis)}')
    offset = _vector(d['offset'], 3, f'{where}.offset') if 'offset' in d else (0.0, 0.0, 0.0)
    limits = _mapping(d['limits'], f'{where}.limits',
                      ('position', 'velocity', 'acceleration', 'effort'), ('position',))
    position = _vector(limits['position'], 2, f'{where}.limits.position')
    for i, v in enumerate(position):
        _number(v, f'{where}.limits.position[{i}]', lo=-MAX_ANGLE, hi=MAX_ANGLE)
    if not position[0] < position[1]:
        raise _fail(f'{where}.limits.position', f'alt sınır üstten küçük olmalı: {list(position)}')
    origin = (offset[0], offset[1], offset[2] + parent_link.length)
    return Joint(
        name, d['parent'], d['child'], origin, axis, position[0], position[1],
        _number(limits.get('velocity', DEFAULT_VELOCITY), f'{where}.limits.velocity',
                positive=True, hi=MAX_SPEED),
        _number(limits.get('acceleration', DEFAULT_ACCELERATION), f'{where}.limits.acceleration',
                positive=True, hi=MAX_ACCEL),
        _number(limits.get('effort', DEFAULT_EFFORT), f'{where}.limits.effort', positive=True,
                hi=MAX_EFFORT))


def parse_profile(data: Any) -> Profile:
    top = ('profile_version', 'robot', 'materials', 'mount', 'links', 'joints', 'moveit',
           'safety')
    d = _mapping(data, 'profil', top, tuple(k for k in top if k != 'safety'))
    if d['profile_version'] != PROFILE_VERSION:
        raise _fail('profile_version',
                    f"{PROFILE_VERSION} olmalı, bulunan: {d['profile_version']!r}")
    robot = _name(d['robot'], 'robot')
    materials = _parse_materials(d['materials'])

    # links and joints (serial chain)
    if not isinstance(d['links'], Mapping) or len(d['links']) < 2:
        raise ProfileError('links: en az 2 link (taban + 1) olmalı')
    links = tuple(_parse_link(_name(n, f'links.{n}'), v, materials)
                  for n, v in d['links'].items())
    found = len(d['joints']) if isinstance(d['joints'], Mapping) else d['joints']
    if not isinstance(d['joints'], Mapping) or len(d['joints']) != len(links) - 1:
        raise ProfileError(f'joints: {len(links) - 1} eklem olmalı (link sayısı − 1), '
                           f'bulunan: {found!r}')
    joints: List[Joint] = []
    for (name, joint_data), parent, child in zip(d['joints'].items(), links, links[1:]):
        _name(name, f'joints.{name}')
        joint = _parse_joint(name, joint_data, parent)
        if (joint.parent, joint.child) != (parent.name, child.name):
            raise _fail(f'joints.{name}',
                        f'zincir sırası: {parent.name} -> {child.name} bekleniyordu, '
                        f'bulunan: {joint.parent} -> {joint.child}')
        joints.append(joint)
    mount_data = _mapping(d['mount'], 'mount', ('name', 'parent', 'child'),
                          ('name', 'parent', 'child'))
    if mount_data['parent'] != 'world' or mount_data['child'] != links[0].name:
        raise _fail('mount', f"world -> {links[0].name} olmalı, bulunan: {mount_data['parent']} -> "
                             f"{mount_data['child']}")
    mount = (_name(mount_data['name'], 'mount.name'), 'world', links[0].name)
    used = {link.name for link in links} | {j.name for j in joints}
    clash = {n for n in (mount[0], 'world') if n in used}
    if clash:
        raise _fail('mount', f'ad çakışması: {sorted(clash)}')

    # MoveIt
    mv = _mapping(d['moveit'], 'moveit', ('planning_group', 'default_velocity_scaling',
                                          'default_acceleration_scaling', 'initial_positions',
                                          'named_poses', 'collision'))
    names = [j.name for j in joints]
    by_name = {j.name: j for j in joints}

    def inside(joint_name: str, value: float, where: str) -> float:
        value = _number(value, where)
        j = by_name[joint_name]
        if not j.lower <= value <= j.upper:
            raise _fail(where, f'{value:g}, {joint_name} limiti [{j.lower:g}, {j.upper:g}] dışında')
        return value

    init = mv.get('initial_positions', {n: 0.0 for n in names})
    init = _mapping(init, 'moveit.initial_positions', names, names)
    initial = {n: inside(n, init[n], f'moveit.initial_positions.{n}') for n in names}
    poses: Dict[str, Tuple[float, ...]] = {}
    for pose, values in (mv.get('named_poses') or {}).items():
        if not isinstance(pose, str) or not _POSE_NAME.match(pose):
            raise _fail(f'moveit.named_poses.{pose}', 'ad küçük harf/rakam/_ olmalı')
        vec = _vector(values, len(names), f'moveit.named_poses.{pose}')
        poses[pose] = tuple(inside(n, v, f'moveit.named_poses.{pose}.{n}')
                            for n, v in zip(names, vec))
    collision = _mapping(mv.get('collision', {}), 'moveit.collision', ('allow_extra',))
    adjacent = {frozenset(p) for p in ((j.parent, j.child) for j in joints)}
    link_names = {link.name for link in links}
    extra: List[Tuple[str, str, str]] = []
    for i, entry in enumerate(collision.get('allow_extra') or []):
        where = f'moveit.collision.allow_extra[{i}]'
        if (not isinstance(entry, (list, tuple)) or len(entry) != 3
                or not all(isinstance(x, str) and x for x in entry)):
            raise _fail(where, f'[link1, link2, gerekçe] olmalı (gerekçe zorunlu): {entry!r}')
        a, b, reason = entry
        if a == b or a not in link_names or b not in link_names:
            raise _fail(where, f'geçerli, farklı iki link olmalı: {a!r}, {b!r}')
        if frozenset((a, b)) in adjacent:
            raise _fail(where, f'{a}-{b} komşu çift, zaten her zaman kapalı')
        if any(frozenset((a, b)) == frozenset(e[:2]) for e in extra):
            raise _fail(where, f'{a}-{b} tekrar ediyor')
        extra.append((a, b, reason))

    # reach, safety
    shoulder, reach = _reach(joints, links[-1].length)
    safety_in = _mapping(d.get('safety') or {}, 'safety', ('frame', 'box', 'max_velocity_scale'))
    derived = 'box' not in safety_in
    if derived:
        safety_in['box'] = derive_box(shoulder, reach)
    safety_in.setdefault('max_velocity_scale', DEFAULT_SCALE)
    try:
        safety = parse_safety(safety_in)
    except SafetyConfigError as exc:
        raise ProfileError(f'safety: {exc}') from exc
    if safety.zone.frame != 'world':
        raise _fail('safety.frame', "yalnızca 'world' destekleniyor (taban orijinde)")
    if safety.zone.lower[2] < 0:
        raise _fail('safety.box.z',
                    f'kutu zemin altına iniyor (alt sınır {safety.zone.lower[2]:g})')
    if not derived:
        lo, hi = safety.zone.lower, safety.zone.upper
        far = max(math.dist(shoulder, (x, y, z)) for x in (lo[0], hi[0])
                  for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))
        if far > reach:
            raise _fail('safety.box', f'kutunun en uzak köşesi omuzdan {far:.3f} m, kolun erişimi '
                                      f'{reach:.3f} m; kutuyu küçült')

    return Profile(
        robot=robot, materials=materials, mount=mount, links=links, joints=tuple(joints),
        planning_group=_name(mv.get('planning_group', 'robot_arm'), 'moveit.planning_group'),
        default_velocity_scaling=_number(mv.get('default_velocity_scaling', DEFAULT_SCALE),
                                         'moveit.default_velocity_scaling', positive=True, hi=1.0),
        default_acceleration_scaling=_number(mv.get('default_acceleration_scaling', DEFAULT_SCALE),
                                             'moveit.default_acceleration_scaling', positive=True,
                                             hi=1.0),
        initial_positions=initial, named_poses=poses, allow_extra=tuple(extra), safety=safety,
        reach_center=shoulder, reach_radius=reach, box_derived=derived)


def load_profile(path: Union[str, Path]) -> Profile:
    try:
        with open(path, encoding='utf-8') as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ProfileError(f'{path}: okunamadı ({exc.strerror or exc})') from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f'{path}: geçerli YAML değil ({exc})') from exc
    try:
        return parse_profile(data)
    except ProfileError as exc:
        raise ProfileError(f'{path}: {exc}') from exc
