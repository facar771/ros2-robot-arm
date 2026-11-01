"""Planar reachability model of the arm (tool pointing down) vs. the objects in workspace.sdf.

Approximation of robot.urdf (joint_1 swings the arm towards the target, so the problem is planar):
shoulder 0.24 m above the world, 0.35 m upper arm, 0.50 m forearm (joint_3 -> joint_5), 0.15 m
wrist to tip. It reproduces what the Gazebo checks (pose_check) measured: green 0.25 and red 0.30
are reachable, red 0.25 is not, and an object at x=0.30 (the old blue ball) has no clearance at all.
Guards against moving an object (or a joint limit) so that a button silently becomes unreachable.
"""
import math
from pathlib import Path

import numpy as np
import pytest
from robot_core.workspace import load_workspace

SHOULDER_Z, L1, L2, L3 = 0.24, 0.35, 0.50, 0.15
Q2, Q3, Q5 = 1.57, 2.0, 1.57
TILT = 0.15            # rad, orientation tolerance used by the planner
WORLD = Path(__file__).resolve().parents[2] / 'robot_sim' / 'worlds' / 'workspace.sdf'


def tool_down_reachable(r: float, z: float, tilt: float = TILT) -> bool:
    for s in np.linspace(-tilt, tilt, 9):
        a = math.pi + s
        dx, dz = r - L3 * math.sin(a), z - L3 * math.cos(a) - SHOULDER_Z
        c = (dx * dx + dz * dz - L1 * L1 - L2 * L2) / (2 * L1 * L2)
        if abs(c) > 1:
            continue
        for sign in (1, -1):
            q3 = sign * math.acos(c)
            q2 = math.atan2(dx, dz) - math.atan2(L2 * math.sin(q3), L1 + L2 * math.cos(q3))
            for flip in (1, -1):
                q5 = (flip * (a - q2 - q3) + math.pi) % (2 * math.pi) - math.pi
                if abs(q2) <= Q2 and abs(q3) <= Q3 and abs(q5) <= Q5:
                    return True
    return False


def window(r: float, top: float):
    heights = [h for h in np.arange(0.0, 0.5001, 0.025) if tool_down_reachable(r, top + h)]
    return (min(heights), max(heights)) if heights else None


def test_model_matches_measured_gazebo_results():
    assert tool_down_reachable(math.hypot(0.40, -0.10), 0.10 + 0.25)     # green 0.25: measured ok
    assert tool_down_reachable(math.hypot(0.35, 0.15), 0.06 + 0.30)      # red 0.30: measured ok
    assert not tool_down_reachable(math.hypot(0.35, 0.15), 0.06 + 0.25)  # red 0.25: refused
    assert window(0.30, 0.08) is None                                    # old blue ball: never


@pytest.mark.parametrize('clearance', [0.20, 0.30])
def test_every_world_object_is_reachable_at_default_clearances(clearance):
    ws = load_workspace(str(WORLD))
    assert ws.shapes
    for shape in ws.shapes:
        r = math.hypot(shape.center[0], shape.center[1])
        top = shape.center[2] + shape.height / 2
        if clearance == 0.20 and shape.name == 'red_box':
            continue                       # red needs >= 0.30 (documented window)
        assert tool_down_reachable(r, top + clearance), (shape.name, r, top, clearance)


def test_ui_default_height_is_reachable_for_every_object():
    ws = load_workspace(str(WORLD))
    for shape in ws.shapes:
        r = math.hypot(shape.center[0], shape.center[1])
        assert tool_down_reachable(r, shape.center[2] + shape.height / 2 + 0.30), shape.name
