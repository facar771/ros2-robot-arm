"""P4 Tezgâh pot paneli: pot mantığı sınır değerleri + panel SAHTE veriyle (Hub'da pot uç noktası henüz yok),
ve mevcut Tezgâh paneli testinin bozulmadığı. node yoksa atlanır."""
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
WEB = APP / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'


def run_node(script):
    if shutil.which('node') is None:
        pytest.skip('node yok')
    return subprocess.run(['node', str(JS / script), str(WEB)], capture_output=True, text=True, timeout=60)


def test_pot_logic_and_panel_with_fake_data_in_node():
    r = run_node('pot_panel_check.js')
    assert r.returncode == 0 and 'POT_PANEL_JS_OK' in r.stdout, r.stdout + r.stderr


def test_existing_bench_panel_check_still_passes_with_pot_panel_appended():
    r = run_node('bench_panel_check.js')
    assert r.returncode == 0 and 'BENCH_PANEL_JS_OK' in r.stdout, r.stdout + r.stderr


def test_bench_page_loads_shared_pot_logic_before_bench_js_and_wires_show_hide():
    page = (WEB / 'pages' / 'bench.js').read_text(encoding='utf-8')
    assert page.index("'/pot_logic.js'") < page.index("'/bench.js'")
    assert 'PotPanel.start()' in page and 'PotPanel.stop()' in page
    assert (WEB / 'pages' / 'bench.css').is_file() and (WEB / 'pot_logic.js').is_file()


def test_pot_files_have_no_dom_injection_or_inline_handlers():
    import re
    pattern = re.compile(r'\.innerHTML|\.outerHTML|insertAdjacentHTML|\beval\(|document\.write|\bonclick\s*=')
    for name in ('pot_logic.js', 'bench.js', 'pages/bench.js'):
        text = (WEB / name).read_text(encoding='utf-8')
        assert not pattern.search(text), name
