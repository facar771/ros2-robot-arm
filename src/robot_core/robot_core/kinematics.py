"""Forward kinematics and Jacobian of the serial revolute chain, built from the robot PROFILE.

Same chain the generated URDF has (``robot_core.profile``: each joint sits at ``origin`` in its
parent link frame, rotates about the unit ``axis`` expressed in that common orientation). The
profile has no mount transform: ``world`` == ``base_link`` today. The tool point is the profile's
``tcp`` (offset + W-P-R relative to the LAST link's frame, the flange / ``RobotConfig.tip_link``);
without a ``tcp`` section it is the flange itself. An explicit ``tcp`` argument overrides it.
MoveIt / pose goals still address the flange frame: the TCP applies to this chain (jog) only.

``jacobian`` is the geometric Jacobian in the BASE frame about the tool point: rows 0-2 linear
velocity of the point, rows 3-5 angular velocity, columns = joints.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from robot_core.profile import Profile
from robot_core.se3 import Matrix, make_transform


class Chain:
    def __init__(self, profile: Profile, tcp: Optional[Matrix] = None) -> None:
        self.joint_names: Tuple[str, ...] = profile.joint_names
        self._origins = [np.array(j.origin, float) for j in profile.joints]
        self._axes = [np.array(j.axis, float) / np.linalg.norm(j.axis) for j in profile.joints]
        self.lower = np.array([j.lower for j in profile.joints])
        self.upper = np.array([j.upper for j in profile.joints])
        self.velocity = np.array([j.velocity for j in profile.joints])
        # jog: locked (K8, e.g. a gripper): the joint stays in the chain (FK, MoveIt) but its
        # Jacobian column is zero, so the Cartesian jog (DLS) can never command it.
        self.jog_locked = np.array([j.jog == 'locked' for j in profile.joints])
        self.tcp = profile.tcp.transform if tcp is None else np.asarray(tcp, float)
        self.tip_link = profile.tip_link

    @property
    def dof(self) -> int:
        return len(self._axes)

    def _check(self, q: Sequence[float]) -> np.ndarray:
        q = np.asarray(q, float)
        if q.shape != (self.dof,) or not np.all(np.isfinite(q)):
            raise ValueError(f'{self.dof} sonlu eklem değeri bekleniyordu, bulunan: {q.tolist()}')
        return q

    def fk(self, q: Sequence[float]) -> Matrix:
        """4x4 pose of the tool point in the base frame."""
        return self.fk_and_jacobian(q)[0]

    def jacobian(self, q: Sequence[float]) -> np.ndarray:
        return self.fk_and_jacobian(q)[1]

    def fk_and_jacobian(self, q: Sequence[float]) -> Tuple[Matrix, np.ndarray]:
        q = self._check(q)
        from robot_core.se3 import matrix_from_rotvec
        rot, pos = np.eye(3), np.zeros(3)
        axes, points = [], []
        for origin, axis, angle in zip(self._origins, self._axes, q):
            pos = pos + rot @ origin            # joint frame, before its own rotation
            axes.append(rot @ axis)
            points.append(pos.copy())
            rot = rot @ matrix_from_rotvec(axis * angle)
        pose = make_transform(rot, pos) @ self.tcp
        tip = pose[:3, 3]
        jac = np.zeros((6, self.dof))
        for i, (z, p) in enumerate(zip(axes, points)):
            jac[:3, i] = np.cross(z, tip - p)
            jac[3:, i] = z
        jac[:, self.jog_locked] = 0.0
        return pose, jac
