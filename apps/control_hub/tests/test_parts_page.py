"""Parçalar sayfası (S2): UI_CONTRACT §5 şablonu (kayıt, kendi CSS'i, temizlik), sunulan varlıklar,
vendor sabitleme ve gerçek pages/parts.js'in node'da akışı.
Parça uçlarının testleri test_parts_api.py'dedir."""
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: JS testi atlandı')
MAX_LINES = 400                                                       # K12: bir dosya 400 satırı aşmaz
S2_FILES = ('scene3d.js', 'scene_hub.js', 'scene_logic.js', 'approach.js', 'parts_logic.js',
            'pages/parts.js', 'pages/parts.css')


def run_node(script, *args):
    cmd = ['node', str(JS / script), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


# --- UI_CONTRACT §5 şablonu -----------------------------------------------------------------------
def test_page_file_is_registered_clean_and_listed(make_client):
    src = (WEB / 'pages' / 'parts.js').read_text(encoding='utf-8')
    assert re.search(r"HubPages\.register\(\{\s*id:\s*'parts'", src)
    assert "styles: ['/pages/parts.css']" in src and "'/approach.js'" in src
    for bad in ('innerHTML', 'eval(', 'onclick', 'document.write', 'style="'):
        assert bad not in src, bad
    c, _ = make_client('open')
    assert 'parts' in [p['id'] for p in c.get('/api/pages').json()['pages']]


def test_page_css_selectors_are_all_scoped_to_the_page_box():
    """§5: sayfa stili yalnız kendi kutusunu etkiler (başka sayfaya/üst çubuğa sızmaz)."""
    css = re.sub(r'/\*.*?\*/', '', (WEB / 'pages' / 'parts.css').read_text(encoding='utf-8'), flags=re.S)
    css = re.sub(r'@media[^{]*\{', '', css)
    selectors = [s.strip() for block in re.findall(r'([^{}]+)\{', css) for s in block.split(',') if s.strip()]
    assert selectors, 'CSS boş'
    loose = [s for s in selectors if not s.startswith('#page-parts')]
    assert not loose, loose
    assert 'url(' not in css and '@import' not in css                # dış kaynak yok (CSP)


def test_s2_files_stay_under_the_line_limit():
    for name in S2_FILES:
        assert len((WEB / name).read_text(encoding='utf-8').splitlines()) <= MAX_LINES, name


# --- sunulan varlıklar ----------------------------------------------------------------------------
@pytest.mark.parametrize('path,ctype', [
    ('/scene_hub.js', 'javascript'), ('/approach.js', 'javascript'), ('/scene_logic.js', 'javascript'),
    ('/parts_logic.js', 'javascript'),
    ('/pages/parts.js', 'javascript'), ('/pages/parts.css', 'css'),
    ('/vendor/three/TransformControls.js', 'javascript')])   # ES modülü olarak yüklenir: içerik türü şart
def test_s2_assets_are_served_with_the_right_type(make_client, path, ctype):
    c, _ = make_client('open')
    r = c.get(path)
    assert r.status_code == 200 and ctype in r.headers['content-type'], path
    assert "default-src 'self'" in r.headers['content-security-policy']


# --- node: gerçek JS dosyaları ----------------------------------------------------------------------
@needs_node
def test_parts_page_flow_in_node():
    """Gerçek pages/parts.js sahte DOM'da: yükleme sınırları, alanlar, gizmo (yalnız phase 'end' kaydeder),
    dokun-git (kontrol → onay → hareket), ACİL DUR/bayat veride hareket gitmez, çıkışta temizlik,
    iki adımlı silme."""
    assert 'PARTS_PAGE_JS_OK' in run_node('parts_page_check.js', WEB)


# --- vendor: TransformControls.js sabitlenmiş mi -----------------------------------------------------
TC_ORIGINAL_SHA256 = '151befe25bb0d68626f9a6b033625b7b0cf6848e39ba71623b7bed8e3df565d6'
TC_PATCHED_SHA256 = '6c099bd9080a57ba70b0a30ecf6e6b313f13000bf2c8ecfbaea6e3ec539d065d'


def test_transform_controls_is_pinned():
    """K5: vendor dosyası sürümüyle ve SHA-256'sıyla README'de; yama TEK satır; lisans dosyası yanında."""
    vendor = WEB / 'vendor' / 'three'
    data = (vendor / 'TransformControls.js').read_bytes()
    readme = (vendor / 'README.md').read_text(encoding='utf-8')
    assert 'three@0.187.0' in readme and TC_ORIGINAL_SHA256 in readme and TC_PATCHED_SHA256 in readme
    assert hashlib.sha256(data).hexdigest() == TC_PATCHED_SHA256, 'dosya değişmiş: README + SHA-256 güncelle'
    assert data.count(b"} from './three.module.js';") == 1 and b"from 'three'" not in data
    restored = data.replace(b"} from './three.module.js';", b"} from 'three';")
    assert hashlib.sha256(restored).hexdigest() == TC_ORIGINAL_SHA256, 'yama tek satırdan fazla'
    assert 'MIT' in (vendor / 'LICENSE').read_text(encoding='utf-8')
