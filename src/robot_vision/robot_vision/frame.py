"""Plain data types shared by detectors, geometry and the tracker (numpy only, no ROS)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

import numpy as np
from robot_core.perception import Intrinsics, Transform

Vec = Tuple[float, float, float]


@dataclass(frozen=True)
class Frame:
    """One RGB-D sample. ``depth`` is the z-depth in metres (NaN/inf/out of range = no
    measurement); ``world_from_optical`` maps optical-frame points (x right, y down, z forward)
    into the world frame."""
    rgb: np.ndarray                       # (H, W, 3) uint8
    depth: np.ndarray                     # (H, W) float, z-depth
    intrinsics: Intrinsics
    world_from_optical: Transform
    stamp: float = 0.0                    # seconds

    def __post_init__(self):
        h, w = self.depth.shape
        intr = (self.intrinsics.height, self.intrinsics.width)
        if self.rgb.shape[:2] != (h, w) or intr != (h, w):
            raise ValueError(f'rgb {self.rgb.shape}, depth {self.depth.shape} and intrinsics '
                             f'{(self.intrinsics.height, self.intrinsics.width)} disagree')

    @property
    def camera_position(self) -> Vec:
        t = self.world_from_optical.translation
        return (float(t[0]), float(t[1]), float(t[2]))


@dataclass(frozen=True)
class Segment:
    """Pixels a detector believes belong to one object (the detector-independent hand-over)."""
    label: str
    rows: np.ndarray = field(repr=False)  # pixel row indices
    cols: np.ndarray = field(repr=False)  # pixel column indices
    score: float = 1.0                    # detector confidence 0..1 (colour detector: 1.0)

    @property
    def pixels(self) -> int:
        return int(len(self.rows))

    @property
    def centroid(self) -> Tuple[float, float]:
        """(u, v) pixel centroid (index coordinates)."""
        return float(self.cols.mean()), float(self.rows.mean())

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        """(u_min, v_min, u_max, v_max), inclusive pixel indices."""
        return (int(self.cols.min()), int(self.rows.min()),
                int(self.cols.max()), int(self.rows.max()))
