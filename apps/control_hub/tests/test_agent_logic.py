"""R9: ajan panelinin saf mantığı (web/agent_logic.js) ayrıldı.

Ekran/istek/bildirim DEĞİŞMEDİ (node'da gerçek agent.js sahte DOM + sahte fetch üzerinde), sayfa kaydı mantık
betiğini panelden ÖNCE yükler, dosyalar sunulur, K4/K12 korunur.
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
LITERALS = ("'/api/agent/chat", "'/api/agent/message", "'/api/agent/config", "'/api/agent/key",
            'JSON.stringify', 'parseInt', 'encodeURIComponent', 'Math.max', 'Math.ceil')
UNSAFE = re.compile(r'\.innerHTML|\.outerHTML|insertAdjacentHTML|\beval\(|new Function|document\.write'
                    r'|\bonclick\s*=|style\.cssText')


def run_node(script):
    r = subprocess.run(['node', str(JS / script), str(WEB)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@needs_node
def test_agent_logic_and_panel_in_node():
    """A) DOM'suz saf mantık (boş vm bağlamında da yüklenir) B) panel: kurulum, yenileme, onay, gönder, ayar,
    anahtar C) sayfa kaydı ve available()."""
    assert 'AGENT_LOGIC_OK' in run_node('agent_logic_check.js')


def test_page_loads_logic_before_panel_and_assets_are_served(make_client):
    c, _ = make_client('open')
    assert 'agent' in {p['id'] for p in c.get('/api/pages').json()['pages']}
    page = c.get('/pages/agent.js').text
    assert page.index("'/agent_logic.js'") < page.index("'/agent.js'")    # panel yüklenirken mantık hazır
    for path in ('/agent_logic.js', '/agent.js'):
        r = c.get(path)
        assert r.status_code == 200 and r.headers['content-type'].startswith('text/javascript'), path


def test_panel_holds_no_decision_logic_and_new_files_have_no_unsafe_dom():
    """Kararlar (adresler, gövdeler, imzalar, sayılar) mantık dosyasındadır; panel yalnız çizer."""
    panel = (WEB / 'agent.js').read_text(encoding='utf-8')
    assert 'window.AgentLogic' in panel
    for literal in LITERALS:
        assert literal not in panel, literal
    for name in ('agent.js', 'agent_logic.js', 'pages/agent.js'):             # K4
        assert not UNSAFE.search((WEB / name).read_text(encoding='utf-8')), name


def test_every_new_or_touched_file_stays_under_400_lines():
    """K12."""
    checks = ('agent_logic_check.js', 'robot_page_check.js', 'page_load_order_check.js', 'page_env.js')
    files = [WEB / n for n in ('agent.js', 'agent_logic.js', 'pages/agent.js')]
    files += [JS / n for n in checks] + [TESTS / 'test_agent_logic.py', TESTS / 'test_page_load_order.py']
    for path in files:
        assert len(path.read_text(encoding='utf-8').splitlines()) < 400, path.name
