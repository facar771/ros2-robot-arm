"""Detector interface and the colour detector. A detector turns a :class:`Frame` into pixel
:class:`Segment` s; everything downstream (geometry, tracker) only sees segments, so a learned
detector (see ``onnx_detector`` in a later PR) can replace the colour one without other changes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Sequence

import numpy as np
from robot_core.perception import (
    DEFAULT_COLORS,
    ColorClass,
    color_masks,
    connected_components,
)

from robot_vision.frame import Frame, Segment


class Detector(ABC):
    """Anything that finds objects in a frame. Must not raise on an empty or odd frame."""

    name: str = 'detector'

    @abstractmethod
    def detect(self, frame: Frame) -> List[Segment]:
        """Segments sorted by decreasing pixel count within each label."""


class ColorDetector(Detector):
    """HSV colour thresholding + connected components (same classes as ``robot_core.perception``).

    ``min_pixels`` drops specks; ``max_per_color`` bounds the work per frame. Pixels without a valid
    depth are *kept* in the segment (geometry decides what to do with them) so the pixel centroid
    stays meaningful when depth drops out; only depth-valid pixels count toward ``min_valid``.
    """

    name = 'color'

    def __init__(self, colors: Sequence[ColorClass] = DEFAULT_COLORS, *, min_pixels: int = 30,
                 max_per_color: int = 4, min_valid: int = 10,
                 depth_range: tuple = (0.1, 5.0)):
        self.colors = tuple(colors)
        self.min_pixels = min_pixels
        self.max_per_color = max_per_color
        self.min_valid = min_valid
        self.depth_range = depth_range

    def detect(self, frame: Frame) -> List[Segment]:
        depth = frame.depth
        valid = np.isfinite(depth) & (depth >= self.depth_range[0]) & (depth <= self.depth_range[1])
        w = depth.shape[1]
        out: List[Segment] = []
        for name, mask in color_masks(frame.rgb, self.colors).items():
            kept = 0
            for blob in connected_components(mask):
                if len(blob) < self.min_pixels or kept >= self.max_per_color:
                    break
                rows, cols = np.divmod(blob, w)
                if int(valid[rows, cols].sum()) < self.min_valid:
                    continue
                out.append(Segment(label=name, rows=rows, cols=cols, score=1.0))
                kept += 1
        return out
