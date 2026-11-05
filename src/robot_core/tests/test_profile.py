"""Robot profile reader + validator: today's robot loads, bad profiles fail with a clear place."""
import copy
import math

import numpy as np
import pytest
import yaml
from robot_core.profile import (
    ProfileError,
    derive_box,
    load_profile,
    parse_profile,
    solid_inertia,
)

from tests.conftest import SRC

PROFILE = SRC / 'robot_description' / 'profile' / 'six_axis_robot.yaml'


@pytest.fixture(scope='module')
def raw():
    return yaml.safe_load(PROFILE.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE)


def bad(raw, mutate, match):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(ProfileError, match=match):
        parse_profile(data)


def good(raw, mutate):
    data = copy.deepcopy(raw)
    mutate(data)
    return parse_profile(data)


# ---- today's robot ----------------------------------------------------------------------------
def test_today_loads(profile):
    assert profile.robot == 'six_axis_robot' and profile.tip_link == 'link_6'
    assert profile.joint_names == tuple(f'joint_{i}' for i in range(1, 7))
    assert profile.reach_radius == pytest.approx(1.05)
    assert profile.reach_center == pytest.approx((0.0, 0.0, 0.24))
    assert not profile.box_derived and profile.safety.max_velocity_scale == 0.5


def test_link_length_drives_geometry_and_next_joint_offset(profile):
    link_2 = profile.link('link_2')
    assert link_2.dims == (0.06, 0.06, 0.35) and link_2.origin == (0.0, 0.0, 0.175)
    assert profile.joints[2].origin == (0.0, 0.0, 0.35)          # joint_3 sits at link_2.length
    base = profile.link('base_link')
    assert base.dims == (0.12, 0.08) and base.origin == (0.0, 0.0, 0.0)   # explicit override
    assert profile.joints[0].origin == (0.0, 0.0, 0.04)


def test_editing_one_length_moves_geometry_and_everything_after(raw):
    p = good(raw, lambda d: d['links']['link_2'].update(length=0.40))
    assert p.link('link_2').dims == (0.06, 0.06, 0.40)
    assert p.link('link_2').origin == (0.0, 0.0, 0.20)
    assert p.joints[2].origin == (0.0, 0.0, 0.40)
    assert p.reach_radius == pytest.approx(1.10)
    assert p.link('link_2').inertia != solid_inertia('box', (0.06, 0.06, 0.35), 1.5)


def test_derived_inertia_equals_the_original_urdf_values(profile):
    assert profile.link('link_2').inertia == (0.015762, 0.015762, 0.0009)
    assert profile.link('base_link').inertia == (0.020667, 0.020667, 0.036)


# ---- structure and scope ----------------------------------------------------------------------
def test_unknown_and_missing_keys(raw):
    bad(raw, lambda d: d.update(bogus=1), 'bilinmeyen anahtar')
    bad(raw, lambda d: d.update(motors={}), 'bilinmeyen anahtar')   # motors: hardware.profile
    bad(raw, lambda d: d['joints']['joint_1'].update(origin=[0, 0, 1]),
        'joints.joint_1.*bilinmeyen')
    bad(raw, lambda d: d.update(profile_version=2), 'profile_version')


def test_only_revolute_joints_in_a_serial_chain(raw):
    bad(raw, lambda d: d['joints']['joint_3'].update(type='prismatic'), "'revolute'.*kapsam dışı")
    bad(raw, lambda d: d['joints']['joint_3'].update(mimic='joint_2'), 'bilinmeyen anahtar')
    bad(raw, lambda d: d['joints']['joint_3'].update(child='link_5'), 'zincir sırası')
    bad(raw, lambda d: d['joints'].pop('joint_6'), 'eklem olmalı')


def test_axis_must_be_a_unit_vector(raw):
    bad(raw, lambda d: d['joints']['joint_2'].update(axis=[0, 2, 0]), 'birim vektör')
    bad(raw, lambda d: d['joints']['joint_2'].update(axis=[0, 1]), '3 sayılık')
    p = good(raw, lambda d: d['joints']['joint_2'].update(axis=[1, 0, 0]))
    assert p.joints[1].axis == (1.0, 0.0, 0.0)


def test_limits_and_defaults_are_the_narrower_choice(raw):
    def strip(d):
        d['joints']['joint_1']['limits'] = {'position': [-1.0, 1.0]}
    j = good(raw, strip).joints[0]
    assert (j.velocity, j.acceleration, j.effort) == (0.5, 0.5, 10.0)
    bad(raw, lambda d: d['joints']['joint_1']['limits'].pop('position'), 'eksik anahtar')
    bad(raw, lambda d: d['joints']['joint_1']['limits'].update(position=[1.0, -1.0]),
        'alt sınır üstten küçük')


@pytest.mark.parametrize('mutate, match', [
    (lambda d: d['joints']['joint_1']['limits'].update(position=[-180, 180]), 'sınır'),    # degrees
    (lambda d: d['links']['link_2'].update(length=350), 'üst sınır'),                      # mm
    (lambda d: d['links']['link_2'].update(length=0.0001), 'alt sınır'),
    (lambda d: d['joints']['joint_1']['limits'].update(velocity=500), 'üst sınır'),
    (lambda d: d['links']['link_1'].update(mass=-1), 'pozitif'),
    (lambda d: d['links']['link_1'].update(mass=float('nan')), 'sonlu'),
    (lambda d: d['links']['link_1']['shape'].update(radius='0.08'), 'sayı'),
])
def test_unit_sanity(raw, mutate, match):
    bad(raw, mutate, match)


def test_materials_and_names(raw):
    bad(raw, lambda d: d['links']['link_1'].update(material='purple'), 'tanımsız malzeme')
    bad(raw, lambda d: d['materials']['gray'].__setitem__(0, 2.0), 'üst sınır')
    bad(raw, lambda d: d['mount'].update(parent='table'), 'world')
    bad(raw, lambda d: d['mount'].update(name='joint_1'), 'çakışması')


# ---- MoveIt -----------------------------------------------------------------------------------
def test_named_poses_and_initial_positions_stay_inside_the_limits(raw):
    bad(raw, lambda d: d['moveit']['named_poses'].update(bad=[0, 2.0, 0, 0, 0, 0]),
        'limiti .* dışında')
    bad(raw, lambda d: d['moveit']['initial_positions'].update(joint_5=1.6), 'dışında')
    bad(raw, lambda d: d['moveit']['named_poses'].update(Home=[0] * 6), 'küçük harf')
    bad(raw, lambda d: d['moveit']['named_poses'].update(short=[0] * 5), '6 sayılık')


def test_extra_collision_pairs_need_a_reason_and_must_not_be_adjacent(raw):
    p = good(raw, lambda d: d['moveit']['collision'].update(
        allow_extra=[['link_1', 'link_3', 'sampled: never touch within limits']]))
    assert p.allow_extra == (('link_1', 'link_3', 'sampled: never touch within limits'),)
    bad(raw, lambda d: d['moveit']['collision'].update(allow_extra=[['link_1', 'link_3']]),
        'gerekçe zorunlu')
    bad(raw, lambda d: d['moveit']['collision'].update(allow_extra=[['link_1', 'link_3', '']]),
        'gerekçe zorunlu')
    bad(raw, lambda d: d['moveit']['collision'].update(allow_extra=[['link_1', 'link_2', 'x']]),
        'zaten her zaman kapalı')
    bad(raw, lambda d: d['moveit']['collision'].update(allow_extra=[['link_1', 'nope', 'x']]),
        'geçerli, farklı iki link')


# ---- safety box -------------------------------------------------------------------------------
def test_explicit_box_must_lie_inside_the_reach(raw):
    bad(raw, lambda d: d['safety']['box'].update(x=[0.05, 1.2]), 'erişimi')
    bad(raw, lambda d: d['safety']['box'].update(z=[-0.1, 0.5]), 'zemin altına')
    bad(raw, lambda d: d['safety'].update(max_velocity_scale=1.5), 'max_velocity_scale')
    bad(raw, lambda d: d['safety'].update(frame='base_link'), 'world')


def test_missing_box_is_derived_and_narrower_than_the_explicit_one(raw, profile):
    p = good(raw, lambda d: d['safety'].pop('box'))
    assert p.box_derived
    lo, hi = p.safety.zone.lower, p.safety.zone.upper
    explicit_lo, explicit_hi = profile.safety.zone.lower, profile.safety.zone.upper
    assert all(a >= b for a, b in zip(lo, explicit_lo))
    assert all(a <= b for a, b in zip(hi, explicit_hi))
    center, radius = p.reach_center, p.reach_radius / 2
    for x in (lo[0], hi[0]):                       # every corner inside the half-reach sphere
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                assert math.dist(center, (x, y, z)) <= radius + 1e-9
    assert lo[2] >= 0.02 and lo[0] >= 0.05


def test_missing_safety_section_entirely_uses_the_narrow_defaults(raw):
    p = good(raw, lambda d: d.pop('safety'))
    assert p.box_derived and p.safety.max_velocity_scale == 0.5


def test_derive_box_rejects_a_degenerate_arm():
    with pytest.raises(ProfileError, match='boş'):
        derive_box((0.0, 0.0, 0.0), 0.05)


def test_load_profile_names_the_file_and_the_problem(tmp_path):
    path = tmp_path / 'p.yaml'
    path.write_text('profile_version: 3\n', encoding='utf-8')
    with pytest.raises(ProfileError, match=r'p\.yaml.*eksik anahtar'):
        load_profile(path)
    with pytest.raises(ProfileError, match='okunamadı'):
        load_profile(tmp_path / 'missing.yaml')
    path.write_text('a: [1', encoding='utf-8')
    with pytest.raises(ProfileError, match='geçerli YAML değil'):
        load_profile(path)


# ---- tool centre point and the `ready` pose (J4) ------------------------------------------------
def test_tcp_defaults_to_the_flange_and_is_optional(raw, profile):
    assert profile.tcp.offset == (0.0, 0.0, 0.0) and profile.tcp.wpr == (0.0, 0.0, 0.0)
    assert (profile.tcp.transform == np.eye(4)).all()
    assert good(raw, lambda d: d.pop('tcp')).tcp == profile.tcp          # omitted = the flange
    assert good(raw, lambda d: d.update(tcp=None)).tcp == profile.tcp
    assert good(raw, lambda d: d.update(tcp={})).tcp == profile.tcp


def test_tcp_values_are_parsed_and_validated(raw):
    p = good(raw, lambda d: d.update(tcp={'offset': [0.01, -0.02, 0.15], 'wpr': [0.1, 0.2, 0.3]}))
    assert p.tcp.offset == (0.01, -0.02, 0.15) and p.tcp.wpr == (0.1, 0.2, 0.3)
    assert p.tcp.transform[:3, 3] == pytest.approx([0.01, -0.02, 0.15])
    bad(raw, lambda d: d.update(tcp={'offset': [0, 0, 150]}), r'tcp\.offset\[2\]')     # mm typed
    bad(raw, lambda d: d.update(tcp={'offset': [0, 0]}), r'3 sayılık')
    bad(raw, lambda d: d.update(tcp={'offset': [0, 0, math.nan]}), 'sonlu')
    # degrees typed where radians belong
    bad(raw, lambda d: d.update(tcp={'wpr': [0, 0, 90]}), r'tcp\.wpr\[2\]')
    bad(raw, lambda d: d.update(tcp={'rpy': [0, 0, 0]}), 'bilinmeyen anahtar')
    bad(raw, lambda d: d.update(tcp=[0, 0, 0]), 'eşleme')


def test_tcp_does_not_widen_the_derived_safe_box(raw, profile):
    p = good(raw, lambda d: (d.update(tcp={'offset': [0, 0, 0.4]}), d['safety'].pop('box')))
    q = good(raw, lambda d: d['safety'].pop('box'))
    assert p.safety.zone == q.safety.zone                                # narrower default stays
    assert p.reach_radius == pytest.approx(q.reach_radius + 0.35)         # 0.4 - tool link 0.05


def test_ready_pose_is_inside_the_box_well_clear_of_limits_and_not_singular(profile):
    from robot_core.kinematics import Chain
    ready = np.array(profile.named_poses['ready'])
    assert ready.tolist() == [0.0, -0.1, 1.65, 0.0, 1.2, 0.0]
    chain = Chain(profile)
    pose, jac = chain.fk_and_jacobian(ready)
    assert profile.safety.zone.check(tuple(pose[:3, 3])) is None
    assert np.linalg.svd(jac, compute_uv=False)[-1] == pytest.approx(0.23, abs=0.01)
    assert np.min(np.minimum(ready - chain.lower, chain.upper - ready)) >= 0.3


def test_ready_pose_matches_the_constant_the_jog_api_still_carries(profile):
    """jogapi.READY_POSE is a copy (Robot_3's file); this keeps the two from drifting apart."""
    from robot_core.jogapi import READY_NAME, READY_POSE
    assert profile.named_poses[READY_NAME] == pytest.approx(READY_POSE, abs=0)
