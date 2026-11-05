"""Q1 tools: profile `tools:` parsing, apply_tool (TCP + safe box + collision together), URDF."""
import copy
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import yaml
from robot_core.jog import DlsJogEngine
from robot_core.kinematics import Chain
from robot_core.profile import ProfileError, apply_tool, load_profile, parse_profile
from robot_core.profile_gen import PROFILE_PATH, generate
from robot_core.se3 import matrix_from_wpr

from robot_core.config import RobotConfig

POINTER = {
    'tcp': {'offset': [0.0, 0.0, 0.15], 'wpr': [0.0, 0.0, 0.0]},
    'mass': 0.2,
    'collision': [
        {'type': 'cylinder', 'radius': 0.01, 'length': 0.15, 'origin': [0.0, 0.0, 0.075]},
        {'type': 'sphere', 'radius': 0.02, 'origin': [0.0, 0.0, 0.15]},
    ],
    'sensors': [
        {'type': 'camera', 'name': 'cam', 'pose': {'offset': [0.05, 0.0, 0.03]},
         'resolution': [640, 480], 'fov': math.pi / 2},
        {'type': 'laser', 'name': 'laser', 'origin': [0.0, 0.0, 0.14], 'axis': [0.0, 0.0, 1.0]},
    ],
}


@pytest.fixture(scope='module')
def raw():
    return yaml.safe_load(PROFILE_PATH.read_text(encoding='utf-8'))


def with_tools(raw, active=None, **tool_edits):
    data = copy.deepcopy(raw)
    tool = copy.deepcopy(POINTER)
    tool.update(tool_edits)
    data['tools'] = {'pointer': tool}
    if active:
        data['active_tool'] = active
    return data


def bad(raw, match, active=None, **tool_edits):
    with pytest.raises(ProfileError, match=match):
        parse_profile(with_tools(raw, active, **tool_edits))


# ---- default = flange ---------------------------------------------------------------------------
def test_no_tool_is_the_flange_and_changes_nothing(raw):
    p = load_profile(PROFILE_PATH)
    assert p.tools == {} and p.active_tool is None and p.tool is None
    assert (p.tcp.transform == np.eye(4)).all() and p.safety == p.base_safety
    p2 = parse_profile(with_tools(raw))                 # defined but not active: still the flange
    assert p2.active_tool is None and p2.tcp == p.tcp and p2.safety == p.safety
    assert generate(p2) == generate(p)                  # generated files identical
    assert 'pointer' in p2.tools and len(p2.tools['pointer'].sensors) == 2


# ---- one switch updates everything -------------------------------------------------------------
def test_apply_tool_updates_tcp_box_and_collision_together(raw):
    base = parse_profile(with_tools(raw))
    p = apply_tool(base, 'pointer')
    tool = p.tool
    assert p.active_tool == 'pointer' and p.tcp.offset == (0.0, 0.0, 0.15)
    # TCP -> Chain (jog FK) uses it
    q = np.array([0.1, -0.2, 0.9, 0.2, 0.8, -0.3])
    flange, tcp = Chain(base).fk(q), Chain(p).fk(q)
    assert tcp[:3, 3] == pytest.approx(flange[:3, 3] + flange[:3, 2] * 0.15)
    # safe box shrinks by the default margin = farthest geometry point around the TCP
    m = tool.box_margin
    assert m == pytest.approx(max(0.075 + math.sqrt(0.01 ** 2 + 0.01 ** 2 + 0.075 ** 2),
                                  0.0 + math.sqrt(3) * 0.02))
    for lo0, lo1, hi0, hi1 in zip(base.safety.zone.lower, p.safety.zone.lower,
                                  base.safety.zone.upper, p.safety.zone.upper):
        assert lo1 == pytest.approx(lo0 + m) and hi1 == pytest.approx(hi0 - m)
    assert DlsJogEngine(Chain(p), p.safety).safety.zone == p.safety.zone      # jog gets it
    # collision geometry + mass in the generated URDF, adjacent pair in the SRDF
    out = generate(p)
    root = ET.fromstring(out['urdf'].split('?>', 1)[1])
    link = next(e for e in root.findall('link') if e.get('name') == 'tool_pointer')
    assert len(link.findall('collision')) == 2 and len(link.findall('visual')) == 2
    assert float(link.find('inertial/mass').get('value')) == 0.2
    joint = next(e for e in root.findall('joint') if e.get('name') == 'tool_pointer_mount')
    assert (joint.get('type'), joint.find('parent').get('link')) == ('fixed', 'link_6')
    assert 'link1="link_6" link2="tool_pointer"' in out['srdf']
    # back to the flange restores everything exactly
    back = apply_tool(p, None)
    assert back.tcp == base.tcp and back.safety == base.safety and generate(back) == generate(base)


def test_yaml_active_tool_and_loader_override(raw, tmp_path):
    path = tmp_path / 'p.yaml'
    path.write_text(yaml.safe_dump(with_tools(raw, active='pointer')), encoding='utf-8')
    assert load_profile(path).active_tool == 'pointer'
    assert load_profile(path, tool='pointer').tcp.offset == (0.0, 0.0, 0.15)
    with pytest.raises(ProfileError, match='yok'):
        load_profile(path, tool='nope')
    with pytest.raises(ProfileError, match='yok'):
        apply_tool(load_profile(path), 'nope')


def test_generated_urdf_with_tool_still_loads_as_a_robot_config(raw, tmp_path):
    out = generate(parse_profile(with_tools(raw, active='pointer')))
    (tmp_path / 'r.urdf').write_text(out['urdf'], encoding='utf-8')
    (tmp_path / 'r.srdf').write_text(out['srdf'], encoding='utf-8')
    cfg = RobotConfig.from_files(tmp_path / 'r.urdf', tmp_path / 'r.srdf')
    assert cfg.joint_names[0] == 'joint_1' and len(cfg.joint_names) == 6
    assert cfg.tip_link == 'tool_pointer'               # same frame as link_6 (fixed, zero offset)


def test_rotated_shape_bounds_and_default_inertia(raw):
    shape = {'type': 'box', 'size': [0.1, 0.02, 0.04], 'wpr': [0.0, 0.0, math.pi / 2]}
    p = parse_profile(with_tools(raw, collision=[shape], mass=1.2))
    t = p.tools['pointer']
    # box turned 90 deg about z: bounds are 0.02 x 0.1 x 0.04 around the origin
    ixx, iyy, izz = 1.2 * (0.1 ** 2 + 0.04 ** 2) / 12, 1.2 * (0.02 ** 2 + 0.04 ** 2) / 12, \
        1.2 * (0.02 ** 2 + 0.1 ** 2) / 12
    assert t.inertia == pytest.approx((ixx, iyy, izz), abs=1e-6) and t.com == (0.0, 0.0, 0.0)
    assert matrix_from_wpr(0, 0, math.pi / 2)[0, 1] == pytest.approx(-1)


def test_explicit_margin_zero_keeps_the_box_and_an_empty_box_is_refused(raw):
    base = parse_profile(with_tools(raw))
    p = apply_tool(parse_profile(with_tools(raw, box_margin=0.0)), 'pointer')
    assert p.safety.zone == base.safety.zone
    with pytest.raises(ProfileError, match='boşalıyor'):
        parse_profile(with_tools(raw, active='pointer', box_margin=0.4))


# ---- sensors ------------------------------------------------------------------------------------
def test_camera_intrinsics_from_resolution_and_fov(raw):
    cam = parse_profile(with_tools(raw)).tools['pointer'].sensors[0]
    k = cam.intrinsics()
    assert k['fx'] == pytest.approx(320.0) and k['fy'] == k['fx']          # 90 deg, 640 px
    assert (k['cx'], k['cy'], k['width'], k['height']) == (320.0, 240.0, 640, 480)
    laser = parse_profile(with_tools(raw)).tools['pointer'].sensors[1]
    assert laser.origin == (0.0, 0.0, 0.14) and laser.axis == (0.0, 0.0, 1.0)


# ---- validation ---------------------------------------------------------------------------------
def test_tool_validation_names_the_place(raw):
    bad(raw, r'tools\.pointer.*mass', mass=0)
    bad(raw, r'tools\.pointer.*mass', mass=50.0)                       # kg typed for g
    bad(raw, r'collision', collision=[])
    bad(raw, r'collision\[0\]\.type', collision=[{'type': 'cone', 'radius': 0.1}])
    bad(raw, r'collision\[0\]\.radius', collision=[{'type': 'sphere', 'radius': 20}])
    bad(raw, 'bilinmeyen', surprise=1)
    bad(raw, r'tcp\.offset', tcp={'offset': [0, 0, 5]})
    bad(raw, r'box_margin', box_margin=-0.1)
    bad(raw, r'sensors\[0\]\.fov', sensors=[{**POINTER['sensors'][0], 'fov': 90}])      # degrees
    bad(raw, r'resolution', sensors=[{**POINTER['sensors'][0], 'resolution': [640]}])
    bad(raw, r'resolution\[0\]', sensors=[{**POINTER['sensors'][0], 'resolution': [4, 480]}])
    bad(raw, r'axis', sensors=[{**POINTER['sensors'][1], 'axis': [0, 0, 2]}])
    bad(raw, r'sensors\[0\]\.type', sensors=[{'type': 'radar', 'name': 'r'}])
    bad(raw, 'benzersiz', sensors=[POINTER['sensors'][0], POINTER['sensors'][0]])
    bad(raw, r'active_tool.*yok', active='ghost')


def test_active_tool_and_top_level_tcp_are_mutually_exclusive(raw):
    data = with_tools(raw, active='pointer')
    data['tcp'] = {'offset': [0.0, 0.0, 0.1]}
    with pytest.raises(ProfileError, match='active_tool varken'):
        parse_profile(data)
    data['tcp'] = {'offset': [0.0, 0.0, 0.0]}                   # explicit zero is the flange: ok
    assert parse_profile(data).active_tool == 'pointer'


def test_collision_geometry_and_mass_are_required(raw):
    for key in ('collision', 'mass'):
        data = with_tools(raw)
        del data['tools']['pointer'][key]
        with pytest.raises(ProfileError, match=f'eksik.*{key}'):
            parse_profile(data)
