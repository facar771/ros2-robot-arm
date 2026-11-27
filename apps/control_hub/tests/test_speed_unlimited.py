"""P1e: SİM'de hız/ivme SINIRSIZ (tek sınır: sonlu, > 0, % <= 100000, ölçek <= 1000); gerçek mod kilitli.

Formül: etkin hız = profil limiti × ölçek × %/100; etkin ivme = profil ivmesi × ivme ölçeği × %/100.
Konum limitleri (eklem, güvenli kutu) hız ne olursa olsun zorlanır. Zaman sahte saatle ilerler."""
import builtins
import hashlib
import math
from pathlib import Path

import numpy as np
import pytest

from control_hub.hub_core import FakeBackend
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.hub_core.sim_plan import (
    MAX_JOINT_JUMP_RAD,
    MIN_DURATION_S,
    PCT_MAX,
    RAMP,
    SCALE_MAX,
    duration_for,
    plan_joint,
    plan_linear,
)
from tests.test_speed_limits import BODY_423, build, limits

LEASE = 'lease-test-p1e'
# Kutu içinde, tekil olmayan (σ_min 0,074 > σ_slow) bir poz: jog stres testleri buradan başlar
Q_MID = [-0.163, 1.836, 0.765, -0.055, -1.406, -1.24]


@pytest.fixture
def sim_api():
    backend = SimBackend(clock=SimClock(), autostart=False)
    client, hub = build(backend)
    yield client, hub, backend
    hub.close()


def post(client, **body):
    return client.post('/api/sim/speed', json=body)


def raw_post(client, text):
    return client.post('/api/sim/speed', content=text, headers={'content-type': 'application/json'})


# --- a) formül: hız ve ivme ------------------------------------------------------------------------
@pytest.mark.parametrize('pct', [25, 100, 400, 2500, PCT_MAX])
@pytest.mark.parametrize('scale, accel',
                         [(1.0, None), (0.3, None), (2.0, 0.5), (1.0, 10.0), (SCALE_MAX, 1.0)])
def test_plan_time_follows_speed_and_acceleration_formula(sim_api, pct, scale, accel):
    client, _, sim = sim_api
    body = {'speed_pct': pct, 'scale': scale}
    if accel is not None:
        body.update(accel_scale=accel, accel_linked=False)
    assert post(client, **body).status_code == 200
    k = pct / 100 * scale
    ka = pct / 100 * (scale if accel is None else accel)
    assert sim.sm.scale_for(pct) == pytest.approx(k) and sim.sm.accel_scale_for(pct) == pytest.approx(ka)
    q0 = sim.home_q.copy()
    goal = np.degrees(q0) + np.array([3.0, 4.0, -5.0, 6.0, 2.0, -7.0])
    plan = plan_joint(sim.sm, q0, goal, pct)
    delta = np.abs(np.radians(goal) - q0)
    want = max(MIN_DURATION_S, float(np.max(delta / (sim.sm.vmax * k))) / (1 - RAMP),
               math.sqrt(float(np.max(delta / (sim.sm.amax * ka))) / (RAMP * (1 - RAMP))))
    assert plan.duration_s == pytest.approx(want, rel=1e-9)
    assert plan.speed_scale == pytest.approx(k)
    if plan.duration_s > MIN_DURATION_S + 1e-9:        # süre tabana yapışmadıysa limitler gerçekten dolu
        peak_v = delta / (plan.duration_s * (1 - RAMP))
        peak_a = delta / (plan.duration_s ** 2 * RAMP * (1 - RAMP))
        use_v, use_a = peak_v / (sim.sm.vmax * k), peak_a / (sim.sm.amax * ka)
        assert use_v.max() <= 1 + 1e-9 and use_a.max() <= 1 + 1e-9
        assert max(use_v.max(), use_a.max()) == pytest.approx(1.0)


def test_limits_doc_has_the_acceleration_column_and_the_over_profile_flags(sim_api):
    client, _, sim = sim_api
    post(client, speed_pct=400, scale=1.0)
    d = limits(client)
    assert d['above_profile'] and d['over_speed'] and d['over_accel'] and d['accel_linked']
    for row, v, a in zip(d['joints'], sim.sm.vmax, sim.sm.amax):
        assert row['effective_rad_s'] == pytest.approx(v * 4.0, abs=1e-4)
        assert row['accel_limit_rad_s2'] == pytest.approx(a, abs=1e-4)
        assert row['accel_effective_rad_s2'] == pytest.approx(a * 4.0, abs=1e-4)
        assert row['over_speed'] and row['over_accel']
    post(client, speed_pct=100)
    d = limits(client)
    assert not d['above_profile'] and not any(r['over_speed'] or r['over_accel'] for r in d['joints'])


def test_accel_scale_is_linked_by_default_and_independent_when_unlinked(sim_api):
    client, _, sim = sim_api
    assert post(client, scale=2.0).json()['accel_scale'] == 2.0                  # bağlı: hızla birlikte
    r = post(client, accel_scale=0.5, accel_linked=False).json()
    assert r['accel_scale'] == 0.5 and r['accel_linked'] is False and sim.sm.velocity_cap == 2.0
    assert post(client, scale=3.0).json()['accel_scale'] == 0.5          # bağımsız: hız değişti, ivme kaldı
    r = post(client, accel_linked=True).json()
    assert r['accel_linked'] is True and r['accel_scale'] == 3.0
    assert post(client, accel_scale=1.0, accel_linked=True).status_code == 400   # bağlıyken değer verilemez


# --- 400 / sınırsız kabul --------------------------------------------------------------------------
@pytest.mark.parametrize('pct', [400, 1234.5, 100000, 0.1, 0.01])
def test_percent_above_100_and_fractions_are_accepted_without_clipping(sim_api, pct):
    client, _, sim = sim_api
    assert post(client, speed_pct=pct).json() == {'ok': True, 'speed_pct': pct}
    assert sim.speed_pct == pct and limits(client)['speed_pct'] == pct
    assert client.get('/api/state').json()['robot']['speed_pct'] == pct


@pytest.mark.parametrize('text', ['{"speed_pct": NaN}', '{"speed_pct": Infinity}', '{"speed_pct": -Infinity}',
                                  '{"speed_pct": 1e999}', '{"scale": NaN}', '{"scale": 1e999}',
                                  '{"scale": 1, "accel_scale": NaN}'])
def test_nan_and_inf_are_400_and_change_nothing(sim_api, text):
    client, _, _ = sim_api
    r = raw_post(client, text)
    assert r.status_code == 400
    d = limits(client)
    assert d['speed_pct'] == 50 and d['scale'] == pytest.approx(0.3) and d['accel_linked']


@pytest.mark.parametrize('body', [{'speed_pct': 0}, {'speed_pct': -1}, {'speed_pct': PCT_MAX + 1},
                                  {'speed_pct': 'x'}, {'speed_pct': True}, {'speed_pct': None},
                                  {'scale': 0}, {'scale': -2}, {'scale': SCALE_MAX + 0.001}, {'scale': None},
                                  {'scale': True}, {'accel_scale': 0}, {'accel_scale': -1},
                                  {'accel_scale': SCALE_MAX + 1, 'accel_linked': False},
                                  {'accel_linked': 'evet'}, {'scale': 1.0, 'accel_scale': 'x'}])
def test_out_of_range_and_wrong_types_are_400_with_no_partial_change(sim_api, body):
    client, _, _ = sim_api
    r = post(client, **body)
    assert r.status_code == 400 and r.json()['error'] == 'bad_request'
    d = limits(client)
    assert d['speed_pct'] == 50 and d['scale'] == pytest.approx(0.3) and d['accel_linked']


def test_the_only_limits_are_the_documented_ones(sim_api):
    client, _, _ = sim_api
    assert post(client, speed_pct=PCT_MAX, scale=SCALE_MAX).status_code == 200
    d = limits(client)
    assert d['pct_max'] == PCT_MAX and d['scale_max'] == SCALE_MAX and d['scale_min'] == 0.0
    assert client.get('/api/speed/limits', params={'speed_pct': PCT_MAX + 1}).status_code == 400
    assert client.get('/api/speed/limits', params={'speed_pct': 0}).status_code == 400
    r = client.post('/api/move', json={'type': 'joint', 'speed_pct': PCT_MAX + 1, 'joints_deg': [0] * 6})
    assert r.status_code == 400


# --- gerçek / tezgâh: bugünkü kısıt birebir ---------------------------------------------------------
@pytest.mark.parametrize('body', [{'speed_pct': 50, 'scale': 1.01}, {'speed_pct': 400, 'scale': 1.01},
                                  {'scale': 1.0}, {'scale': 0.5}, {'accel_scale': 1.01},
                                  {'speed_pct': 400, 'accel_scale': 2.0, 'accel_linked': False}])
def test_real_mode_rejects_every_scale_change_with_409_sim_only(body):
    client, hub = build(FakeBackend(time_scale=0.02))
    try:
        r = client.post('/api/sim/speed', json=body)
        assert r.status_code == 409 and r.json()['error'] == 'sim_only'
        d = limits(client)
        assert d['scale_locked'] is True and d['scale'] is None and d['joints'] == []
    finally:
        hub.close()


@pytest.mark.parametrize('pct', [400, 101, 100000])
def test_real_mode_rejects_percent_above_100(pct):
    client, hub = build(FakeBackend(time_scale=0.02))
    try:
        r = client.post('/api/sim/speed', json={'speed_pct': pct})
        assert r.status_code == 409 and r.json()['error'] == 'not_sim'
        assert client.get('/api/speed/limits', params={'speed_pct': pct}).status_code == 400
        assert hub.backend.snapshot().get('speed_pct') != pct
    finally:
        hub.close()


# --- kalıcılık: hiçbir şey diske yazılmaz; yeniden başlayınca varsayılan ---------------------------
def test_nothing_is_written_and_restart_returns_to_the_default(sim_api, monkeypatch):
    client, hub, sim = sim_api
    files = [p for p in Path(sim._profile_path).parent.glob('*') if p.is_file()]     # profil + taslak klasörü
    assert files, 'profil dosyası bulunamadı: test boş geçmesin'
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    writes = []
    real_open = builtins.open

    def spy(file, mode='r', *a, **kw):
        if any(c in str(mode) for c in 'wax+'):
            writes.append(str(file))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, 'open', spy)
    monkeypatch.setattr(Path, 'write_text', lambda self, *a, **k: writes.append(str(self)))
    monkeypatch.setattr(Path, 'write_bytes', lambda self, *a, **k: writes.append(str(self)))
    assert post(client, speed_pct=777, scale=5.0, accel_scale=9.0, accel_linked=False).status_code == 200
    assert limits(client)['above_profile']
    monkeypatch.undo()
    assert writes == []
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
    fresh = SimBackend(clock=SimClock(), autostart=False)                 # "Hub yeniden başladı"
    try:
        d = fresh.speed_limits()[1]
        assert d['scale'] == pytest.approx(0.3) and d['speed_pct'] == 50 and d['accel_linked']
        assert not d['above_profile']
    finally:
        fresh.close()


# --- profil sınırının ÜSTÜ: Günlük olayı yalnız geçişte -------------------------------------------------
def test_journal_gets_one_event_when_crossing_above_and_one_when_returning(sim_api):
    client, hub, _ = sim_api
    seq = hub.events.last_seq

    def kinds():
        return [(e.kind, e.level) for e in hub.events.since(seq) if e.kind == 'sim_over_profile']

    post(client, speed_pct=100, scale=1.0)                       # tam limit: aşmıyor
    assert kinds() == []
    post(client, speed_pct=400)                                  # aştı
    post(client, speed_pct=500)                                  # hâlâ üstünde: yeni olay yok
    assert kinds() == [('sim_over_profile', 'warning')]
    assert 'ÜSTÜNDE' in [e for e in hub.events.since(seq) if e.kind == 'sim_over_profile'][0].message
    post(client, speed_pct=100)                                  # limit içine döndü
    assert kinds() == [('sim_over_profile', 'warning'), ('sim_over_profile', 'info')]
    post(client, speed_pct=100, scale=3.0)                       # ölçekle aşmak da sayılır
    assert len(kinds()) == 3


# --- ACİL DUR ----------------------------------------------------------------------------------
@pytest.mark.parametrize('body', [{'scale': 5.0}, {'accel_scale': 5.0, 'accel_linked': False},
                                  {'speed_pct': 400, 'scale': 1.0}])
def test_estop_blocks_scale_changes_with_the_same_423_body(sim_api, body):
    client, hub, _ = sim_api
    assert client.post('/api/estop').status_code == 200
    r = post(client, **body)
    assert r.status_code == 423 and r.text == BODY_423
    assert limits(client)['scale'] == pytest.approx(0.3)


# --- c) yüksek hızda hareket: çıldırma yok, konum limitleri ve kutu yine zorlanır ---------------------
def run_move(sim, body, seconds):
    """Hareketi kare kare sür; her karede eklem/kutu/sonlu denetimi. ``(max adım rad, süre s)`` döner."""
    z = sim.profile.safety.zone
    lo, hi = np.array(z.lower), np.array(z.upper)
    st, doc = sim.move_submit(body)
    assert st == 202, doc
    prev, worst, t = np.array(sim._q), 0.0, 0.0
    while sim._job is not None and sim._job.state == 'running' and t < seconds:
        sim.advance(sim.tick_s)
        t += sim.tick_s
        q = np.array(sim._q)
        assert np.all(np.isfinite(q))
        assert np.all(q >= sim.sm.lower - 1e-9) and np.all(q <= sim.sm.upper + 1e-9)
        p = sim.sm.tcp(q)[:3, 3]
        assert np.all(p >= lo - 1e-3) and np.all(p <= hi + 1e-3), p
        worst, prev = max(worst, float(np.max(np.abs(q - prev)))), q
    return worst, t


@pytest.mark.parametrize('pct, scale', [(400, 1.0), (5000, 10.0), (PCT_MAX, SCALE_MAX)])
def test_joint_move_at_extreme_speed_stays_finite_inside_limits_and_box(sim_api, pct, scale):
    _, _, sim = sim_api
    assert sim.set_speed_scale(pct, scale)[0] == 200
    sim.set_joints(Q_MID)
    goal = list(np.degrees(Q_MID))
    goal[1] -= 12.0
    goal[2] += 10.0
    worst, took = run_move(sim, {'type': 'joint', 'joints_deg': goal, 'speed_pct': pct}, 30)
    assert sim._job is None or sim._job.state != 'running'
    assert took >= MIN_DURATION_S - sim.tick_s                    # süre tabanı: tek karede ışınlanma yok
    assert np.allclose(np.degrees(sim._q), goal, atol=1e-6)
    assert worst <= MAX_JOINT_JUMP_RAD                                   # kare başına eklem sıçraması sınırı


def test_extreme_speed_still_refuses_targets_outside_joint_limits_and_the_box(sim_api):
    _, _, sim = sim_api
    sim.set_speed_scale(PCT_MAX, SCALE_MAX)
    sim.set_joints(Q_MID)
    over = list(np.degrees(Q_MID))
    over[1] = math.degrees(sim.sm.upper[1]) + 5.0                        # eklem limiti dışı
    st, doc = sim.move_submit({'type': 'joint', 'joints_deg': over, 'speed_pct': PCT_MAX})
    assert st == 409 and doc['error'] == 'joint_limit'
    st, doc = sim.move_submit({'type': 'linear', 'pos_m': [0.5, 0.0, 0.1], 'speed_pct': PCT_MAX})
    assert st == 409 and doc['error'] == 'outside_safe_zone'
    assert np.allclose(sim._q, Q_MID)                                    # hiçbiri hareket başlatmadı


@pytest.mark.parametrize('pct, scale', [(400, 1.0), (PCT_MAX, SCALE_MAX)])
def test_linear_move_at_extreme_speed_has_no_joint_jump_and_stays_in_the_box(sim_api, pct, scale):
    _, _, sim = sim_api
    sim.set_speed_scale(pct, scale)
    sim.set_joints(Q_MID)
    p0 = sim.sm.tcp(Q_MID)[:3, 3]
    target = (p0 + np.array([0.01, 0.0, -0.01])).tolist()
    plan = plan_linear(sim.sm, Q_MID, target, None, 'fixed', pct)
    assert plan.duration_s >= MIN_DURATION_S
    assert float(np.max(np.abs(np.diff(plan.path, axis=0)))) <= MAX_JOINT_JUMP_RAD
    assert np.all(np.isfinite(plan.path))
    run_move(sim, {'type': 'linear', 'pos_m': target, 'speed_pct': pct}, 30)
    assert np.allclose(sim.sm.tcp(sim._q)[:3, 3], target, atol=2e-4)


# --- c) yüksek hızda jog: sıçrama/NaN yok, kutu ve eklem limitleri zorlanır ---------------------------------
def hold(sim, pct, axis, sign, seconds):
    tw = [0.0] * 6
    tw[axis] = sign * min(1.0, (0.02 if axis < 3 else 0.10) * pct / 100)
    body = {'lease': LEASE, 'frame': 'World', 'twist': tw, 'ttl_ms': 400, 'speed_pct': pct}
    z = sim.profile.safety.zone
    lo, hi = np.array(z.lower), np.array(z.upper)
    prev, worst, seen = np.array(sim._q), 0.0, set()
    for _ in range(int(seconds / 0.02)):
        assert sim.jog_command(body)[0] == 200
        sim.advance(0.02)
        q = np.array(sim._q)
        assert np.all(np.isfinite(q)), 'NaN/sonsuz eklem'
        assert np.all(q >= sim.sm.lower - 1e-9) and np.all(q <= sim.sm.upper + 1e-9), 'eklem limiti aşıldı'
        p = sim.sm.tcp(q)[:3, 3]
        assert np.all(p >= lo - 1e-6) and np.all(p <= hi + 1e-6), f'güvenli kutu aşıldı: {p}'
        worst, prev = max(worst, float(np.max(np.abs(q - prev)))), q
        seen.add(sim.jog_status()[1]['status'])
    return worst, seen


@pytest.mark.parametrize('pct, scale', [(100, 0.3), (400, 1.0), (5000, 10.0), (PCT_MAX, SCALE_MAX)])
@pytest.mark.parametrize('axis, sign', [(0, 1), (0, -1), (1, 1), (2, -1), (3, 1)])
def test_hold_jog_at_extreme_speed_never_jumps_or_leaves_the_box_and_joint_limits(pct, scale, axis, sign):
    sim = SimBackend(clock=SimClock(), autostart=False)
    try:
        sim.set_speed_scale(pct, scale)
        sim.set_joints(Q_MID)
        worst, seen = hold(sim, pct, axis, sign, 6.0)
        assert worst < 0.1, f'tek tikte eklem sıçraması {worst:.3f} rad'
        assert not seen & {'tracking'}, seen                              # izleme kaybı = çıldırma belirtisi
    finally:
        sim.close()


def test_jog_gain_scales_with_speed_and_saturates_at_the_per_tick_step_bound(sim_api):
    client, _, sim = sim_api
    gains = []
    for pct in (100, 200, 400, 100000):
        post(client, speed_pct=pct, scale=1.0)
        j = limits(client)['jog']
        gains.append((j['gain'], j['requested_gain'], j['capped']))
    assert gains[0] == (1.0, 1.0, False) and gains[1] == (2.0, 2.0, False) and gains[2] == (4.0, 4.0, False)
    assert gains[3][2] is True and gains[3][0] < gains[3][1]                # sınırsız sayı, sınırlı adım
    assert sim.jog_gain == pytest.approx(gains[3][0])


def test_default_jog_behaviour_is_unchanged_below_100_percent(sim_api):
    _, _, sim = sim_api
    cfg = sim.jog.engine.config
    assert (cfg.max_linear_speed, cfg.max_angular_speed) == (0.10, 0.50) and sim.jog_gain == 1.0
    assert np.allclose(sim.jog._vmax, [0.02] * 3 + [0.10] * 3)
    np.testing.assert_allclose(sim.jog.engine.chain.velocity, sim.sm.vmax)


def test_jog_gain_returns_to_one_when_speed_returns_to_100(sim_api):
    client, _, sim = sim_api
    post(client, speed_pct=800, scale=1.0)
    assert sim.jog_gain == pytest.approx(8.0)
    post(client, speed_pct=100, scale=0.3)
    assert sim.jog_gain == 1.0
    np.testing.assert_allclose(sim.jog.engine.chain.velocity, sim.sm.vmax)


# --- d) ACİL DUR kesme gecikmesi yüksek hızda -------------------------------------------------------
@pytest.mark.parametrize('pct, scale', [(100, 0.3), (400, 1.0), (PCT_MAX, SCALE_MAX)])
def test_estop_cuts_a_move_within_one_tick_at_any_speed(pct, scale):
    sim = SimBackend(clock=SimClock(), autostart=False)
    try:
        sim.set_speed_scale(pct, scale)
        sim.set_joints(Q_MID)
        goal = list(np.degrees(Q_MID))
        goal[1] -= 20.0
        assert sim.move_submit({'type': 'joint', 'joints_deg': goal, 'speed_pct': pct})[0] == 202
        sim.advance(0.1)
        assert sim._job.state == 'running'
        sim.estop.trip('test')
        q_at = np.array(sim._q)
        sim.advance(sim.tick_s)                                          # TEK tik
        assert sim._job is None or sim._job.state != 'running'
        assert np.max(np.abs(np.array(sim._q) - q_at)) < 1e-9
    finally:
        sim.close()


@pytest.mark.parametrize('pct, scale', [(400, 1.0), (PCT_MAX, SCALE_MAX)])
def test_estop_cuts_hold_jog_within_one_tick_at_high_speed(pct, scale):
    sim = SimBackend(clock=SimClock(), autostart=False)
    try:
        sim.set_speed_scale(pct, scale)
        sim.set_joints(Q_MID)
        hold(sim, pct, 1, 1, 0.5)
        assert sim.jog.active
        sim.estop.trip('test')
        q_at = np.array(sim._q)
        sim.advance(sim.tick_s)
        assert not sim.jog.active
        assert np.max(np.abs(np.array(sim._q) - q_at)) < 1e-9, 'ACİL DUR sonrası jog hâlâ hareket ettirdi'
    finally:
        sim.close()


# --- b) kabul ölçümü: J2 15°, ölçek 1,0 + %400, kartın tahminiyle ±%25 ---------------------------------
def test_j2_15_degrees_at_scale_1_and_400_percent_matches_the_card_estimate_within_25_percent(sim_api):
    client, _, sim = sim_api
    assert post(client, speed_pct=400, scale=1.0).status_code == 200
    card = {e['deg']: e['duration_s'] for e in limits(client)['example_moves']}
    est = card[15.0]
    cur = [j['deg'] for j in sim.snapshot()['joints']]
    goal = list(cur)
    goal[1] += 15.0
    assert sim.move_submit({'type': 'joint', 'joints_deg': goal, 'speed_pct': 400})[0] == 202
    t = 0.0
    while sim._job is not None and sim._job.state == 'running' and t < 60:
        sim.advance(sim.tick_s)
        t += sim.tick_s
    assert abs(t - est) / est <= 0.25, f'ölçülen {t:.2f} s, kart {est:.2f} s'
    assert [j['deg'] for j in sim.snapshot()['joints']][1] == pytest.approx(goal[1], abs=1e-3)
    # ivme ölçeği bağımsız yükseltilince süre kısalır: tahmin ve ölçüm yine uyuşur
    post(client, accel_scale=4.0, accel_linked=False)
    est2 = {e['deg']: e['duration_s'] for e in limits(client)['example_moves']}[15.0]
    assert est2 < est and abs(duration_for(
        np.array([0, math.radians(15), 0, 0, 0, 0]), sim.sm.vmax, sim.sm.amax, 4.0,
        accel_scale=16.0) - est2) < 0.01
