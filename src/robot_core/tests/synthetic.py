"""Synthetic RGB-D renderer for perception tests: ray-casts the world model (robot_core.workspace)
so the expected image is exact; shading and depth noise are added on top."""
from dataclasses import replace

import numpy as np
from robot_core.perception import Intrinsics, optical_frame_in_world
from robot_core.workspace import GROUND, Workspace, expected_depth

GROUND_RGB = (140, 140, 140)
SKY_RGB = (178, 204, 229)   # the world file's background colour: pale, must not be detected


def half_resolution(ws: Workspace) -> Workspace:
    """Same camera (same field of view), half the pixels: keeps the Python renderer fast."""
    cam = ws.camera
    return replace(ws, camera=replace(cam, width=cam.width // 2, height=cam.height // 2))


def render(ws: Workspace, colors: dict, shade: dict = None, depth_noise: float = 0.0,
           seed: int = 0):
    """Return (rgb uint8 HxWx3, depth float32 HxW, Intrinsics, world<-optical transform).

    ``colors``: shape name -> base RGB (0-255). ``shade``: shape name -> brightness factor.
    """
    cam = ws.camera
    rng = np.random.default_rng(seed)
    rgb = np.empty((cam.height, cam.width, 3), dtype=np.uint8)
    depth = np.full((cam.height, cam.width), np.inf, dtype=np.float32)
    for v in range(cam.height):
        for u in range(cam.width):
            hit = expected_depth(ws, u, v)
            if hit is None:
                rgb[v, u] = SKY_RGB
                continue
            z, name = hit
            base = GROUND_RGB if name == GROUND else colors[name]
            factor = (shade or {}).get(name, 1.0)
            rgb[v, u] = np.clip(np.array(base) * factor, 0, 255)
            depth[v, u] = z + (rng.normal(0.0, depth_noise) if depth_noise else 0.0)
    return rgb, depth, Intrinsics.from_camera_spec(cam), optical_frame_in_world(cam)
