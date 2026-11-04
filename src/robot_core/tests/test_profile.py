"""Robot profile reader + validator: today's robot loads, bad profiles fail with a clear place."""
import copy
import math

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


def test_motor_conversions(profile, raw):
    m = profile.motors['joint_2']
    assert (m.type, m.encoder, m.steps_per_motor_rev) == ('step', 'none', 3200)
    assert m.to_motor(0.7) == 0.7 and m.to_joint(m.to_motor(0.7)) == pytest.approx(0.7)
    p = good(raw, lambda d: d['motors']['joint_2'].update(
        direction=-1, gear_ratio=10.0, zero_offset=0.1))
    m = p.motors['joint_2']
    assert m.to_motor(0.6) == pytest.approx(-10.0 * 0.5)
    assert m.to_joint(m.to_motor(-1.0)) == pytest.approx(-1.0)
    expected = -10 * (math.pi / 5 - 0.1) / (2 * math.pi) * 3200
    assert m.to_steps(math.pi / 5) == pytest.approx(expected)


# ---- structure and scope ----------------------------------------------------------------------
def test_unknown_and_missing_keys(raw):
    bad(raw, lambda d: d.update(bogus=1), 'bilinmeyen anahtar')
    bad(raw, lambda d: d.pop('motors'), 'eksik anahtar')
    bad(raw, lambda d: d['joints']['joint_1'].update(origin=[0, 0, 1]),
        'joints.joint_1.*bilinmeyen')
    bad(raw, lambda d: d.update(profile_version=1), 'profile_version')


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


# ---- motors -----------------------------------------------------------------------------------
@pytest.mark.parametrize('mutate, match', [
    (lambda m: m.update(direction=2), r'\+1 ya da -1'),
    (lambda m: m.update(direction=0), r'\+1 ya da -1'),
    (lambda m: m.update(direction='cw'), r'\+1 ya da -1'),
    (lambda m: m.update(direction=True), r'\+1 ya da -1'),
    (lambda m: m.update(gear_ratio=0), 'pozitif'),
    (lambda m: m.update(gear_ratio=-2.0), 'pozitif'),
    (lambda m: m.update(microstep=0), 'aralık'),
    (lambda m: m.update(microstep=512), 'aralık'),
    (lambda m: m.update(microstep=1.5), 'tam sayı'),
    (lambda m: m.update(steps_per_rev=0), 'aralık'),
    (lambda m: m.update(type='dc'), "yalnızca 'step'"),
    (lambda m: m.update(encoder='optical'), 'none | incremental | absolute'),
    (lambda m: m.update(zero_offset=90), 'üst sınır'),
    (lambda m: m.pop('limits'), 'eksik anahtar'),
    (lambda m: m.update(unknown=1), 'bilinmeyen anahtar'),
])
def test_motor_validation(raw, mutate, match):
    bad(raw, lambda d: mutate(d['motors']['joint_3']), match)


def test_encoder_is_optional_and_defaults_to_none(raw):
    def drop(d):
        d['motors']['joint_1'].pop('encoder')
    assert good(raw, drop).motors['joint_1'].encoder == 'none'
    for kind in ('none', 'incremental', 'absolute'):
        p = good(raw, lambda d, k=kind: d['motors']['joint_1'].update(encoder=k))
        assert p.motors['joint_1'].encoder == kind


def test_joint_limits_must_lie_inside_motor_limits_minus_margin(raw):
    # equal limits and margin 0 (today): passes; any shortfall or margin: fails
    bad(raw, lambda d: d['motors']['joint_2']['limits'].update(position=[-1.5, 1.57]),
        r'motor limiti .* içinde değil')
    bad(raw, lambda d: d['motors']['joint_2']['limits'].update(position=[-1.57, 1.5]),
        r'motor limiti .* içinde değil')
    bad(raw, lambda d: d['motors']['joint_2']['margin'].update(position=0.01),
        r'marj 0\.01')
    good(raw, lambda d: (d['motors']['joint_2']['limits'].update(position=[-1.7, 1.7]),
                         d['motors']['joint_2']['margin'].update(position=0.1)))
    bad(raw, lambda d: (d['motors']['joint_2']['limits'].update(position=[-1.7, 1.7]),
                        d['motors']['joint_2']['margin'].update(position=0.2)),
        r'motor limiti .* içinde değil')


def test_omitted_margin_is_the_narrow_default(raw):
    def widen(d):
        d['motors']['joint_2'].pop('margin')
        d['motors']['joint_2']['limits']['position'] = [-1.62, 1.62]      # exactly 0.05 slack
        d['motors']['joint_2']['limits']['velocity'] = 1.05              # likewise for speed
    p = good(raw, widen)
    margins = (p.motors['joint_2'].margin_position, p.motors['joint_2'].margin_velocity)
    assert margins == (0.05, 0.05)

    def too_tight(d):
        d['motors']['joint_2'].pop('margin')
        d['motors']['joint_2']['limits']['position'] = [-1.6, 1.6]
    bad(raw, too_tight, 'marj 0.05')


def test_joint_speed_must_lie_inside_motor_speed_minus_margin(raw):
    bad(raw, lambda d: d['motors']['joint_1']['limits'].update(velocity=0.9), 'hız limiti')
    bad(raw, lambda d: d['motors']['joint_1']['margin'].update(velocity=0.1), 'hız limiti')
    good(raw, lambda d: d['motors']['joint_1']['limits'].update(velocity=1.2))


def test_motors_must_match_the_joints(raw):
    bad(raw, lambda d: d['motors'].pop('joint_6'), 'motors: eklemlerle aynı')
    bad(raw, lambda d: d.update(motors=dict(reversed(list(d['motors'].items())))), 'aynı adlar')


def test_load_profile_names_the_file_and_the_problem(tmp_path):
    path = tmp_path / 'p.yaml'
    path.write_text('profile_version: 2\n', encoding='utf-8')
    with pytest.raises(ProfileError, match=r'p\.yaml.*eksik anahtar'):
        load_profile(path)
    with pytest.raises(ProfileError, match='okunamadı'):
        load_profile(tmp_path / 'missing.yaml')
    path.write_text('a: [1', encoding='utf-8')
    with pytest.raises(ProfileError, match='geçerli YAML değil'):
        load_profile(path)
