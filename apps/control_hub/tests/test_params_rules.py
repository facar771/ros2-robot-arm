# ruff: noqa: F811  (pytest fixture, params_support.py içinden içe aktarılır)
"""P4 Parametreler: saf kurallar ve alan kataloğu, SINIR değerlerinde (min=max, hız 0, z 19.99/20, pot 8/9 ve
1014/1015, birim sağduyusu) ve sabitlerin robot_core ile aynı kaynaktan olduğu."""
import math

import pytest

pytest.importorskip('numpy')
pytest.importorskip('yaml')

from control_hub.hub_core import profile_fields as pf  # noqa: E402
from control_hub.hub_core import profile_rules as rules  # noqa: E402
from control_hub.hub_core import profile_view as pv  # noqa: E402
from tests.params_support import box, data_dir, fields_of, pot, src  # noqa: E402,F401


# --- saf kurallar: sınır değerleri ------------------------------------------------------------------------
@pytest.mark.parametrize('lo,hi,bad', [(0, 0, True), (10, 5, True), (0, 0.001, False), (-90, 90, False)])
def test_axis_min_max_boundary(lo, hi, bad):
    out = rules.check_axis({'axes.j.min_deg': lo, 'axes.j.max_deg': hi}, 'j')
    assert ('axes.j.min_deg' in fields_of(out)) is bad


@pytest.mark.parametrize('suffix', ['vel_dps', 'acc_dps2', 'drive_vel_dps', 'drive_acc_dps2', 'slew_dps',
                                    'gear_ratio'])
@pytest.mark.parametrize('value,bad', [(-1, True), (0, True), (0.001, False), (45, False)])
def test_positive_caps_boundary(suffix, value, bad):
    out = rules.check_axis({f'axes.j.{suffix}': value}, 'j')
    assert (f'axes.j.{suffix}' in fields_of(out)) is bad


@pytest.mark.parametrize('home,bad', [(-90, False), (90, False), (-90.001, True), (90.001, True), (0, False)])
def test_home_inside_axis_range_boundary(home, bad):
    v = {'axes.j.min_deg': -90, 'axes.j.max_deg': 90, 'axes.j.home_deg': home}
    assert ('axes.j.home_deg' in fields_of(rules.check_axis(v, 'j'))) is bad


@pytest.mark.parametrize('lo,hi,bad', [(100, 100, True), (101, 100, True), (100, 100.001, False)])
def test_safe_box_min_max_boundary(lo, hi, bad):
    out = rules.check_box({'safety.x_min_mm': lo, 'safety.x_max_mm': hi})
    assert ('safety.x_min_mm' in fields_of(out)) is bad


@pytest.mark.parametrize('z,bad', [(19.99, True), (20.0, False), (0, True), (-5, True), (200, False)])
def test_safe_box_floor_boundary(z, bad):
    assert bool(fields_of(rules.check_box({'safety.z_min_mm': z}))) is bad


@pytest.mark.parametrize('tcp,bad', [((60, 0, 20), False), ((220, 100, 300), False), ((59.999, 0, 100), True),
                                     ((100, 100.001, 100), True), ((100, 0, 300.001), True),
                                     ((140, 0, 160), False)])
def test_home_in_box_boundary_is_inclusive(tcp, bad):
    got = rules.home_in_box(box(), tcp)
    assert (got is not None) is bad
    if got:
        assert got.field == 'safety' and got.severity == 'error'


@pytest.mark.parametrize('lo,bad', [(8, True), (9, False), (0, True), (100, False)])
def test_pot_low_invalid_zone_boundary(lo, bad):
    assert ('pot.j.raw_min' in fields_of(rules.check_pot(pot(lo=lo, hi=900), ['j']))) is bad


@pytest.mark.parametrize('hi,bad', [(1014, False), (1015, True), (1023, True)])
def test_pot_high_invalid_zone_boundary(hi, bad):
    assert ('pot.j.raw_max' in fields_of(rules.check_pot(pot(lo=100, hi=hi), ['j']))) is bad


def test_pot_raw_order_span_channel_and_completeness():
    assert 'pot.j.raw_min' in fields_of(rules.check_pot(pot(lo=500, hi=500), ['j']))
    assert 'pot.j.raw_min' in fields_of(rules.check_pot(pot(lo=600, hi=500), ['j']))
    narrow = rules.check_pot(pot(lo=100, hi=199), ['j'])                     # 99 sayı: yalnız UYARI
    assert not fields_of(narrow) and [i.severity for i in narrow] == ['warning']
    assert not rules.check_pot(pot(lo=100, hi=200), ['j'])                  # tam 100: uyarı yok
    assert 'pot.j.angle_at_max_deg' in fields_of(rules.check_pot(pot(a0=10, a1=10), ['j']))
    two = {**pot('a', ch=3), **pot('b', ch=3)}
    assert 'pot.b.channel' in fields_of(rules.check_pot(two, ['a', 'b']))
    half = pot()
    half['pot.j.raw_max'] = None
    assert 'pot.j.raw_max' in fields_of(rules.check_pot(half, ['j']))
    assert not rules.check_pot({}, ['j'])                                    # hiç girilmemiş satır sessiz


def test_pot_angle_is_linear_and_not_clamped():
    assert rules.pot_angle(500, 100, 900, -90, 90) == pytest.approx(0)
    assert rules.pot_angle(100, 100, 900, -90, 90) == pytest.approx(-90)
    assert rules.pot_angle(1000, 100, 900, -90, 90) == pytest.approx(112.5)    # uç dışı: olduğu gibi


@pytest.mark.parametrize('key,value,bad', [
    ('axes.j.min_deg', 720, False), ('axes.j.min_deg', 720.001, True), ('axes.j.min_deg', -720.001, True),
    ('axes.j.pulse_min_us', 300, False), ('axes.j.pulse_min_us', 299.99, True),
    ('axes.j.pulse_max_us', 3000, False), ('axes.j.pulse_max_us', 3000.01, True),
    ('pot.j.raw_min', 0, False), ('pot.j.raw_max', 1023, False), ('pot.j.raw_max', 1024, True),
    ('pot.j.channel', 0, True), ('pot.j.channel', 255, False), ('pot.j.channel', 256, True),
    ('axes.j.direction', 1, False), ('axes.j.direction', -1, False), ('axes.j.direction', 0, True),
    ('axes.j.steps_per_rev', 1.5, True), ('axes.j.vel_dps', math.inf, True), ('axes.j.vel_dps', True, True),
    ('axes.j.vel_dps', '45', True), ('axes.j.vel_dps', pf.MAX_SPEED_DPS, False),
    ('axes.j.vel_dps', pf.MAX_SPEED_DPS + 0.01, True)])
def test_structural_value_checks(key, value, bad):
    suffix = key.split('.', 2)[2]
    maker = {'min_deg': dict(unit='°', kind='float', lo=-pf.MAX_ANGLE_DEG, hi=pf.MAX_ANGLE_DEG),
             'pulse_min_us': dict(unit='µs', kind='float', lo=pf.PULSE_US_MIN, hi=pf.PULSE_US_MAX),
             'pulse_max_us': dict(unit='µs', kind='float', lo=pf.PULSE_US_MIN, hi=pf.PULSE_US_MAX),
             'raw_min': dict(unit='ADC', kind='int', lo=0, hi=pf.ADC_MAX),
             'raw_max': dict(unit='ADC', kind='int', lo=0, hi=pf.ADC_MAX),
             'channel': dict(unit='', kind='int', lo=1, hi=pf.POT_CHANNEL_MAX),
             'direction': dict(unit='', kind='sign'),
             'steps_per_rev': dict(unit='', kind='int', lo=1, hi=100000),
             'vel_dps': dict(unit='°/s', kind='float', hi=pf.MAX_SPEED_DPS)}[suffix]
    spec = pf.FieldSpec(key=key, label='x', doc='profile', path=('x',), group='axes', **maker)
    assert (pf.check_edit(spec, value) is not None) is bad


# --- sabitler robot_core ile AYNI kaynaktan -----------------------------------------------------------------
def test_sanity_constants_equal_robot_core(src):
    from robot_core import hardware as rh
    from robot_core import maturity as rm
    from robot_core import profile as rp
    assert pf.MAX_ANGLE_DEG == pytest.approx(math.degrees(rp.MAX_ANGLE))
    assert pf.MAX_SPEED_DPS == pytest.approx(math.degrees(rp.MAX_SPEED))
    assert pf.MAX_ACCEL_DPS2 == pytest.approx(math.degrees(rp.MAX_ACCEL))
    assert pf.MAX_LENGTH_MM == pytest.approx(rp.MAX_LENGTH * 1000)
    assert pf.MAX_TCP_MM == pytest.approx(rp.MAX_TCP_OFFSET * 1000)
    assert rules.BOX_FLOOR_MM == pytest.approx(rp.BOX_FLOOR * 1000)
    assert (pf.PULSE_US_MIN, pf.PULSE_US_MAX) == tuple(rh.PULSE_US_BOUNDS)
    assert rules.MIN_PULSE_SPAN_US == rh.MIN_PULSE_SPAN_US
    assert rules.MAX_SERVO_SPAN_DEG == pytest.approx(math.degrees(rh.MAX_SERVO_SPAN))
    assert pv.TAGS == tuple(rm.TAGS) and pv.DEFAULT_TAG == rm.DEFAULT_TAG


def test_steps_per_degree_formula():
    assert pf.steps_per_degree(200, 16, 1) == pytest.approx(200 * 16 / 360)
    assert pf.steps_per_degree(2048, 1, 4) == pytest.approx(2048 * 4 / 360)
    assert pf.steps_per_degree(200, 16, 5.18) == pytest.approx(200 * 16 * 5.18 / 360)


