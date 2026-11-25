"""SİM kabul betiğinin SAF mantığı — rapor, 3B izdüşüm, STL üreteci, CLI parçaları, mutasyon aracı.

Tarayıcı, Playwright ya da Hub gerekmez (docs/KABUL.md). Adım koşucusu / çıkış kodu / ATLANDI:
tests/test_sim_acceptance.py. Ortak yardımcılar: tests/helpers_acceptance.py.
"""
from __future__ import annotations

import base64
import json
import struct

import pytest

from tests.helpers_acceptance import IDENT, META, PNG, RECT, R, console_msg, sample_results
from tools import acceptance_mutate, sim_acceptance
from tools.acceptance_core import (
    FAIL,
    PASS,
    Ctx,
    Fail,
    project,
    render_html,
    render_md,
    stl_cube,
    write_reports,
)


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
