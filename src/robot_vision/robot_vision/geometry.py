"""Depth geometry: from the pixels of one segment to 3-D measurements useful for grasping.

Pipeline: segment pixels -> valid depth -> optical frame -> world frame -> robust filtering ->
footprint (convex hull, minimum-area rectangle), flat-top level, sphere fit -> shape guess ->
best-estimate centre / size, nearest / farthest visible point, edge points and grasp candidates.

Assumptions (the same ones as ``robot_core.perception``, stated so they can be checked)
- The object rests on the ground plane ``ground_z`` and its axis is vertical (box, cylinder,
  sphere). The top is visible from the camera; the far side is not.
- ``nearest`` is a measurement of the visible surface. ``farthest`` is only the farthest *visible*
  point: the hidden back side is never seen, so it is NOT the object's far extent.
- The shape guess is a heuristic (sphere-fit residual, then footprint fill ratio). When it is
  wrong, ``shape == 'unknown'`` is preferred over a confident mistake, and the oriented box (which
  only needs the footprint) is always reported.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
from robot_core.perception import backproject

from robot_vision.frame import Frame, Segment

Vec = Tuple[float, float, float]


@dataclass(frozen=True)
class GeometryConfig:
    ground_z: float = 0.0
    depth_range: Tuple[float, float] = (0.1, 5.0)
    max_depth_spread: float = 0.15       # m around the blob's median depth (drops edge flyers)
    min_points: int = 20
    flat_band: float = 0.006             # m below the top that still counts as 'top face'
    sphere_rms: float = 0.006            # m, max sphere-fit residual for 'sphere'
    box_fill: float = 0.90               # hull / min-rect area of the top face: >= this -> box
    sphere_max_coverage: float = 0.5     # flat-top share of the footprint; a sphere has little
    max_aperture: float = 0.08           # m, widest object the gripper can close on
    min_side_height: float = 0.02        # m, lower objects get no side grasps


@dataclass(frozen=True)
class Grasp:
    kind: str                            # 'top' | 'side'
    position: Vec                        # gripper centre on the object, world
    approach: Vec                        # unit vector the gripper moves along (towards the object)
    yaw: float                           # rotation of the closing axis about world z (rad)
    width: float                         # object width between the fingers (m)
    fits: bool                           # width <= max_aperture


@dataclass(frozen=True)
class ObjectMeasure:
    label: str
    pixels: int
    points: int
    shape: str                           # 'box' | 'cylinder' | 'sphere' | 'unknown'
    center: Vec                          # best-estimate object centre (world)
    size: Vec                            # best-estimate axis-aligned extent (x, y, z)
    height: float                        # top - ground_z
    top_z: float
    top_center: Vec                      # centre of the top face (sphere: its pole)
    radius: Optional[float]              # cylinder / sphere
    obb_center: Vec                      # oriented box (footprint rectangle x height)
    obb_size: Vec                        # (a, b, height); a >= b
    obb_yaw: float                       # direction of side ``a``, rad, in (-pi/2, pi/2]
    aabb_min: Vec                        # axis-aligned box of the *visible* points
    aabb_max: Vec
    nearest: Vec                         # visible point nearest to the camera
    nearest_distance: float
    farthest: Vec                        # farthest *visible* point (not the hidden far side)
    farthest_distance: float
    edge_points: Dict[str, Vec] = field(repr=False, default_factory=dict)
    grasps: Tuple[Grasp, ...] = ()
    rms_sphere: float = float('nan')

    def distances_from(self, origin: Vec) -> Dict[str, float]:
        """Distance from ``origin`` (e.g. the gripper or camera) to every named edge point."""
        o = np.asarray(origin, dtype=np.float64)
        return {k: float(np.linalg.norm(np.asarray(v) - o)) for k, v in self.edge_points.items()}


# ---- point extraction --------------------------------------------------------------------------
def segment_points(frame: Frame, seg: Segment, cfg: Optional[GeometryConfig] = None) -> np.ndarray:
    """(N, 3) world-frame points of the segment's depth-valid pixels, edge flyers removed."""
    cfg = cfg or GeometryConfig()
    z = frame.depth[seg.rows, seg.cols].astype(np.float64)
    ok = np.isfinite(z) & (z >= cfg.depth_range[0]) & (z <= cfg.depth_range[1])
    if not ok.any():
        return np.empty((0, 3))
    rows, cols, z = seg.rows[ok], seg.cols[ok], z[ok]
    keep = np.abs(z - np.median(z)) <= cfg.max_depth_spread
    pts = backproject(frame.depth, frame.intrinsics, rows[keep], cols[keep])
    return frame.world_from_optical.apply(pts)


# ---- 2-D helpers -------------------------------------------------------------------------------
def _cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def convex_hull(xy: np.ndarray) -> np.ndarray:
    """Andrew's monotone chain; returns hull vertices counter-clockwise."""
    pts = np.unique(np.round(xy, 6), axis=0)
    if len(pts) < 3:
        return pts
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def half(points):
        h = []
        for p in points:
            while len(h) >= 2 and _cross(h[-1] - h[-2], p - h[-2]) <= 0:
                h.pop()
            h.append(p)
        return h
    lower, upper = half(pts), half(pts[::-1])
    return np.array(lower[:-1] + upper[:-1])


def polygon_area(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def min_area_rect(xy: np.ndarray) -> Tuple[np.ndarray, float, float, float]:
    """Minimum-area bounding rectangle of 2-D points: (centre, a, b, yaw) with a >= b, yaw = angle
    of side ``a`` in (-pi/2, pi/2]. Brute-force angle search over the hull (0.5 deg, then 0.02)."""
    hull = convex_hull(xy)

    def fit(theta):
        c, s = math.cos(theta), math.sin(theta)
        u = hull[:, 0] * c + hull[:, 1] * s
        v = -hull[:, 0] * s + hull[:, 1] * c
        return (u.max() - u.min()) * (v.max() - v.min()), theta, u, v

    best = min((fit(math.radians(a)) for a in np.arange(0.0, 90.0, 0.5)), key=lambda r: r[0])
    refine = min((fit(best[1] + math.radians(d)) for d in np.arange(-0.5, 0.5001, 0.02)),
                 key=lambda r: r[0])
    _, theta, u, v = refine
    cu, cv = 0.5 * (u.max() + u.min()), 0.5 * (v.max() + v.min())
    c, s = math.cos(theta), math.sin(theta)
    centre = np.array([cu * c - cv * s, cu * s + cv * c])
    a, b = u.max() - u.min(), v.max() - v.min()
    if b > a:
        a, b, theta = b, a, theta + math.pi / 2
    theta = (theta + math.pi / 2) % math.pi - math.pi / 2     # -> [-pi/2, pi/2)
    return centre, float(a), float(b), float(theta)


def fit_circle(xy: np.ndarray) -> Tuple[np.ndarray, float]:
    """Algebraic (Kasa) circle fit: (centre, radius)."""
    a = np.column_stack([2 * xy[:, 0], 2 * xy[:, 1], np.ones(len(xy))])
    sol, *_ = np.linalg.lstsq(a, (xy ** 2).sum(axis=1), rcond=None)
    return sol[:2], float(math.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0.0)))


def fit_sphere(pts: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """Linear least-squares sphere fit: (centre, radius, rms residual in m)."""
    a = np.column_stack([2 * pts, np.ones(len(pts))])
    sol, *_ = np.linalg.lstsq(a, (pts ** 2).sum(axis=1), rcond=None)
    c = sol[:3]
    r = float(math.sqrt(max(sol[3] + c @ c, 0.0)))
    rms = float(np.sqrt(np.mean((np.linalg.norm(pts - c, axis=1) - r) ** 2)))
    return c, r, rms


# ---- measurement -------------------------------------------------------------------------------
def _top_level(z: np.ndarray, band: float) -> float:
    """Robust height of the highest *surface* (not the noisiest single point)."""
    p = float(np.percentile(z, 98))
    return float(np.median(z[z >= p - band]))


def measure(frame: Frame, seg: Segment, cfg: Optional[GeometryConfig] = None,
            origin: Optional[Vec] = None) -> Optional[ObjectMeasure]:
    """3-D measurements of one segment, or None if too few valid depth pixels.
    ``origin``: reference point for nearest/farthest (default: the camera)."""
    cfg = cfg or GeometryConfig()
    pts = segment_points(frame, seg, cfg)
    if len(pts) < cfg.min_points:
        return None
    o = np.asarray(origin if origin is not None else frame.camera_position, dtype=np.float64)
    ground = cfg.ground_z
    top = _top_level(pts[:, 2], cfg.flat_band)
    height = max(top - ground, 1e-3)

    # nearest / farthest visible point
    dist = np.linalg.norm(pts - o, axis=1)
    i_near, i_far = int(dist.argmin()), int(dist.argmax())

    # footprint: the flat top face is the true outline of a box / cylinder (side faces project onto
    # its edges); without a flat top (sphere) the footprint is the visible points' projection
    flat = pts[pts[:, 2] >= top - cfg.flat_band]
    has_flat = len(flat) >= 12
    flat_hull = convex_hull(flat[:, :2]) if has_flat else np.empty((0, 2))
    f_c, f_a, f_b, f_yaw = min_area_rect(flat[:, :2]) if has_flat else (None, 0.0, 0.0, 0.0)
    fill = polygon_area(flat_hull) / max(f_a * f_b, 1e-9) if has_flat else 0.0
    all_c, all_a, all_b, all_yaw = min_area_rect(pts[:, :2])
    # share of the visible footprint that is flat top: ~0.8-1.0 for box / cylinder, ~0.25 for a
    # sphere (its 'top' is a small cap)
    top_coverage = polygon_area(flat_hull) / max(all_a * all_b, 1e-9) if has_flat else 0.0

    c_sph, r_sph, rms_sph = fit_sphere(pts)
    shape, radius = 'unknown', None
    if rms_sph < cfg.sphere_rms and 0.01 < r_sph < 0.3 and top_coverage < cfg.sphere_max_coverage:
        shape, radius = 'sphere', r_sph
    elif has_flat:
        shape = 'box' if fill >= cfg.box_fill else 'cylinder'

    if has_flat and shape != 'sphere':
        rect_c, a, b, yaw = f_c, f_a, f_b, f_yaw
    else:
        rect_c, a, b, yaw = all_c, all_a, all_b, all_yaw
    top_xy = rect_c
    if shape == 'cylinder':
        c_xy, radius = fit_circle(flat_hull)       # boundary points only: a filled disc biases r
        top_xy = c_xy
    if shape == 'sphere':
        center = (float(c_sph[0]), float(c_sph[1]), float(c_sph[2]))
        top_center = (center[0], center[1], center[2] + r_sph)
        size = (2 * r_sph, 2 * r_sph, 2 * r_sph)
        height = 2 * r_sph if ground <= center[2] - r_sph + 0.02 else height
    elif shape == 'cylinder':
        center = (float(top_xy[0]), float(top_xy[1]), ground + height / 2)
        top_center = (center[0], center[1], top)
        size = (2 * radius, 2 * radius, height)
    else:
        center = (float(rect_c[0]), float(rect_c[1]), ground + height / 2)
        top_center = (center[0], center[1], top)
        cy, sy = abs(math.cos(yaw)), abs(math.sin(yaw))
        size = (a * cy + b * sy, a * sy + b * cy, height)

    obb_center = (float(rect_c[0]), float(rect_c[1]), ground + height / 2)
    ex = np.array([math.cos(yaw), math.sin(yaw)])
    ey = np.array([-math.sin(yaw), math.cos(yaw)])
    edge = _edge_points(shape, center, radius, rect_c, a, b, ex, ey, ground, top, o)
    grasps = _grasps(shape, center, top_center, radius, rect_c, a, b, yaw, ex, ey, ground, top, cfg)
    mn, mx = pts.min(axis=0), pts.max(axis=0)
    edge['nearest'] = tuple(float(v) for v in pts[i_near])
    edge['farthest_visible'] = tuple(float(v) for v in pts[i_far])
    edge['top_center'] = top_center
    return ObjectMeasure(
        label=seg.label, pixels=seg.pixels, points=len(pts), shape=shape, center=center, size=size,
        height=float(height), top_z=float(top), top_center=top_center,
        radius=None if radius is None else float(radius), obb_center=obb_center,
        obb_size=(float(a), float(b), float(height)), obb_yaw=yaw,
        aabb_min=tuple(float(v) for v in mn), aabb_max=tuple(float(v) for v in mx),
        nearest=edge['nearest'], nearest_distance=float(dist[i_near]),
        farthest=edge['farthest_visible'], farthest_distance=float(dist[i_far]),
        edge_points=edge, grasps=grasps, rms_sphere=rms_sph)


def _edge_points(shape, center, radius, rect_c, a, b, ex, ey, ground, top,
                 origin) -> Dict[str, Vec]:
    pts: Dict[str, Vec] = {}
    if shape in ('box', 'unknown'):
        for i, (sa, sb) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
            xy = rect_c + sa * a / 2 * ex + sb * b / 2 * ey
            pts[f'bottom_corner_{i}'] = (float(xy[0]), float(xy[1]), ground)
            pts[f'top_corner_{i}'] = (float(xy[0]), float(xy[1]), top)
    elif shape == 'cylinder':
        toward = np.asarray(origin[:2]) - np.asarray(center[:2])
        n = np.linalg.norm(toward)
        u = toward / n if n > 1e-9 else np.array([1.0, 0.0])
        v = np.array([-u[1], u[0]])
        for name, d in (('near', u), ('far', -u), ('left', v), ('right', -v)):
            xy = np.asarray(center[:2]) + radius * d
            pts[f'top_rim_{name}'] = (float(xy[0]), float(xy[1]), top)
            pts[f'bottom_rim_{name}'] = (float(xy[0]), float(xy[1]), ground)
    else:  # sphere
        for name, d in (('+x', (1, 0, 0)), ('-x', (-1, 0, 0)), ('+y', (0, 1, 0)),
                        ('-y', (0, -1, 0)), ('top', (0, 0, 1)), ('bottom', (0, 0, -1))):
            pts[f'extreme_{name}'] = tuple(float(center[i] + radius * d[i]) for i in range(3))
    return pts


def _grasps(shape, center, top_center, radius, rect_c, a, b, yaw, ex, ey, ground, top, cfg):
    height = top - ground
    out = []

    def make(kind, pos, approach, yw, width):
        return Grasp(kind, tuple(float(v) for v in pos), tuple(float(v) for v in approach),
                     float(yw), float(width), bool(width <= cfg.max_aperture))

    if shape == 'sphere':
        d = 2 * radius
        out.append(make('top', top_center, (0, 0, -1), 0.0, d))
        for k in range(4):
            ang = k * math.pi / 2
            out.append(make('side', center, (-math.cos(ang), -math.sin(ang), 0),
                            ang + math.pi / 2, d))
        return tuple(out)
    if shape == 'cylinder':
        d = 2 * radius
        out.append(make('top', top_center, (0, 0, -1), 0.0, d))
        if height >= cfg.min_side_height:
            for k in range(4):
                ang = k * math.pi / 2
                out.append(make('side', center, (-math.cos(ang), -math.sin(ang), 0),
                                ang + math.pi / 2, d))
        return tuple(out)
    # box / unknown: close across the short side from the top; side grasps on each face
    short_yaw = yaw + math.pi / 2          # closing axis along side b
    out.append(make('top', (rect_c[0], rect_c[1], top), (0, 0, -1), short_yaw, b))
    if height >= cfg.min_side_height:
        mid = ground + height / 2
        for axis, half, width in ((ex, a / 2, a), (ey, b / 2, b)):
            for sign in (1, -1):
                xy = rect_c + sign * half * axis
                approach = (-sign * axis[0], -sign * axis[1], 0.0)
                out.append(make('side', (xy[0], xy[1], mid), approach,
                                math.atan2(axis[1], axis[0]), width))
    return tuple(out)
