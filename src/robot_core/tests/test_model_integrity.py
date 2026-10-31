"""Regression guards for configuration bugs found in the original project.

Each test documents a real failure that existed before (see git history of stage0-temel).
"""
import xml.etree.ElementTree as ET

import pytest
import yaml

from tests.conftest import CONFIG_DIR, SRDF, URDF


def _urdf():
    return ET.parse(URDF).getroot()


def test_every_link_has_collision_geometry():
    """Without <collision>, MoveIt ignores obstacles (planning scene boxes had no effect)."""
    missing = [link.get('name') for link in _urdf().findall('link')
               if link.find('collision') is None]
    assert missing == []


def test_adjacent_links_are_in_the_allowed_collision_matrix():
    """Adjacent links overlap at the joints; without ACM entries every state self-collides."""
    srdf = ET.parse(SRDF).getroot()
    disabled = {frozenset((d.get('link1'), d.get('link2')))
                for d in srdf.findall('disable_collisions')}
    for joint in _urdf().findall('joint'):
        pair = frozenset((joint.find('parent').get('link'), joint.find('child').get('link')))
        assert pair in disabled, f'{sorted(pair)} komşu ama ACM\'de yok'


def _load_yaml_without_duplicate_keys(path):
    class Loader(yaml.SafeLoader):
        pass

    def construct(loader, node, deep=False):
        keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        assert not dupes, f'{path.name}: tekrarlanan anahtar {dupes}'
        return yaml.SafeLoader.construct_mapping(loader, node, deep)

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct)
    return yaml.load(path.read_text(encoding='utf-8'), Loader)


@pytest.mark.parametrize('name', sorted(p.name for p in CONFIG_DIR.glob('*.yaml')))
def test_yaml_has_no_duplicate_keys(name):
    """joint_limits.yaml used to define `joint_limits:` twice (first block silently dropped)."""
    _load_yaml_without_duplicate_keys(CONFIG_DIR / name)


def test_joint_limits_yaml_covers_all_joints(config):
    data = _load_yaml_without_duplicate_keys(CONFIG_DIR / 'joint_limits.yaml')
    assert set(data['joint_limits']) == set(config.joint_names)
    for name, entry in data['joint_limits'].items():
        assert entry['has_velocity_limits'] and entry['has_acceleration_limits'], name
        assert isinstance(entry['max_velocity'], float), name  # ROS params are typed


def test_single_srdf_only():
    """A second, never-loaded robot.srdf with a different robot name caused confusion."""
    assert [p.name for p in CONFIG_DIR.glob('*.srdf')] == ['six_axis_robot.srdf']


def test_srdf_robot_name_matches_urdf():
    assert ET.parse(SRDF).getroot().get('name') == _urdf().get('name')


def test_controller_joint_lists_match_urdf(config):
    ctrl = yaml.safe_load((CONFIG_DIR / 'ros2_controllers.yaml').read_text(encoding='utf-8'))
    assert tuple(ctrl['arm_controller']['ros__parameters']['joints']) == config.joint_names
    moveit = yaml.safe_load((CONFIG_DIR / 'moveit_controllers.yaml').read_text(encoding='utf-8'))
    joints = moveit['moveit_simple_controller_manager']['arm_controller']['joints']
    assert tuple(joints) == config.joint_names


