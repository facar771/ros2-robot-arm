import math

import pytest
from robot_core.workspace import (
    GROUND,
    Pose,
    WorldError,
    cast,
    expected_depth,
    load_workspace,
    project,
    stable_probes,
)

from tests.conftest import SRC

WORLD = SRC / 'robot_sim' / 'worlds' / 'workspace.sdf'


@pytest.fixture(scope='module')
def ws():
    return load_workspace(WORLD)


def test_camera_parsed_from_world(ws):
    cam = ws.camera
    assert cam.pose == Pose(1.0, 0.0, 0.8, 0.0, 0.9, math.pi)
    assert (cam.width, cam.height) == (320, 240)
    assert cam.frame_id == 'camera_link'
    assert cam.fx == pytest.approx(160 / math.tan(1.047 / 2))


def test_objects_parsed_from_world(ws):
    assert {s.name for s in ws.shapes} == {'red_box', 'green_cylinder', 'blue_ball'}


def test_shape_heights(ws):
    assert {s.name: s.height for s in ws.shapes} == pytest.approx(
        {'red_box': 0.06, 'green_cylinder': 0.10, 'blue_ball': 0.08})


def test_camera_looks_toward_minus_x_and_down(ws):
    fx, fy, fz = ws.camera.pose.direction_to_world((1, 0, 0))
    assert (fx, fy, fz) == pytest.approx((-math.cos(0.9), 0.0, -math.sin(0.9)), abs=1e-9)


def test_rotation_is_orthonormal():
    r = Pose(0, 0, 0, 0.3, -0.7, 2.1).rotation()
    for i in range(3):
        for j in range(3):
            dot = sum(r[i][k] * r[j][k] for k in range(3))
            assert dot == pytest.approx(1.0 if i == j else 0.0, abs=1e-12)


def test_centre_pixel_depth_matches_closed_form(ws):
    """Centre ray hits the ground: depth = camera height / sin(pitch)."""
    depth, name = expected_depth(ws, 159.5, 119.5)
    assert name == GROUND
    assert depth == pytest.approx(0.8 / math.sin(0.9), abs=1e-9)


def test_projection_and_raycast_agree_on_sphere(ws):
    ball = next(s for s in ws.shapes if s.name == 'blue_ball')
    u, v, z = project(ws, ball.center)
    depth, name = expected_depth(ws, u, v)
    assert name == 'blue_ball'
    assert z - ball.dims[0] <= depth <= z  # front surface is between centre-r and centre


def test_project_behind_camera_is_none(ws):
    assert project(ws, (5.0, 0.0, 0.8)) is None  # camera at x=1 looks to -x


def test_cast_misses_return_none():
    ws = load_workspace(WORLD)
    assert cast(ws, (0, 0, 1), (0, 0, 1)) is None  # straight up


def test_probes_cover_ground_and_all_objects(ws):
    probes = stable_probes(ws)
    assert set(probes) == {GROUND, 'red_box', 'green_cylinder', 'blue_ball'}
    for u, v, depth in probes.values():
        assert 0 <= u < ws.camera.width and 0 <= v < ws.camera.height
        assert ws.camera.near < depth < ws.camera.far


def test_box_cylinder_raycasts():
    ws = load_workspace(WORLD)
    # straight down onto the red box top from above its centre
    hit = cast(ws, (0.35, 0.15, 1.0), (0, 0, -1))
    assert hit[1] == 'red_box' and hit[0] == pytest.approx(0.94)
    hit = cast(ws, (0.40, -0.10, 1.0), (0, 0, -1))  # cylinder top cap at z=0.1
    assert hit[1] == 'green_cylinder' and hit[0] == pytest.approx(0.9)
    hit = cast(ws, (1.0, -0.10, 0.05), (-1, 0, 0))  # cylinder side, radius 0.03 at x=0.4
    assert hit[1] == 'green_cylinder' and hit[0] == pytest.approx(0.57)


def test_world_without_camera_is_rejected(tmp_path):
    bad = tmp_path / 'w.sdf'
    bad.write_text('<sdf version="1.9"><world name="w"/></sdf>')
    with pytest.raises(WorldError, match='rgbd_camera'):
        load_workspace(bad)


