"""L9a view_model.json: the committed file equals the generator output and describes the robot."""
import copy
import json
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import yaml
from robot_core.kinematics import Chain
from robot_core.profile import load_profile, parse_profile
from robot_core.profile_gen import PROFILE_PATH, generate, output_paths
from robot_core.se3 import matrix_from_wpr

from tests.conftest import SRC

REL = output_paths('six_axis_robot')['view_model']


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def model():
    return json.loads((SRC / REL).read_text(encoding='utf-8'))


def axis_angle(axis, angle):
    x, y, z = axis
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


def fk_from_model(model, q):
    """Independent forward kinematics using ONLY what a viewer receives."""
    pose = np.eye(4)
    for joint, angle in zip(model['joints'], q):
        step = np.eye(4)
        step[:3, :3] = axis_angle(joint['axis'], angle)
        move = np.eye(4)
        move[:3, 3] = joint['origin']
        pose = pose @ move @ step
    tcp = np.eye(4)
    tcp[:3, :3] = matrix_from_wpr(*model['tcp']['wpr'])
    tcp[:3, 3] = model['tcp']['offset']
    return pose @ tcp


def test_committed_file_is_the_generator_output_and_says_generated(profile, model):
    assert (SRC / REL).read_text(encoding='utf-8') == generate(profile)['view_model']
    assert 'ÜRETİLDİ' in model['_generated'] and 'ELLE DÜZENLEME' in model['_generated']
    assert model['view_model_version'] == 1 and model['robot'] == 'six_axis_robot'


def test_viewer_fk_equals_the_core_fk(profile, model):
    chain = Chain(profile)
    rng = np.random.default_rng(5)
    for _ in range(25):
        q = rng.uniform(chain.lower, chain.upper)
        assert fk_from_model(model, q) == pytest.approx(chain.fk(q), abs=1e-9)


def test_joints_and_limits_match_the_profile_and_the_urdf(profile, model):
    root = ET.parse(SRC / 'robot_description' / 'urdf' / 'robot.urdf').getroot()
    urdf = {j.get('name'): j for j in root.findall('joint')}
    assert [j['name'] for j in model['joints']] == list(profile.joint_names)
    for joint in model['joints']:
        u = urdf[joint['name']]
        xyz = [float(v) for v in u.find('origin').get('xyz').split()]
        axis = [float(v) for v in u.find('axis').get('xyz').split()]
        assert joint['origin'] == pytest.approx(xyz) and joint['axis'] == pytest.approx(axis)
        lim = u.find('limit')
        assert (joint['limit']['lower'], joint['limit']['upper'], joint['limit']['velocity']) == \
            (float(lim.get('lower')), float(lim.get('upper')), float(lim.get('velocity')))
        assert (joint['parent'], joint['child']) == (u.find('parent').get('link'),
                                                      u.find('child').get('link'))


def test_link_primitives_match_the_urdf_and_are_box_or_cylinder(model):
    root = ET.parse(SRC / 'robot_description' / 'urdf' / 'robot.urdf').getroot()
    urdf = {link.get('name'): link for link in root.findall('link')}
    assert model['links'][0]['joint'] is None and model['links'][1]['joint'] == 'joint_1'
    for link in model['links']:
        geom = urdf[link['name']].find('collision/geometry')[0]
        assert geom.tag == link['shape']['type'] and link['shape']['type'] in ('box', 'cylinder')
        if geom.tag == 'cylinder':
            assert (link['shape']['radius'], link['shape']['length']) == (
                float(geom.get('radius')), float(geom.get('length')))
        else:
            assert link['shape']['size'] == [float(v) for v in geom.get('size').split()]
        assert link['origin'] == [float(v) for v in urdf[link['name']].find(
            'collision/origin').get('xyz').split()]
        assert len(link['color']) == 4


def test_tcp_safe_box_and_poses(profile, model):
    assert model['tcp'] == {'offset': [0.0, 0.0, 0.0], 'wpr': [0.0, 0.0, 0.0], 'frame': 'link_6'}
    assert model['tool'] is None
    z = profile.safety.zone
    assert model['safe_box'] == {'frame': 'world', 'x': [z.lower[0], z.upper[0]],
                                 'y': [z.lower[1], z.upper[1]], 'z': [z.lower[2], z.upper[2]]}
    assert model['named_poses']['ready'] == [0.0, -0.1, 1.65, 0.0, 1.2, 0.0]


def test_no_mesh_no_paths_no_negative_zero(model):
    text = (SRC / REL).read_text(encoding='utf-8')
    assert 'mesh' not in text.lower() and 'package://' not in text and '/home/' not in text
    assert '-0.0' not in text


def test_active_tool_shows_up_with_its_tcp_shapes_and_shrunk_box():
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding='utf-8'))
    data = copy.deepcopy(raw)
    data['tools'] = {'pointer': {
        'tcp': {'offset': [0.0, 0.0, 0.15]}, 'mass': 0.2, 'box_margin': 0.05,
        'collision': [{'type': 'cylinder', 'radius': 0.01, 'length': 0.15,
                       'origin': [0.0, 0.0, 0.075]},
                      {'type': 'sphere', 'radius': 0.02, 'origin': [0.0, 0.0, 0.15]},
                      {'type': 'box', 'size': [0.04, 0.04, 0.03], 'wpr': [0.0, 0.0, 0.5]}]}}
    data['active_tool'] = 'pointer'
    p = parse_profile(data)
    model = json.loads(generate(p)['view_model'])
    assert model['tcp']['offset'] == [0.0, 0.0, 0.15]
    tool = model['tool']
    assert (tool['name'], tool['link'], tool['parent']) == ('pointer', 'tool_pointer', 'link_6')
    assert [s['type'] for s in tool['shapes']] == ['cylinder', 'sphere', 'box']
    assert tool['shapes'][2]['wpr'] == [0.0, 0.0, 0.5] and tool['shapes'][0]['radius'] == 0.01
    base = json.loads(generate(parse_profile(raw))['view_model'])
    assert model['safe_box']['x'][0] == pytest.approx(base['safe_box']['x'][0] + 0.05)
    assert model['links'] == base['links'] and model['joints'] == base['joints']
