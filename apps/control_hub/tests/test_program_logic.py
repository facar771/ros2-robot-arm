"""R12: Program sayfasının saf mantığı (web/program_logic.js) ayrıldı.

Mantık DOM'suz node'da sınanır; sayfa (gerçek program.js, sahte DOM) aynı davranışı verir
(tests/test_program_ui.py); sayfa mantık betiğini kendisi yükler, dosyalar sunulur, program.js < 350 satır,
K4/K12 korunur.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
TESTS = Path(__file__).parent
JS = TESTS / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')
UNSAFE = re.compile(r'\.innerHTML|\.outerHTML|insertAdjacentHTML|\beval\(|new Function|document\.write'
                    r'|\bonclick\s*=|style\.cssText')
# Kararlar mantık dosyasındadır: sayfa bu metin/sayı/renk sabitlerini KENDİ içinde taşımaz.
DECISIONS = ('Kontroller kilitli', 'ACİL DUR aktif', 'Ulaşılabilir', 'Reddedildi', 'Eklem hareketi (J)',
             'program:wp', '#4f8cff', '#e3b341', 'En çok', 'kaydedilmemiş', 'Önizleme geçti', 'MAX_STEPS',
             'NAME_RE', 'toFixed(')


@needs_node
def test_program_logic_in_node():
    """DOM'suz saf mantık (boş vm bağlamında da yüklenir): kapı, düğme kapalılıkları, model düzenleme,
    hedef, sahne nesneleri."""
    r = subprocess.run(['node', str(JS / 'program_logic_check.js'), str(WEB)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and 'PROGRAM_LOGIC_OK' in r.stdout, r.stdout + r.stderr


def test_page_loads_its_logic_and_assets_are_served(make_client):
    c, _ = make_client('open')
    assert 'program' in {p['id'] for p in c.get('/api/pages').json()['pages']}
    page = c.get('/pages/program.js').text
    assert "loadScript('/program_logic.js')" in page and 'await ready' in page   # mount mantıksız kurulmaz
    for path in ('/program_logic.js', '/pages/program.js', '/pages/program.css'):
        assert c.get(path).status_code == 200, path
    assert c.get('/program_logic.js').headers['content-type'].startswith('text/javascript')


def test_page_holds_no_decision_logic_and_files_have_no_unsafe_dom():
    page = (WEB / 'pages' / 'program.js').read_text(encoding='utf-8')
    assert 'window.ProgramLogic' in page and "id: 'program'" in page
    for literal in DECISIONS:
        assert literal not in page, literal
    for name in ('pages/program.js', 'program_logic.js'):                         # K4
        text = (WEB / name).read_text(encoding='utf-8')
        assert not UNSAFE.search(text), name
        assert 'http://' not in text and 'https://' not in text, name


def test_program_page_is_under_350_lines_and_every_file_under_400():
    """R12 hedefi (program.js < 350) ve K12."""
    page = (WEB / 'pages' / 'program.js').read_text(encoding='utf-8')
    assert len(page.splitlines()) < 350
    files = [WEB / 'program_logic.js', JS / 'program_logic_check.js', JS / 'program_page_check.js',
             TESTS / 'test_program_logic.py']
    for path in files:
        assert len(path.read_text(encoding='utf-8').splitlines()) < 400, path.name
