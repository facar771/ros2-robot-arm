"""Colour-object perception from an RGB-D image (pure numpy, no ROS, no OpenCV).

Pipeline: RGB -> hue/saturation colour masks -> connected components -> depth back-projection with
the camera intrinsics -> optical frame -> world frame -> one :class:`Detection` per object.

Conventions
- Optical frame: x right, y down, z forward (image convention); depth is the z-depth along z.
- Pixel (u, v) is an *index*; its centre is at (u + 0.5, v + 0.5) in intrinsics coordinates. This is
  the same convention as :func:`robot_core.workspace.expected_depth`, which ``sensor_check`` has
  verified against the real Gazebo render.

Assumptions (documented, not hidden)
- Objects rest on the ground plane ``ground_z`` and are convex and roughly symmetric (box,
  cylinder, sphere). The camera looks down onto them, so the top of the object is always visible:
  the centre is the middle of the visible *top cap*, and the centre height is half the object
  height. A camera that cannot see the top would need a different estimator.
- Size is the bounding box of the visible points; the far side of round objects is hidden, so their
  footprint is underestimated by up to ~1 cm. Use it for collision boxes, not for measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from robot_core.workspace import CameraSpec

Vec = Tuple[float, float, float]


@dataclass(frozen=True)
class ColorClass:
    """A named colour as an HSV window. Hue is in degrees; shading changes V, rarely H."""
    name: str
    hue: float
    tolerance: float = 25.0
    min_saturation: float = 0.45   # rejects grey ground and the pale sky background
    min_value: float = 0.12


DEFAULT_COLORS: Tuple[ColorClass, ...] = (
    ColorClass('red', 0.0, 20.0),
    ColorClass('green', 120.0, 30.0),
    ColorClass('blue', 232.0, 25.0),
)


@dataclass(frozen=True)
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @classmethod
    def from_camera_info_k(cls, k: Sequence[float], width: int, height: int) -> 'Intrinsics':
        """From the row-major 3x3 ``K`` of a sensor_msgs/CameraInfo."""
        return cls(fx=float(k[0]), fy=float(k[4]), cx=float(k[2]), cy=float(k[5]),
                   width=int(width), height=int(height))

    @classmethod
    def from_camera_spec(cls, cam: CameraSpec) -> 'Intrinsics':
        return cls(cam.fx, cam.fy, cam.width / 2.0, cam.height / 2.0, cam.width, cam.height)


@dataclass(frozen=True)
class Detection:
    label: str                      # colour class name
    index: int                      # 0 = largest blob of this colour in the image
    position: Vec                   # estimated object centre, world frame (m)
    size: Vec                       # bounding box (x, y, z extent) in the world frame (m)
    top_z: float                    # height of the highest visible point above the ground (m)
    pixels: int                     # blob size in pixels (confidence proxy)
    pixel_centroid: Tuple[float, float]   # (u, v) of the blob


class Transform:
    """Rigid transform p_parent = R p_child + t (numpy)."""

    def __init__(self, rotation: np.ndarray, translation: Sequence[float]):
        self.rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
        self.translation = np.asarray(translation, dtype=np.float64).reshape(3)

    @classmethod
    def from_quaternion(cls, x: float, y: float, z: float, w: float,
                        translation: Sequence[float]) -> 'Transform':
        n = x * x + y * y + z * z + w * w
        if n < 1e-12:
            raise ValueError('quaternion has zero norm')
        s = 2.0 / n
        r = np.array([
            [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
            [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
            [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)]])
        return cls(r, translation)

    def apply(self, points: np.ndarray) -> np.ndarray:
        """Transform an (N, 3) array."""
        return points @ self.rotation.T + self.translation


# optical axes expressed in the camera_link frame: x_opt = -y_link, y_opt = -z_link, z_opt = x_link
_LINK_FROM_OPTICAL = np.array([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])


def optical_frame_in_world(cam: CameraSpec) -> Transform:
    """world <- camera_optical_frame from the camera pose in the world file (same as the launch
    file's static TF chain world -> camera_link -> camera_optical_frame)."""
    return Transform(np.array(cam.pose.rotation()) @ _LINK_FROM_OPTICAL, cam.pose.position)


# ---- colour ------------------------------------------------------------------------------------
def rgb_to_hsv(rgb: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(..., 3) uint8 or 0-255 floats -> hue in degrees [0, 360), saturation, value in [0, 1]."""
    x = rgb.astype(np.float32) / 255.0
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    mx, mn = x.max(axis=-1), x.min(axis=-1)
    d = mx - mn
    safe = np.where(d > 0, d, 1.0)
    hue = np.zeros_like(mx)
    is_r = (mx == r) & (d > 0)
    is_g = (mx == g) & (mx != r) & (d > 0)
    is_b = (mx == b) & (mx != r) & (mx != g) & (d > 0)
    hue = np.where(is_r, 60.0 * (((g - b) / safe) % 6.0), hue)
    hue = np.where(is_g, 60.0 * ((b - r) / safe + 2.0), hue)
    hue = np.where(is_b, 60.0 * ((r - g) / safe + 4.0), hue)
    sat = np.where(mx > 0, d / np.where(mx > 0, mx, 1.0), 0.0)
    return hue, sat, mx


def color_masks(rgb: np.ndarray,
                colors: Sequence[ColorClass] = DEFAULT_COLORS) -> Dict[str, np.ndarray]:
    """Boolean mask (H, W) per colour class."""
    hue, sat, val = rgb_to_hsv(rgb)
    masks = {}
    for c in colors:
        hue_dist = np.abs((hue - c.hue + 180.0) % 360.0 - 180.0)
        masks[c.name] = (hue_dist <= c.tolerance) & (sat >= c.min_saturation) & (val >= c.min_value)
    return masks


def classify_color(rgb01: Sequence[float],
                   colors: Sequence[ColorClass] = DEFAULT_COLORS) -> Optional[str]:
    """Colour class of a single material colour (0-1 floats, e.g. from the world file)."""
    pixel = np.array([[[c * 255.0 for c in rgb01[:3]]]], dtype=np.float32)
    for name, mask in color_masks(pixel, colors).items():
        if mask[0, 0]:
            return name
    return None


# ---- image helpers -----------------------------------------------------------------------------
def erode(mask: np.ndarray) -> np.ndarray:
    """4-neighbour binary erosion (border pixels are eroded away)."""
    out = mask.copy()
    out[1:, :] &= mask[:-1, :]
    out[:-1, :] &= mask[1:, :]
    out[:, 1:] &= mask[:, :-1]
    out[:, :-1] &= mask[:, 1:]
    out[0, :] = out[-1, :] = False
    out[:, 0] = out[:, -1] = False
    return out


def connected_components(mask: np.ndarray) -> List[np.ndarray]:
    """4-connected blobs of a boolean mask as flat index arrays, largest first."""
    h, w = mask.shape
    flat = mask.ravel().tolist()
    seen = bytearray(len(flat))
    blobs: List[List[int]] = []
    for start in np.flatnonzero(mask.ravel()).tolist():
        if seen[start]:
            continue
        seen[start] = 1
        stack, members = [start], []
        while stack:
            i = stack.pop()
            members.append(i)
            c = i % w
            for j, ok in ((i - 1, c > 0), (i + 1, c < w - 1), (i - w, i >= w),
                          (i + w, i < (h - 1) * w)):
                if ok and flat[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        blobs.append(members)
    blobs.sort(key=len, reverse=True)
    return [np.array(b, dtype=np.int64) for b in blobs]


def backproject(depth: np.ndarray, intr: Intrinsics, rows: np.ndarray,
                cols: np.ndarray) -> np.ndarray:
    """Pixel indices + z-depth -> (N, 3) points in the optical frame."""
    z = depth[rows, cols].astype(np.float64)
    x = (cols + 0.5 - intr.cx) * z / intr.fx
    y = (rows + 0.5 - intr.cy) * z / intr.fy
    return np.stack([x, y, z], axis=1)


# ---- detection ---------------------------------------------------------------------------------
def detect_objects(rgb: np.ndarray, depth: np.ndarray, intr: Intrinsics,
                   world_from_optical: Transform,
                   colors: Sequence[ColorClass] = DEFAULT_COLORS, *,
                   min_pixels: int = 30, depth_range: Tuple[float, float] = (0.1, 5.0),
                   ground_z: float = 0.0, cap_band: float = 0.01,
                   max_per_color: int = 4) -> List[Detection]:
    """Find coloured objects. ``rgb``: (H, W, 3) uint8, ``depth``: (H, W) float z-depth in metres
    (NaN/inf/out-of-range = no measurement). Returns detections sorted by (label, index)."""
    if rgb.shape[:2] != depth.shape or rgb.shape[:2] != (intr.height, intr.width):
        raise ValueError(f'rgb {rgb.shape}, depth {depth.shape} and intrinsics '
                         f'{(intr.height, intr.width)} disagree')
    valid = np.isfinite(depth) & (depth >= depth_range[0]) & (depth <= depth_range[1])
    w = depth.shape[1]
    out: List[Detection] = []
    for name, mask in color_masks(rgb, colors).items():
        kept = 0
        for blob in connected_components(mask):
            if len(blob) < min_pixels or kept >= max_per_color:
                break  # blobs are sorted by size
            rows, cols = np.divmod(blob, w)
            ok = valid[rows, cols]
            if ok.sum() < min_pixels:
                continue
            pts = world_from_optical.apply(backproject(depth, intr, rows[ok], cols[ok]))
            top = float(pts[:, 2].max())
            cap = pts[pts[:, 2] >= top - cap_band]
            cx = 0.5 * (cap[:, 0].min() + cap[:, 0].max())
            cy = 0.5 * (cap[:, 1].min() + cap[:, 1].max())
            height = top - ground_z
            ext = pts.max(axis=0) - pts.min(axis=0)
            out.append(Detection(
                label=name, index=kept,
                position=(float(cx), float(cy), ground_z + height / 2.0),
                size=(float(ext[0]), float(ext[1]), float(height)),
                top_z=float(height), pixels=int(len(blob)),
                pixel_centroid=(float(cols.mean()), float(rows.mean()))))
            kept += 1
    return out
