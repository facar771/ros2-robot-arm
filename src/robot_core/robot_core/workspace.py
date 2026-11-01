"""Workspace geometry parsed from the Gazebo world file (single source of truth).

The world SDF defines the camera pose/intrinsics and the static objects. Everything else (static TF
in the launch file, expected depth values in the sensor check) is derived from it here, so the
simulation, the TF tree and the tests cannot drift apart. Pure Python, no ROS, no numpy.

Conventions
- World frame: robot base at the origin, z up.
- Camera (sensor) frame: x forward, y left, z up (ROS body convention). Pixels: u right, v down.
- A depth value is the distance along the camera x axis (z-depth), not the Euclidean range.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

Vec = Tuple[float, float, float]
GROUND = 'ground'


class WorldError(ValueError):
    """The world file is missing something the tools rely on."""


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    @property
    def position(self) -> Vec:
        return (self.x, self.y, self.z)

    def rotation(self) -> Tuple[Vec, Vec, Vec]:
        """Rotation matrix R = Rz(yaw) Ry(pitch) Rx(roll) (SDF / ROS fixed-axis RPY)."""
        cr, sr = math.cos(self.roll), math.sin(self.roll)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        return ((cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
                (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
                (-sp, cp * sr, cp * cr))

    def to_world(self, v: Vec) -> Vec:
        r = self.rotation()
        return tuple(sum(r[i][j] * v[j] for j in range(3)) + self.position[i] for i in range(3))

    def direction_to_world(self, v: Vec) -> Vec:
        r = self.rotation()
        return tuple(sum(r[i][j] * v[j] for j in range(3)) for i in range(3))


@dataclass(frozen=True)
class CameraSpec:
    pose: Pose
    horizontal_fov: float
    width: int
    height: int
    near: float
    far: float
    frame_id: str

    @property
    def fx(self) -> float:
        return (self.width / 2.0) / math.tan(self.horizontal_fov / 2.0)

    @property
    def fy(self) -> float:
        return self.fx  # square pixels

    def ray(self, u: float, v: float) -> Vec:
        """Direction in the camera frame for pixel centre (u, v), scaled so that x == 1."""
        return (1.0,
                -(u + 0.5 - self.width / 2.0) / self.fx,
                -(v + 0.5 - self.height / 2.0) / self.fy)


@dataclass(frozen=True)
class Shape:
    name: str
    kind: str                      # 'sphere' | 'box' | 'cylinder'
    center: Vec
    dims: Tuple[float, ...]        # sphere: (r,)  box: (sx, sy, sz)  cylinder: (r, length)
    color: Optional[Tuple[float, float, float]] = None   # visual diffuse RGB (0-1), if given

    @property
    def height(self) -> float:
        """Vertical extent (the shapes are axis-aligned and unrotated)."""
        if self.kind == 'sphere':
            return 2 * self.dims[0]
        return self.dims[2] if self.kind == 'box' else self.dims[1]


@dataclass(frozen=True)
class Workspace:
    camera: CameraSpec
    shapes: Tuple[Shape, ...]
    ground_z: float = 0.0


# ---- parsing -----------------------------------------------------------------------------------
def _floats(text: Optional[str], default: str) -> List[float]:
    return [float(v) for v in (text or default).split()]


def _pose(elem: Optional[ET.Element]) -> Pose:
    return Pose(*_floats(elem.text if elem is not None else None, '0 0 0 0 0 0'))


def load_workspace(sdf_path: Union[str, Path]) -> Workspace:
    world = ET.parse(str(sdf_path)).getroot().find('world')
    if world is None:
        raise WorldError('SDF içinde <world> yok')
    camera: Optional[CameraSpec] = None
    shapes: List[Shape] = []
    for model in world.findall('model'):
        name = model.get('name')
        if name == 'ground_plane':
            continue
        model_pose = _pose(model.find('pose'))
        link = model.find('link')
        if link is None:
            raise WorldError(f'{name}: <link> yok')
        if link.find('pose') is not None:
            raise WorldError(f'{name}: link pozu desteklenmiyor (model pozunu kullan)')
        sensor = link.find("sensor[@type='rgbd_camera']")
        if sensor is not None:
            cam = sensor.find('camera')
            clip = cam.find('clip')
            frame = sensor.find('gz_frame_id')
            camera = CameraSpec(
                pose=model_pose,
                horizontal_fov=float(cam.find('horizontal_fov').text),
                width=int(cam.find('image/width').text), height=int(cam.find('image/height').text),
                near=float(clip.find('near').text), far=float(clip.find('far').text),
                frame_id=frame.text if frame is not None else sensor.get('name'))
            continue
        geom = link.find('collision/geometry')
        if geom is None:
            raise WorldError(f'{name}: <collision><geometry> yok')
        if model_pose.roll or model_pose.pitch or model_pose.yaw:
            raise WorldError(f'{name}: dönük nesneler desteklenmiyor')
        shapes.append(_shape(name, model_pose.position, geom, _diffuse(link)))
    if camera is None:
        raise WorldError('world içinde rgbd_camera yok')
    return Workspace(camera=camera, shapes=tuple(shapes))


def _diffuse(link: ET.Element) -> Optional[Tuple[float, float, float]]:
    elem = link.find('visual/material/diffuse')
    if elem is None:
        return None
    r, g, b = _floats(elem.text, '')[:3]
    return (r, g, b)


def _shape(name: str, center: Vec, geom: ET.Element,
           color: Optional[Tuple[float, float, float]] = None) -> Shape:
    if geom.find('sphere') is not None:
        return Shape(name, 'sphere', center, (float(geom.find('sphere/radius').text),), color)
    if geom.find('box') is not None:
        return Shape(name, 'box', center, tuple(_floats(geom.find('box/size').text, '')), color)
    if geom.find('cylinder') is not None:
        c = geom.find('cylinder')
        return Shape(name, 'cylinder', center,
                     (float(c.find('radius').text), float(c.find('length').text)), color)
    raise WorldError(f'{name}: desteklenmeyen geometri')


# ---- ray casting -------------------------------------------------------------------------------
def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _hit_sphere(o: Vec, d: Vec, center: Vec, r: float) -> Optional[float]:
    oc = _sub(o, center)
    a, b, c = _dot(d, d), 2 * _dot(oc, d), _dot(oc, oc) - r * r
    disc = b * b - 4 * a * c
    if disc < 0:
        return None
    t = (-b - math.sqrt(disc)) / (2 * a)
    return t if t > 0 else None


def _hit_box(o: Vec, d: Vec, center: Vec, size: Sequence[float]) -> Optional[float]:
    t_near, t_far = -math.inf, math.inf
    for i in range(3):
        lo, hi = center[i] - size[i] / 2, center[i] + size[i] / 2
        if abs(d[i]) < 1e-12:
            if not lo <= o[i] <= hi:
                return None
            continue
        t1, t2 = (lo - o[i]) / d[i], (hi - o[i]) / d[i]
        t_near, t_far = max(t_near, min(t1, t2)), min(t_far, max(t1, t2))
    return t_near if t_near <= t_far and t_near > 0 else None


def _hit_cylinder(o: Vec, d: Vec, center: Vec, r: float, length: float) -> Optional[float]:
    best: Optional[float] = None
    z_lo, z_hi = center[2] - length / 2, center[2] + length / 2
    a = d[0] ** 2 + d[1] ** 2
    if a > 1e-12:  # side surface
        ox, oy = o[0] - center[0], o[1] - center[1]
        b, c = 2 * (ox * d[0] + oy * d[1]), ox * ox + oy * oy - r * r
        disc = b * b - 4 * a * c
        if disc >= 0:
            for t in sorted(((-b - math.sqrt(disc)) / (2 * a), (-b + math.sqrt(disc)) / (2 * a))):
                if t > 0 and z_lo <= o[2] + t * d[2] <= z_hi:
                    best = t
                    break
    if abs(d[2]) > 1e-12:  # caps
        for z in (z_lo, z_hi):
            t = (z - o[2]) / d[2]
            dx, dy = o[0] + t * d[0] - center[0], o[1] + t * d[1] - center[1]
            if t > 0 and dx * dx + dy * dy <= r * r:
                best = t if best is None else min(best, t)
    return best


def cast(ws: Workspace, origin: Vec, direction: Vec) -> Optional[Tuple[float, str]]:
    """Nearest hit of a world-frame ray: (t, shape name or 'ground'); t is in |direction| units."""
    best: Optional[Tuple[float, str]] = None
    if direction[2] < -1e-12:
        t = (ws.ground_z - origin[2]) / direction[2]
        if t > 0:
            best = (t, GROUND)
    for s in ws.shapes:
        if s.kind == 'sphere':
            t = _hit_sphere(origin, direction, s.center, s.dims[0])
        elif s.kind == 'box':
            t = _hit_box(origin, direction, s.center, s.dims)
        else:
            t = _hit_cylinder(origin, direction, s.center, s.dims[0], s.dims[1])
        if t is not None and (best is None or t < best[0]):
            best = (t, s.name)
    return best


def expected_depth(ws: Workspace, u: float, v: float) -> Optional[Tuple[float, str]]:
    """Ideal z-depth the camera should report at pixel (u, v), and which object it sees."""
    cam = ws.camera
    direction = cam.pose.direction_to_world(cam.ray(u, v))  # camera x component == 1
    hit = cast(ws, cam.pose.position, direction)
    if hit is None or hit[0] > cam.far or hit[0] < cam.near:
        return None
    return hit  # direction has unit x component in camera frame => t is the z-depth


def project(ws: Workspace, point: Vec) -> Optional[Tuple[float, float, float]]:
    """World point -> (u, v, z-depth); None if behind the camera."""
    cam = ws.camera
    rel = _sub(point, cam.pose.position)
    r = cam.pose.rotation()
    local = tuple(sum(r[j][i] * rel[j] for j in range(3)) for i in range(3))  # R^T * rel
    if local[0] <= 1e-9:
        return None
    u = cam.width / 2.0 - cam.fx * local[1] / local[0] - 0.5
    v = cam.height / 2.0 - cam.fy * local[2] / local[0] - 0.5
    return (u, v, local[0])


def stable_probes(ws: Workspace, margin: int = 3, tolerance: float = 0.01,
                  step: int = 8) -> Dict[str, Tuple[int, int, float]]:
    """Pixels away from object silhouettes, one per visible object (and the ground).

    A pixel is 'stable' when the expected depth *and* the hit object are identical at +/- margin
    pixels, so small rendering offsets cannot change the expectation. Returns
    {object name: (u, v, expected depth)}; for each object the most central stable pixel.
    """
    cam = ws.camera
    best: Dict[str, Tuple[float, Tuple[int, int, float]]] = {}
    for v in range(margin, cam.height - margin, step):
        for u in range(margin, cam.width - margin, step):
            centre = expected_depth(ws, u, v)
            if centre is None:
                continue
            ok = True
            for du, dv in ((margin, 0), (-margin, 0), (0, margin), (0, -margin)):
                other = expected_depth(ws, u + du, v + dv)
                if (other is None or other[1] != centre[1]
                        or abs(other[0] - centre[0]) > tolerance * 5):
                    ok = False
                    break
            if not ok:
                continue
            dist = math.hypot(u - cam.width / 2, v - cam.height / 2)
            if centre[1] not in best or dist < best[centre[1]][0]:
                best[centre[1]] = (dist, (u, v, centre[0]))
    return {name: probe for name, (_, probe) in best.items()}
