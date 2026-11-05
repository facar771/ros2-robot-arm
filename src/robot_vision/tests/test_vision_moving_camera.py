"""H6: with the camera platform moving, a static object keeps its WORLD position.

The old live source placed every frame with the FIXED camera of ``workspace.sdf``; these tests show
that this is wrong as soon as the platform pans/tilts/slides (``test_the_fixed_pose_is_wrong...``)
and that the per-frame pose from the joint states keeps the object within 1 cm."""
import math

import numpy as np
import pytest
from robot_core.workspace import Pose, Shape, load_workspace
from robot_vision.detectors import ColorDetector
from robot_vision.frame import Frame
from robot_vision.geometry import fit_sphere, segment_points
from robot_vision.moving_camera import JointHistory, PlatformFrameBuilder
from robot_vision.platform import HOME, optical_in_world, with_pose
from robot_vision.synth import render
from robot_vision.tracking_sim import CAMERA, TARGET_COLOR

TRUTH = np.array([2.6, -0.5, 0.95])
BALL = Shape('target', 'sphere', tuple(TRUTH), (0.10,))
POSES = [HOME, (-0.5, 0.0, 0.4), (-0.3, 0.6, 0.2), (-0.15, 1.2, 0.0), (0.0, 1.8, -0.2),
         (0.2, 0.6, 0.0), (0.1, 1.2, 0.2), (-0.3, 1.8, 0.4)]
NOISE = 0.003       # 3 mm depth noise (the thresholds are tuned for sigma <= 3 mm)
TOL = 0.01          # the acceptance criterion: <= 1 cm


def snapshot(q, stamp=0.0):
    """What the simulator camera delivers at joint position q: image + depth, NO pose."""
    return render(with_pose(CAMERA, q), [BALL], colors={'target': TARGET_COLOR}, stamp=stamp,
                  depth_noise=NOISE, seed=7)


def world_center(frame: Frame):
    seg = max((s for s in ColorDetector().detect(frame) if s.label == 'red'),
              key=lambda s: s.pixels, default=None)
    if seg is None:
        return None
    pts = segment_points(frame, seg)
    return fit_sphere(pts)[0] if len(pts) >= 30 else None


def builder_for(q, stamp=0.0):
    b = PlatformFrameBuilder()
    for dt in (-0.02, -0.01, 0.0, 0.01, 0.02):               # a static platform's joint states
        b.history.add(stamp + dt, q)
    return b


@pytest.mark.parametrize('q', POSES)
def test_static_object_keeps_its_world_position_after_pan_tilt_slide(q):
    raw = snapshot(q, 1.0)
    frame = builder_for(q, 1.0).frame(raw.rgb, raw.depth, raw.intrinsics, 1.0)
    assert frame is not None
    c = world_center(frame)
    assert c is not None, f'the target is not visible from {q}'
    assert np.linalg.norm(c - TRUTH) <= TOL, (q, c)


def test_drift_between_all_poses_is_within_one_centimetre():
    centers = []
    for q in POSES:
        raw = snapshot(q, 1.0)
        f = builder_for(q, 1.0).frame(raw.rgb, raw.depth, raw.intrinsics, 1.0)
        centers.append(world_center(f))
    arr = np.array(centers)
    drift = np.linalg.norm(arr[:, None, :] - arr[None, :, :], axis=2).max()
    assert drift <= TOL, drift


def test_the_fixed_pose_is_wrong_as_soon_as_the_platform_moves():
    """The behaviour before H6: every frame placed with the workspace.sdf camera."""
    fixed = load_workspace(
        __import__('robot_plugins.frames', fromlist=['x']).default_world()).camera
    from robot_core.perception import optical_frame_in_world
    fixed_pose = optical_frame_in_world(fixed)
    errors = []
    for q in POSES:
        raw = snapshot(q, 1.0)
        c = world_center(Frame(raw.rgb, raw.depth, raw.intrinsics, fixed_pose, 1.0))
        errors.append(np.inf if c is None else float(np.linalg.norm(c - TRUTH)))
    moved = [e for q, e in zip(POSES, errors) if q != HOME]
    assert min(moved) > 0.10, errors            # tens of centimetres off, not millimetres


def test_pose_is_taken_at_the_image_stamp_while_the_platform_moves():
    """Joint states every 10 ms, the image exposed between two samples while the platform pans
    and slides (constant rates, so the true pose at any instant is known exactly)."""
    rates = (0.9, 0.3)                                    # pan rad/s, slide m/s

    def q_at(t):
        return (rates[0] * t * 0.5, rates[1] * t, 0.1)    # yaw 0.45 t keeps the target in view

    hist = JointHistory()
    for k in range(100):
        hist.add(k * 0.01, q_at(k * 0.01))
    t_img = 0.4137                                        # between samples 0.41 and 0.42
    raw = snapshot(q_at(t_img), t_img)
    frame = PlatformFrameBuilder(hist).frame(raw.rgb, raw.depth, raw.intrinsics, t_img)
    c = world_center(frame)
    assert c is not None and np.linalg.norm(c - TRUTH) <= TOL
    # the pose really differs between neighbouring samples (so interpolation matters here)
    d = np.linalg.norm(optical_in_world(q_at(0.42), CAMERA).translation
                       - optical_in_world(q_at(0.41), CAMERA).translation)
    assert d > 0.002


def test_a_frame_newer_than_the_last_joint_state_is_not_guessed():
    b = builder_for(HOME, 1.0)
    raw = snapshot(HOME, 5.0)
    assert b.frame(raw.rgb, raw.depth, raw.intrinsics, 1.5) is None     # waits for newer states
    assert b.frame(raw.rgb, raw.depth, raw.intrinsics, 0.5) is None     # older than the history
    assert PlatformFrameBuilder().frame(raw.rgb, raw.depth, raw.intrinsics, 0.0) is None


# ---- JointHistory ----------------------------------------------------------------------------
def test_history_interpolates_linearly():
    h = JointHistory()
    h.add(1.0, (0.0, 0.0, 0.0))
    h.add(1.1, (1.0, 2.0, -1.0))
    q = h.at(1.025)
    assert q == pytest.approx((0.25, 0.5, -0.25))
    assert h.at(1.0) == (0.0, 0.0, 0.0) and h.at(1.1) == (1.0, 2.0, -1.0)


def test_history_never_extrapolates_and_refuses_gaps():
    h = JointHistory(max_gap_s=0.25)
    h.add(1.0, (0, 0, 0))
    h.add(2.0, (1, 0, 0))                                  # one second without joint states
    assert h.at(0.99) is None and h.at(2.01) is None
    assert h.at(1.5) is None                               # inside a gap: not interpolated
    assert h.at(1.0) is not None


def test_history_drops_bad_samples():
    h = JointHistory()
    h.add(1.0, (0, 0, 0))
    h.add(float('nan'), (0, 0, 0))
    h.add(1.1, (float('inf'), 0, 0))
    h.add(0.5, (0, 0, 0))                                  # out of order
    h.add(1.05, (0.5, 0, 0))
    assert h.oldest == 1.0 and h.newest == 1.05
    assert h.at(float('nan')) is None


def test_pose_matches_the_platform_forward_kinematics():
    q = (0.3, 0.9, 0.2)
    raw = snapshot(q, 1.0)
    f = builder_for(q, 1.0).frame(raw.rgb, raw.depth, raw.intrinsics, 1.0)
    assert np.allclose(f.world_from_optical.rotation, raw.world_from_optical.rotation, atol=1e-9)
    assert np.allclose(f.world_from_optical.translation, raw.world_from_optical.translation,
                       atol=1e-9)
    assert math.isclose(Pose(*raw.world_from_optical.translation, 0, 0, 0).x,
                        f.world_from_optical.translation[0], abs_tol=1e-9)
