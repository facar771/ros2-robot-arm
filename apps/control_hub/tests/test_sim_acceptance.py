"""SİM kabul betiğinin SAF mantığı (tools/acceptance_core.py, tools/sim_acceptance.py).

Tarayıcı, Playwright ya da Hub gerekmez: sahte `page`/`ctx` ile adım koşucusu, çıkış kodu, rapor,
izdüşüm ve STL üreteci sınanır. Betiğin kendisi (gerçek tarayıcı) `python -m tools.sim_acceptance`
ile koşulur (docs/KABUL.md).
"""
from __future__ import annotations

import base64
import json
import struct
from types import SimpleNamespace

import pytest

from tools import acceptance_core as core
from tools import acceptance_mutate, sim_acceptance
from tools.acceptance_core import (
    FAIL,
    PASS,
    SKIP,
    Ctx,
    Fail,
    Result,
    Step,
    check_hub_log,
    exit_code,
    ordered,
    project,
    render_html,
    render_md,
    run_steps,
    stl_cube,
    summary_line,
    write_reports,
)

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHnOcQAAAAABJRU5ErkJggg==')
IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
RECT = {'l': 100.0, 't': 50.0, 'w': 400.0, 'h': 200.0}


class FakePage:
    def __init__(self, fail_shot: bool = False) -> None:
        self.fail_shot = fail_shot

    def screenshot(self, path: str, full_page: bool = False) -> None:
        if self.fail_shot:
            raise RuntimeError('ekran alınamadı')
        with open(path, 'wb') as fh:
            fh.write(PNG)


def make_runner(tmp_path, fail_shot: bool = False):
    made = []

    def make_ctx(step_id: str) -> Ctx:
        c = Ctx(FakePage(fail_shot), None, tmp_path / 'shots', '', closer=lambda: made.append(step_id))
        return c

    return make_ctx, made


def S(step_id, fn, section='A', last=False) -> Step:
    return Step(step_id, f'Başlık {step_id}', fn, section, last)


def ok(c: Ctx) -> None:
    c.measure('değer', 1.5)


def bad_fail(c: Ctx) -> None:
    c.measure('J1 (°)', 10.0)
    c.near('J1 sonra (°)', 10.0, 20.0, 0.5, '°')


def bad_exc(c: Ctx) -> None:
    raise TimeoutError('düğme 20 sn içinde etkinleşmedi\nikinci satır')


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


# ---- çıkış kodu ----
def R(status: str) -> Result:
    return Result('i', 't', status)


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


# ---- 3B izdüşüm ----
def test_project_identity_maps_ndc_to_canvas_rect():
    px, py, nz = project((0, 0, 0), IDENT, IDENT, RECT)
    assert (px, py, nz) == (300.0, 150.0, 0.0)                    # NDC (0,0) = tuvalin ortası
    assert project((1, 1, 0), IDENT, IDENT, RECT)[:2] == (500.0, 50.0)     # sağ-üst
    assert project((-1, -1, 0), IDENT, IDENT, RECT)[:2] == (100.0, 250.0)  # sol-alt
    assert project((0, 0, 3), IDENT, IDENT, RECT)[2] == 3.0       # ndc_z çağırana: görüş dışı denetimi


def test_project_applies_column_major_translation_and_perspective_divide():
    view = IDENT[:12] + [0.5, 0.0, 0.0, 1.0]                      # sütun-öncelikli: x'e +0.5 öteleme
    assert project((0, 0, 0), view, IDENT, RECT)[:2] == (400.0, 150.0)
    persp = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0]       # w = z
    px, _, _ = project((1, 0, 2), IDENT, persp, RECT)             # x/w = 0.5 → 3/4 genişlik
    assert px == pytest.approx(100 + 0.75 * 400)


def test_project_w_zero_raises():
    with pytest.raises(Fail, match='w=0'):
        project((0, 0, 0), IDENT, [0.0] * 16, RECT)


# ---- STL ----
def test_stl_cube_is_valid_binary_stl_with_requested_size():
    data = stl_cube(40.0)
    assert len(data) == 84 + 12 * 50 == 684
    assert struct.unpack('<I', data[80:84])[0] == 12
    xs, ys, zs = [], [], []
    for i in range(12):
        rec = struct.unpack('<12fH', data[84 + 50 * i:84 + 50 * (i + 1)])
        for v in range(3):
            xs.append(rec[3 + 3 * v]), ys.append(rec[4 + 3 * v]), zs.append(rec[5 + 3 * v])
    for axis in (xs, ys, zs):
        assert (min(axis), max(axis)) == (-20.0, 20.0)
    corner = stl_cube(10.0, center=False)
    verts = [struct.unpack('<3f', corner[84 + 50 * i + 12 + 12 * v:84 + 50 * i + 24 + 12 * v])
             for i in range(12) for v in range(3)]
    assert min(min(v) for v in verts) == 0.0 and max(max(v) for v in verts) == 10.0


# ---- rapor ----
def sample_results():
    return [Result('a.ok', 'Tamam', PASS, {'J1': '20.00 ° (beklenen 20.00 ± 0.5)'},
                   ['a_ok__son.png'], '', 1.2),
            Result('b.bad', 'Bozuk <b>', FAIL, {'x | y': '<script>alert(1)</script>'}, [],
                   'ölçülen 10 beklenen 20 <img src=x onerror=alert(1)>', 0.4),
            Result('c.skip', 'Atlanan', SKIP, note='--skip ile atlandı')]


META = {'Hub': 'python -m control_hub --sim', 'Not': '<tahmini> & SİM'}


def test_render_md_lists_failures_and_escapes_pipes():
    md = render_md(sample_results(), META)
    assert '**Sonuç: 1 GEÇTİ · 1 KALDI · 1 ATLANDI**' in md
    assert '## KALDI listesi' in md and '- `b.bad` — Bozuk <b>: ölçülen 10 beklenen 20' in md
    assert '[son](shots/a_ok__son.png)' in md
    assert 'x / y: <script>' in md                                 # tablo bozulmasın: | → /
    ok_only = render_md([R(PASS)], META)
    assert 'KALDI listesi' not in ok_only


def test_render_html_escapes_everything_and_embeds_screenshots(tmp_path):
    shots = tmp_path / 'shots'
    shots.mkdir()
    (shots / 'a_ok__son.png').write_bytes(PNG)
    page = render_html(sample_results(), META, shots)
    assert '<script' not in page and '<img src=x' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page and '&lt;img src=x onerror' in page
    assert '&lt;tahmini&gt; &amp; SİM' in page
    assert 'data:image/png;base64,' + base64.b64encode(PNG).decode() in page
    assert 'class="bad"' in page and 'class="ok"' in page and 'class="skip"' in page
    assert 'src=' in page and 'http://' not in page and 'https://' not in page   # tek dosya, dış kaynak yok
    missing = render_html(sample_results(), META, tmp_path / 'yok')            # görüntü yoksa çökmez
    assert 'data:image' not in missing


def test_write_reports_creates_three_files(tmp_path):
    out = tmp_path / 'rapor'
    paths = write_reports(sample_results(), META, out)
    assert {k: p.name for k, p in paths.items()} == {'md': 'rapor.md', 'html': 'rapor.html',
                                                    'json': 'sonuc.json'}
    assert all(p.is_file() for p in paths.values())
    doc = json.loads(paths['json'].read_text(encoding='utf-8'))
    assert doc['summary'] == '1 GEÇTİ · 1 KALDI · 1 ATLANDI'
    assert [r['id'] for r in doc['results']] == ['a.ok', 'b.bad', 'c.skip']
    assert doc['results'][1]['status'] == FAIL


# ---- CLI parçaları ----
def test_cli_csv_and_parser_defaults():
    assert sim_acceptance._csv(' a.1, ,B ,') == ['a.1', 'B']
    assert sim_acceptance._csv('') == []
    a = sim_acceptance.build_parser().parse_args([])
    assert (a.viewport, a.port, a.skip, a.only, a.list) == ('desktop', 0, '', '', False)
    assert sim_acceptance.VIEWPORTS == {'desktop': {'width': 1280, 'height': 800},
                                       'phone': {'width': 390, 'height': 844}}
    with pytest.raises(SystemExit):
        sim_acceptance.build_parser().parse_args(['--viewport', 'tablet'])


def test_cli_list_exits_zero_without_browser(capsys):
    assert sim_acceptance.main(['--list']) == 0
    out = capsys.readouterr().out
    assert 'kontrol.eklem' in out and out.strip().splitlines()[-1].startswith('acil_dur.kilit')


def console_msg(text: str, url: str, kind: str = 'error'):
    return SimpleNamespace(type=kind, text=text, location={'url': url})


def test_console_filter_ignores_only_the_known_404():
    c = Ctx(None, None, '.')
    sim_acceptance._on_console(c, console_msg('Failed to load resource: 404', 'http://h/api/pot'))
    assert c.errors == [] and c.notes == ['bilinen eksik uç: pot → 404']
    sim_acceptance._on_console(c, console_msg('Failed to load resource: 404', 'http://h/api/pot'))
    assert len(c.notes) == 1                                      # not tekrarlanmaz
    sim_acceptance._on_console(c, console_msg('Failed to load resource: 404', 'http://h/api/parts'))
    sim_acceptance._on_console(c, console_msg('Refused to execute inline script', 'http://h/'))
    sim_acceptance._on_console(c, console_msg('bilgi', 'http://h/', kind='log'))
    assert len(c.errors) == 2 and 'api/parts' in c.errors[0]


# ---- mutasyon aracı (saf kısım) ----
def test_mutate_apply_edit_requires_unique_pattern():
    src = 'a = 1\nb = 2\nb = 2\n'
    assert acceptance_mutate.apply_edit(src, 'a = 1', 'a = 9') == 'a = 9\nb = 2\nb = 2\n'
    with pytest.raises(ValueError, match='bulunamadı'):
        acceptance_mutate.apply_edit(src, 'c = 3', 'c = 4')
    with pytest.raises(ValueError, match='2 yerde'):
        acceptance_mutate.apply_edit(src, 'b = 2', 'b = 3')
    assert acceptance_mutate.apply_edit(src, 'b = 2', 'b = 3', first=True) == 'a = 1\nb = 3\nb = 2\n'


def test_mutate_cli_requires_step_and_edit():
    parser = acceptance_mutate.build_parser()
    a = parser.parse_args(['--step', 'kontrol.jog', '--edit', 'f.js', 'x', 'y', '--edit', 'g.py', 'p', 'q'])
    assert a.step == 'kontrol.jog' and a.edit == [['f.js', 'x', 'y'], ['g.py', 'p', 'q']] and not a.first
    with pytest.raises(SystemExit):
        parser.parse_args(['--step', 'kontrol.jog'])


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
