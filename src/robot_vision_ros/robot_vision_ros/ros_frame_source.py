"""Live camera frames for plugins: subscribes to ``/camera/{image,depth_image,camera_info}`` and
hands the newest RGB-D pair to ``robot_plugins`` as a :class:`robot_vision.Frame`.

The camera pose comes from the world file (``workspace.sdf``: a FIXED camera, like the TF chain the
launch file publishes), so this source is for the workspace world. For a moving camera (the tracking
platform) a source would have to compute the pose from the joint states per frame; not done here.

Use through the plugin manager: ``PluginManager(..., frames='ros')`` (needs a sourced ROS 2
environment and the simulation running; only message stamps are used, ``use_sim_time`` is not
required).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional, Union

import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from robot_core.perception import Intrinsics, optical_frame_in_world
from robot_core.workspace import load_workspace
from robot_plugins.frames import FrameSource, default_world
from robot_vision.frame import Frame
from sensor_msgs.msg import CameraInfo, Image


def _stamp(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


class RosFrameSource(FrameSource):
    """``latest()`` returns the newest RGB + depth pair whose stamps agree (within 60 ms)."""

    def __init__(self, world: Optional[Union[str, Path]] = None, max_age_s: float = 5.0):
        self._transform = optical_frame_in_world(load_workspace(world or default_world()).camera)
        self._max_age = max_age_s
        self._lock = threading.Lock()
        self._frame: Optional[Frame] = None
        self._received_at = 0.0
        self._pending = {}
        self._intr: Optional[Intrinsics] = None
        if not rclpy.ok():
            rclpy.init()
        self._node = rclpy.create_node('plugin_camera')
        self._node.create_subscription(CameraInfo, '/camera/camera_info', self._on_info, 5)
        self._node.create_subscription(Image, '/camera/image', self._on_rgb, 5)
        self._node.create_subscription(Image, '/camera/depth_image', self._on_depth, 5)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        threading.Thread(target=self._executor.spin, daemon=True).start()

    def _on_info(self, msg: CameraInfo) -> None:
        if self._intr is None:
            self._intr = Intrinsics.from_camera_info_k(msg.k, msg.width, msg.height)

    def _on_rgb(self, msg: Image) -> None:
        data = np.frombuffer(bytes(msg.data), np.uint8).reshape(msg.height, msg.width, 3).copy()
        self._pair(_stamp(msg), 'rgb', data)

    def _on_depth(self, msg: Image) -> None:
        data = np.frombuffer(bytes(msg.data), np.float32).reshape(msg.height, msg.width).copy()
        self._pair(_stamp(msg), 'depth', data)

    def _pair(self, stamp: float, key: str, data: np.ndarray) -> None:
        slot = self._pending.setdefault(round(stamp, 3), {})
        slot[key] = data
        if 'rgb' in slot and 'depth' in slot and self._intr is not None:
            del self._pending[round(stamp, 3)]
            for old in [s for s in self._pending if s < stamp - 1.0]:
                del self._pending[old]
            frame = Frame(slot['rgb'], slot['depth'], self._intr, self._transform, stamp)
            with self._lock:
                self._frame, self._received_at = frame, time.monotonic()

    def latest(self) -> Optional[Frame]:
        with self._lock:
            if self._frame is None or time.monotonic() - self._received_at > self._max_age:
                return None
            return self._frame
