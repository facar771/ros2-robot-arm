"""Live camera frames for plugins: subscribes to ``/camera/{image,depth_image,camera_info}`` and
hands the newest RGB-D pair to ``robot_plugins`` as a :class:`robot_vision.Frame`.

Camera pose (``camera=``):

* ``'fixed'``: from the world file (``workspace.sdf``: a FIXED camera, like the TF chain the launch
  file publishes). Right for the workspace world, WRONG for a camera that moves.
* ``'platform'``: the tracking platform (``tracking.sdf``) moves the camera, so every frame is
  placed with the pose at ITS OWN stamp: ``/platform/joint_states`` interpolated at the image
  stamp, through the platform forward kinematics (:mod:`robot_vision.moving_camera`, as in
  ``tracking_node``). A frame waits for joint states newer than its stamp (never an extrapolated
  pose) and is dropped if the joint states have a gap or never arrive.
* ``'auto'`` (default): ``'platform'`` once somebody publishes ``/platform/joint_states``,
  otherwise ``'fixed'``. Pass ``camera=`` explicitly when it matters.

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
from robot_vision.moving_camera import JointHistory, PlatformFrameBuilder
from sensor_msgs.msg import CameraInfo, Image, JointState

AUTO_SETTLE_S = 1.0     # camera='auto': wait this long for a platform before the fixed pose


def _stamp(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


class RosFrameSource(FrameSource):
    """``latest()`` returns the newest RGB + depth pair whose stamps agree (within 60 ms)."""

    def __init__(self, world: Optional[Union[str, Path]] = None, max_age_s: float = 5.0,
                 camera: str = 'auto', pose_wait_s: float = 1.0):
        if camera not in ('auto', 'fixed', 'platform'):
            raise ValueError("camera must be 'auto' | 'fixed' | 'platform'")
        self._transform = optical_frame_in_world(load_workspace(world or default_world()).camera)
        self._camera = camera
        self._pose_wait = pose_wait_s
        self._builder = PlatformFrameBuilder(JointHistory())
        self._joint_idx = None
        self._waiting = []                  # (stamp, rgb, depth, monotonic arrival) awaiting a pose
        self._wlock = threading.RLock()
        self._born = time.monotonic()
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
        self._node.create_subscription(JointState, '/platform/joint_states', self._on_joints, 100)
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
            self._waiting.append((stamp, slot['rgb'], slot['depth'], time.monotonic()))
            self._resolve()

    # ---- moving camera: pose per frame -----------------------------------------------------
    @property
    def pose_source(self) -> str:
        """``'fixed'`` | ``'platform'`` (``'auto'`` resolves on the first image)."""
        return self._mode()

    def _mode(self) -> str:
        if self._camera == 'auto' and self._node.count_publishers('/platform/joint_states') > 0:
            self._camera = 'platform'                # latched: a platform was announced
        return 'fixed' if self._camera == 'auto' else self._camera

    def _on_joints(self, msg: JointState) -> None:
        if self._joint_idx is None:
            names = list(msg.name)
            wanted = ('yaw_joint', 'slide_joint', 'pitch_joint')
            if not all(j in names for j in wanted) or not msg.position:
                return
            self._joint_idx = tuple(names.index(j) for j in wanted)
        i0, i1, i2 = self._joint_idx
        self._builder.history.add(_stamp(msg), (msg.position[i0], msg.position[i1],
                                                msg.position[i2]))
        if self._waiting:
            self._resolve()

    def _resolve(self) -> None:
        """Place waiting frames: fixed pose at once, platform pose once the joint history covers
        the stamp. In ``auto`` mode the first ``AUTO_SETTLE_S`` seconds count as undecided (DDS
        discovery of ``/platform/joint_states`` can lag behind the first image), so a first frame
        is never placed with the wrong pose; stale waiting frames are dropped."""
        with self._wlock:
            mode = self._mode()
            undecided = self._camera == 'auto'
            now = time.monotonic()
            newest = self._builder.history.newest
            keep = []
            for entry in self._waiting:
                stamp, rgb, depth, arrived = entry
                if mode == 'fixed':
                    if undecided and now - self._born < AUTO_SETTLE_S:
                        keep.append(entry)
                    else:
                        self._publish(Frame(rgb, depth, self._intr, self._transform, stamp))
                    continue
                if newest is not None and stamp <= newest:
                    frame = self._builder.frame(rgb, depth, self._intr, stamp)
                    if frame is not None:
                        self._publish(frame)
                    continue                # placed, or its pose is unknowable (gap): drop
                if now - arrived < self._pose_wait:
                    keep.append(entry)
            self._waiting = keep

    def _publish(self, frame: Frame) -> None:
        with self._lock:
            if self._frame is None or frame.stamp >= self._frame.stamp:
                self._frame, self._received_at = frame, time.monotonic()

    def latest(self) -> Optional[Frame]:
        if self._waiting:
            self._resolve()                 # lets an undecided / waiting frame through in time
        with self._lock:
            if self._frame is None or time.monotonic() - self._received_at > self._max_age:
                return None
            return self._frame
