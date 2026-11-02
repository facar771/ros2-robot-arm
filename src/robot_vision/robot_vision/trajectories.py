"""Deterministic target trajectories (functions of time only, so the Gazebo run and the unit
simulation move the target identically). Positions are world metres; ``None`` = target removed
from the scene (blackout).

The platform pivot is at (0, 0, ``PIVOT_HEIGHT``); targets live 3-4 m ahead (+x), +-1.2 m sideways
and 0.7-1.3 m high, so the camera has to turn, tilt AND slide to reach the set distance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

Vec = Tuple[float, float, float]

PIVOT_HEIGHT = 1.0
HIDDEN: Vec = (50.0, 50.0, -5.0)       # where a 'removed' target is parked (far, below the ground)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    duration: float                      # s of tracking after the target starts to move
    position: Callable[[float], Optional[Vec]]
    max_loss_events: int                 # how many separate losses are *expected*
    min_hold_ratio: float                # pass threshold: share of time locked after first lock
    max_acquisition: float               # pass threshold (s)
    disturbance: Optional[float] = None  # s: abrupt turn / blackout end (lock expected to recover)
    max_relock: float = 9.5              # pass threshold: 4.5 s lock timer + 5 s recovery


def _linear(t: float) -> Vec:
    return (3.5, -0.8 + 0.25 * t, 1.0)


def _sine(t: float) -> Vec:
    return (3.5 + 0.4 * math.sin(2 * math.pi * t / 7.0),
            1.0 * math.sin(2 * math.pi * t / 9.0),
            1.0 + 0.25 * math.sin(2 * math.pi * t / 5.0))


def _abrupt(t: float) -> Vec:
    turn = 10.0                          # s: velocity flips from +0.35 to -0.35 m/s instantly
    y = -0.5 + 0.35 * t if t < turn else -0.5 + 0.35 * turn - 0.35 * (t - turn)
    return (3.5, y, 1.0 + (0.0 if t < turn else 0.05 * (t - turn)))


def _blackout(t: float) -> Optional[Vec]:
    if 9.0 <= t < 10.0:                  # exactly 1 s without a target
        return None
    return (3.5, -0.6 + 0.2 * t, 1.0)


SCENARIOS: Dict[str, Scenario] = {s.name: s for s in (
    Scenario('linear', 'Düz çizgide sabit hız (0.25 m/s)', 16.0, _linear, 0, 0.9, 10.0),
    Scenario('sine', 'Sinüs yörünge (y, x ve z birlikte salınır)', 20.0, _sine, 0, 0.8, 10.0),
    Scenario('abrupt', 'Ani yön değişimi (t=10 s, 0.35 → -0.35 m/s)', 24.0, _abrupt, 0, 0.3, 10.0,
             disturbance=10.0),
    Scenario('blackout', 'Hedef 1 s kaybolur (t=9-10 s) ve geri gelir', 22.0, _blackout, 1, 0.3,
             10.0, disturbance=10.0),
)}
