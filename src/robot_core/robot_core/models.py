"""Plain data types shared by all layers (immutable, no behaviour)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class JointLimit:
    name: str
    lower: float
    upper: float
    velocity: Optional[float] = None


@dataclass(frozen=True)
class JointTarget:
    """A joint-space goal. ``label`` is the pose name when it came from a named pose."""
    values: Tuple[float, ...]
    label: Optional[str] = None


@dataclass(frozen=True)
class MotionResult:
    """Outcome of a plan+execute request, in a form that can be stored later (DB, logs)."""
    ok: bool
    code: Optional[int] = None
    code_name: str = ''
    message: str = ''


@dataclass(frozen=True)
class BoxObstacle:
    name: str
    x: float
    y: float
    z: float
    size_x: float
    size_y: float
    size_z: float
    frame: str = 'base_link'


@dataclass(frozen=True)
class PoseTarget:
    """Cartesian goal for the tool link (``RobotConfig.tip_link``).

    ``orientation`` is a quaternion (x, y, z, w) in ``frame``; None = position only. Roll and pitch
    of the tool may deviate by ``orientation_tolerance`` rad; rotation about the tool axis is free
    (a six-axis arm with a symmetric tool does not care).
    """
    x: float
    y: float
    z: float
    frame: str = 'world'
    orientation: Optional[Tuple[float, float, float, float]] = None
    position_tolerance: float = 0.005
    orientation_tolerance: float = 0.15
    label: Optional[str] = None


@dataclass(frozen=True)
class ObjectInfo:
    """An object the robot knows about (from perception, via the planning scene)."""
    name: str                                   # stable id, e.g. 'obj_red_1'
    label: str                                  # colour class ('red'), '' for hand-made obstacles
    position: Tuple[float, float, float]        # centre (m, ``frame``)
    size: Tuple[float, float, float]            # bounding box (m)
    frame: str = 'world'

    @property
    def top_z(self) -> float:
        return self.position[2] + self.size[2] / 2.0


@dataclass(frozen=True)
class SkillResult:
    """What a skill returns: always a value, never an exception (an LLM tool must get an answer)."""
    ok: bool
    skill: str
    message: str = ''           # human/LLM readable: what happened or why it failed
    code_name: str = ''         # MoveIt error code name when the motion layer was involved
    data: Dict[str, Any] = field(default_factory=dict)
