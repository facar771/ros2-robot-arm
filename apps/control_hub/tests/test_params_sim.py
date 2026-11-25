# ruff: noqa: F811  (pytest fixture, params_support.py içinden içe aktarılır)
"""P4b: Parametreler -> "SİM'e uygula / Sıfırla" (taslak profil YALNIZ SİM belleğine geçici yüklenir).

Hakem GERÇEK ``SimBackend`` (robot_core kinematiği) + ``SimClock``: gerçek bekleme yok, deterministik.
Kabul: limit değişince Git/jog yeni limitte reddedilir (ölçülür), Sıfırla geri getirir, ACİL DUR 423,
geçersiz taslak nedeniyle reddedilir, çalışan iş varken reddedilir, depo profil dosyaları DEĞİŞMEZ."""
import math
from types import SimpleNamespace

import pytest

pytest.importorskip('numpy')
pytest.importorskip('yaml')

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from control_hub.hub_core import AccessControl, Hub, profile_apply  # noqa: E402
from control_hub.hub_core import profile_view as pv  # noqa: E402
from control_hub.hub_core.sim_backend import SimBackend, SimClock  # noqa: E402
from control_hub.hub_core.sim_profile import BADGE  # noqa: E402
from control_hub.server import create_app  # noqa: E402
from tests.conftest import J  # noqa: E402
from tests.params_support import data_dir, login, repo_hashes, src  # noqa: E402,F401

NARROW = {'axes.joint_1.min_deg': -5, 'axes.joint_1.max_deg': 5}
SLOW = {'axes.joint_1.vel_dps': 5}
THIN_BOX = {'safety.y_min_mm': -20, 'safety.y_max_mm': 20}
LEASE = 'lease-narrow-01'
JOG_Y = {'lease': LEASE, 'frame': 'World', 'increment': {'axis': 1, 'amount': 10}}


@pytest.fixture
def sim(data_dir):
    b = SimBackend(clock=SimClock(), autostart=False)
    yield b
    b.close()


@pytest.fixture
def api(data_dir, scenarios, ui):
    backend = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    yield TestClient(create_app(hub), base_url='http://localhost'), hub, backend
    hub.close()


def joint_deg(b, i=0):
    return b.snapshot()['joints'][i]['deg']


def tcp_mm(b):
    return [round(v * 1000, 1) for v in b.snapshot()['tcp']['pos_m']]


def target(b, deg_1):
    """Home'dan yalnız 1. eklemi değiştirilmiş hedef (derece)."""
    return [deg_1] + [math.degrees(v) for v in b.home_q][1:]


def save(src, edits):
    return pv.save_draft(src, edits)


def apply(b, robot, edits, src):
    save(src, edits)
    prep = profile_apply.prepare(robot)
    return b.profile_override_apply(prep.profile, prep.info)


def tamper(src, edits=None, **doc_changes):
    """Taslak DOSYASINI elle bozar (arayüz geçersiz taslağı kaydettirmez; dosya yine de elle değişebilir)."""
    path = pv.draft_path(src.robot)
    doc = yaml.safe_load(path.read_text('utf-8'))
    if edits is not None:
        doc['edits'] = edits
    doc.update(doc_changes)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), 'utf-8')


def settle(b):
    for _ in range(80):
        if not b.jog.active:
            return
        b.advance(0.5)


def jog_y(b, times):
    for _ in range(times):
        settle(b)
        b.jog_command(JOG_Y)
        settle(b)


# --- backend: uygula / sıfırla -----------------------------------------------------------------
def test_apply_swaps_limits_speed_and_box_in_memory_and_reset_restores_them(sim, src):
    lower, upper, vmax = sim.sm.lower.copy(), sim.sm.upper.copy(), sim.sm.vmax.copy()
    zone = sim.profile.safety.zone
    status, doc = apply(sim, src.robot, {**NARROW, **SLOW, **THIN_BOX}, src)
    assert status == 200 and doc['ok'] and doc['override']['active'] and doc['rehomed'] is False
    assert doc['override']['changes'] == 5 and doc['override']['robot'] == src.robot
    assert (sim.sm.lower[0], sim.sm.upper[0]) == pytest.approx((-math.radians(5), math.radians(5)))
    assert sim.sm.vmax[0] == pytest.approx(math.radians(5)) and sim.sm.vmax[0] < vmax[0]
    zone_y = (sim.profile.safety.zone.lower[1], sim.profile.safety.zone.upper[1])
    assert zone_y == pytest.approx((-0.02, 0.02))
    snap = sim.snapshot()
    assert snap['profile_override']['active'] is True and BADGE in snap['note'] and snap['mode'] == 'sim'
    status, doc = sim.profile_override_reset()
    assert (status, doc['ok'], doc['noop'], doc['override']) == (200, True, False, None)
    assert (sim.sm.lower == lower).all() and (sim.sm.upper == upper).all() and (sim.sm.vmax == vmax).all()
    assert sim.profile.safety.zone == zone
    snap = sim.snapshot()
    assert snap['profile_override'] is None and BADGE not in snap['note']
    assert sim.profile_override_reset()[1]['noop'] is True            # ikinci Sıfırla zararsız


def test_second_apply_keeps_the_real_profile_as_the_reset_target(sim, src):
    upper = sim.sm.upper.copy()
    apply(sim, src.robot, NARROW, src)
    status, _ = apply(sim, src.robot, {'axes.joint_1.min_deg': -9, 'axes.joint_1.max_deg': 9}, src)
    assert status == 200 and sim.sm.upper[0] == pytest.approx(math.radians(9))
    sim.profile_override_reset()
    assert (sim.sm.upper == upper).all()                       # ilk taslağa değil GERÇEK profile döner


def test_git_and_check_are_refused_at_the_new_limit_and_allowed_again_after_reset(sim, src):
    far = {'type': 'joint', 'joints_deg': target(sim, 25), 'speed_pct': 100}
    assert sim.move_check(far)[1]['ok'] is True                          # gerçek profilde 25° serbest
    apply(sim, src.robot, NARROW, src)
    status, doc = sim.move_check(far)
    assert status == 200 and doc['ok'] is False and doc['violations'][0]['kind'] == 'joint_limit'
    status, doc = sim.move_submit(far)
    assert (status, doc['error']) == (409, 'joint_limit') and 'joint_1' in doc['message']
    assert sim.move_submit({**far, 'joints_deg': target(sim, 3)})[0] == 202   # limit İÇİ hedef yine çalışır
    sim.advance(120)                                          # iş bitsin (sürerken Sıfırla reddedilir)
    sim.profile_override_reset()
    sim.sim_reset()
    assert sim.move_submit(far)[0] == 202


def test_git_is_refused_outside_the_new_safe_box(sim, src):
    side = {'type': 'joint', 'joints_deg': target(sim, 10), 'speed_pct': 100}    # y ≈ +34 mm
    assert sim.move_check(side)[1]['ok'] is True
    apply(sim, src.robot, THIN_BOX, src)
    status, doc = sim.move_check(side)
    assert doc['ok'] is False and 'outside_safe_zone' in {v['kind'] for v in doc['violations']}
    assert sim.move_submit(side)[0] == 409


def test_jog_stops_at_the_new_limit_measured_on_the_real_sim(sim, src):
    apply(sim, src.robot, NARROW, src)
    jog_y(sim, 5)                                              # 5 × +10 mm: gerçek profilde ≈ 49 mm
    narrow_deg, narrow_y = joint_deg(sim), tcp_mm(sim)[1]
    assert narrow_deg <= 5.0 + 1e-6 and narrow_y < 15                     # yeni limitte durdu
    sim.profile_override_reset()
    sim.sim_reset()
    jog_y(sim, 5)
    assert joint_deg(sim) > 10 and tcp_mm(sim)[1] > 30                    # gerçek profil devam eder
    assert narrow_deg < joint_deg(sim) / 2


def test_pose_outside_the_new_limits_is_rehomed_and_inside_is_kept(sim, src):
    sim.set_joints([math.radians(v) for v in target(sim, 20)])
    assert joint_deg(sim) == pytest.approx(20, abs=0.1)
    status, doc = apply(sim, src.robot, NARROW, src)
    assert status == 200 and doc['rehomed'] is True and doc['override']['rehomed'] is True
    assert joint_deg(sim) == pytest.approx(0, abs=0.1)                   # home (sıfır) yeni limit içinde
    assert sim.profile_override_reset()[1]['rehomed'] is False           # home gerçek profilde de içeride


def test_apply_and_reset_are_refused_while_a_move_or_jog_runs(sim, src):
    save(src, NARROW)
    prep = profile_apply.prepare(src.robot)
    assert sim.move_submit({'type': 'joint', 'joints_deg': target(sim, 3), 'speed_pct': 50})[0] == 202
    status, doc = sim.profile_override_apply(prep.profile, prep.info)
    assert (status, doc['error']) == (409, 'busy') and sim.override is None
    sim.advance(120)
    assert sim.profile_override_apply(prep.profile, prep.info)[0] == 200
    sim.sim_reset()
    sim.jog_command(JOG_Y)                                               # jog SÜRERKEN (bekletilmeden)
    assert sim.jog.active
    assert sim.profile_override_reset()[0] == 409 and sim.override is not None
    settle(sim)
    assert sim.profile_override_reset()[0] == 200


def test_estop_blocks_apply_and_reset_with_423_and_changes_nothing(sim, src):
    save(src, NARROW)
    prep = profile_apply.prepare(src.robot)
    upper = sim.sm.upper.copy()
    sim.emergency_stop()
    status, doc = sim.profile_override_apply(prep.profile, prep.info)
    assert (status, doc['error']) == (423, 'estop_latched') and sim.override is None
    assert (sim.sm.upper == upper).all()
    sim.override = {'base': sim.profile, 'info': {'active': True}}       # etkinken de Sıfırla 423
    assert sim.profile_override_reset()[0] == 423 and sim.override is not None


def test_a_profile_with_other_joints_is_refused(sim):
    other = SimpleNamespace(joint_names=list(reversed(sim.profile.joint_names)))      # farklı eklem sırası
    status, doc = sim.profile_override_apply(other, {})
    assert (status, doc['error']) == (422, 'joint_mismatch') and sim.override is None


# --- taslak -> Profile hattı: geçersiz olan NEDENİYLE reddedilir --------------------------------
@pytest.mark.parametrize('bad, field', [
    ({'axes.joint_1.min_deg': 10, 'axes.joint_1.max_deg': -10}, 'axes.joint_1.min_deg'),
    ({'safety.x_min_mm': 300, 'safety.x_max_mm': 100}, 'safety.x_min_mm'),
    ({'safety.x_min_mm': 5000, 'safety.x_max_mm': 6000}, 'safety'),
])
def test_invalid_draft_file_is_refused_with_reasons(sim, src, bad, field):
    save(src, NARROW)
    tamper(src, bad)                                                     # dosya elle bozulmuş
    with pytest.raises(pv.ViewError) as err:
        profile_apply.prepare(src.robot)
    assert (err.value.status, err.value.code) == (422, 'invalid')
    assert any(i['field'].startswith(field) and i['severity'] == 'error' for i in err.value.extra['issues'])
    assert sim.override is None


def test_prepare_refuses_missing_stale_empty_and_unreadable_drafts(src, data_dir):
    def code():
        with pytest.raises(pv.ViewError) as err:
            profile_apply.prepare(src.robot)
        return err.value.status, err.value.code
    assert code() == (404, 'no_draft')
    save(src, NARROW)
    assert profile_apply.prepare(src.robot).info['changes'] == 2
    tamper(src, {})
    assert code() == (400, 'no_changes')
    tamper(src, NARROW, base={**yaml.safe_load(pv.draft_path(src.robot).read_text('utf-8'))['base'],
                              'profile': 'eski'})
    assert code() == (409, 'draft_stale')
    pv.draft_path(src.robot).write_text('{ bozuk: [', 'utf-8')
    assert code() == (422, 'draft_unreadable')


# --- HTTP uçları -------------------------------------------------------------------------------
def post(c, path, **kw):
    return c.post(path, json={}, **kw)


def test_http_flow_state_apply_event_snapshot_and_reset(api, src):
    c, hub, sim = api
    st = c.get('/api/params/sim').json()
    assert (st['can_apply'], st['mode'], st['override']) == (True, 'sim', None)
    r = post(c, '/api/params/sim/apply')
    assert r.status_code == 404 and r.json()['error'] == 'no_draft' and r.json()['ok'] is False
    assert c.post('/api/params/draft', json={'edits': NARROW}, headers=J).status_code == 200
    r = post(c, '/api/params/sim/apply')
    assert r.status_code == 200 and r.json()['override']['changes'] == 2
    robot = c.get('/api/state').json()['robot']
    assert robot['profile_override']['active'] is True and BADGE in robot['note']
    assert c.get('/api/params/sim').json()['override']['active'] is True
    events = c.get('/api/events').json()['events']
    assert any(e['kind'] == 'sim_profile' and e['level'] == 'warning' for e in events)
    r = c.post('/api/move', json={'type': 'joint', 'joints_deg': target(sim, 25)})
    assert r.status_code == 409 and r.json()['error'] == 'joint_limit'
    r = post(c, '/api/params/sim/reset')
    assert r.status_code == 200 and r.json()['override'] is None and r.json()['noop'] is False
    assert c.get('/api/state').json()['robot']['profile_override'] is None
    assert post(c, '/api/params/sim/reset').json()['noop'] is True
    assert c.post('/api/move', json={'type': 'joint', 'joints_deg': target(sim, 25)}).status_code == 202


def test_http_estop_gives_423_on_apply_and_reset_and_state_explains_why(api, src):
    c, hub, sim = api
    c.post('/api/params/draft', json={'edits': NARROW}, headers=J)
    assert post(c, '/api/params/sim/apply').status_code == 200
    assert c.post('/api/estop').status_code == 200
    for path in ('/api/params/sim/apply', '/api/params/sim/reset'):
        r = post(c, path)
        assert r.status_code == 423 and r.json()['error'] == 'estop_latched', path
    st = c.get('/api/params/sim').json()
    assert (st['can_apply'], st['blocker']) == (False, 'estop_latched') and st['reason']
    assert st['override']['active'] is True                    # ACİL DUR'da profil sessizce değişmedi


def test_http_running_move_scenario_or_program_blocks_with_409_busy(api, src, monkeypatch):
    c, hub, sim = api
    c.post('/api/params/draft', json={'edits': NARROW}, headers=J)
    assert c.post('/api/move', json={'type': 'joint', 'joints_deg': target(sim, 3)}).status_code == 202
    r = post(c, '/api/params/sim/apply')
    assert r.status_code == 409 and r.json()['error'] == 'busy' and sim.override is None
    sim.advance(120)
    with monkeypatch.context() as scoped:                # (undo() data_dir ortam değişkenini de geri alırdı)
        scoped.setattr(type(hub.runner), 'running', property(lambda self: True))
        r = post(c, '/api/params/sim/apply')
    assert r.status_code == 409 and 'Senaryo' in r.json()['message']
    hub.program_runner = type('R', (), {'running': True})()              # İSTEK P2: Hub'a bağlanınca sayılır
    r = post(c, '/api/params/sim/apply')
    assert r.status_code == 409 and 'Program' in r.json()['message'] and sim.override is None
    hub.program_runner = type('R', (), {'running': False})()
    assert post(c, '/api/params/sim/apply').status_code == 200


def test_http_invalid_or_stale_draft_is_422_409_and_nothing_is_applied(api, src):
    c, hub, sim = api
    upper = sim.sm.upper.copy()
    c.post('/api/params/draft', json={'edits': NARROW}, headers=J)
    tamper(src, {'axes.joint_1.min_deg': 10, 'axes.joint_1.max_deg': -10})
    r = post(c, '/api/params/sim/apply')
    body = r.json()
    assert r.status_code == 422 and body['error'] == 'invalid' and body['ok'] is False
    assert any(i['field'] == 'axes.joint_1.min_deg' for i in body['issues'])
    assert sim.override is None and (sim.sm.upper == upper).all()
    tamper(src, NARROW, base={'profile': 'eski'})
    assert post(c, '/api/params/sim/apply').status_code == 409


def test_http_robot_mismatch_real_mode_and_fake_backend_are_409(api, make_client, data_dir, monkeypatch):
    c, hub, sim = api
    c.post('/api/params/draft', json={'edits': NARROW}, headers=J)
    r = post(c, '/api/params/sim/apply?robot=baska_robot')
    assert r.status_code == 409 and r.json()['error'] == 'robot_mismatch'
    monkeypatch.setattr(sim, 'snapshot', lambda: {'mode': 'real', 'profile_override': None})
    for path in ('/api/params/sim/apply', '/api/params/sim/reset'):
        r = post(c, path)
        assert r.status_code == 409 and r.json()['error'] == 'real_mode', path
    assert c.get('/api/params/sim').json()['can_apply'] is False
    assert sim.override is None
    fake, _ = make_client('open')                                         # --fake (Docker hub-fake): SİM yok
    for path in ('/api/params/sim/apply', '/api/params/sim/reset'):
        r = fake.post(path, json={})
        assert r.status_code == 409 and r.json()['error'] == 'not_sim', path
    st = fake.get('/api/params/sim').json()
    assert (st['can_apply'], st['blocker']) == (False, 'not_sim')


def test_http_pin_and_locked_modes_protect_apply_and_reset(data_dir, scenarios, ui):
    for mode, denied in (('pin', 401), ('locked', 403)):
        hub = Hub(SimBackend(clock=SimClock(), autostart=False), scenarios, ui, AccessControl(mode, '123456'))
        try:
            c = TestClient(create_app(hub), base_url='http://localhost')
            if mode == 'pin':
                assert c.get('/api/params/sim').status_code == 401
            else:
                assert c.get('/api/params/sim').status_code == 200        # görüntüleme serbest
            for path in ('/api/params/sim/apply', '/api/params/sim/reset'):
                assert post(c, path).status_code == denied, (mode, path)
            if mode == 'pin':
                login(c)
                assert post(c, '/api/params/sim/reset').status_code == 200
        finally:
            hub.close()


def test_the_whole_flow_never_touches_repo_profile_files_or_writes_outside_the_draft(api, src, data_dir):
    before = repo_hashes()
    c, hub, sim = api
    c.post('/api/params/draft', json={'edits': {**NARROW, **SLOW, **THIN_BOX}}, headers=J)
    assert post(c, '/api/params/sim/apply').status_code == 200
    assert post(c, '/api/params/sim/reset').status_code == 200
    assert repo_hashes() == before
    written = sorted(str(p.relative_to(data_dir)) for p in data_dir.rglob('*') if p.is_file())
    assert written == [f'profiles/{src.robot}/draft.yaml']                # uygulama/sıfırlama dosya yazmadı
