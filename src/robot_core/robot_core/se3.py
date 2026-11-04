"""SE(3) helpers and the ONE orientation converter (pure numpy, no ROS).

Inside: rotation matrices and unit quaternions in ``xyzw`` order (ROS/tf order). API units are SI
(metres, radians). On screen (FANUC): W-P-R in degrees, **fixed-axis XYZ**::

    R = Rz(R) @ Ry(P) @ Rx(W)     (rotate about fixed X by W, then fixed Y by P, then fixed Z by R)

which is the URDF ``rpy`` convention with roll = W, pitch = P, yaw = R. Euler *rates* do not exist
anywhere: rotation jog is an angular-velocity vector (see ``robot_core.jog``).

At P = +-90 deg W and R are not independent (gimbal lock): ``wpr_from_matrix`` then returns R = 0
and puts the whole rotation into W, and the display layer warns when |P| is within
``GIMBAL_WARN_DEG`` (a suggestion, tune it) of 90 deg.
"""
from __future__ import annotations

import math
from typing import Dict, NamedTuple, Optional, Sequence, Tuple

import numpy as np

GIMBAL_WARN_DEG = 10.0       # SUGGESTION: warn when |P| is within this many degrees of 90
_GIMBAL_EPS = 1e-9           # cos(P) below this: treated as exact lock when extracting W-P-R

Matrix = np.ndarray          # 3x3 rotation or 4x4 transform
Vector = np.ndarray


class Wpr(NamedTuple):
    w: float                 # rad, about fixed X
    p: float                 # rad, about fixed Y, in [-pi/2, pi/2]
    r: float                 # rad, about fixed Z
    near_gimbal: bool        # |P| within the warning margin of +-90 deg


# ---- basic rotations --------------------------------------------------------------------------
def rot_x(a: float) -> Matrix:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(a: float) -> Matrix:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_z(a: float) -> Matrix:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def skew(v: Sequence[float]) -> Matrix:
    x, y, z = v
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def is_rotation(r: Matrix, tol: float = 1e-9) -> bool:
    """R^T R = I and det = +1 (a proper rotation, no mirroring)."""
    r = np.asarray(r, float)
    return (r.shape == (3, 3) and bool(np.all(np.isfinite(r)))
            and np.allclose(r.T @ r, np.eye(3), atol=tol) and abs(np.linalg.det(r) - 1) <= tol)


def make_transform(rotation: Matrix, translation: Sequence[float]) -> Matrix:
    t = np.eye(4)
    t[:3, :3] = rotation
    t[:3, 3] = translation
    return t


def invert_transform(t: Matrix) -> Matrix:
    out = np.eye(4)
    out[:3, :3] = t[:3, :3].T
    out[:3, 3] = -t[:3, :3].T @ t[:3, 3]
    return out


# ---- axis-angle / quaternion ------------------------------------------------------------------
def matrix_from_rotvec(v: Sequence[float]) -> Matrix:
    """Rodrigues: rotation by |v| about v/|v|."""
    v = np.asarray(v, float)
    angle = float(np.linalg.norm(v))
    if angle < 1e-12:
        return np.eye(3) + skew(v)
    k = skew(v / angle)
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)


def quat_normalize(q: Sequence[float]) -> Vector:
    q = np.asarray(q, float)
    n = float(np.linalg.norm(q))
    if not n > 0 or not math.isfinite(n):
        raise ValueError('kuaterniyon sıfır ya da sonlu değil')
    return q / n


def quat_multiply(a: Sequence[float], b: Sequence[float]) -> Vector:
    """Hamilton product a*b, both ``xyzw``."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz])


def quat_conjugate(q: Sequence[float]) -> Vector:
    x, y, z, w = q
    return np.array([-x, -y, -z, w])


def quat_from_matrix(r: Matrix) -> Vector:
    """Unit quaternion ``xyzw`` with w >= 0 (the canonical one of the +-q pair)."""
    r = np.asarray(r, float)
    tr = r[0, 0] + r[1, 1] + r[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, s / 4]
    elif r[0, 0] >= r[1, 1] and r[0, 0] >= r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [s / 4, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] >= r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, s / 4, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, s / 4, (r[1, 0] - r[0, 1]) / s]
    q = quat_normalize(q)
    return -q if q[3] < 0 else q


def matrix_from_quat(q: Sequence[float]) -> Matrix:
    x, y, z, w = quat_normalize(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def rotvec_from_matrix(r: Matrix) -> Vector:
    """Axis-angle vector (angle in [0, pi]); stable near 0 and near pi."""
    q = quat_from_matrix(r)
    s = float(np.linalg.norm(q[:3]))
    if s < 1e-12:
        return 2 * q[:3]
    return q[:3] * (2 * math.atan2(s, q[3]) / s)


def quat_angle(a: Sequence[float], b: Sequence[float]) -> float:
    """Smallest rotation angle (rad) between two orientations."""
    d = abs(float(np.dot(quat_normalize(a), quat_normalize(b))))
    return 2 * math.acos(min(1.0, d))


def pose_error(target: Matrix, current: Matrix) -> Vector:
    """6-vector (position difference, rotation vector) in the BASE frame that moves ``current`` to
    ``target`` for small errors: ``[p_t - p_c, rotvec(R_t R_c^T)]``."""
    return np.concatenate([target[:3, 3] - current[:3, 3],
                           rotvec_from_matrix(target[:3, :3] @ current[:3, :3].T)])


# ---- FANUC W-P-R ------------------------------------------------------------------------------
def matrix_from_wpr(w: float, p: float, r: float) -> Matrix:
    """R = Rz(r) Ry(p) Rx(w); radians (SI)."""
    return rot_z(r) @ rot_y(p) @ rot_x(w)


def wpr_from_matrix(rot: Matrix, margin_deg: float = GIMBAL_WARN_DEG) -> Wpr:
    """Inverse of ``matrix_from_wpr`` with P in [-pi/2, pi/2]. At exact lock R is set to 0."""
    rot = np.asarray(rot, float)
    p = math.asin(max(-1.0, min(1.0, -rot[2, 0])))
    if math.cos(p) > _GIMBAL_EPS:
        w = math.atan2(rot[2, 1], rot[2, 2])
        r = math.atan2(rot[1, 0], rot[0, 0])
    else:
        w, r = math.atan2(-rot[1, 2], rot[1, 1]), 0.0
    return Wpr(w, p, r, abs(math.degrees(p)) >= 90.0 - margin_deg)


def display_wpr(rot: Matrix, margin_deg: float = GIMBAL_WARN_DEG) -> Dict[str, object]:
    """Screen form: degrees and, near P = +-90 deg, a warning (W and R become ill-defined)."""
    w = wpr_from_matrix(rot, margin_deg)
    warning: Optional[str] = None
    if w.near_gimbal:
        warning = (f'P={math.degrees(w.p):.1f}°, ±90°a {margin_deg:g}° ya da daha yakın: W ve R '
                   'ayrışmaz (gösterim belirsiz; hareket etkilenmez)')
    return {'w': math.degrees(w.w), 'p': math.degrees(w.p), 'r': math.degrees(w.r),
            'warning': warning}


def matrix_from_wpr_display(w_deg: float, p_deg: float, r_deg: float) -> Matrix:
    """Screen (degrees) -> rotation matrix: the single entry point for typed-in W-P-R."""
    return matrix_from_wpr(math.radians(w_deg), math.radians(p_deg), math.radians(r_deg))


def as_vec3(value: Sequence[float], what: str) -> Tuple[float, float, float]:
    """Finite 3-vector or ValueError naming ``what``."""
    try:
        x, y, z = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{what}: 3 sayı olmalı, bulunan: {value!r}') from exc
    if not all(math.isfinite(v) for v in (x, y, z)):
        raise ValueError(f'{what}: sonlu olmayan sayı: {value!r}')
    return x, y, z
