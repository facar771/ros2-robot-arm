"""Pan scan: sweep a camera across the scene, measure what each view sees, merge the views.

The camera turns about its own vertical axis (``Panner``); every view goes through the normal
detector + :func:`robot_vision.geometry.measure`; objects seen in several overlapping views are
merged by world position and label. Objects cut off by the image border are only measured from a
view where they are whole (``partial`` is reported when no such view exists), so a half-visible
object never overrides a good measurement.

Honest limits: the camera must see the *top* of objects (same assumption as ``geometry``), the pan
axis is the camera's own (a platform whose camera is offset from the pan axis needs the true camera
pose in each :class:`Frame`; this module only trusts ``frame.world_from_optical``), and merging
uses the object's footprint centre, so two same-coloured objects closer than ``merge_distance``
look like one.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from robot_core.workspace import CameraSpec, Pose, Shape

from robot_vision.detectors import ColorDetector, Detector
from robot_vision.frame import Frame
from robot_vision.geometry import GeometryConfig, ObjectMeasure, measure


class Panner(ABC):
    """A camera that can turn. ``frame()`` must carry the pose of the camera *at that pan angle*."""

    @property
    @abstractmethod
    def limits(self) -> Tuple[float, float]:
        """(lowest, highest) pan angle in radians."""

    @abstractmethod
    def move_to(self, yaw: float) -> None:
        """Turn to ``yaw`` (rad) and return once the camera has settled."""

    @abstractmethod
    def frame(self) -> Optional[Frame]:
        """A frame taken at the current angle, or None."""


@dataclass(frozen=True)
class ScanConfig:
    yaw_min: float = -math.pi / 2
    yaw_max: float = math.pi / 2
    steps: int = 7
    merge_distance: float = 0.05        # m: same label closer than this = same object
    border_margin: int = 2              # px: a segment this close to the image edge is 'cut off'


@dataclass
class ScannedObject:
    label: str
    measure: ObjectMeasure
    yaw: float                          # pan angle of the view the measurement comes from
    views: int                          # in how many views the object was seen
    partial: bool                       # True: it was cut off by the image border in every view

    @property
    def center(self) -> Tuple[float, float, float]:
        return self.measure.center


@dataclass
class ScanResult:
    objects: List[ScannedObject]
    angles: List[float]
    views_used: int                     # views that produced a frame
    stopped: bool = False
    notes: List[str] = field(default_factory=list)


def sweep_angles(cfg: ScanConfig, limits: Tuple[float, float]) -> List[float]:
    lo, hi = max(cfg.yaw_min, limits[0]), min(cfg.yaw_max, limits[1])
    if hi < lo or cfg.steps < 1:
        return []
    if cfg.steps == 1 or hi == lo:
        return [0.5 * (lo + hi)]
    return [lo + (hi - lo) * k / (cfg.steps - 1) for k in range(cfg.steps)]


def scan(panner: Panner, config: Optional[ScanConfig] = None, detector: Optional[Detector] = None,
         geometry: Optional[GeometryConfig] = None,
         should_stop: Callable[[], bool] = lambda: False) -> ScanResult:
    """Run the sweep and return the merged object list (ordered by label, then world y)."""
    cfg = config or ScanConfig()
    detector = detector or ColorDetector()
    geometry = geometry or GeometryConfig()
    angles = sweep_angles(cfg, panner.limits)
    candidates: List[Tuple[Tuple[int, float, int], str, ObjectMeasure, float, bool]] = []
    views, notes, stopped = 0, [], False
    for yaw in angles:
        if should_stop():
            stopped = True
            break
        panner.move_to(yaw)
        frame = panner.frame()
        if frame is None:
            notes.append(f'yaw {math.degrees(yaw):.0f}°: kare yok')
            continue
        views += 1
        h, w = frame.depth.shape
        for seg in detector.detect(frame):
            m = measure(frame, seg, geometry)
            if m is None:
                continue
            u0, v0, u1, v1 = seg.bbox
            cut = (u0 <= cfg.border_margin or v0 <= cfg.border_margin
                   or u1 >= w - 1 - cfg.border_margin or v1 >= h - 1 - cfg.border_margin)
            centred = abs(0.5 * (u0 + u1) - w / 2.0) / w
            candidates.append(((int(cut), centred, -m.points), seg.label, m, yaw, cut))
    objects: List[ScannedObject] = []
    for _, label, m, yaw, cut in sorted(candidates, key=lambda c: c[0]):
        for obj in objects:
            if obj.label == label and math.dist(obj.center[:2], m.center[:2]) < cfg.merge_distance:
                obj.views += 1
                break
        else:
            objects.append(ScannedObject(label, m, yaw, 1, cut))
    objects.sort(key=lambda o: (o.label, o.center[1]))
    return ScanResult(objects, angles, views, stopped, notes)


# ---- synthetic scene for tests / validation -----------------------------------------------------
class SyntheticPanner(Panner):
    """Renders ``shapes`` from a camera that turns about its own vertical axis."""

    def __init__(self, camera: CameraSpec, shapes: Sequence[Shape], depth_noise: float = 0.0,
                 limits: Tuple[float, float] = (-math.pi, math.pi),
                 yaws: Optional[Dict[str, float]] = None):
        self._camera, self._shapes = camera, list(shapes)
        self._noise, self._limits, self._yaws = depth_noise, limits, yaws
        self._yaw = 0.0
        self.moves: List[float] = []

    @property
    def limits(self) -> Tuple[float, float]:
        return self._limits

    def move_to(self, yaw: float) -> None:
        self._yaw = yaw
        self.moves.append(yaw)

    def frame(self) -> Optional[Frame]:
        from robot_vision.synth import render
        cam = replace(self._camera, pose=replace(self._camera.pose,
                                                 yaw=self._camera.pose.yaw + self._yaw))
        return render(cam, self._shapes, depth_noise=self._noise, seed=len(self.moves),
                      yaws=self._yaws)


RING_CAMERA = CameraSpec(pose=Pose(0.0, 0.0, 0.6, 0.0, 0.45, 0.0), horizontal_fov=1.047,
                         width=320, height=240, near=0.05, far=5.0, frame_id='camera_link')

# name, kind, dims, colour (0-1), bearing from the camera's zero heading (deg), ground distance (m)
_RING = (
    ('red_box_a', 'box', (0.06, 0.06, 0.06), (0.9, 0.1, 0.1), -75.0, 0.80),
    ('green_cylinder_a', 'cylinder', (0.03, 0.10), (0.1, 0.8, 0.1), -28.0, 0.90),
    ('blue_ball_a', 'sphere', (0.04,), (0.1, 0.2, 0.9), 0.0, 1.10),
    ('red_box_b', 'box', (0.05, 0.05, 0.08), (0.9, 0.1, 0.1), 30.0, 0.85),
    ('green_cylinder_b', 'cylinder', (0.025, 0.07), (0.1, 0.8, 0.1), 62.0, 0.80),
    ('blue_ball_b', 'sphere', (0.03,), (0.1, 0.2, 0.9), 88.0, 0.95),
)


def ring_world() -> Tuple[CameraSpec, List[Shape]]:
    """Six coloured objects spread over 160° around a camera at (0, 0, 0.6): one view (60° wide)
    sees at most three of them, so only a pan scan can list them all."""
    shapes = []
    for name, kind, dims, rgb, bearing, dist in _RING:
        a = math.radians(bearing)
        height = {'box': dims[2] if kind == 'box' else 0, 'sphere': 2 * dims[0] if kind == 'sphere'
                  else 0, 'cylinder': dims[1] if kind == 'cylinder' else 0}[kind]
        shapes.append(Shape(name, kind, (dist * math.cos(a), dist * math.sin(a), height / 2.0),
                            dims, rgb))
    return RING_CAMERA, shapes
