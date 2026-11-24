"""P4 Parametreler sayfası: saf kurallar (SINIR değerleri), katalog, doğrulama hattı, diff, taslak (atomik),
depo dosyalarına YAZILMADIĞININ kanıtı ve uçlar (oturum / kilit / gövde denetimi)."""
import hashlib
import math
import os
from pathlib import Path

import pytest

pytest.importorskip('numpy')
pytest.importorskip('yaml')

import yaml  # noqa: E402

from control_hub.hub_core import profile_fields as pf  # noqa: E402
from control_hub.hub_core import profile_rules as rules  # noqa: E402
from control_hub.hub_core import profile_view as pv  # noqa: E402
from tests.conftest import J  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PROFILE_ROOT = REPO / 'src' / 'robot_description' / 'profile'


def repo_hashes():
    return {str(p.relative_to(PROFILE_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(PROFILE_ROOT.rglob('*')) if p.is_file()}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / 'hubdata'
    monkeypatch.setenv('HUB_DATA_DIR', str(d))
    monkeypatch.delenv('HUB_PROFILE_DIR', raising=False)
    return d


@pytest.fixture
def src(data_dir):
    return pv.load_source()


def fields_of(issues):
    return {i.field for i in issues if i.severity == 'error'}


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


def box(x=(60, 220), y=(-100, 100), z=(20, 300)):
    return {'safety.x_min_mm': x[0], 'safety.x_max_mm': x[1], 'safety.y_min_mm': y[0],
            'safety.y_max_mm': y[1], 'safety.z_min_mm': z[0], 'safety.z_max_mm': z[1]}


@pytest.mark.parametrize('tcp,bad', [((60, 0, 20), False), ((220, 100, 300), False), ((59.999, 0, 100), True),
                                     ((100, 100.001, 100), True), ((100, 0, 300.001), True),
                                     ((140, 0, 160), False)])
def test_home_in_box_boundary_is_inclusive(tcp, bad):
    got = rules.home_in_box(box(), tcp)
    assert (got is not None) is bad
    if got:
        assert got.field == 'safety' and got.severity == 'error'


def pot(n='j', ch=1, lo=100, hi=900, a0=-90, a1=90):
    return {f'pot.{n}.channel': ch, f'pot.{n}.raw_min': lo, f'pot.{n}.raw_max': hi,
            f'pot.{n}.angle_at_min_deg': a0, f'pot.{n}.angle_at_max_deg': a1}


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


# --- katalog + görünüm -------------------------------------------------------------------------------------
def test_view_model_lists_axes_kinematics_box_tool_and_pot(src):
    vm = pv.view_model(src)
    keys = {f['key'] for f in vm['fields']}
    assert vm['robot'] == 'karma_robot' and vm['joints'] == src.joints
    for n in src.joints:
        assert {f'axes.{n}.min_deg', f'axes.{n}.max_deg', f'axes.{n}.vel_dps', f'axes.{n}.acc_dps2',
                f'axes.{n}.home_deg'} <= keys
        assert any(k.startswith(f'pot.{n}.') for k in keys)
    assert {'safety.x_min_mm', 'safety.z_max_mm', 'tcp.x_mm', 'tcp.w_deg', 'kin.link_1.length_mm'} <= keys
    assert [a['motor'] for a in vm['axes']][:3] == ['step', 'step', 'hobby_servo']
    assert vm['home_tcp_mm'] and len(vm['home_tcp_mm']) == 3 and not vm['issues']
    # türetilen: adım/° = adım/tur × mikroadım × dişli / 360 (aynı formül, ham YAML'dan bağımsız hesap)
    ax = src.docs.hardware['axes']['joint_1']
    want = ax['motor']['steps_per_rev'] * ax['driver']['microstep'] * ax['gear_ratio'] / 360
    assert vm['derived']['joint_1']['steps_per_deg'] == pytest.approx(want, rel=1e-4)
    assert vm['derived']['joint_3']['servo_dir'] in (1, -1)


def test_every_displayed_value_matches_the_yaml_in_display_units(src):
    vm = {f['key']: f for f in pv.view_model(src)['fields']}
    j2 = src.docs.profile['joints']['joint_2']['limits']
    assert vm['axes.joint_2.min_deg']['value'] == pytest.approx(math.degrees(j2['position'][0]))
    assert vm['axes.joint_2.vel_dps']['value'] == pytest.approx(math.degrees(j2['velocity']))
    link = src.docs.profile['links']['link_1']['length']
    assert vm['kin.link_1.length_mm']['value'] == pytest.approx(link * 1000)


def test_quality_badges_come_from_the_quality_file_and_default_to_placeholder(src):
    vm = {f['key']: f for f in pv.view_model(src)['fields']}
    assert vm['kin.link_1.length_mm']['quality'] == {'tag': 'estimated', 'label': 'tahmini'}
    assert vm['axes.joint_2.vel_dps']['quality'] is None                 # etiketli alan değil
    assert pv._quality_tags({'fields': {'a': 'measured', 'b': {'tag': 'estimated', 'value': 1}, 'c': None,
                                        'd': 'bogus'}}) == {'a': 'measured', 'b': 'estimated', 'c': None,
                                                            'd': None}
    assert pv._quality_tags(None) == {}


# --- doğrulama hattı ---------------------------------------------------------------------------------------
@pytest.mark.parametrize('edits,field', [
    ({'axes.joint_2.min_deg': 100, 'axes.joint_2.max_deg': 50}, 'axes.joint_2.min_deg'),
    ({'axes.joint_2.vel_dps': 0}, 'axes.joint_2.vel_dps'),
    ({'axes.joint_2.vel_dps': -5}, 'axes.joint_2.vel_dps'),
    ({'safety.x_min_mm': 5000}, 'safety.x_min_mm'),
    ({'safety.z_min_mm': 5}, 'safety.z_min_mm'),
    ({'axes.joint_2.min_deg': -1000}, 'axes.joint_2.min_deg'),
    ({'axes.joint_3.pulse_min_us': 2500}, 'axes.joint_3.pulse_min_us'),
    ({'tcp.z_mm': 900}, 'safety'),                                       # home kutu dışına çıktı
    ({'axes.joint_1.home_deg': 60}, 'safety'),
    ({'kin.link_2.length_mm': 6000}, 'kin.link_2.length_mm'),
    ({'nope.field': 1}, 'nope.field'),
])
def test_nonsense_values_are_errors_on_the_field(src, edits, field):
    val = pv.validate(src, edits)
    assert not val.ok and field in fields_of(val.issues)


def test_robot_core_errors_are_located_on_the_field_without_duplicates(src):
    val = pv.validate(src, {'axes.joint_2.vel_dps': 0})
    on_field = [i for i in val.issues if i.field == 'axes.joint_2.vel_dps']
    assert len(on_field) == 1
    # robot_core'un bildiği ama kuralların bilmediği hata: link uzunluğu negatif birim sınırı dışında
    val = pv.validate(src, {'safety.y_max_mm': 4000})
    assert not val.ok and val.issues


def test_valid_edit_changes_derived_values_and_is_ok(src):
    val = pv.validate(src, {'axes.joint_1.microstep': 32})
    assert val.ok and [c['field'] for c in val.changes] == ['axes.joint_1.microstep']
    base = pv.view_model(src)['derived']['joint_1']['steps_per_deg']
    assert val.derived['joint_1']['steps_per_deg'] == pytest.approx(base * 32, rel=1e-3)


def test_no_op_and_display_rounding_are_not_changes(src):
    vm = {f['key']: f for f in pv.view_model(src)['fields']}
    shown = round(vm['axes.joint_2.min_deg']['value'], 3)
    speed = vm['axes.joint_2.vel_dps']['value']
    val = pv.validate(src, {'axes.joint_2.min_deg': shown, 'axes.joint_2.vel_dps': speed})
    assert val.ok and val.changes == []


def test_diff_before_after_and_quality_of_draft_values(src):
    vm = {f['key']: f for f in pv.view_model(src)['fields']}
    before = vm['safety.x_max_mm']['value']
    val = pv.validate(src, {'safety.x_max_mm': before + 10})
    (c,) = val.changes
    assert c['field'] == 'safety.x_max_mm' and c['unit'] == 'mm' and c['group'] == 'safety'
    assert c['before'] == pytest.approx(before) and c['after'] == pytest.approx(before + 10)
    assert c['quality'] == 'estimated'                                     # taslak değeri ölçülmemiş


def test_home_edit_keeps_named_pose_and_hardware_reference_in_sync(src):
    vm = {f['key']: f for f in pv.view_model(src)['fields']}
    val = pv.validate(src, {'axes.joint_2.home_deg': vm['axes.joint_2.home_deg']['value'] + 5})
    assert val.ok
    new = val.docs.profile['moveit']
    idx = src.joints.index('joint_2')
    assert new['initial_positions']['joint_2'] != src.docs.profile['moveit']['initial_positions']['joint_2']
    assert new['named_poses']['home'][idx] == pytest.approx(new['initial_positions']['joint_2'])


def test_pot_calibration_is_draft_only_and_validated(src):
    ok = pv.validate(src, pot('joint_1', ch=1, lo=120, hi=880, a0=-90, a1=90))
    assert ok.ok and ok.docs.pot['joint_1']['raw_min'] == 120 and ok.docs.pot['joint_1']['channel'] == 1
    assert pv.validate(src, pot('joint_1', lo=5, hi=880)).ok is False
    assert pv.validate(src, pot('joint_1', lo=120, hi=1020)).ok is False
    assert 'pot' not in src.docs.profile and src.docs.pot == {}            # kaynak belge değişmedi


def test_edits_never_mutate_the_loaded_source(src):
    import copy
    before = copy.deepcopy((src.docs.profile, src.docs.hardware))
    pv.validate(src, {'axes.joint_2.max_deg': 150, 'axes.joint_1.microstep': 8})
    assert (src.docs.profile, src.docs.hardware) == before


# --- taslak: atomik, depo dosyalarına yazılmaz --------------------------------------------------------------
def test_save_draft_writes_only_the_data_dir_and_repo_files_are_byte_identical(src, data_dir):
    before = repo_hashes()
    res = pv.save_draft(src, {'axes.joint_2.max_deg': 150, 'safety.x_max_mm': 230})
    assert res['ok'] and res['applied'] is False and res['changes'] == 2
    draft = data_dir / 'profiles' / 'karma_robot' / 'draft.yaml'
    assert draft.is_file()
    assert repo_hashes() == before                                         # depo profil dosyaları AYNI
    doc = yaml.safe_load(draft.read_text(encoding='utf-8'))
    assert doc['uygulandi'] is False and doc['robot'] == 'karma_robot'
    assert doc['edits'] == {'axes.joint_2.max_deg': 150, 'safety.x_max_mm': 230}
    assert doc['tahmini'] == ['axes.joint_2.max_deg', 'safety.x_max_mm']
    assert doc['profile']['joints']['joint_2']['limits']['position'][1] == pytest.approx(math.radians(150))
    assert draft.read_text(encoding='utf-8').startswith('# TASLAK')
    assert not [p for p in draft.parent.iterdir() if p.name != 'draft.yaml']   # geçici dosya kalmadı


def test_save_refuses_invalid_and_empty_edits_and_writes_nothing(src, data_dir):
    for edits in ({'axes.joint_2.vel_dps': 0}, {'tcp.z_mm': 900}):
        with pytest.raises(pv.ViewError) as e:
            pv.save_draft(src, edits)
        assert e.value.status == 422 and e.value.extra['ok'] is False and e.value.extra['issues']
    with pytest.raises(pv.ViewError) as e:
        pv.save_draft(src, {})
    assert e.value.status == 400 and e.value.code == 'no_changes'
    assert not (data_dir / 'profiles' / 'karma_robot' / 'draft.yaml').exists()


def test_draft_write_is_atomic_old_draft_survives_a_failed_replace(src, data_dir, monkeypatch):
    pv.save_draft(src, {'axes.joint_2.max_deg': 150})
    draft = data_dir / 'profiles' / 'karma_robot' / 'draft.yaml'
    old = draft.read_bytes()

    def boom(*_a, **_k):
        raise OSError('disk dolu')
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(pv.ViewError) as e:
        pv.save_draft(src, {'axes.joint_2.max_deg': 140})
    assert e.value.status == 500 and e.value.code == 'draft_write_failed'
    assert draft.read_bytes() == old                                       # yarım/bozuk dosya YOK
    assert [p.name for p in draft.parent.iterdir()] == ['draft.yaml']      # geçici dosya temizlendi
    assert str(data_dir) not in e.value.message                            # ileti yol sızdırmaz


def test_read_and_delete_draft_and_stale_detection(src, data_dir):
    assert pv.read_draft(src) == {'exists': False, 'edits': {}}
    pv.save_draft(src, {'axes.joint_2.max_deg': 150})
    got = pv.read_draft(src)
    assert got['exists'] and got['edits'] == {'axes.joint_2.max_deg': 150} and got['stale'] is False
    src.hashes['profile'] = 'deadbeef'                                     # depo profili sonradan değişti
    assert pv.read_draft(src)['stale'] is True
    assert pv.delete_draft(src) == {'ok': True, 'deleted': True}
    assert pv.delete_draft(src) == {'ok': True, 'deleted': False}


def test_corrupt_draft_does_not_break_the_page(src, data_dir):
    d = data_dir / 'profiles' / 'karma_robot'
    d.mkdir(parents=True)
    (d / 'draft.yaml').write_text('{{{ bu yaml değil', encoding='utf-8')
    got = pv.read_draft(src)
    assert got['exists'] and got['edits'] == {} and 'error' in got


def test_draft_inside_the_repo_profile_dir_is_refused(src, monkeypatch):
    monkeypatch.setenv('HUB_DATA_DIR', str(PROFILE_ROOT / 'karma_robot' / 'oops'))
    before = repo_hashes()
    with pytest.raises(pv.ViewError) as e:
        pv.save_draft(src, {'axes.joint_2.max_deg': 150})
    assert e.value.code == 'data_dir_in_repo'
    assert repo_hashes() == before and not (PROFILE_ROOT / 'karma_robot' / 'oops').exists()


def test_yaml_text_is_the_draft_and_blocked_when_invalid(src):
    text = pv.yaml_text(src, {'axes.joint_2.max_deg': 150})
    assert text.startswith('# TASLAK') and yaml.safe_load(text)['edits'] == {'axes.joint_2.max_deg': 150}
    with pytest.raises(pv.ViewError) as e:
        pv.yaml_text(src, {'safety.z_min_mm': 1})
    assert e.value.status == 422


def test_atomic_write_roundtrip_and_no_partial_file_on_error(tmp_path, monkeypatch):
    target = tmp_path / 'sub' / 'draft.yaml'
    pv.atomic_write(target, 'a: 1\n')
    assert target.read_text() == 'a: 1\n'
    monkeypatch.setattr(os, 'fsync', lambda _fd: (_ for _ in ()).throw(OSError('x')))
    with pytest.raises(OSError):
        pv.atomic_write(target, 'b: 2\n')
    assert target.read_text() == 'a: 1\n' and [p.name for p in target.parent.iterdir()] == ['draft.yaml']


def test_missing_engine_gives_503_not_a_crash(monkeypatch):
    import builtins
    real = builtins.__import__

    def deny(name, *a, **k):
        if name.startswith('robot_core'):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', deny)
    with pytest.raises(pv.ViewError) as e:
        pv.load_source()
    assert e.value.status == 503 and e.value.code == 'engine_unavailable'


def test_unknown_robot_is_404_and_bad_name_is_refused(data_dir):
    with pytest.raises(pv.ViewError) as e:
        pv.load_source('yok_robot')
    assert e.value.status == 404
    with pytest.raises(pv.ViewError) as e:
        pv.load_source('../etc')
    assert e.value.status == 404 and str(REPO) not in e.value.message


# --- uçlar -------------------------------------------------------------------------------------------------
def login(c):
    assert c.post('/api/login', json={'pin': '123456'}, headers=J).status_code == 200


def test_get_params_needs_a_session_in_pin_mode_and_lists_fields(make_client, data_dir):
    c, _ = make_client('pin')
    assert c.get('/api/params').status_code == 401
    assert c.post('/api/params/validate', json={'edits': {}}, headers=J).status_code == 401
    login(c)
    doc = c.get('/api/params').json()
    assert doc['ok'] and doc['robot'] == 'karma_robot' and doc['draft'] == {'exists': False, 'edits': {}}
    assert any(f['key'] == 'axes.joint_2.min_deg' for f in doc['fields'])


def test_validate_endpoint_reports_issues_and_diff_and_writes_nothing(make_client, data_dir):
    c, _ = make_client('open')
    before = repo_hashes()
    bad = c.post('/api/params/validate', json={'edits': {'axes.joint_2.vel_dps': 0}}, headers=J).json()
    assert bad['ok'] is False and bad['issues'][0]['field'] == 'axes.joint_2.vel_dps'
    good = c.post('/api/params/validate', json={'edits': {'axes.joint_2.max_deg': 150}}, headers=J).json()
    assert good['ok'] and good['changes'][0]['field'] == 'axes.joint_2.max_deg'
    assert repo_hashes() == before and not (data_dir / 'profiles').exists()


@pytest.mark.parametrize('body', [b'', b'{', b'[]', b'{"edits": 3}', b'{"edits": []}', b'{"nope": {}}'])
def test_bad_bodies_are_400_not_500(make_client, data_dir, body):
    c, _ = make_client('open')
    r = c.post('/api/params/validate', content=body, headers=J)
    assert r.status_code == 400 and r.json()['ok'] is False


def test_oversized_edit_set_and_body_are_refused(make_client, data_dir):
    c, _ = make_client('open')
    many = {f'k{i}': 1 for i in range(pv.MAX_EDITS + 1)}
    assert c.post('/api/params/validate', json={'edits': many}, headers=J).status_code == 413
    huge = {'edits': {'a': 1}, 'pad': 'x' * 300_000}
    assert c.post('/api/params/validate', json=huge, headers=J).status_code == 413


def test_draft_endpoints_save_read_download_delete(make_client, data_dir):
    c, _ = make_client('open')
    before = repo_hashes()
    r = c.post('/api/params/draft', json={'edits': {'axes.joint_2.max_deg': 150}}, headers=J)
    assert r.status_code == 200 and r.json()['applied'] is False
    assert c.get('/api/params').json()['draft']['edits'] == {'axes.joint_2.max_deg': 150}
    dl = c.post('/api/params/yaml', json={'edits': {'axes.joint_2.max_deg': 150}}, headers=J)
    assert dl.status_code == 200 and dl.headers['content-type'].startswith('text/yaml')
    assert 'attachment' in dl.headers['content-disposition'] and 'karma_robot-taslak.yaml' in dl.headers[
        'content-disposition']
    assert str(data_dir) not in dl.text and str(REPO) not in dl.text      # yol sızmaz
    assert c.delete('/api/params/draft', headers=J).json() == {'ok': True, 'deleted': True}
    assert c.get('/api/params').json()['draft']['exists'] is False
    assert repo_hashes() == before


def test_invalid_draft_is_422_with_issues_and_nothing_is_written(make_client, data_dir):
    c, _ = make_client('open')
    r = c.post('/api/params/draft', json={'edits': {'safety.z_min_mm': 1}}, headers=J)
    assert r.status_code == 422 and r.json()['issues'][0]['field'] == 'safety.z_min_mm'
    assert not (data_dir / 'profiles' / 'karma_robot' / 'draft.yaml').exists()


def test_locked_mode_blocks_writes_but_allows_validation_and_view(make_client, data_dir):
    c, _ = make_client('locked')
    assert c.get('/api/params').status_code == 200
    assert c.post('/api/params/validate', json={'edits': {}}, headers=J).status_code == 200
    assert c.post('/api/params/draft', json={'edits': {'axes.joint_2.max_deg': 150}},
                  headers=J).status_code == 403
    assert c.delete('/api/params/draft', headers=J).status_code == 403
    assert not (data_dir / 'profiles').exists()


def test_robot_query_is_validated(make_client, data_dir):
    c, _ = make_client('open')
    assert c.get('/api/params?robot=yok_robot').status_code == 404
    assert c.get('/api/params?robot=..%2Fx').status_code == 404


def test_no_secret_or_path_leaks_in_any_response(make_client, data_dir, monkeypatch):
    c, hub = make_client('open')
    bodies = [c.get('/api/params').text,
              c.post('/api/params/validate', json={'edits': {'tcp.z_mm': 900}}, headers=J).text,
              c.post('/api/params/draft', json={'edits': {'axes.joint_2.max_deg': 150}}, headers=J).text]
    for text in bodies:
        assert str(REPO) not in text and str(data_dir) not in text and '/home/' not in text
        assert hub.access.pin not in text if getattr(hub.access, 'pin', None) else True
