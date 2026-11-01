"""Planar reachability model of the arm (tool pointing down) vs. the objects in workspace.sdf.

Approximation of robot.urdf (joint_1 swings the arm towards the target, so the problem is planar):
shoulder 0.24 m above the world, 0.35 m upper arm, 0.50 m forearm (joint_3 -> joint_5), 0.15 m
wrist to tip. It reproduces what the Gazebo checks (pose_check) measured: green 0.25 and red 0.30
are reachable, red 0.25 is not, and an object at x=0.30 (the old blue ball) has no clearance at all.

Reachable is not enough: ``red 0.30`` sits on the edge of the window (joint_3 within ~0.01 rad of
its limit). In Gazebo the arm then overshoots the limit slightly and MoveIt rejects the *next* plan
with START_STATE_INVALID (seen in 1 of 3 runs). So the defaults the UI and the demo use must keep a
margin to the joint limits. Guards against moving an object (or a joint limit) so that a button
silently becomes unreachable or flaky.
"""
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pytest
from robot_core.workspace import load_workspace

SHOULDER_Z, L1, L2, L3 = 0.24, 0.35, 0.50, 0.15
Q2, Q3, Q5 = 1.57, 2.0, 1.57
TILT = 0.15            # rad, orientation tolerance used by the planner
MIN_MARGIN = 0.04      # rad of slack to the nearest joint limit for the defaults
UI_DEFAULT, DEMO_RED, DEMO_GREEN = 0.35, 0.35, 0.25
WORLD = Path(__file__).resolve().parents[2] / 'robot_sim' / 'worlds' / 'workspace.sdf'


def tool_down_slack(r: float, z: float, tilt: float = TILT) -> Optional[float]:
    """Best slack (rad) to the nearest joint limit over all tool-down solutions with the tool tilted
    by at most ``tilt``; None when no solution exists inside the limits."""
    best = None
    for s in np.linspace(-tilt, tilt, 31):
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
                slack = min(Q2 - abs(q2), Q3 - abs(q3), Q5 - abs(q5))
                if slack >= 0 and (best is None or slack > best):
                    best = slack
    return best


def reachable(r: float, z: float) -> bool:
    return tool_down_slack(r, z) is not None


def window(r: float, top: float):
    heights = [h for h in np.arange(0.0, 0.5001, 0.025) if reachable(r, top + h)]
    return (min(heights), max(heights)) if heights else None


def objects():
    ws = load_workspace(str(WORLD))
    assert ws.shapes
    return {s.name.split('_')[0]: (math.hypot(s.center[0], s.center[1]),
                                   s.center[2] + s.height / 2) for s in ws.shapes}


def test_model_matches_measured_gazebo_results():
    assert reachable(math.hypot(0.40, -0.10), 0.10 + 0.25)         # green 0.25: measured ok
    assert reachable(math.hypot(0.35, 0.15), 0.06 + 0.30)          # red 0.30: measured ok (edge)
    assert not reachable(math.hypot(0.35, 0.15), 0.06 + 0.25)      # red 0.25: refused
    assert window(0.30, 0.08) is None                              # old blue ball: never


def test_red_030_is_on_the_edge_which_is_why_it_is_not_a_default():
    r, top = objects()['red']
    assert tool_down_slack(r, top + 0.30) < MIN_MARGIN             # the flaky one
    assert tool_down_slack(r, top + 0.35) >= MIN_MARGIN            # the default


@pytest.mark.parametrize('color, clearance', [
    ('red', DEMO_RED), ('green', DEMO_GREEN), ('green', UI_DEFAULT), ('blue', UI_DEFAULT)])
def test_defaults_keep_a_margin_to_the_joint_limits(color, clearance):
    r, top = objects()[color]
    slack = tool_down_slack(r, top + clearance)
    assert slack is not None and slack >= MIN_MARGIN, (color, clearance, slack)


def test_every_world_object_is_reachable_at_the_ui_default():
    for color, (r, top) in objects().items():
        assert reachable(r, top + UI_DEFAULT), color
