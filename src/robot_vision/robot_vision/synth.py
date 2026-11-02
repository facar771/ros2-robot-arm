"""Vectorised synthetic RGB-D renderer: exact ray casting of the world model.

Used by the tests and by the validation report, so image-processing code can be checked against
*known* geometry without Gazebo. It reproduces the project conventions of
:mod:`robot_core.workspace` (pixel centres at +0.5, depth = z-depth along the camera x axis) and
accepts any camera pose, so a moving camera platform can be rendered too. Shading is a simple
ambient + Lambert term, enough to exercise colour thresholds on dark sides; it is NOT Gazebo's
renderer (see the PR's "Denenmeyenler").
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

import numpy as np
from robot_core.perception import Intrinsics, optical_frame_in_world
from robot_core.workspace import CameraSpec, Shape

from robot_vision.frame import Frame

GROUND_RGB = (140, 140, 140)
SKY_RGB = (178, 204, 229)          # the world file's background colour (pale, never detected)
_LIGHT = [-0.3, 0.2, -1.0]                                   # the world file's <direction>
LIGHT_DIR = np.array(_LIGHT) / np.linalg.norm(_LIGHT)
AMBIENT, DIFFUSE = 0.45, 0.55
_INF = np.inf


def _rays(cam: CameraSpec) -> np.ndarray:
    """(H*W, 3) world-frame ray directions, scaled so the camera-x component is 1."""
    v, u = np.mgrid[0:cam.height, 0:cam.width]
    local = np.stack([np.ones(u.size),
                      -(u.ravel() + 0.5 - cam.width / 2.0) / cam.fx,
                      -(v.ravel() + 0.5 - cam.height / 2.0) / cam.fy], axis=1)
    return local @ np.array(cam.pose.rotation()).T


def _sphere(o, d, c, r):
    oc = o - c
    a = np.einsum('ij,ij->i', d, d)
    b = 2.0 * d @ oc
    disc = b * b - 4.0 * a * (oc @ oc - r * r)
    ok = disc >= 0
    t = np.where(ok, (-b - np.sqrt(np.where(ok, disc, 0.0))) / (2.0 * a), _INF)
    t = np.where(t > 0, t, _INF)
    n = (o + t[:, None] * d - c) / r
    return t, np.where(np.isfinite(t)[:, None], n, 0.0)


def _box(o, d, c, size):
    lo, hi = c - np.asarray(size) / 2.0, c + np.asarray(size) / 2.0
    with np.errstate(divide='ignore', invalid='ignore'):
        t1, t2 = (lo - o) / d, (hi - o) / d
    parallel = np.abs(d) < 1e-12
    inside = (o >= lo) & (o <= hi)
    tn_axis = np.where(parallel, np.where(inside, -_INF, _INF), np.minimum(t1, t2))
    tf_axis = np.where(parallel, np.where(inside, _INF, -_INF), np.maximum(t1, t2))
    t_near, t_far = tn_axis.max(axis=1), tf_axis.min(axis=1)
    hit = (t_near <= t_far) & (t_near > 0)
    t = np.where(hit, t_near, _INF)
    axis = tn_axis.argmax(axis=1)
    n = np.zeros_like(d)
    rows = np.arange(len(d))
    n[rows, axis] = -np.sign(d[rows, axis])
    return t, np.where(hit[:, None], n, 0.0)


def _cylinder(o, d, c, r, length):
    z_lo, z_hi = c[2] - length / 2.0, c[2] + length / 2.0
    best_t = np.full(len(d), _INF)
    best_n = np.zeros_like(d)
    a = d[:, 0] ** 2 + d[:, 1] ** 2
    ox, oy = o[0] - c[0], o[1] - c[1]
    with np.errstate(divide='ignore', invalid='ignore'):
        b = 2.0 * (ox * d[:, 0] + oy * d[:, 1])
        disc = b * b - 4.0 * a * (ox * ox + oy * oy - r * r)
        sq = np.sqrt(np.where(disc >= 0, disc, 0.0))
        for t in ((-b - sq) / (2.0 * a), (-b + sq) / (2.0 * a)):
            z = o[2] + t * d[:, 2]
            ok = (a > 1e-12) & (disc >= 0) & (t > 0) & (z >= z_lo) & (z <= z_hi) & (t < best_t)
            n = np.stack([o[0] + t * d[:, 0] - c[0], o[1] + t * d[:, 1] - c[1],
                          np.zeros(len(d))], axis=1) / r
            best_t = np.where(ok, t, best_t)
            best_n = np.where(ok[:, None], n, best_n)
        for z_cap, sign in ((z_lo, -1.0), (z_hi, 1.0)):
            t = (z_cap - o[2]) / d[:, 2]
            dx, dy = o[0] + t * d[:, 0] - c[0], o[1] + t * d[:, 1] - c[1]
            ok = (np.abs(d[:, 2]) > 1e-12) & (t > 0) & (dx * dx + dy * dy <= r * r) & (t < best_t)
            best_t = np.where(ok, t, best_t)
            best_n[ok] = (0.0, 0.0, sign)
    return best_t, best_n


def _intersect(origin, d, shape: Shape, yaw: float = 0.0):
    c = np.asarray(shape.center, dtype=np.float64)
    if yaw:
        # rotate the ray into the shape's frame (yaw about its own vertical axis), intersect, rotate
        # the normal back
        cy, sy = np.cos(-yaw), np.sin(-yaw)
        rot = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
        t, n = _intersect(c + rot @ (origin - c), d @ rot.T, shape)
        return t, n @ rot
    if shape.kind == 'sphere':
        return _sphere(origin, d, c, shape.dims[0])
    if shape.kind == 'box':
        return _box(origin, d, c, shape.dims)
    return _cylinder(origin, d, c, shape.dims[0], shape.dims[1])


def render(cam: CameraSpec, shapes: Sequence[Shape], *,
           colors: Optional[Dict[str, Tuple[float, ...]]] = None,
           ground_z: float = 0.0, depth_noise: float = 0.0, seed: int = 0, stamp: float = 0.0,
           hidden: Sequence[str] = (), yaws: Optional[Dict[str, float]] = None) -> Frame:
    """Render ``shapes`` seen by ``cam``.

    ``colors``: shape name -> RGB 0-255 (default: the shape's world-file diffuse colour).
    ``hidden``: shape names to leave out (simulates an object that leaves the scene).
    ``yaws``: shape name -> rotation about the shape's vertical axis (rad); boxes and cylinders.
    ``depth_noise``: gaussian sigma in metres on valid depth pixels.
    """
    o = np.array(cam.pose.position, dtype=np.float64)
    d = _rays(cam)
    n_pix = len(d)
    best_t = np.full(n_pix, _INF)
    best_n = np.zeros((n_pix, 3))
    best_rgb = np.tile(np.array(SKY_RGB, dtype=np.float64), (n_pix, 1))
    with np.errstate(divide='ignore', invalid='ignore'):
        t_ground = (ground_z - o[2]) / d[:, 2]
    ok = (d[:, 2] < -1e-12) & (t_ground > 0)
    best_t = np.where(ok, t_ground, best_t)
    best_n[ok] = (0.0, 0.0, 1.0)
    best_rgb[ok] = GROUND_RGB
    for s in shapes:
        if s.name in hidden:
            continue
        t, n = _intersect(o, d, s, (yaws or {}).get(s.name, 0.0))
        closer = t < best_t
        if colors and s.name in colors:
            base = np.array(colors[s.name][:3], dtype=np.float64)
        elif s.color is not None:
            base = np.array(s.color, dtype=np.float64) * 255.0
        else:
            base = np.array((200.0, 200.0, 200.0))
        best_t = np.where(closer, t, best_t)
        best_n = np.where(closer[:, None], n, best_n)
        best_rgb[closer] = base
    lam = np.clip(-(best_n @ LIGHT_DIR), 0.0, 1.0)
    shade = np.where(np.isfinite(best_t), AMBIENT + DIFFUSE * lam, 1.0)   # sky is not shaded
    rgb = np.clip(best_rgb * shade[:, None], 0, 255).astype(np.uint8)
    depth = best_t.astype(np.float64)
    depth[(depth > cam.far) | (depth < cam.near)] = _INF
    if depth_noise:
        rng = np.random.default_rng(seed)
        valid = np.isfinite(depth)
        depth[valid] += rng.normal(0.0, depth_noise, valid.sum())
    return Frame(rgb=rgb.reshape(cam.height, cam.width, 3),
                 depth=depth.astype(np.float32).reshape(cam.height, cam.width),
                 intrinsics=Intrinsics.from_camera_spec(cam),
                 world_from_optical=optical_frame_in_world(cam), stamp=stamp)
