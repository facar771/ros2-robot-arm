"""The 3-DoF camera platform (yaw, forward slide, pitch): forward kinematics and a simple actuator
model for the closed-loop simulation. The SDF in ``robot_sim/worlds/tracking.sdf`` defines the same
chain; ``tests`` check that the two agree.

Chain: world -> yaw (about z at the pivot) -> slide (along the yawed x axis) -> pitch (about y)
-> camera (x forward, y left, z up). Positive pitch tilts the camera down.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional, Tuple

import numpy as np
from robot_core.perception import optical_frame_in_world
from robot_core.workspace import CameraSpec, Pose

from robot_vision.servo import Command, clamp
from robot_vision.trajectories import PIVOT_HEIGHT

Q = Tuple[float, float, float]           # (yaw rad, slide m, pitch rad)


@dataclass(frozen=True)
class PlatformLimits:
    yaw: Tuple[float, float] = (-1.2, 1.2)
    slide: Tuple[float, float] = (0.0, 2.5)
    pitch: Tuple[float, float] = (-0.6, 1.2)
    yaw_rate: float = 1.5
    slide_rate: float = 0.8
    pitch_rate: float = 1.0


HOME: Q = (0.0, 0.0, 0.1)


def camera_pose(q: Q, pivot_height: float = PIVOT_HEIGHT) -> Pose:
    yaw, slide, pitch = q
    return Pose(slide * math.cos(yaw), slide * math.sin(yaw), pivot_height, 0.0, pitch, yaw)


def with_pose(cam: CameraSpec, q: Q) -> CameraSpec:
    return replace(cam, pose=camera_pose(q))


class SimPlatform:
    """First-order velocity actuators with rate and joint limits (what the Gazebo controllers do,
    minus the physics). ``step(command, dt)`` integrates ``dt`` seconds."""

    def __init__(self, q: Q = HOME, limits: Optional[PlatformLimits] = None, lag: float = 0.05):
        self.q = q
        self.limits = limits or PlatformLimits()
        self.lag = lag
        self._v = [0.0, 0.0, 0.0]

    @property
    def rates(self) -> Command:
        """The rates the joints are really moving with (what joint_states would report)."""
        return Command(yaw_rate=self._v[0], slide_rate=self._v[1], pitch_rate=self._v[2])

    def step(self, cmd: Command, dt: float) -> Q:
        lim = self.limits
        want = (clamp(cmd.yaw_rate, -lim.yaw_rate, lim.yaw_rate),
                clamp(cmd.slide_rate, -lim.slide_rate, lim.slide_rate),
                clamp(cmd.pitch_rate, -lim.pitch_rate, lim.pitch_rate))
        a = dt / (self.lag + dt)
        self._v = [v + a * (w - v) for v, w in zip(self._v, want)]
        bounds = (lim.yaw, lim.slide, lim.pitch)
        self.q = tuple(clamp(q + v * dt, b[0], b[1]) for q, v, b in zip(self.q, self._v, bounds))
        return self.q


def optical_in_world(q: Q, cam: CameraSpec, pivot_height: float = PIVOT_HEIGHT):
    """world <- camera_optical_frame for the platform at ``q`` (a ``robot_core`` Transform)."""
    return optical_frame_in_world(with_pose(cam, q))


def world_to_optical(q: Q, cam: CameraSpec, p_world) -> np.ndarray:
    """A world point in the camera optical frame (x right, y down, z forward) for platform ``q``."""
    t = optical_in_world(q, cam)
    return t.rotation.T @ (np.asarray(p_world, dtype=np.float64) - t.translation)


def camera_velocity(q: Q, rates: Command, pivot_height: float = PIVOT_HEIGHT):
    """World velocity of the camera position (m/s) from the joint rates (yaw, slide)."""
    yaw, slide, _ = q
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([rates.slide_rate * c - slide * rates.yaw_rate * s,
                     rates.slide_rate * s + slide * rates.yaw_rate * c, 0.0])
