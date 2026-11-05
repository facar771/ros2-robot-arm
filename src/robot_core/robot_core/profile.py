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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import yaml

from robot_core.safezone import AXES, SafetyConfig, SafetyConfigError, SafeZone, parse_safety
from robot_core.se3 import make_transform, matrix_from_wpr

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
MAX_TOOL_MASS = 20.0             # kg: more is a different robot (or grams typed as kg)
MAX_TOOL_SHAPES = 8
MAX_TCP_OFFSET = 1.0             # m: a longer tool offset is a unit mistake (mm?) or a new robot

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
class Tcp:
    """Tool centre point relative to the tool-link (flange) frame; default = the flange."""
    offset: Vec = (0.0, 0.0, 0.0)           # m, in the tool-link frame
    wpr: Vec = (0.0, 0.0, 0.0)              # rad: R = Rz(R) Ry(P) Rx(W) (FANUC W-P-R)

    @property
    def transform(self):
        return make_transform(matrix_from_wpr(*self.wpr), self.offset)


@dataclass(frozen=True)
class ToolShape:
    """One collision/visual primitive of a tool, in the flange frame."""
    kind: str                                # 'cylinder' | 'box' | 'sphere'
    dims: Tuple[float, ...]                  # cylinder (radius, length); box (x, y, z); sphere (r,)
    origin: Vec = (0.0, 0.0, 0.0)            # m, shape centre in the flange frame
    wpr: Vec = (0.0, 0.0, 0.0)               # rad, R = Rz(R) Ry(P) Rx(W)

    @property
    def half_extents(self) -> Vec:           # local axis-aligned half sizes
        if self.kind == 'sphere':
            return (self.dims[0],) * 3
        if self.kind == 'cylinder':
            return (self.dims[0], self.dims[0], self.dims[1] / 2)
        return tuple(d / 2 for d in self.dims)


@dataclass(frozen=True)
class CameraSensor:
    """Camera: pose of the camera frame in the flange frame (z = optical axis)."""
    name: str
    offset: Vec
    wpr: Vec
    resolution: Tuple[int, int]              # width, height (px)
    fov: float                               # rad, HORIZONTAL field of view

    def intrinsics(self) -> Dict[str, float]:
        """Pinhole camera_info numbers derived from resolution + FOV (square pixels, no
        distortion)."""
        w, h = self.resolution
        f = (w / 2) / math.tan(self.fov / 2)
        return {'fx': f, 'fy': f, 'cx': w / 2, 'cy': h / 2, 'width': w, 'height': h}


@dataclass(frozen=True)
class LaserSensor:
    """Laser pointer: exit point and beam direction (unit) in the flange frame."""
    name: str
    origin: Vec
    axis: Vec


@dataclass(frozen=True)
class Tool:
    name: str
    tcp: Tcp                                 # relative to the flange (tool link) frame
    mass: float                              # kg
    com: Vec                                 # centre of mass in the flange frame
    inertia: Tuple[float, float, float]      # ixx, iyy, izz about the COM, flange axes
    shapes: Tuple[ToolShape, ...]            # collision + visual geometry
    box_margin: float                        # m the safe box shrinks on every wall
    material: str
    sensors: Tuple[Union[CameraSensor, LaserSensor], ...] = ()

    @property
    def link_name(self) -> str:
        return f'tool_{self.name}'


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
    tcp: Tcp
    reach_center: Vec                                # shoulder: where joint_2's frame sits at zero
    reach_radius: float                              # straight-arm reach from the shoulder
    box_derived: bool
    # tools (Q1): `tcp` and `safety` above are the EFFECTIVE ones for `active_tool`; the base_*
    # fields keep the tool-less values so `apply_tool` can switch without re-reading the YAML.
    tools: Mapping[str, Tool] = field(default_factory=dict)
    active_tool: Optional[str] = None
    base_tcp: Tcp = Tcp()
    base_safety: Optional[SafetyConfig] = None

    @property
    def tool(self) -> Optional[Tool]:
        return self.tools[self.active_tool] if self.active_tool else None

    @property
    def joint_names(self) -> Tuple[str, ...]:
        return tuple(j.name for j in self.joints)

    @property
    def tip_link(self) -> str:
        return self.links[-1].name

    def link(self, name: str) -> Link:
        return next(link for link in self.links if link.name == name)

    def adjacent_pairs(self) -> List[Tuple[str, str]]:
        pairs = [(j.parent, j.child) for j in self.joints]
        if self.tool:                        # the tool link hangs on the flange (fixed joint)
            pairs.append((self.tip_link, self.tool.link_name))
        return pairs


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


# ---- tools (Q1) -------------------------------------------------------------------------------
def _parse_wpr_pose(d: Mapping, where: str, off_key: str = 'offset') -> Tuple[Vec, Vec]:
    offset = tuple(_number(v, f'{where}.{off_key}[{i}]', lo=-MAX_TCP_OFFSET, hi=MAX_TCP_OFFSET)
                   for i, v in enumerate(_vector(d.get(off_key, (0, 0, 0)), 3,
                                                 f'{where}.{off_key}')))
    wpr = tuple(_number(v, f'{where}.wpr[{i}]', lo=-MAX_ANGLE, hi=MAX_ANGLE)
                for i, v in enumerate(_vector(d.get('wpr', (0, 0, 0)), 3, f'{where}.wpr')))
    return offset, wpr


def _parse_shape(data: Any, where: str) -> ToolShape:
    d = _mapping(data, where, ('type', 'radius', 'length', 'size', 'origin', 'wpr'), ('type',))
    kind = d['type']
    dim = dict(lo=MIN_LENGTH, hi=MAX_LENGTH)
    if kind == 'sphere':
        _mapping(d, where, ('type', 'radius', 'origin', 'wpr'), ('type', 'radius'))
        dims: Tuple[float, ...] = (_number(d['radius'], f'{where}.radius', **dim),)
    elif kind == 'cylinder':
        _mapping(d, where, ('type', 'radius', 'length', 'origin', 'wpr'),
                 ('type', 'radius', 'length'))
        dims = (_number(d['radius'], f'{where}.radius', **dim),
                _number(d['length'], f'{where}.length', **dim))
    elif kind == 'box':
        _mapping(d, where, ('type', 'size', 'origin', 'wpr'), ('type', 'size'))
        dims = tuple(_number(v, f'{where}.size[{i}]', **dim)
                     for i, v in enumerate(_vector(d['size'], 3, f'{where}.size')))
    else:
        raise _fail(f'{where}.type', f"'sphere', 'cylinder' ya da 'box' olmalı, bulunan: {kind!r}")
    origin, wpr = _parse_wpr_pose(d, where, 'origin')
    return ToolShape(kind, dims, origin, wpr)


def _parse_sensor(data: Any, where: str) -> Union[CameraSensor, LaserSensor]:
    kind = data.get('type') if isinstance(data, Mapping) else None
    if kind == 'camera':
        d = _mapping(data, where, ('type', 'name', 'pose', 'resolution', 'fov'),
                     ('type', 'name', 'pose', 'resolution', 'fov'))
        pose = _mapping(d['pose'], f'{where}.pose', ('offset', 'wpr'), ('offset',))
        offset, wpr = _parse_wpr_pose(pose, f'{where}.pose')
        res = d['resolution']
        if not isinstance(res, (list, tuple)) or len(res) != 2:
            raise _fail(f'{where}.resolution', f'[genişlik, yükseklik] olmalı: {res!r}')
        width, height = (_integer(v, f'{where}.resolution[{i}]', 16, 8192)
                         for i, v in enumerate(res))
        fov = _number(d['fov'], f'{where}.fov', lo=0.05, hi=3.0)   # rad: > ~172 deg = degrees typed
        return CameraSensor(_name(d['name'], f'{where}.name'), offset, wpr, (width, height), fov)
    if kind == 'laser':
        d = _mapping(data, where, ('type', 'name', 'origin', 'axis'),
                     ('type', 'name', 'origin', 'axis'))
        origin = tuple(_number(v, f'{where}.origin[{i}]', lo=-MAX_TCP_OFFSET, hi=MAX_TCP_OFFSET)
                       for i, v in enumerate(_vector(d['origin'], 3, f'{where}.origin')))
        axis = _vector(d['axis'], 3, f'{where}.axis')
        if abs(_norm(axis) - 1.0) > 1e-9:
            raise _fail(f'{where}.axis', f'birim vektör olmalı (uzunluk {_norm(axis):g}): '
                                         f'{list(axis)}')
        return LaserSensor(_name(d['name'], f'{where}.name'), origin, axis)
    raise _fail(f'{where}.type', f"'camera' ya da 'laser' olmalı, bulunan: {kind!r}")


def _shape_bounds(shapes: Sequence[ToolShape]) -> Tuple[Vec, Vec]:
    """Axis-aligned bounds (flange frame) of all shapes, rotations included."""
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for sh in shapes:
        rot = abs(matrix_from_wpr(*sh.wpr))
        reach = [sum(rot[i][j] * sh.half_extents[j] for j in range(3)) for i in range(3)]
        for i in range(3):
            lo[i], hi[i] = min(lo[i], sh.origin[i] - reach[i]), max(hi[i], sh.origin[i] + reach[i])
    return tuple(lo), tuple(hi)


def _parse_tool(name: str, data: Any, materials: Mapping, used: set) -> Tool:
    where = f'tools.{name}'
    d = _mapping(data, where, ('tcp', 'mass', 'com', 'inertia', 'box_margin', 'material',
                               'collision', 'sensors'), ('mass', 'collision'))
    link_name = f'tool_{name}'
    if link_name in used:
        raise _fail(where, f'ad çakışması: {link_name}')
    tcp_in = _mapping(d.get('tcp') or {}, f'{where}.tcp', ('offset', 'wpr'))
    offset, wpr = _parse_wpr_pose(tcp_in, f'{where}.tcp')
    mass = _number(d['mass'], f'{where}.mass', positive=True, hi=MAX_TOOL_MASS)
    raw_shapes = d['collision']
    if (not isinstance(raw_shapes, (list, tuple)) or not 1 <= len(raw_shapes) <= MAX_TOOL_SHAPES):
        raise _fail(f'{where}.collision', f'1-{MAX_TOOL_SHAPES} şekilli liste olmalı (çarpışma '
                                          f'geometrisi zorunlu): {raw_shapes!r}')
    shapes = tuple(_parse_shape(sh, f'{where}.collision[{i}]') for i, sh in enumerate(raw_shapes))
    lo, hi = _shape_bounds(shapes)
    # defaults: COM at the centre of the bounds, inertia of a solid box of those bounds
    com = (tuple(_number(v, f'{where}.com[{i}]', lo=-MAX_TCP_OFFSET, hi=MAX_TCP_OFFSET)
                 for i, v in enumerate(_vector(d['com'], 3, f'{where}.com'))) if 'com' in d
           else tuple((a + b) / 2 for a, b in zip(lo, hi)))
    if 'inertia' in d:
        inertia = tuple(_number(v, f'{where}.inertia[{i}]', positive=True)
                        for i, v in enumerate(_vector(d['inertia'], 3, f'{where}.inertia')))
    else:
        inertia = solid_inertia('box', [max(b - a, MIN_LENGTH) for a, b in zip(lo, hi)], mass)
    tcp_point = Tcp(offset, wpr).transform[:3, 3]
    # narrow default: the box shrinks by the farthest reach of the tool geometry around the TCP
    bound = max(math.dist(sh.origin, tcp_point) + _norm(sh.half_extents) for sh in shapes)
    margin = (_number(d['box_margin'], f'{where}.box_margin', lo=0.0, hi=MAX_TCP_OFFSET)
              if 'box_margin' in d else bound)
    material = d.get('material', next(iter(materials)))
    if material not in materials:
        raise _fail(f'{where}.material', f'tanımsız malzeme {material!r}')
    sensors = []
    raw_sensors = d.get('sensors') or []
    if not isinstance(raw_sensors, (list, tuple)):
        raise _fail(f'{where}.sensors', f'liste olmalı: {raw_sensors!r}')
    for i, sensor in enumerate(raw_sensors):
        sensors.append(_parse_sensor(sensor, f'{where}.sensors[{i}]'))
    if len({s.name for s in sensors}) != len(sensors):
        raise _fail(f'{where}.sensors', 'sensör adları benzersiz olmalı')
    return Tool(name, Tcp(offset, wpr), mass, com, inertia, shapes, margin, material,
                tuple(sensors))


def apply_tool(profile: Profile, name: Optional[str]) -> Profile:
    """Profile with tool ``name`` mounted (``None`` = the bare flange, the narrowest choice).

    One call updates everything that depends on the tool together: the TCP (Chain / jog FK and
    Jacobian, ``Tool`` jog frame), the safe box (shrunk on every wall by the tool's ``box_margin``)
    and the collision geometry (the generated URDF/SRDF read ``profile.tool``). Pure: returns a new
    ``Profile``; never moves anything (changing the tool on a running robot is Q3)."""
    base_safety = profile.base_safety or profile.safety
    if name is None:
        return replace(profile, active_tool=None, tcp=profile.base_tcp, safety=base_safety)
    if name not in profile.tools:
        raise ProfileError(f'tools: {name!r} adlı takım yok (tanımlılar: '
                           f'{sorted(profile.tools) or "hiçbiri"})')
    tool = profile.tools[name]
    zone = base_safety.zone
    m = tool.box_margin
    lower = tuple(v + m for v in zone.lower)
    upper = tuple(v - m for v in zone.upper)
    if not all(lo < hi for lo, hi in zip(lower, upper)):
        raise _fail(f'tools.{name}.box_margin',
                    f'güvenli kutu {m:.3f} m payla boşalıyor; kutuyu büyüt ya da payı bilerek '
                    'küçült (varsayılan: takım geometrisinin TCP çevresindeki en uzak noktası)')
    safety = SafetyConfig(SafeZone(lower, upper, zone.frame), base_safety.max_velocity_scale)
    return replace(profile, active_tool=name, tcp=tool.tcp, safety=safety)


def parse_profile(data: Any, tool: Optional[str] = None) -> Profile:
    """``tool`` overrides ``active_tool`` of the YAML (``None`` = use the YAML)."""
    top = ('profile_version', 'robot', 'materials', 'mount', 'links', 'joints', 'moveit',
           'safety', 'tcp', 'tools', 'active_tool')
    d = _mapping(data, 'profil', top, tuple(k for k in top if k not in (
        'safety', 'tcp', 'tools', 'active_tool')))
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
    tcp_in = _mapping(d.get('tcp') or {}, 'tcp', ('offset', 'wpr'))
    offset = _vector(tcp_in.get('offset', (0.0, 0.0, 0.0)), 3, 'tcp.offset')
    wpr = _vector(tcp_in.get('wpr', (0.0, 0.0, 0.0)), 3, 'tcp.wpr')
    tcp = Tcp(
        offset=tuple(_number(v, f'tcp.offset[{i}]', lo=-MAX_TCP_OFFSET, hi=MAX_TCP_OFFSET)
                     for i, v in enumerate(offset)),
        wpr=tuple(_number(v, f'tcp.wpr[{i}]', lo=-MAX_ANGLE, hi=MAX_ANGLE)
                  for i, v in enumerate(wpr)))
    # a tool sticking out farther than the tool link extends the reach that vets an explicit box
    reach = reach + max(0.0, _norm(tcp.offset) - links[-1].length)
    if not derived:
        lo, hi = safety.zone.lower, safety.zone.upper
        far = max(math.dist(shoulder, (x, y, z)) for x in (lo[0], hi[0])
                  for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))
        if far > reach:
            raise _fail('safety.box', f'kutunun en uzak köşesi omuzdan {far:.3f} m, kolun erişimi '
                                      f'{reach:.3f} m; kutuyu küçült')

    tools_in = d.get('tools') or {}
    if not isinstance(tools_in, Mapping):
        raise _fail('tools', f'eşleme (ad: takım) olmalı, bulunan: {tools_in!r}')
    tools = {_name(n, f'tools.{n}'): _parse_tool(n, v, materials, used)
             for n, v in tools_in.items()}
    wanted = tool if tool is not None else d.get('active_tool')
    if wanted is not None and wanted not in tools:
        raise _fail('active_tool', f'{wanted!r} adlı takım yok (tanımlılar: '
                                   f'{sorted(tools) or "hiçbiri"})')
    if wanted is not None and ('offset' in tcp_in or 'wpr' in tcp_in) and (
            tcp.offset != (0.0, 0.0, 0.0) or tcp.wpr != (0.0, 0.0, 0.0)):
        raise _fail('tcp', 'active_tool varken üst düzey `tcp` yazılamaz: TCP takımdan gelir '
                           '(takımın `tcp`si)')

    base = Profile(
        robot=robot, materials=materials, mount=mount, links=links, joints=tuple(joints),
        planning_group=_name(mv.get('planning_group', 'robot_arm'), 'moveit.planning_group'),
        default_velocity_scaling=_number(mv.get('default_velocity_scaling', DEFAULT_SCALE),
                                         'moveit.default_velocity_scaling', positive=True, hi=1.0),
        default_acceleration_scaling=_number(mv.get('default_acceleration_scaling', DEFAULT_SCALE),
                                             'moveit.default_acceleration_scaling', positive=True,
                                             hi=1.0),
        initial_positions=initial, named_poses=poses, allow_extra=tuple(extra), safety=safety,
        tcp=tcp, reach_center=shoulder, reach_radius=reach, box_derived=derived,
        tools=tools, active_tool=None, base_tcp=tcp, base_safety=safety)
    return apply_tool(base, wanted)


def load_profile(path: Union[str, Path], tool: Optional[str] = None) -> Profile:
    try:
        with open(path, encoding='utf-8') as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ProfileError(f'{path}: okunamadı ({exc.strerror or exc})') from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f'{path}: geçerli YAML değil ({exc})') from exc
    try:
        return parse_profile(data, tool)
    except ProfileError as exc:
        raise ProfileError(f'{path}: {exc}') from exc
