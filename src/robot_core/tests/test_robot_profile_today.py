"""The robot profile (src/robot_description/profile/six_axis_robot.yaml) IS today's robot.

Design: docs/ROBOT_PROFILE.md. Until the generator exists, the hand-written URDF, SRDF,
joint_limits.yaml, safe_zone.yaml, initial_positions.yaml and ros2_control xacro stay the source
the planner reads; this test proves the profile says exactly the same thing, value for value
(exact float equality: same decimal literals), so that generating those files from it later
changes nothing. It is replaced by "generated == committed" when the generator lands.
"""
import re
import xml.etree.ElementTree as ET

import pytest
import yaml

from tests.conftest import CONFIG_DIR, SRC, SRDF, URDF

PROFILE = SRC / 'robot_description' / 'profile' / 'six_axis_robot.yaml'


@pytest.fixture(scope='module')
def profile():
    return yaml.safe_load(PROFILE.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def urdf():
    return ET.parse(URDF).getroot()


def floats(text, default='0 0 0'):
    return [float(v) for v in (text or default).split()]


def load(name):
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding='utf-8'))


# ---- the profile is internally consistent ----------------------------------------------------
def test_profile_header(profile):
    assert profile['profile_version'] == 1 and profile['robot'] == 'six_axis_robot'
    assert set(profile) == {'profile_version', 'robot', 'materials', 'mount', 'links', 'joints',
                            'moveit', 'safety', 'motors'}


def test_chain_is_connected_in_order(profile):
    links, joints = list(profile['links']), profile['joints']
    assert len(links) == len(joints) + 1
    for (name, joint), parent, child in zip(joints.items(), links, links[1:]):
        assert (joint['parent'], joint['child']) == (parent, child), name
        lo, hi = joint['limits']['position']
        assert lo < hi
        assert sum(a * a for a in joint['axis']) == 1, f'{name}: eksen birim vektör değil'
    assert profile['mount'] == {'name': 'masa', 'parent': 'world', 'child': links[0]}


def test_named_poses_and_initial_positions_are_inside_the_limits(profile):
    joints = profile['joints']
    for pose, values in profile['moveit']['named_poses'].items():
        assert len(values) == len(joints), pose
        for (name, joint), value in zip(joints.items(), values):
            lo, hi = joint['limits']['position']
            assert lo <= value <= hi, f'{pose}: {name}={value}'
    assert list(profile['moveit']['initial_positions']) == list(joints)


def test_motors_are_a_separate_valid_section(profile):
    assert list(profile['motors']) == list(profile['joints'])
    for name, motor in profile['motors'].items():
        assert set(motor) == {'direction', 'zero_offset', 'gear_ratio'}, name
        assert motor['direction'] in (1, -1) and motor['gear_ratio'] > 0, name
        # today's robot is simulated: identity mapping, so nothing downstream can depend on motors
        assert (motor['direction'], motor['zero_offset'], motor['gear_ratio']) == (1, 0.0, 1.0)


def test_safety_box_is_well_formed(profile):
    box = profile['safety']['box']
    assert set(box) == {'x', 'y', 'z'} and all(lo < hi for lo, hi in box.values())
    assert 0 < profile['safety']['max_velocity_scale'] <= 1


# ---- the profile equals the files the planner reads today -------------------------------------
def test_joints_equal_urdf(profile, urdf):
    urdf_joints = {j.get('name'): j for j in urdf.findall('joint')}
    assert set(urdf_joints) == set(profile['joints'])     # `masa` lives in the xacro wrapper
    for name, joint in profile['joints'].items():
        j = urdf_joints[name]
        assert j.get('type') == joint['type']
        assert (j.find('parent').get('link'), j.find('child').get('link')) == (
            joint['parent'], joint['child'])
        origin = j.find('origin')
        assert origin is not None and origin.get('rpy') is None
        assert floats(origin.get('xyz')) == joint['origin']
        assert floats(j.find('axis').get('xyz')) == joint['axis']
        limit, want = j.find('limit'), joint['limits']
        assert [float(limit.get('lower')), float(limit.get('upper'))] == want['position']
        assert float(limit.get('velocity')) == want['velocity']
        assert float(limit.get('effort')) == want['effort']


def test_links_equal_urdf(profile, urdf):
    colours = {m.get('name'): [float(v) for v in m.find('color').get('rgba').split()]
               for m in urdf.findall('material')}
    assert colours == profile['materials']
    urdf_links = {link.get('name'): link for link in urdf.findall('link')}
    assert set(urdf_links) == set(profile['links'])
    for name, want in profile['links'].items():
        link = urdf_links[name]
        shape = want['shape']
        for part in ('visual', 'collision'):
            geom = link.find(f'{part}/geometry')
            if shape['type'] == 'cylinder':
                cyl = geom.find('cylinder')
                assert (float(cyl.get('radius')), float(cyl.get('length'))) == (
                    shape['radius'], shape['length']), (name, part)
            else:
                assert shape['type'] == 'box' and floats(geom.find('box').get('size')) == \
                    shape['size'], (name, part)
            origin = link.find(f'{part}/origin')
            assert floats(None if origin is None else origin.get('xyz')) == want['origin']
        assert link.find('visual/material').get('name') == want['material']
        inertial = link.find('inertial')
        origin = inertial.find('origin')
        assert floats(None if origin is None else origin.get('xyz')) == want['origin']
        assert float(inertial.find('mass').get('value')) == want['mass']
        i = inertial.find('inertia')
        assert [float(i.get(k)) for k in ('ixx', 'iyy', 'izz')] == want['inertia']
        assert [float(i.get(k)) for k in ('ixy', 'ixz', 'iyz')] == [0.0, 0.0, 0.0]


def test_mount_joint_equals_xacro_wrapper(profile):
    wrapper = ET.parse(CONFIG_DIR / 'six_axis_robot.urdf.xacro').getroot()
    mount = profile['mount']
    joint = next(j for j in wrapper.findall('joint') if j.get('name') == mount['name'])
    assert joint.get('type') == 'fixed'
    assert (joint.find('parent').get('link'), joint.find('child').get('link')) == (
        mount['parent'], mount['child'])
    assert wrapper.find(f"link[@name='{mount['parent']}']") is not None


def test_joint_limits_yaml_equals_profile(profile):
    data = load('joint_limits.yaml')
    assert data['default_velocity_scaling_factor'] == profile['moveit']['default_velocity_scaling']
    assert data['default_acceleration_scaling_factor'] == \
        profile['moveit']['default_acceleration_scaling']
    assert set(data['joint_limits']) == set(profile['joints'])
    for name, joint in profile['joints'].items():
        entry = data['joint_limits'][name]
        assert entry['has_velocity_limits'] and entry['has_acceleration_limits']
        assert entry['max_velocity'] == joint['limits']['velocity']
        assert entry['max_acceleration'] == joint['limits']['acceleration']


def test_srdf_equals_profile(profile):
    srdf = ET.parse(SRDF).getroot()
    group = srdf.find(f"group[@name='{profile['moveit']['planning_group']}']")
    assert [j.get('name') for j in group.findall('joint')] == list(profile['joints'])
    poses = {s.get('name'): [float(j.get('value')) for j in s.findall('joint')]
             for s in srdf.findall('group_state')}
    assert poses == profile['moveit']['named_poses']
    virtual = srdf.find('virtual_joint')
    mount = profile['mount']
    assert (virtual.get('name'), virtual.get('parent_frame'), virtual.get('child_link')) == (
        mount['name'], mount['parent'], mount['child'])
    # the generator rule: parent-child pairs are disabled, plus the explicit allow_extra list
    derived = {frozenset((j['parent'], j['child'])) for j in profile['joints'].values()}
    derived |= {frozenset(pair[:2]) for pair in profile['moveit']['collision']['allow_extra']}
    disabled = [frozenset((d.get('link1'), d.get('link2'))) for d in srdf.findall(
        'disable_collisions')]
    assert len(disabled) == len(set(disabled)) and set(disabled) == derived


def test_safe_zone_yaml_equals_profile(profile):
    assert load('safe_zone.yaml') == profile['safety']


def test_initial_positions_yaml_equals_profile(profile):
    assert load('initial_positions.yaml')['initial_positions'] == \
        profile['moveit']['initial_positions']


def test_ros2_control_xacro_lists_the_profile_joints(profile):
    text = (CONFIG_DIR / 'six_axis_robot.ros2_control.xacro').read_text(encoding='utf-8')
    assert re.findall(r'<joint name="(\w+)">', text) == list(profile['joints'])
