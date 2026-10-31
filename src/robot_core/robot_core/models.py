"""Plain data types shared by all layers (immutable, no behaviour)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


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
