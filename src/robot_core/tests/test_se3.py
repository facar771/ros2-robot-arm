"""SE(3) helpers and the single W-P-R converter (FANUC: fixed-axis XYZ, R = Rz(R) Ry(P) Rx(W))."""
import math

import numpy as np
import pytest
from robot_core.se3 import (
    display_wpr,
    is_rotation,
    matrix_from_quat,
    matrix_from_rotvec,
    matrix_from_wpr,
    matrix_from_wpr_display,
    pose_error,
    quat_angle,
    quat_conjugate,
    quat_from_matrix,
    quat_multiply,
    rot_x,
    rot_y,
    rot_z,
    rotvec_from_matrix,
    wpr_from_matrix,
)

RNG = np.random.default_rng(7)


def random_rotation():
    return matrix_from_rotvec(RNG.normal(size=3) * 1.3)


def test_wpr_convention_is_fixed_axis_xyz():
    # rotate about FIXED X by W, then FIXED Y by P, then FIXED Z by R  ==  Rz(R) Ry(P) Rx(W)
    w, p, r = 0.3, -0.7, 1.1
    v = np.array([0.2, -0.5, 0.9])
    stepwise = rot_z(r) @ (rot_y(p) @ (rot_x(w) @ v))
    assert matrix_from_wpr(w, p, r) @ v == pytest.approx(stepwise)
    # concrete case: W=90 puts +Y on +Z; then R=90 turns +X into +Y (X is untouched by Rx)
    rot = matrix_from_wpr(math.radians(90), 0.0, math.radians(90))
    assert rot @ [1, 0, 0] == pytest.approx([0, 1, 0], abs=1e-12)
    assert rot @ [0, 1, 0] == pytest.approx([0, 0, 1], abs=1e-12)
    # same as URDF rpy (roll=W, pitch=P, yaw=R): rpy composes exactly Rz(yaw) Ry(pitch) Rx(roll)
    assert np.allclose(matrix_from_wpr(0.1, 0.2, 0.3), rot_z(0.3) @ rot_y(0.2) @ rot_x(0.1))


@pytest.mark.parametrize('trial', range(200))
def test_wpr_matrix_round_trip(trial):
    w, r = RNG.uniform(-math.pi, math.pi, 2)
    p = RNG.uniform(-math.radians(89.5), math.radians(89.5))
    rot = matrix_from_wpr(w, p, r)
    assert is_rotation(rot)
    back = wpr_from_matrix(rot)
    assert (back.w, back.p, back.r) == pytest.approx((w, p, r), abs=1e-7)
    assert np.allclose(matrix_from_wpr(back.w, back.p, back.r), rot, atol=1e-12)


@pytest.mark.parametrize('sign', [1, -1])
@pytest.mark.parametrize('w, r', [(0.4, 0.9), (-2.0, 1.0), (0.0, 0.0)])
def test_gimbal_lock_still_round_trips_as_a_matrix(sign, w, r):
    rot = matrix_from_wpr(w, sign * math.pi / 2, r)
    back = wpr_from_matrix(rot)
    assert back.r == 0.0 and abs(back.p) == pytest.approx(math.pi / 2)    # R folded into W
    assert np.allclose(matrix_from_wpr(back.w, back.p, back.r), rot, atol=1e-9)
    assert back.near_gimbal


def test_gimbal_warning_only_near_plus_minus_90():
    assert display_wpr(matrix_from_wpr_display(10, 60, 20))['warning'] is None
    assert display_wpr(matrix_from_wpr_display(10, 79, 20))['warning'] is None
    assert display_wpr(matrix_from_wpr_display(10, 81, 20))['warning'] is not None
    assert display_wpr(matrix_from_wpr_display(10, -89.9, 20))['warning'] is not None
    shown = display_wpr(matrix_from_wpr_display(10, 30, 20))
    assert (shown['w'], shown['p'], shown['r']) == pytest.approx((10, 30, 20))   # degrees on screen
    assert display_wpr(matrix_from_wpr_display(0, 82, 0), margin_deg=5)['warning'] is None


def test_quaternion_round_trip_and_canonical_sign():
    for _ in range(200):
        rot = random_rotation()
        q = quat_from_matrix(rot)
        assert q[3] >= 0 and np.linalg.norm(q) == pytest.approx(1.0)
        assert np.allclose(matrix_from_quat(q), rot, atol=1e-12)
        assert np.allclose(matrix_from_quat(-q), rot, atol=1e-12)           # +-q is the same turn
    for rot in (np.eye(3), rot_x(math.pi), rot_y(math.pi), rot_z(math.pi)):  # every branch
        assert np.allclose(matrix_from_quat(quat_from_matrix(rot)), rot, atol=1e-12)


def test_quaternion_product_equals_matrix_product():
    for _ in range(50):
        a, b = random_rotation(), random_rotation()
        q = quat_multiply(quat_from_matrix(a), quat_from_matrix(b))
        assert np.allclose(matrix_from_quat(q), a @ b, atol=1e-12)
        assert quat_angle(q, quat_from_matrix(a @ b)) == pytest.approx(0, abs=1e-6)
        ident = quat_multiply(quat_from_matrix(a), quat_conjugate(quat_from_matrix(a)))
        assert ident == pytest.approx([0, 0, 0, 1], abs=1e-12)


def test_rotvec_round_trip_including_near_pi():
    for angle in (1e-9, 1e-4, 0.5, 2.0, math.pi - 1e-6):
        axis = RNG.normal(size=3)
        axis /= np.linalg.norm(axis)
        rot = matrix_from_rotvec(axis * angle)
        assert rotvec_from_matrix(rot) == pytest.approx(axis * angle, abs=1e-6)


def test_is_rotation_rejects_mirrors_and_scaling():
    assert is_rotation(np.eye(3))
    assert not is_rotation(np.diag([1, 1, -1]))                # det = -1
    assert not is_rotation(np.diag([1, 1, 1.01]))
    assert not is_rotation(np.full((3, 3), np.nan))


def test_pose_error_moves_current_to_target():
    cur = np.eye(4)
    tgt = np.eye(4)
    tgt[:3, :3] = matrix_from_rotvec([0.0, 0.0, 0.2])
    tgt[:3, 3] = [0.01, -0.02, 0.03]
    err = pose_error(tgt, cur)
    assert err == pytest.approx([0.01, -0.02, 0.03, 0, 0, 0.2], abs=1e-12)
    assert pose_error(cur, cur) == pytest.approx(np.zeros(6))

