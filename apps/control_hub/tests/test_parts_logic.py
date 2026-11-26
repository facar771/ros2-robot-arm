"""R12: Parçalar sayfasının saf mantığı (web/parts_logic.js) ayrıldı.

Mantık DOM'suz node'da sınanır; sayfa (gerçek parts.js, sahte DOM) aynı davranışı verir
(tests/test_parts_page.py); sayfa mantık betiğini `scripts` ile kendisi yükler, dosyalar sunulur,
parts.js < 350 satır, K4/K12 korunur.
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
# Kararlar mantık dosyasındadır: sayfa bu metin/sayı/renk/kimlik sabitlerini taşımaz.
DECISIONS = (
    'ACİL DUR aktif', 'Bu oturum salt-okunur', 'Robot verisi bayat: hareket yok',
    'Robotun TCP konumu bilinmiyor', 'Önce bir dosya seç', 'sınırını aşıyor', 'Yükleniyor',
    'Dönüştürülüyor', 'Kontrol geçti', 'Hareket komutu gönderildi', 'GÖNDERİLMEDİ', 'Pitch ±90°',
    'Boyut (ölçekli)', 'Takım ofseti (flanşa göre)', 'Yerleşim (dünya çerçevesi)', 'parts:ap-',
    '0xf0883e', '0xda3633', 'MAX_BYTES', 'STEP_EXT', 'PART_SCENE_ID', 'Serbest: yalnız KONUM',
    'GERÇEK ya da bilinmeyen', 'OpenCASCADE', 'LGPL', 'Henüz nokta seçilmedi', 'Emin misin',
    'Math.round(', 'Parça ekle (STL', 'En çok 50 MB')


@needs_node
def test_parts_logic_in_node():
    """DOM'suz saf mantık (boş vm bağlamında da yüklenir): düğme kapalılıkları, STEP/IGES kararları,
    kayıt yaması, sahne nesneleri, yönelim kipi, iletiler."""
    r = subprocess.run(['node', str(JS / 'parts_logic_check.js'), str(WEB)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and 'PARTS_LOGIC_OK' in r.stdout, r.stdout + r.stderr


def test_page_loads_its_logic_and_assets_are_served(make_client):
    c, _ = make_client('open')
    assert 'parts' in {p['id'] for p in c.get('/api/pages').json()['pages']}
    page = c.get('/pages/parts.js').text
    # mantık betiği mount'tan ÖNCE yüklenir (pages.js `scripts`): mantıksız sayfa kurulmaz
    assert re.search(r"scripts:\s*\[[^\]]*'/parts_logic\.js'", page) and 'window.PartsLogic' in page
    for path in ('/parts_logic.js', '/pages/parts.js', '/pages/parts.css'):
        assert c.get(path).status_code == 200, path
    assert c.get('/parts_logic.js').headers['content-type'].startswith('text/javascript')


def test_page_holds_no_decision_logic_and_files_have_no_unsafe_dom():
    page = (WEB / 'pages' / 'parts.js').read_text(encoding='utf-8')
    assert "id: 'parts'" in page
    code = re.sub(r'/\*.*?\*/', '', page, flags=re.S)         # yorumlar serbest: yalnız KOD taranır
    code = re.sub(r'(?m)^\s*//.*$|\s//[^\n]*$', '', code)
    for literal in DECISIONS:
        assert literal not in code, literal
    for name in ('pages/parts.js', 'parts_logic.js'):                             # K4
        text = (WEB / name).read_text(encoding='utf-8')
        assert not UNSAFE.search(text), name
        assert 'http://' not in text and 'https://' not in text, name


def test_say_goes_through_the_shared_writer():
    """R12'de say() ayrı iş olarak bayt-bayt korunmuştu (T7: 8 kopya); R8b onu ortak HubPages.say'e geçirdi:
    yerel sınıf/metin yazıcı kalmaz, yalnız ince sarmalayıcı (cls: bad | warn | ok | '')."""
    page = (WEB / 'pages' / 'parts.js').read_text(encoding='utf-8')
    assert "const say = (n, text, cls = '') => HubPages.say(n, text, cls);" in page
    assert "n.className = 'say '" not in page and 'n.textContent = text' not in page


def test_parts_page_is_under_350_lines_and_every_file_under_400():
    """R12 hedefi (parts.js < 350) ve K12."""
    page = (WEB / 'pages' / 'parts.js').read_text(encoding='utf-8')
    assert len(page.splitlines()) < 350
    files = [WEB / 'parts_logic.js', JS / 'parts_logic_check.js', JS / 'parts_page_check.js',
             TESTS / 'test_parts_logic.py', TESTS / 'test_parts_page.py']
    for path in files:
        assert len(path.read_text(encoding='utf-8').splitlines()) < 400, path.name
