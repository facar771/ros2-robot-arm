"""Shared helpers for the robot_vision tests (not a test module)."""
from pathlib import Path

from robot_core.workspace import Shape, load_workspace

SRC = Path(__file__).resolve().parents[2]
WORLD = SRC / 'robot_sim' / 'worlds' / 'workspace.sdf'


def workspace():
    return load_workspace(WORLD)


def shapes_by_name():
    return {s.name: s for s in workspace().shapes}


def tall_box(name='tall_box', center=(0.40, 0.05, 0.025), dims=(0.08, 0.04, 0.05)) -> Shape:
    """A non-square red box (not in the world file) to check yaw recovery."""
    return Shape(name, 'box', center, dims, (0.9, 0.1, 0.1))
