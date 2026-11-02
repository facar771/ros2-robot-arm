"""What a plugin sees: parameters, a logger, a stop token, and permission-checked capabilities."""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, FrozenSet, Optional

from robot_vision.frame import Frame

from robot_plugins.frames import FrameSource
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


class CameraView:
    def __init__(self, source: FrameSource, allowed: bool):
        self._source, self._allowed = source, allowed

    def latest_frame(self) -> Optional[Frame]:
        if not self._allowed:
            raise PermissionDenied(
                "camera:read izni yok (package.yaml'da 'permissions' içine ekle)")
        return self._source.latest()


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
                 stop: Optional[StopToken] = None):
        self.name = name
        self.params = dict(params)
        self.permissions = permissions
        self.stop = stop or StopToken()
        self.camera = CameraView(frames, 'camera:read' in permissions)
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
