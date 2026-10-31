"""Geometric check with FCL: the ACM must cover every collision that occurs inside joint limits.

Skipped automatically when python-fcl / numpy are not installed.
"""
import itertools
import math
import random
import xml.etree.ElementTree as ET

import pytest

fcl = pytest.importorskip('fcl')
np = pytest.importorskip('numpy')

from tests.conftest import SRDF, URDF  # noqa: E402


def _rot(axis, theta):
    a = np.array(axis, float)
    a /= np.linalg.norm(a)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(theta) * k + (1 - math.cos(theta)) * (k @ k)


def _vec(text, default='0 0 0'):
    return [float(v) for v in (text or default).split()]


def _model():
    root = ET.parse(URDF).getroot()
    joints = {}
    for j in root.findall('joint'):
        lim = j.find('limit')
        origin = j.find('origin')
        joints[j.get('name')] = dict(
            parent=j.find('parent').get('link'), child=j.find('child').get('link'),
            xyz=_vec(origin.get('xyz') if origin is not None else None),
            axis=_vec(j.find('axis').get('xyz')), lo=float(lim.get('lower')),
            up=float(lim.get('upper')))
    shapes = {}
    for link in root.findall('link'):
        col = link.find('collision')
        geom = col.find('geometry')[0]
        origin = col.find('origin')
        offset = np.array(_vec(origin.get('xyz') if origin is not None else None))
        if geom.tag == 'box':
            shape = fcl.Box(*_vec(geom.get('size')))
        else:
            shape = fcl.Cylinder(float(geom.get('radius')), float(geom.get('length')))
        shapes[link.get('name')] = (shape, offset)
    return joints, shapes


def _colliding(joints, shapes, q):
    transforms = {'base_link': np.eye(4)}
    for name, theta in zip(sorted(joints), q):
        j = joints[name]
        t_joint = np.eye(4)
        t_joint[:3, 3] = j['xyz']
        rot = np.eye(4)
        rot[:3, :3] = _rot(j['axis'], theta)
        transforms[j['child']] = transforms[j['parent']] @ t_joint @ rot
    objects = {}
    for name, (shape, offset) in shapes.items():
        t = transforms[name].copy()
        t[:3, 3] = t[:3, 3] + t[:3, :3] @ offset
        objects[name] = fcl.CollisionObject(shape, fcl.Transform(t[:3, :3], t[:3, 3]))
    hits = set()
    for a, b in itertools.combinations(sorted(shapes), 2):
        result = fcl.CollisionResult()
        if fcl.collide(objects[a], objects[b], fcl.CollisionRequest(), result) > 0:
            hits.add(frozenset((a, b)))
    return hits


def test_acm_covers_all_collisions_within_joint_limits():
    joints, shapes = _model()
    disabled = {frozenset((d.get('link1'), d.get('link2')))
                for d in ET.parse(SRDF).getroot().findall('disable_collisions')}
    rng = random.Random(1)
    names = sorted(joints)
    uncovered = set()
    for _ in range(1500):
        q = [rng.uniform(joints[n]['lo'], joints[n]['up']) for n in names]
        uncovered |= _colliding(joints, shapes, q) - disabled
    assert uncovered == set(), f'ACM dışında çarpışan çiftler: {[sorted(p) for p in uncovered]}'


def test_home_pose_is_collision_free_after_acm():
    joints, shapes = _model()
    disabled = {frozenset((d.get('link1'), d.get('link2')))
                for d in ET.parse(SRDF).getroot().findall('disable_collisions')}
    assert _colliding(joints, shapes, [0.0] * len(joints)) - disabled == set()
