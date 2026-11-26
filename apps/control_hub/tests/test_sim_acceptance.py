"""SİM kabul betiğinin SAF mantığı — kayıt/sıra, adım koşucusu, ölçüm yardımcıları, çıkış kodu,
hub.log, ATLANDI ve beklenen konsol hatası (tools/acceptance_core.py).

Tarayıcı, Playwright ya da Hub gerekmez: sahte `page`/`ctx` kullanılır (tests/helpers_acceptance.py).
Betiğin kendisi (gerçek tarayıcı) `python -m tools.sim_acceptance` ile koşulur (docs/KABUL.md).
Rapor, izdüşüm, STL, CLI ve mutasyon aracı: tests/test_sim_acceptance_out.py.
"""
from __future__ import annotations

import pytest

from tests.helpers_acceptance import (
    META,
    FakePage,
    R,
    S,
    bad_exc,
    bad_fail,
    make_runner,
    ok,
)
from tools import acceptance_core as core
from tools.acceptance_core import (
    FAIL,
    PASS,
    SKIP,
    Ctx,
    Fail,
    Result,
    check_hub_log,
    exit_code,
    ordered,
    render_html,
    render_md,
    run_steps,
    summary_line,
)


# ---- adım kaydı / sıra ----
def test_step_registry_rejects_duplicate_ids(monkeypatch):
    monkeypatch.setattr(core, 'STEPS', [])
    core.step('x.bir', 'Bir', 'X')(ok)
    with pytest.raises(ValueError, match='tekrarlandı'):
        core.step('x.bir', 'Yine bir', 'X')(ok)
    assert [s.id for s in core.STEPS] == ['x.bir']


def test_ordered_moves_last_steps_to_the_end_keeping_order():
    steps = [S('a', ok), S('acil', ok, last=True), S('b', ok), S('c', ok)]
    assert [s.id for s in ordered(steps)] == ['a', 'b', 'c', 'acil']


def test_registered_steps_are_unique_and_estop_runs_last():
    ids = [s.id for s in core.STEPS]
    assert len(ids) == len(set(ids)) and len(ids) >= 12
    assert {'kontrol.klavye', 'parametreler.sim_uygula', 'acil_dur.kilit'} <= set(ids)
    assert ordered(core.STEPS)[-1].id == 'acil_dur.kilit'
    seq = [s.section for s in ordered(core.STEPS)]
    runs = [x for i, x in enumerate(seq) if i == 0 or x != seq[i - 1]]
    assert len(runs) == len(set(runs))                               # her bölümün adımları yan yana
    for section in ('Kontrol', 'Parçalar', 'Algoritmalar', 'Parametreler', 'Günlük', 'ACİL DUR'):
        assert any(s.section == section for s in core.STEPS), section


def test_ordered_groups_sections_by_first_appearance_keeping_order_inside():
    steps = [S('a.1', ok, 'A'), S('b.1', ok, 'B'), S('a.2', ok, 'A'), S('z', ok, 'Z', last=True),
             S('b.2', ok, 'B')]
    assert [s.id for s in ordered(steps)] == ['a.1', 'a.2', 'b.1', 'b.2', 'z']


# ---- koşucu ----
def test_run_steps_pass_fail_exception_isolated(tmp_path):
    make_ctx, made = make_runner(tmp_path)
    res = run_steps(make_ctx, [S('p', ok), S('f', bad_fail), S('e', bad_exc), S('p2', ok)])
    assert [r.status for r in res] == [PASS, FAIL, FAIL, PASS]            # bir KALDI sonrakini durdurmaz
    assert 'ölçülen' in res[1].note and '± 0.5' in res[1].note
    assert res[1].measured['J1 sonra (°)'].startswith('10.00')            # ölçüm KALDI'da da raporda
    assert res[2].note == 'TimeoutError: düğme 20 sn içinde etkinleşmedi'  # yalnız ilk satır
    assert res[1].shots == ['f__kaldi.png'] and (tmp_path / 'shots' / 'f__kaldi.png').is_file()
    assert res[0].shots == []
    assert made == ['p', 'f', 'e', 'p2']                                   # her adımın bağlamı kapatıldı


def test_run_steps_failed_screenshot_does_not_hide_failure(tmp_path):
    make_ctx, _ = make_runner(tmp_path, fail_shot=True)
    res = run_steps(make_ctx, [S('f', bad_fail)])
    assert res[0].status == FAIL and res[0].shots == []


def test_run_steps_browser_errors_fail_the_step(tmp_path):
    def noisy(c: Ctx) -> None:
        c.errors.append('console: Refused to execute inline script (CSP)')

    make_ctx, _ = make_runner(tmp_path)
    res = run_steps(make_ctx, [S('n', noisy)])
    assert res[0].status == FAIL
    assert 'tarayıcıda 1 hata' in res[0].note and 'CSP' in res[0].note
    assert res[0].measured['tarayıcı hataları'] == '1'


def test_run_steps_notes_are_kept_on_pass(tmp_path):
    def noted(c: Ctx) -> None:
        c.notes.append('bilinen eksik uç: pot → 404')

    make_ctx, _ = make_runner(tmp_path)
    res = run_steps(make_ctx, [S('n', noted)])
    assert res[0].status == PASS and res[0].note == 'bilinen eksik uç: pot → 404'


def test_run_steps_skip_and_only_by_id_or_section(tmp_path):
    make_ctx, made = make_runner(tmp_path)
    steps = [S('a.1', ok, 'A'), S('a.2', ok, 'A'), S('b.1', ok, 'B')]
    res = run_steps(make_ctx, steps, skip=['a.2'])
    assert [(r.id, r.status) for r in res] == [('a.1', PASS), ('a.2', SKIP), ('b.1', PASS)]
    assert made == ['a.1', 'b.1']                                          # atlanan adım tarayıcı açmaz
    assert [r.id for r in run_steps(make_ctx, steps, only=['B'])] == ['b.1']
    assert [r.id for r in run_steps(make_ctx, steps, skip=['A'])
            if r.status == SKIP] == ['a.1', 'a.2']
    assert [r.id for r in run_steps(make_ctx, steps, only=['a.2', 'b.1'])] == ['a.2', 'b.1']


# ---- ölçüm yardımcıları ----
def test_ctx_near_records_measurement_even_when_it_passes():
    c = Ctx(None, None, '.')
    c.near('X', 19.9, 20.0, 0.5, '°')
    assert c.measured['X'] == '19.90 ° (beklenen 20.00 ± 0.5)'


@pytest.mark.parametrize('got', [float('nan'), 21.0, 19.0])
def test_ctx_near_fails_outside_tolerance_and_on_nan(got):
    c = Ctx(None, None, '.')
    with pytest.raises(Fail, match='ölçülen'):
        c.near('X', got, 20.0, 0.5)
    assert 'X' in c.measured


def test_ctx_measure_formats_floats_compactly():
    c = Ctx(None, None, '.')
    c.measure('a', 2.0)
    c.measure('b', 0.1234567)
    c.measure('c', 7)
    assert (c.measured['a'], c.measured['b'], c.measured['c']) == ('2', '0.123', '7')


def test_ctx_expect():
    c = Ctx(None, None, '.')
    c.expect(True, 'sorun yok')
    with pytest.raises(Fail, match='423 bekleniyordu'):
        c.expect(False, '423 bekleniyordu')


def test_exit_code_semantics():
    assert exit_code([R(PASS), R(PASS)]) == 0
    assert exit_code([R(PASS), R(SKIP)]) == 0
    assert exit_code([R(PASS), R(FAIL)]) == 1
    assert exit_code([]) == 1                      # boş geçiş kabul sayılmaz
    assert exit_code([R(SKIP), R(SKIP)]) == 1      # hepsi atlandı → kanıt yok
    assert summary_line([R(PASS), R(FAIL), R(SKIP), R(PASS)]) == '2 GEÇTİ · 1 KALDI · 1 ATLANDI'


# ---- hub.log ----
def test_check_hub_log():
    clean = 'INFO:     127.0.0.1 - "GET /api/state HTTP/1.1" 200 OK\nINFO uvicorn started\n'
    assert check_hub_log(clean).status == PASS
    tb = clean + 'ERROR:    Exception in ASGI application\nTraceback (most recent call last):\n  File "x"\n'
    r = check_hub_log(tb)
    assert (r.status, r.id, r.measured['hatalı satır']) == (FAIL, 'hub.log', '2')
    assert 'Exception' in r.note                                   # ilk hatalı satır raporlanır
    assert check_hub_log('2026-11-25 12:00:00 ERROR hub: boom').status == FAIL   # Hub'ın kendi biçimi
    estop = '2026-11-25 19:21:31,506 CRITICAL estop: ACİL DUR (127.0.0.1)'
    assert check_hub_log(estop).status == PASS                     # ACİL DUR beklenen olay, hata değil
    assert check_hub_log('INFO GET /api/log?level=ERROR 200 OK\nWARNING sınır aşıldı').status == PASS

# ---- ATLANDI (Skip), beklenen konsol hatası, başlatma hatası ----
def test_skip_is_reported_with_reason_and_never_fails(tmp_path):
    def skipper(c: Ctx) -> None:
        c.measure('kart', 'gizli')
        c.errors.append('console: hata (atlanan adımda önemsiz)')
        c.skip('sınanamaz: donanım yok')

    def empty_reason(c: Ctx) -> None:
        c.skip('')

    make_ctx, made = make_runner(tmp_path)
    res = run_steps(make_ctx, [S('s', skipper), S('e', empty_reason), S('p', ok)])
    assert [r.status for r in res] == [SKIP, SKIP, PASS]
    assert res[0].note == 'sınanamaz: donanım yok' and res[0].measured == {'kart': 'gizli'}
    assert res[0].shots == []                                    # KALDI değil: kırılma görüntüsü yok
    assert res[1].note == 'sebep belirtilmedi'
    assert exit_code(res) == 0 and summary_line(res) == '1 GEÇTİ · 0 KALDI · 2 ATLANDI'
    assert made == ['s', 'e', 'p']                               # bağlam yine kapatılır


def test_skip_note_is_visible_in_reports_even_with_measurements(tmp_path):
    res = [Result('s', 'Atlanan', SKIP, {'kart': 'gizli'}, [], 'sebep: telefon düzeni', 0.1)]
    assert '**sebep: telefon düzeni**' in render_md(res, META)
    assert 'sebep: telefon düzeni' in render_html(res, META, tmp_path)


def test_allowed_console_errors_are_filtered_noted_and_scoped(tmp_path):
    def estop_noise(c: Ctx) -> None:
        c.allow_error(r'status of 423 \(Locked\).*/api/jog')
        c.errors.append('console: Failed to load resource: the server responded with a status of 423 '
                        '(Locked) http://h/api/jog')

    def other_noise(c: Ctx) -> None:                             # izin yalnız o adım + yalnız o örüntü için
        c.errors.append('console: Failed to load resource: the server responded with a status of 423 '
                        '(Locked) http://h/api/move')

    def mixed(c: Ctx) -> None:
        estop_noise(c)
        c.errors.append('console: Refused to execute inline script')

    make_ctx, _ = make_runner(tmp_path)
    res = run_steps(make_ctx, [S('a', estop_noise), S('b', other_noise), S('c', mixed)])
    assert [r.status for r in res] == [PASS, FAIL, FAIL]
    assert res[0].note == '1 beklenen tarayıcı hatası süzüldü'
    assert res[0].measured['tarayıcı hataları'] == '0'
    assert 'api/move' in res[1].note
    assert 'inline script' in res[2].note and res[2].measured['tarayıcı hataları'] == '1'


def test_run_steps_prep_failure_fails_only_that_step(tmp_path):
    made = []

    def make_ctx(step_id: str) -> Ctx:
        if step_id == 'b':
            raise RuntimeError('SİM sıfırlanamadı: HTTP 423\nayrıntı')
        made.append(step_id)
        return Ctx(FakePage(), None, tmp_path / 'shots')

    res = run_steps(make_ctx, [S('a', ok), S('b', ok), S('c', ok)])
    assert [r.status for r in res] == [PASS, FAIL, PASS]          # koşum çökmez, sonrakiler koşar
    assert res[1].note == 'adım başlatılamadı: RuntimeError: SİM sıfırlanamadı: HTTP 423'
    assert made == ['a', 'c']
