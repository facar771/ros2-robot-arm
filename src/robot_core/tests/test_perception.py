import dataclasses
import math

import numpy as np
import pytest
from robot_core.perception import (
    DEFAULT_COLORS,
    Intrinsics,
    Transform,
    backproject,
    classify_color,
    color_masks,
    connected_components,
    detect_objects,
    erode,
    optical_frame_in_world,
    rgb_to_hsv,
)
from robot_core.workspace import Shape, load_workspace, project

from tests.conftest import SRC
from tests.synthetic import SKY_RGB, half_resolution, render

WORLD = SRC / 'robot_sim' / 'worlds' / 'workspace.sdf'
COLORS = {'red_box': (230, 26, 26), 'green_cylinder': (26, 204, 26), 'blue_ball': (26, 51, 230)}
SHADE = {'red_box': 0.9, 'green_cylinder': 0.55, 'blue_ball': 0.75}   # hue must survive shading
LABEL = {'red_box': 'red', 'green_cylinder': 'green', 'blue_ball': 'blue'}
POSITION_TOLERANCE = 0.015   # m


@pytest.fixture(scope='module')
def ws():
    return half_resolution(load_workspace(WORLD))


@pytest.fixture(scope='module')
def scene(ws):
    return render(ws, COLORS, SHADE, depth_noise=0.001)


def by_label(detections):
    return {d.label: d for d in detections if d.index == 0}


# ---- colour ------------------------------------------------------------------------------------
def test_hsv_primary_colours():
    rgb = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [128, 128, 128]]], dtype=np.uint8)
    hue, sat, val = rgb_to_hsv(rgb)
    assert hue[0, :3] == pytest.approx([0, 120, 240])
    assert sat[0] == pytest.approx([1, 1, 1, 0])
    assert val[0, 3] == pytest.approx(128 / 255, abs=1e-3)


def test_color_masks_ignore_grey_and_pale_sky():
    rgb = np.array([[SKY_RGB, (140, 140, 140), (230, 26, 26), (26, 204, 26), (26, 51, 230)]],
                   dtype=np.uint8)
    masks = color_masks(rgb)
    assert {n: m[0].tolist() for n, m in masks.items()} == {
        'red': [False, False, True, False, False],
        'green': [False, False, False, True, False],
        'blue': [False, False, False, False, True]}


def test_classify_world_file_colours():
    assert classify_color((0.9, 0.1, 0.1)) == 'red'
    assert classify_color((0.1, 0.8, 0.1)) == 'green'
    assert classify_color((0.1, 0.2, 0.9)) == 'blue'
    assert classify_color((0.55, 0.55, 0.55)) is None


def test_world_shapes_have_a_known_colour_class():
    ws = load_workspace(WORLD)
    assert {s.name: classify_color(s.color) for s in ws.shapes} == LABEL


# ---- image helpers -----------------------------------------------------------------------------
def test_connected_components_are_four_connected_and_sorted():
    m = np.zeros((6, 8), dtype=bool)
    m[1:3, 1:4] = True     # 6 px
    m[4, 6] = True         # 1 px
    m[3, 4] = True         # touches the first blob only diagonally -> separate
    blobs = connected_components(m)
    assert [len(b) for b in blobs] == [6, 1, 1]


def test_erode_removes_one_pixel_border():
    m = np.zeros((7, 7), dtype=bool)
    m[1:6, 1:6] = True
    e = erode(m)
    assert e.sum() == 9 and e[3, 3] and not e[1, 1]


# ---- geometry ----------------------------------------------------------------------------------
def test_backproject_inverts_project(ws):
    intr = Intrinsics.from_camera_spec(ws.camera)
    to_world = optical_frame_in_world(ws.camera)
    point = (0.35, 0.15, 0.06)
    u, v, z = project(ws, point)
    depth = np.full((ws.camera.height, ws.camera.width), z, dtype=np.float32)
    # project() returns fractional pixel indices; backproject accepts them as floats too
    p_opt = backproject(depth, intr, np.array([int(round(v))]), np.array([int(round(u))]))
    p_world = to_world.apply(p_opt)[0]
    assert math.dist(p_world, point) < 0.012   # rounding to a whole pixel at ~1 m


def test_optical_frame_matches_launch_file_convention(ws):
    t = optical_frame_in_world(ws.camera)
    cam = ws.camera
    fwd_world = cam.pose.direction_to_world((1, 0, 0))      # camera_link x forward
    z_axis = t.rotation @ np.array([0, 0, 1.0])
    assert z_axis == pytest.approx(fwd_world, abs=1e-9)
    left_world = cam.pose.direction_to_world((0, 1, 0))     # camera_link y left
    assert t.rotation @ np.array([1.0, 0, 0]) == pytest.approx(-np.array(left_world), abs=1e-9)
    assert t.translation == pytest.approx(cam.pose.position)


def test_transform_from_quaternion_identity_and_yaw():
    p = np.array([[1.0, 0.0, 0.0]])
    assert Transform.from_quaternion(0, 0, 0, 1, (1, 2, 3)).apply(p)[0] == pytest.approx([2, 2, 3])
    s = math.sqrt(0.5)    # 90 degrees about z
    assert Transform.from_quaternion(0, 0, s, s, (0, 0, 0)).apply(p)[0] == pytest.approx(
        [0, 1, 0], abs=1e-9)
    with pytest.raises(ValueError):
        Transform.from_quaternion(0, 0, 0, 0, (0, 0, 0))


def test_intrinsics_from_camera_info():
    k = [277.0, 0, 160.0, 0, 277.0, 120.0, 0, 0, 1]
    assert Intrinsics.from_camera_info_k(k, 320, 240) == Intrinsics(277, 277, 160, 120, 320, 240)


# ---- end to end on a synthetic scene ---------------------------------------------------------
def test_detects_all_objects_with_centimetre_accuracy(ws, scene):
    rgb, depth, intr, to_world = scene
    found = by_label(detect_objects(rgb, depth, intr, to_world, min_pixels=15))
    assert set(found) == {'red', 'green', 'blue'}
    for shape in ws.shapes:
        det = found[LABEL[shape.name]]
        err = math.dist(det.position, shape.center)
        assert err < POSITION_TOLERANCE, f'{shape.name}: {err * 100:.1f} cm'


def test_detected_height_matches_objects(ws, scene):
    rgb, depth, intr, to_world = scene
    found = by_label(detect_objects(rgb, depth, intr, to_world, min_pixels=15))
    heights = {'red': 0.06, 'green': 0.10, 'blue': 0.08}
    for label, height in heights.items():
        assert found[label].top_z == pytest.approx(height, abs=0.01)
        assert found[label].size[2] == pytest.approx(height, abs=0.01)


def test_only_ground_and_sky_gives_no_detections(ws):
    empty = dataclasses.replace(ws, shapes=())
    rgb, depth, intr, to_world = render(empty, COLORS)
    assert detect_objects(rgb, depth, intr, to_world, min_pixels=15) == []


def test_small_specks_are_ignored(ws, scene):
    rgb, depth, intr, to_world = scene
    rgb = rgb.copy()
    rgb[2, 2] = (230, 26, 26)    # lone red pixel, with valid depth
    found = detect_objects(rgb, depth, intr, to_world, min_pixels=15)
    assert sorted((d.label, d.index) for d in found) == [('blue', 0), ('green', 0), ('red', 0)]


def test_pixels_without_depth_are_not_used(ws, scene):
    rgb, depth, intr, to_world = scene
    blind = depth.copy()
    blind[:] = np.nan
    assert detect_objects(rgb, blind, intr, to_world, min_pixels=15) == []


def test_two_objects_of_one_colour_get_distinct_indices(ws):
    extra = Shape('red_box_2', 'box', (0.50, -0.25, 0.03), (0.06, 0.06, 0.06))
    two = dataclasses.replace(ws, shapes=ws.shapes + (extra,))
    rgb, depth, intr, to_world = render(two, {**COLORS, 'red_box_2': COLORS['red_box']})
    reds = [d for d in detect_objects(rgb, depth, intr, to_world, min_pixels=15)
            if d.label == 'red']
    assert [d.index for d in reds] == [0, 1]
    assert reds[0].pixels >= reds[1].pixels
    expected = {(0.35, 0.15), (0.50, -0.25)}
    for d in reds:
        assert min(math.hypot(d.position[0] - x, d.position[1] - y) for x, y in expected) < 0.02


def test_mismatched_shapes_are_rejected(scene):
    rgb, depth, intr, to_world = scene
    with pytest.raises(ValueError, match='disagree'):
        detect_objects(rgb, depth[:-1], intr, to_world)


def test_default_colours_are_distinct():
    assert len({c.name for c in DEFAULT_COLORS}) == len(DEFAULT_COLORS)
