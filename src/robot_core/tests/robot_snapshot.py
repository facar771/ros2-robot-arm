"""Everything that defines the robot's behaviour, read back from the files the planner reads.

Used to prove that generating the files from the profile changes nothing: the snapshot of the
generated files must equal ``golden/today_robot.json``, which was taken from the hand-written files
before they were replaced. Independent of robot_core.profile: forward kinematics is computed here
from the URDF alone.
"""
import math
import random
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import yaml


def _floats(text, default='0 0 0'):
    return [float(v) for v in (text or default).split()]


def _geometry(elem):
    if elem.find('cylinder') is not None:
        c = elem.find('cylinder')
        return {'cylinder': [float(c.get('radius')), float(c.get('length'))]}
    return {'box': _floats(elem.find('box').get('size'))}


def _rot(axis, angle):
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    k = 1 - c
    return np.array([[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
                     [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
                     [z * x * k - y * s, z * y * k + x * s, c + z * z * k]])


def forward_kinematics(joints, q):
    """{link: [x, y, z, r11..r33]} for a serial chain of revolute joints (from URDF data)."""
    frame = np.eye(4)
    out = {}
    for joint, angle in zip(joints, q):
        t = np.eye(4)
        t[:3, 3] = joint['origin']
        r = np.eye(4)
        r[:3, :3] = _rot(joint['axis'], angle)
        frame = frame @ t @ r
        out[joint['child']] = [float(v) for v in frame[:3, 3]] + [float(v) for v in
                                                                  frame[:3, :3].ravel()]
    return out


def snapshot(urdf: Path, srdf: Path, config_dir: Path) -> dict:
    root = ET.parse(urdf).getroot()
    links = {}
    for link in root.findall('link'):
        entry = {}
        for part in ('visual', 'collision'):
            origin = link.find(f'{part}/origin')
            entry[part] = {'geometry': _geometry(link.find(f'{part}/geometry')),
                           'origin': _floats(None if origin is None else origin.get('xyz'))}
        entry['material'] = link.find('visual/material').get('name')
        inertial = link.find('inertial')
        origin = inertial.find('origin')
        i = inertial.find('inertia')
        entry['inertial'] = {
            'origin': _floats(None if origin is None else origin.get('xyz')),
            'mass': float(inertial.find('mass').get('value')),
            'inertia': {k: float(i.get(k)) for k in ('ixx', 'ixy', 'ixz', 'iyy', 'iyz', 'izz')}}
        links[link.get('name')] = entry
    joints = []
    for j in root.findall('joint'):
        limit = j.find('limit')
        origin = j.find('origin')
        joints.append({
            'name': j.get('name'), 'type': j.get('type'),
            'parent': j.find('parent').get('link'), 'child': j.find('child').get('link'),
            'origin': _floats(origin.get('xyz')), 'rpy': _floats(origin.get('rpy')),
            'axis': _floats(j.find('axis').get('xyz')),
            'limit': {k: float(limit.get(k)) for k in ('lower', 'upper', 'effort', 'velocity')}})
    materials = {m.get('name'): [float(v) for v in m.find('color').get('rgba').split()]
                 for m in root.findall('material')}

    s = ET.parse(srdf).getroot()
    poses = {st.get('name'): [float(j.get('value')) for j in st.findall('joint')]
             for st in s.findall('group_state')}
    virtual = s.find('virtual_joint')
    acm = sorted(sorted((d.get('link1'), d.get('link2'))) for d in s.findall('disable_collisions'))
    srdf_data = {
        'robot': s.get('name'),
        'groups': {g.get('name'): [j.get('name') for j in g.findall('joint')]
                   for g in s.findall('group')},
        'poses': poses,
        'virtual_joint': [virtual.get(k) for k in ('name', 'type', 'parent_frame', 'child_link')],
        'disabled_pairs': acm}

    def load(name):
        return yaml.safe_load((config_dir / name).read_text(encoding='utf-8'))

    xacro = (config_dir / 'six_axis_robot.ros2_control.xacro').read_text(encoding='utf-8')
    ros2_control = {'joints': re.findall(r'<joint name="(\w+)">', xacro),
                    'plugins': re.findall(r'<plugin>([^<]+)</plugin>', xacro),
                    'initial_value_keys': re.findall(r"initial_positions\['(\w+)'\]", xacro)}

    chain = [j for j in joints if j['type'] == 'revolute']
    rng = random.Random(7)
    configs = [[0.0] * len(chain)] + [list(v) for v in poses.values()]
    for _ in range(6):
        configs.append([rng.uniform(j['limit']['lower'], j['limit']['upper']) for j in chain])
    return {'urdf': {'robot': root.get('name'), 'materials': materials, 'links': links,
                     'joints': joints},
            'fk': [{'q': q, 'frames': forward_kinematics(chain, q)} for q in configs],
            'srdf': srdf_data, 'ros2_control': ros2_control,
            'joint_limits': load('joint_limits.yaml'), 'safe_zone': load('safe_zone.yaml'),
            'initial_positions': load('initial_positions.yaml'),
            'ros2_controllers': load('ros2_controllers.yaml'),
            'moveit_controllers': load('moveit_controllers.yaml')}
