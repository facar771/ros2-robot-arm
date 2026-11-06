"""Interfaces (ports) the core needs from the outside world.

``robot_controller.adapters`` implements them with ROS 2; tests implement them with fakes.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Protocol, Sequence, Tuple

from robot_core.models import (
    BoxObstacle,
    JointTarget,
    MotionResult,
    ObjectInfo,
    PlanResult,
    PoseTarget,
)


class MotionPort(Protocol):
    def move_to(self, target: JointTarget) -> MotionResult: ...


class PosePort(Protocol):
    def move_to_pose(self, target: PoseTarget) -> MotionResult:
        """Plan + execute a Cartesian goal for the tool link (inverse kinematics inside MoveIt)."""


class PlanningPort(Protocol):
    """Plan and execute as two separate steps (F4b), so the planned path can be checked against
    the safe box BEFORE anything moves."""

    def plan_joint(self, target: JointTarget) -> PlanResult: ...

    def plan_pose(self, target: PoseTarget) -> PlanResult: ...

    def execute_plan(self, plan: PlanResult) -> MotionResult:
        """Execute exactly the trajectory that ``plan_*`` returned (no replanning)."""

    def execute_timed(self, times: Sequence[float], positions: Sequence[Sequence[float]],
                      velocities: Sequence[Sequence[float]]) -> MotionResult:
        """Execute a time-parameterised joint trajectory (chain order) as given (park transit)."""

    def states_valid(self, states: Sequence[Sequence[float]]) -> Optional[str]:
        """Collision check of joint states against the current planning scene: None when all are
        valid, otherwise why not. Must fail closed (an unreachable service is an answer too)."""


class ObjectsPort(Protocol):
    def objects(self) -> Optional[Mapping[str, ObjectInfo]]:
        """Objects currently known (name -> info); None if the scene could not be read."""


class ScenePort(Protocol):
    def add_box(self, obstacle: BoxObstacle) -> bool: ...

    def remove_box(self, name: str) -> bool: ...


class StatePort(Protocol):
    def joint_positions(self) -> Optional[Dict[str, float]]:
        """Latest joint positions by name, or None if nothing was received yet."""


class ToolPositionPort(Protocol):
    def tool_position(self) -> Optional[Tuple[float, float, float]]:
        """Measured tool position in the world frame (from TF), or None if it cannot be read."""


class EventSink(Protocol):
    """Receives structured events (later: database writer). Must never raise."""

    def emit(self, kind: str, payload: Dict[str, Any]) -> None: ...


class NullEventSink:
    def emit(self, kind: str, payload: Dict[str, Any]) -> None:
        return None
