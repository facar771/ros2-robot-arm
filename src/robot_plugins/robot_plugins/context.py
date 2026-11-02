"""What a plugin sees: parameters, a logger, a stop token, and permission-checked capabilities."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, FrozenSet, Optional

from robot_vision.frame import Frame
from robot_vision.scan import Panner

from robot_plugins.frames import FrameSource
from robot_plugins.panning import NoPanner
from robot_plugins.robot_api import RobotApi


class PermissionDenied(Exception):
    """The plugin's manifest does not list the permission this call needs."""


class Stopped(Exception):
    """Raised by :meth:`StopToken.check` once the manager asked the plugin to stop."""


class StopToken:
    """Set by the manager on disable, on emergency stop, or when the robot reports an e-stop.
    Long-running tools must poll it (``is_set`` / ``wait`` / ``check``) at least every ~0.5 s."""

    def __init__(self):
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Sleep up to ``timeout`` s; returns True as soon as the token is set."""
        return self._event.wait(timeout)

    def check(self) -> None:
        if self._event.is_set():
            raise Stopped()


class PanView(Panner):
    """The camera turning mechanism as a plugin sees it (``camera:pan``); implements
    ``robot_vision.scan.Panner`` so it can be handed straight to ``scan()``."""

    def __init__(self, panner: Panner, allowed: bool):
        self._panner, self._allowed = panner, allowed

    def _need(self) -> None:
        if not self._allowed:
            raise PermissionDenied(
                "camera:pan izni yok (package.yaml'da 'permissions' içine ekle)")

    @property
    def available(self) -> bool:
        """Permission granted AND the environment has a camera that can turn."""
        return self._allowed and not isinstance(self._panner, NoPanner)

    @property
    def limits(self):
        self._need()
        return self._panner.limits

    def move_to(self, yaw: float) -> None:
        self._need()
        self._panner.move_to(yaw)

    def frame(self):
        self._need()
        return self._panner.frame()


class CameraView:
    def __init__(self, source: FrameSource, allowed: bool, panner: Optional[Panner] = None,
                 pan_allowed: bool = False, default_wait_s: float = 0.0,
                 stop: Optional['StopToken'] = None):
        self._source, self._allowed = source, allowed
        self._default_wait, self._stop = default_wait_s, stop
        self.pan = PanView(panner or NoPanner(), pan_allowed)

    def latest_frame(self, wait_s: Optional[float] = None) -> Optional[Frame]:
        """The newest frame. A live camera has no frame yet right after the plugin process starts
        (first message still in flight), so this WAITS up to ``wait_s`` seconds for one (default:
        the context's ``frame_wait_s``, 5 s under the runner) and returns None only if none came.
        ``wait_s=0`` never waits (use it inside loops that handle None themselves). A set stop
        token ends the wait at once."""
        if not self._allowed:
            raise PermissionDenied(
                "camera:read izni yok (package.yaml'da 'permissions' içine ekle)")
        wait = self._default_wait if wait_s is None else wait_s
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            frame = self._source.latest()
            if frame is not None or time.monotonic() >= deadline:
                return frame
            if self._stop is not None and self._stop.wait(0.05):
                return None
            if self._stop is None:
                time.sleep(0.05)


class RobotView:
    """The robot as a plugin may use it. ``stop`` always works; the rest needs ``robot:jobs``.
    There is deliberately no ``reset``: unlatching an emergency stop is a human decision."""

    def __init__(self, api: RobotApi, jobs_allowed: bool):
        self._api, self._jobs = api, jobs_allowed

    def _need_jobs(self) -> None:
        if not self._jobs:
            raise PermissionDenied("robot:jobs izni yok (package.yaml'da 'permissions' içine ekle)")

    def stop(self, reason: str = 'plugin') -> Dict[str, Any]:
        return self._api.stop(reason)

    def state(self) -> Dict[str, Any]:
        self._need_jobs()
        return self._api.state()

    def submit_job(self, command: str, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        self._need_jobs()
        return self._api.submit_job(command, timeout_s)

    def job(self, job_id: str) -> Dict[str, Any]:
        self._need_jobs()
        return self._api.job(job_id)

    def cancel(self, job_id: str) -> Dict[str, Any]:
        self._need_jobs()
        return self._api.cancel(job_id)

    def wait_for_job(self, job_id: str, stop_token: Optional[StopToken] = None,
                     timeout: float = 120.0) -> Dict[str, Any]:
        self._need_jobs()
        from robot_plugins.robot_api import wait_for_job
        return wait_for_job(self._api, job_id, stop_token=stop_token, timeout=timeout)


class PluginContext:
    def __init__(self, name: str, params: Dict[str, Any], permissions: FrozenSet[str],
                 frames: FrameSource, robot: RobotApi, log: Callable[[str, str], None],
                 stop: Optional[StopToken] = None, panner: Optional[Panner] = None,
                 frame_wait_s: float = 0.0):
        self.name = name
        self.params = dict(params)
        self.permissions = permissions
        self.stop = stop or StopToken()
        self.camera = CameraView(frames, 'camera:read' in permissions, panner,
                                 'camera:pan' in permissions, frame_wait_s, self.stop)
        self.robot = RobotView(robot, 'robot:jobs' in permissions)
        self._log = log

    def log(self, message: str, level: str = 'info') -> None:
        self._log(level, str(message))


class PluginBase:
    """Subclass this in ``entry`` (``main:Plugin``). Override :meth:`call`; ``start`` / ``shutdown``
    are optional. Tool results must be JSON-able dicts. Never raise for expected failures: return
    ``{"ok": False, "error": "..."}`` so the caller (an LLM) gets a message it can act on."""

    def __init__(self, ctx: PluginContext):
        self.ctx = ctx

    def start(self) -> None:
        """Called once after the process is up."""

    def shutdown(self) -> None:
        """Called once before the process exits (after the stop token is set)."""

    def call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
