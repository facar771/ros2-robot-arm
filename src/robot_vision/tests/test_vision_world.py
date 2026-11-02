"""tracking.sdf and the Python models of the platform / scenarios must not drift apart."""
import math
import xml.etree.ElementTree as ET

import pytest
from robot_core.workspace import project
from robot_vision.platform import HOME, PlatformLimits, camera_pose
from robot_vision.tracking_sim import CAMERA, TARGET_RADIUS
from robot_vision.trajectories import PIVOT_HEIGHT, SCENARIOS
from vision_support import SRC

SDF = SRC / 'robot_sim' / 'worlds' / 'tracking.sdf'


@pytest.fixture(scope='module')
def world():
    return ET.parse(SDF).getroot().find('world')


def floats(text):
    return [float(v) for v in text.split()]


def joint(world, name):
    return world.find(f"model[@name='cam_platform']/joint[@name='{name}']")


def test_pivot_is_at_the_python_pivot_height(world):
    pose = floats(world.find("model[@name='cam_platform']/pose").text)
    assert pose[:3] == [0.0, 0.0, PIVOT_HEIGHT]


@pytest.mark.parametrize('name, kind, axis, limits, rate', [
    ('yaw_joint', 'revolute', [0, 0, 1], PlatformLimits().yaw, PlatformLimits().yaw_rate),
    ('slide_joint', 'prismatic', [1, 0, 0], PlatformLimits().slide, PlatformLimits().slide_rate),
    ('pitch_joint', 'revolute', [0, 1, 0], PlatformLimits().pitch, PlatformLimits().pitch_rate),
])
def test_joint_chain_matches_robot_vision_platform(world, name, kind, axis, limits, rate):
    j = joint(world, name)
    assert j.get('type') == kind
    assert floats(j.find('axis/xyz').text) == axis
    assert float(j.find('axis/limit/lower').text) == pytest.approx(limits[0])
    assert float(j.find('axis/limit/upper').text) == pytest.approx(limits[1])
    assert float(j.find('axis/limit/velocity').text) == pytest.approx(rate)


def test_chain_order_is_yaw_then_slide_then_pitch(world):
    order = [(joint(world, n).find('parent').text, joint(world, n).find('child').text)
             for n in ('yaw_joint', 'slide_joint', 'pitch_joint')]
    assert order == [('base', 'yaw_link'), ('yaw_link', 'slide_link'), ('slide_link', 'pitch_link')]


def test_camera_matches_the_python_camera_spec(world):
    cam = world.find(".//sensor[@type='rgbd_camera']/camera")
    assert float(cam.find('horizontal_fov').text) == pytest.approx(CAMERA.horizontal_fov)
    assert int(cam.find('image/width').text) == CAMERA.width
    assert int(cam.find('image/height').text) == CAMERA.height
    assert float(cam.find('clip/near').text) == pytest.approx(CAMERA.near)
    assert float(cam.find('clip/far').text) == pytest.approx(CAMERA.far)


def test_every_joint_has_a_rate_controller_and_the_state_publisher(world):
    plugins = world.findall("model[@name='cam_platform']/plugin")
    controllers = {p.find('joint_name').text: p.find('topic').text for p in plugins
                   if p.get('name').endswith('JointController')}
    assert controllers == {f'{j}_joint': f'/platform/{j}_joint/cmd_vel'
                           for j in ('yaw', 'slide', 'pitch')}
    assert all(p.find('use_velocity_commands').text == 'true' for p in plugins
               if p.get('name').endswith('JointController'))
    assert any(p.get('name').endswith('JointStatePublisher') for p in plugins)


def test_target_is_a_red_sphere_of_the_simulated_radius(world):
    assert float(world.find("model[@name='target']//sphere/radius").text) == \
        pytest.approx(TARGET_RADIUS)
    rgb = floats(world.find("model[@name='target']//material/diffuse").text)
    assert rgb[0] > 0.8 and rgb[1] < 0.2 and rgb[2] < 0.2


def test_physics_choices_that_the_tracking_loop_depends_on(world):
    engine = world.find("plugin[@name='gz::sim::systems::Physics']/engine/filename").text
    assert engine == 'gz-physics-bullet-featherstone-plugin'     # DART ignored later velocity cmds
    assert floats(world.find('gravity').text) == [0.0, 0.0, 0.0]  # the target is a free body
    assert float(world.find('physics/max_step_size').text) >= 0.005


@pytest.mark.parametrize('name', list(SCENARIOS))
def test_every_scenario_starts_inside_the_camera_view_of_the_home_pose(name):
    from dataclasses import replace
    cam = replace(CAMERA, pose=camera_pose(HOME))

    class Ws:
        camera = cam
    start = SCENARIOS[name].position(0.0)
    u, v, depth = project(Ws, start)
    assert 0 <= u < cam.width and 0 <= v < cam.height and cam.near < depth < cam.far
    assert math.isfinite(depth)
