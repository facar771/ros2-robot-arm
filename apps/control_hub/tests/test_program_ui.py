"""P2 Program sayfası ön yüzü: sayfa kaydı, dosya kuralları, node sınaması (UI_CONTRACT §5-§8)."""
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
WEB = APP / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')


def test_page_is_listed_and_its_files_are_served(make_client):
    c, _ = make_client('open')
    assert 'program' in [p['id'] for p in c.get('/api/pages').json()['pages']]
    js, css = c.get('/pages/program.js'), c.get('/pages/program.css')
    assert js.status_code == 200 and js.headers['content-type'].startswith('text/javascript')
    assert css.status_code == 200 and css.headers['content-type'].startswith('text/css')
    assert "style-src 'self'" in css.headers['content-security-policy']          # sayfa stili yerel dosyadan


def test_program_page_files_follow_the_contract():
    js = (WEB / 'pages' / 'program.js').read_text(encoding='utf-8')
    css = (WEB / 'pages' / 'program.css').read_text(encoding='utf-8')
    assert "id: 'program'" in js and 'innerHTML' not in js and 'eval(' not in js
    assert 'http://' not in js + css and 'https://' not in js + css and '@import' not in css
    assert 'style=' not in js and 'onclick' not in js and '<script' not in js
    for name in ('hub_core/program_store.py', 'hub_core/program_runner.py', 'server/program_routes.py',
                 'web/pages/program.js', 'web/pages/program.css'):
        assert len((APP / 'control_hub' / name).read_text(encoding='utf-8').splitlines()) < 400, name


@needs_node
def test_program_page_logic_in_node():
    r = subprocess.run(['node', str(JS / 'program_page_check.js'), str(WEB)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'PROGRAM_PAGE_OK' in r.stdout, r.stdout + r.stderr
