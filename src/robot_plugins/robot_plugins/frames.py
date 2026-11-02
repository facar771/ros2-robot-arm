"""Camera frames for plugins: an abstract source plus two ROS-free implementations.

A live ROS source (``robot_vision_ros``) can implement the same interface; plugins never import ROS.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from robot_vision.frame import Frame


class FrameSource(ABC):
    @abstractmethod
    def latest(self) -> Optional[Frame]:
        """The newest frame, or ``None`` if there is none yet. Must not block for long."""


def default_world() -> Path:
    """``workspace.sdf`` of the installed ``robot_sim`` package, or the source tree's copy."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return Path(get_package_share_directory('robot_sim')) / 'worlds' / 'workspace.sdf'
    except Exception:                                    # noqa: BLE001 - not a ROS environment
        return Path(__file__).resolve().parents[2] / 'robot_sim' / 'worlds' / 'workspace.sdf'


class SyntheticFrameSource(FrameSource):
    """Renders the (static) world file once with the synthetic renderer; for tests and demos."""

    def __init__(self, world: Optional[Union[str, Path]] = None, depth_noise: float = 0.0):
        from robot_core.workspace import load_workspace
        from robot_vision.synth import render
        self.workspace = load_workspace(world or default_world())
        self._frame = render(self.workspace.camera, self.workspace.shapes, depth_noise=depth_noise)

    def latest(self) -> Optional[Frame]:
        f = self._frame
        return Frame(f.rgb, f.depth, f.intrinsics, f.world_from_optical, time.time())


class NpzFrameSource(FrameSource):
    """Replays one captured Gazebo frame (``robot_vision/tools/capture_frame.py`` layout)."""

    def __init__(self, path: Union[str, Path], world: Optional[Union[str, Path]] = None):
        from robot_core.workspace import load_workspace
        from robot_vision.validate import load_frame
        self._frame = load_frame(Path(path), load_workspace(world or default_world()))

    def latest(self) -> Optional[Frame]:
        f = self._frame
        return Frame(f.rgb, f.depth, f.intrinsics, f.world_from_optical, time.time())


class DelayedFrames(FrameSource):
    """Synthetic frames that only start ``delay_s`` seconds after creation: behaves like a live
    camera whose first message is still in flight (tests of the first-frame wait)."""

    def __init__(self, delay_s: float):
        self._start = time.monotonic() + delay_s
        self._inner = SyntheticFrameSource()

    def latest(self) -> Optional[Frame]:
        return self._inner.latest() if time.monotonic() >= self._start else None


class NoFrames(FrameSource):
    def latest(self) -> Optional[Frame]:
        return None


def make_frame_source(spec: str) -> FrameSource:
    """``none`` | ``synthetic`` | ``npz:<path>`` | ``delayed:<seconds>`` (synthetic after a delay,
    a test aid) | ``ros`` (live ``/camera/*``, needs ROS 2 and the ``robot_vision_ros`` package;
    fixed workspace camera only)."""
    if spec in ('', 'none'):
        return NoFrames()
    if spec == 'synthetic':
        return SyntheticFrameSource()
    if spec.startswith('delayed:'):
        return DelayedFrames(float(spec[8:]))
    if spec.startswith('npz:'):
        return NpzFrameSource(spec[4:])
    if spec == 'ros':
        try:
            from robot_vision_ros.ros_frame_source import RosFrameSource
        except ImportError as exc:
            raise ImportError("frames='ros' için ROS 2 ortamı ve robot_vision_ros gerekli "
                              '(source install/setup.bash)') from exc
        return RosFrameSource()
    raise ValueError(f'bilinmeyen kare kaynağı {spec!r}; none | synthetic | npz:<yol> | ros')
