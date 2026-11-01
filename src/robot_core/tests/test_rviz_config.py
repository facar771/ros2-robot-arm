"""The RViz layouts must only name topics/frames that this project really publishes."""
import re

import pytest
from robot_core.perception import DEFAULT_COLORS
from robot_core.rviz_config import available_rviz_configs, read_rviz_config, resolve_rviz_config
from robot_core.workspace import load_workspace

from tests.conftest import CONFIG_DIR, SRC
from tests.test_model_integrity import _load_yaml_without_duplicate_keys

DEMO = CONFIG_DIR / 'robot_demo.rviz'
LAUNCH = (SRC / 'robot_moveit_config' / 'launch' / 'gazebo.launch.py').read_text(encoding='utf-8')
PERCEPTION_NODE = (SRC / 'robot_perception' / 'robot_perception' / 'perception_node.py'
                   ).read_text(encoding='utf-8')
XACRO = (CONFIG_DIR / 'six_axis_robot.urdf.xacro').read_text(encoding='utf-8')
URDF = (SRC / 'robot_description' / 'urdf' / 'robot.urdf').read_text(encoding='utf-8')


# ---- finding a layout --------------------------------------------------------------------------
def test_resolve_by_name_with_and_without_suffix():
    assert resolve_rviz_config('robot_demo.rviz', CONFIG_DIR) == DEMO
    assert resolve_rviz_config('robot_demo', CONFIG_DIR) == DEMO
    assert resolve_rviz_config(' moveit.rviz ', CONFIG_DIR) == CONFIG_DIR / 'moveit.rviz'


def test_resolve_absolute_path(tmp_path):
    f = tmp_path / 'mine.rviz'
    f.write_text('x: 1')
    assert resolve_rviz_config(str(f), CONFIG_DIR) == f


def test_resolve_missing_lists_what_exists():
    with pytest.raises(FileNotFoundError) as exc:
        resolve_rviz_config('nope', CONFIG_DIR)
    assert 'moveit.rviz' in str(exc.value) and 'robot_demo.rviz' in str(exc.value)
    with pytest.raises(FileNotFoundError):
        resolve_rviz_config('  ', CONFIG_DIR)


def test_default_layout_is_still_moveit_rviz():
    """Without rviz_config:= the launch file must keep using the old layout."""
    assert re.search(r"DeclareLaunchArgument\('rviz_config', default_value='moveit.rviz'", LAUNCH)
    assert 'moveit.rviz' in available_rviz_configs(CONFIG_DIR)
    assert read_rviz_config(CONFIG_DIR / 'moveit.rviz').fixed_frame == 'base_link'   # unchanged


def test_layouts_are_installed_with_the_package():
    cmake = (SRC / 'robot_moveit_config' / 'CMakeLists.txt').read_text(encoding='utf-8')
    assert 'install(DIRECTORY config DESTINATION' in cmake


@pytest.mark.parametrize('name', available_rviz_configs(CONFIG_DIR))
def test_layout_is_valid_yaml_without_duplicate_keys(name):
    data = _load_yaml_without_duplicate_keys(CONFIG_DIR / name)
    assert 'Visualization Manager' in data and 'Window Geometry' in data


# ---- robot_demo.rviz <-> the code --------------------------------------------------------------
@pytest.fixture(scope='module')
def demo():
    return read_rviz_config(DEMO)


def test_demo_layout_content(demo):
    assert demo.fixed_frame == 'world'
    assert demo.topics == {'Kamera (RGB)': '/camera/image',
                           'Kamera (derinlik)': '/camera/depth_image',
                           'Algılanan nesneler': '/perception/objects'}
    assert demo.planning_scene_topic == 'monitored_planning_scene'
    assert set(demo.classes) >= {
        'rviz_default_plugins/Grid', 'rviz_default_plugins/TF', 'rviz_default_plugins/Image',
        'rviz_default_plugins/MarkerArray', 'moveit_rviz_plugin/MotionPlanning'}


def test_demo_camera_topics_are_bridged_by_the_launch_file(demo):
    bridged = set(re.findall(r"'(/camera/[a-z_]+)@", LAUNCH))
    for name, topic in demo.topics.items():
        if topic.startswith('/camera/'):
            assert topic in bridged, f'{name}: {topic} köprülenmiyor (köprülenen: {bridged})'


def test_demo_object_topic_is_published_by_perception(demo):
    published = re.search(r"TOPIC_OBJECTS = '([^']+)'", PERCEPTION_NODE).group(1)
    assert demo.topics['Algılanan nesneler'] == published
    assert 'create_publisher(MarkerArray, TOPIC_OBJECTS' in PERCEPTION_NODE


def test_demo_marker_namespaces_are_the_detected_colours(demo):
    assert set(demo.marker_namespaces['Algılanan nesneler']) == {c.name for c in DEFAULT_COLORS}


def test_demo_scene_topic_is_published_by_move_group(demo):
    # move_group publishes MoveIt's default topic `monitored_planning_scene` when asked to:
    assert "'publish_planning_scene': True" in LAUNCH
    assert demo.planning_scene_topic == 'monitored_planning_scene'


def test_demo_frames_exist(demo):
    ws = load_workspace(SRC / 'robot_sim' / 'worlds' / 'workspace.sdf')
    known = {
        'world': '<link name="world"/>' in XACRO,                          # massless root link
        'base_link': 'name="base_link"' in URDF,
        'link_6': 'name="link_6"' in URDF,
        ws.camera.frame_id: "static_tf('world', camera.frame_id" in LAUNCH,   # world -> camera_link
        'camera_optical_frame': "'camera_optical_frame'" in LAUNCH,
    }
    assert demo.fixed_frame in known
    for frame in (demo.fixed_frame, *demo.tf_frames):
        assert known.get(frame), f'{frame} çerçevesini kod yayımlamıyor'
    assert set(demo.tf_frames) == set(known)           # the TF display lists exactly these


def test_launch_resolves_the_layout_through_the_tested_helper():
    assert 'resolve_rviz_config(' in LAUNCH and "'-d', str(layout)" in LAUNCH
    assert re.search(r"if not IfCondition\(use_rviz\)\.evaluate\(context\):\s+return \[\]", LAUNCH)
