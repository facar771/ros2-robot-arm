import math

import numpy as np
import pytest
from robot_vision.geometry import convex_hull, fit_circle, fit_sphere, min_area_rect, polygon_area
from robot_vision.synth import render
from vision_support import shapes_by_name, tall_box, workspace

from robot_vision import ColorDetector, GeometryConfig, Segment, measure, segment_points

TOL = 0.01           # m: centre / size / distance tolerance (pixel size is ~3.5 mm at this range)
HEIGHT_TOL = 0.005


@pytest.fixture(scope='module', params=[0.0, 0.002], ids=['noise-free', 'sigma-2mm'])
def measures(request):
    ws = workspace()
    frame = render(ws.camera, ws.shapes, depth_noise=request.param, seed=11)
    out = {}
    for seg in ColorDetector().detect(frame):
        out[seg.label] = measure(frame, seg)
    return frame, out


def _near(a, b, tol):
    return np.all(np.abs(np.asarray(a) - np.asarray(b)) <= tol)


def test_shapes_are_recognised(measures):
    _, m = measures
    assert (m['red'].shape, m['green'].shape, m['blue'].shape) == ('box', 'cylinder', 'sphere')


def test_centres_and_sizes_match_the_world_file(measures):
    _, m = measures
    shapes = shapes_by_name()
    box, cyl, ball = shapes['red_box'], shapes['green_cylinder'], shapes['blue_ball']
    assert _near(m['red'].center, box.center, TOL)
    assert _near(m['red'].size, box.dims, TOL)            # 6 cm cube
    assert _near(m['green'].center, cyl.center, TOL)
    assert abs(m['green'].radius - cyl.dims[0]) <= 0.005   # r = 3 cm
    assert abs(m['green'].height - cyl.dims[1]) <= HEIGHT_TOL   # h = 10 cm
    assert _near(m['blue'].center, ball.center, TOL)
    assert abs(m['blue'].radius - ball.dims[0]) <= 0.005   # r = 4 cm


def test_heights_use_the_ground_plane(measures):
    _, m = measures
    assert abs(m['red'].height - 0.06) <= HEIGHT_TOL
    assert abs(m['red'].top_z - 0.06) <= HEIGHT_TOL


def test_nearest_point_distance_is_the_true_surface_distance(measures):
    frame, m = measures
    cam = np.array(frame.camera_position)
    box = shapes_by_name()['red_box']
    lo = np.array(box.center) - np.array(box.dims) / 2
    hi = np.array(box.center) + np.array(box.dims) / 2
    true_box = float(np.linalg.norm(cam - np.clip(cam, lo, hi)))
    ball = shapes_by_name()['blue_ball']
    true_ball = float(np.linalg.norm(cam - np.array(ball.center)) - ball.dims[0])
    assert abs(m['red'].nearest_distance - true_box) <= TOL
    assert abs(m['blue'].nearest_distance - true_ball) <= TOL


def test_farthest_visible_is_never_beyond_the_far_surface(measures):
    frame, m = measures
    cam = np.array(frame.camera_position)
    ball = shapes_by_name()['blue_ball']
    far_surface = float(np.linalg.norm(cam - np.array(ball.center)) + ball.dims[0])
    assert m['blue'].nearest_distance < m['blue'].farthest_distance < far_surface - 0.01


def test_box_corner_distances_from_a_reference_point(measures):
    _, m = measures
    box = shapes_by_name()['red_box']
    ref = np.array([0.0, 0.0, 0.45])
    half = np.array(box.dims) / 2
    truth = sorted(float(np.linalg.norm(np.array(box.center) + half * np.array(s) - ref))
                   for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3))
    got = sorted(v for k, v in m['red'].distances_from(tuple(ref)).items() if 'corner' in k)
    assert len(got) == 8
    assert np.allclose(got, truth, atol=0.015)


def test_grasp_candidates_for_each_shape(measures):
    _, m = measures
    top = [g for g in m['red'].grasps if g.kind == 'top'][0]
    assert top.approach == (0.0, 0.0, -1.0) and top.fits
    assert abs(top.width - 0.06) <= TOL
    assert abs(top.position[2] - 0.06) <= HEIGHT_TOL
    assert len([g for g in m['red'].grasps if g.kind == 'side']) == 4
    assert abs([g for g in m['blue'].grasps if g.kind == 'top'][0].width - 0.08) <= 2 * TOL
    assert all(abs(g.width - 0.06) <= TOL for g in m['green'].grasps)


def test_aperture_limit_marks_grasps_as_not_fitting():
    ws = workspace()
    frame = render(ws.camera, ws.shapes)
    seg = [s for s in ColorDetector().detect(frame) if s.label == 'blue'][0]
    tight = measure(frame, seg, GeometryConfig(max_aperture=0.05))
    assert not any(g.fits for g in tight.grasps)
    wide = measure(frame, seg, GeometryConfig(max_aperture=0.12))
    assert all(g.fits for g in wide.grasps)


@pytest.mark.parametrize('yaw_deg', [0, 30, -25, 70])
def test_yawed_non_square_box_orientation_and_size(yaw_deg):
    ws = workspace()
    box = tall_box()
    frame = render(ws.camera, [box], yaws={box.name: math.radians(yaw_deg)}, depth_noise=0.001)
    seg = ColorDetector().detect(frame)[0]
    m = measure(frame, seg)
    assert m.shape == 'box'
    assert abs(m.obb_size[0] - 0.08) <= TOL and abs(m.obb_size[1] - 0.04) <= TOL
    diff = m.obb_yaw - math.radians(yaw_deg)
    err = math.degrees(abs((diff + math.pi / 2) % math.pi - math.pi / 2))
    assert err <= 4.0
    top = [g for g in m.grasps if g.kind == 'top'][0]
    assert abs(top.width - 0.04) <= TOL     # closes across the short side


def test_sparse_or_blind_segments_return_none():
    ws = workspace()
    frame = render(ws.camera, ws.shapes)
    tiny = Segment('red', np.array([100, 101]), np.array([200, 201]))
    assert measure(frame, tiny) is None
    blind = type(frame)(frame.rgb, np.full_like(frame.depth, np.nan), frame.intrinsics,
                        frame.world_from_optical)
    seg = Segment('red', np.arange(5, 30), np.full(25, 5))
    assert segment_points(blind, seg).shape == (0, 3)
    assert measure(blind, seg) is None


def test_edge_flyer_pixels_are_dropped():
    ws = workspace()
    frame = render(ws.camera, ws.shapes)
    seg = [s for s in ColorDetector().detect(frame) if s.label == 'red'][0]
    clean = measure(frame, seg)
    depth = frame.depth.copy()
    depth[seg.rows[0], seg.cols[0]] = 3.0          # a flying pixel far behind the object
    flying = type(frame)(frame.rgb, depth, frame.intrinsics, frame.world_from_optical)
    dirty = measure(flying, seg)
    assert _near(dirty.size, clean.size, 0.003) and dirty.points == clean.points - 1


# ---- 2-D helpers -------------------------------------------------------------------------------
def test_hull_area_and_min_rect_of_a_rotated_rectangle():
    theta = math.radians(33)
    rng = np.random.default_rng(2)
    xy = rng.uniform([-0.04, -0.02], [0.04, 0.02], size=(400, 2))
    xy = np.vstack([xy, [[-0.04, -0.02], [0.04, -0.02], [0.04, 0.02], [-0.04, 0.02]]])
    rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    xy = xy @ rot.T + [0.3, -0.1]
    assert polygon_area(convex_hull(xy)) == pytest.approx(0.08 * 0.04, rel=1e-4)
    c, a, b, yaw = min_area_rect(xy)
    assert (a, b) == pytest.approx((0.08, 0.04), abs=5e-4)
    assert c == pytest.approx([0.3, -0.1], abs=5e-4)
    assert math.degrees(yaw) == pytest.approx(33, abs=0.5)


def test_circle_and_sphere_fits_recover_known_parameters():
    ang = np.linspace(0, 2 * math.pi, 60, endpoint=False)
    circle = np.column_stack([0.2 + 0.03 * np.cos(ang), -0.1 + 0.03 * np.sin(ang)])
    c, r = fit_circle(circle)
    assert c == pytest.approx([0.2, -0.1], abs=1e-9) and r == pytest.approx(0.03)
    rng = np.random.default_rng(1)
    d = rng.normal(size=(300, 3))
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    d[:, 2] = np.abs(d[:, 2])                  # only the upper hemisphere is visible
    centre, radius, rms = fit_sphere(np.array([0.4, 0.1, 0.04]) + 0.04 * d)
    assert centre == pytest.approx([0.4, 0.1, 0.04], abs=1e-6)
    assert radius == pytest.approx(0.04) and rms < 1e-9
