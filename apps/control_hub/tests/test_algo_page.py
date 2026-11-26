"""A1 Algoritmalar sayfası ön yüzü: dosya kuralları (UI_CONTRACT §5-§9, K2/K12) ve node sınaması."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
WEB = APP / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')
SCENE_ID = re.compile(r'^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,64}$')


def read(rel: str) -> str:
    return (APP / 'control_hub' / rel).read_text(encoding='utf-8')


def test_page_files_follow_the_contract():
    js, css = read('web/pages/algo.js'), read('web/pages/algo.css')
    assert "id: 'algo'" in js and 'innerHTML' not in js and 'eval(' not in js
    assert 'http://' not in js + css and 'https://' not in js + css and '@import' not in css
    assert 'style=' not in js and 'onclick' not in js and '<script' not in js and 'document.write' not in js
    # ağ erişimi yalnız api() sarmalayıcısıyla
    assert 'localStorage' not in js and 'XMLHttpRequest' not in js and 'fetch(' not in js


def test_page_only_talks_to_its_own_api_and_the_program_handoff():
    js = read('web/pages/algo.js')
    assert set(re.findall(r"'(/api/[^']*)'", js)) == {'/api/algo/kinds', '/api/algo/generate'}
    assert set(re.findall(r'HubProgram\.(\w+)|\bH\.(addSteps)', js)) <= {('addSteps', ''), ('', 'addSteps')}
    assert 'window.HubProgram' in js and "href: '#/program'" in js


def test_page_scene_ids_use_its_own_prefix_and_are_valid():
    js = read('web/pages/algo.js')
    assert "SC = 'algo:'" in js
    ids = re.findall(r'`\$\{SC\}([^`]*)`', js)          # üretilen nesne kimliği kalıpları
    assert ids and all(i.startswith(('path', 'm$', 'b$', 's$')) for i in ids), ids
    for i in ('path', 'm0', 'm499', 'b17', 's17'):
        assert SCENE_ID.match(f'algo:{i}')


def test_every_new_or_touched_file_stays_under_400_lines():
    for name in ('hub_core/algo_gen.py', 'server/algo_routes.py', 'web/pages/algo.js', 'web/pages/algo.css',
                 'web/pages/program.js', 'web/pages/program.css', 'hub_core/program_store.py',
                 'hub_core/program_runner.py', 'hub_core/program_move.py', 'server/program_routes.py'):
        assert len(read(name).splitlines()) < 400, name
    for name in ('test_algo_gen.py', 'test_algo_routes.py', 'test_algo_page.py', 'js/algo_page_check.js'):
        assert len((Path(__file__).parent / name).read_text(encoding='utf-8').splitlines()) < 400, name


def test_program_page_exposes_the_handoff_the_algo_page_uses():
    prog = read('web/pages/program.js')
    assert 'window.HubProgram' in prog and 'addSteps' in prog
    assert 'MAX_STEPS = 500' in read('web/program_logic.js')     # R12: sınır mantık dosyasına taşındı


@needs_node
def test_algo_page_logic_in_node():
    r = subprocess.run(['node', str(JS / 'algo_page_check.js'), str(WEB / 'pages' / 'algo.js')],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'ALGO_PAGE_OK' in r.stdout, r.stdout + r.stderr
