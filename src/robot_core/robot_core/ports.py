"""Interfaces (ports) the core needs from the outside world.

``robot_controller.adapters`` implements them with ROS 2; tests implement them with fakes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from robot_core.models import BoxObstacle, JointTarget, MotionResult


class MotionPort(Protocol):
    def move_to(self, target: JointTarget) -> MotionResult: ...


class ScenePort(Protocol):
    def add_box(self, obstacle: BoxObstacle) -> bool: ...

    def remove_box(self, name: str) -> bool: ...


class StatePort(Protocol):
    def joint_positions(self) -> Optional[Dict[str, float]]:
        """Latest joint positions by name, or None if nothing was received yet."""


class EventSink(Protocol):
    """Receives structured events (later: database writer). Must never raise."""

    def emit(self, kind: str, payload: Dict[str, Any]) -> None: ...


class NullEventSink:
    def emit(self, kind: str, payload: Dict[str, Any]) -> None:
        return None
