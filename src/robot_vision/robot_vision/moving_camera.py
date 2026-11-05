"""Per-frame camera pose for the moving camera platform (ROS-free).

A frame taken by the tracking platform (``tracking.sdf``: yaw / slide / pitch) must be placed in the
world with the platform pose **at the image time**, not with the fixed camera of ``workspace.sdf``.
:class:`JointHistory` interpolates the joint states at a stamp; :class:`PlatformFrameBuilder` turns
an RGB-D pair plus a stamp into a :class:`robot_vision.Frame` whose ``world_from_optical`` is that
pose (the forward kinematics of :mod:`robot_vision.platform`, which ``tracking_node`` also uses).
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from typing import Deque, Optional

import numpy as np
from robot_core.perception import Intrinsics
from robot_core.workspace import CameraSpec

from robot_vision.frame import Frame
from robot_vision.platform import Q, optical_in_world
from robot_vision.tracking_sim import CAMERA


class JointHistory:
    """Time-ordered joint positions ``(yaw, slide, pitch)`` with linear interpolation.

    ``at(stamp)`` returns ``None`` unless ``stamp`` lies inside the recorded interval (never
    extrapolates: a pose guessed beyond the last sample would be wrong while the platform moves)
    and the neighbouring samples are at most ``max_gap_s`` apart (a gap means joint states were
    lost)."""

    def __init__(self, maxlen: int = 4000, max_gap_s: float = 0.25):
        self._t: Deque[float] = deque(maxlen=maxlen)
        self._q: Deque[Q] = deque(maxlen=maxlen)
        self.max_gap_s = max_gap_s

    def add(self, stamp: float, q: Q) -> None:
        """Append a sample. Non-finite values and out-of-order stamps are dropped."""
        if not math.isfinite(stamp) or any(not math.isfinite(v) for v in q):
            return
        if self._t and stamp < self._t[-1]:
            return
        self._t.append(stamp)
        self._q.append((float(q[0]), float(q[1]), float(q[2])))

    @property
    def newest(self) -> Optional[float]:
        return self._t[-1] if self._t else None

    @property
    def oldest(self) -> Optional[float]:
        return self._t[0] if self._t else None

    def at(self, stamp: float) -> Optional[Q]:
        if not self._t or not math.isfinite(stamp) or stamp < self._t[0] or stamp > self._t[-1]:
            return None
        i = bisect.bisect_left(self._t, stamp)
        if self._t[i] == stamp:
            return self._q[i]
        t0, t1 = self._t[i - 1], self._t[i]
        if t1 - t0 > self.max_gap_s:
            return None
        w = (stamp - t0) / (t1 - t0)
        a, b = self._q[i - 1], self._q[i]
        return (a[0] + w * (b[0] - a[0]), a[1] + w * (b[1] - a[1]), a[2] + w * (b[2] - a[2]))


class PlatformFrameBuilder:
    """Builds world-posed frames for the camera platform from joint states."""

    def __init__(self, history: Optional[JointHistory] = None, camera: Optional[CameraSpec] = None):
        self.history = history or JointHistory()
        self.camera = camera or CAMERA

    def frame(self, rgb: np.ndarray, depth: np.ndarray, intrinsics: Intrinsics,
              stamp: float) -> Optional[Frame]:
        """The frame at ``stamp`` with the platform pose of that instant; None while the pose is
        unknown (no joint state yet, stamp outside the history, gap in the joint states)."""
        q = self.history.at(stamp)
        if q is None:
            return None
        return Frame(rgb, depth, intrinsics, optical_in_world(q, self.camera), stamp)
