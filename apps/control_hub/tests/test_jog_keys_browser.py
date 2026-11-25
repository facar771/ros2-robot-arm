"""P1c klavye jog, gerçek Chromium'da (YEREL; Playwright/Chrome yoksa ATLANIR).
keyboard.down/up ile SİM hareketi, bırakınca durma, metin kutusunda yok sayma, Esc = ACİL DUR (423)."""
import os
import shutil
import socket
import threading
import time

import pytest
import uvicorn

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend
from control_hub.server import create_app
from tests.conftest import CONFIG

pw = pytest.importorskip('playwright.sync_api')
ARGS = ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
        '--use-gl=angle']


@pytest.fixture
def server():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    hub = Hub(SimBackend(), scen, ui, AccessControl('open', '123456'))
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(hub), host='127.0.0.1', port=port, log_level='warning'))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f'http://127.0.0.1:{port}', hub
    srv.should_exit = True
    t.join(5)
    hub.close()


def _tcp_x(page):
    return float(page.eval_on_selector_all('#jp-pose-t .jp-cell b', 'els => els.map(e => e.textContent)')[0])


def test_keyboard_hold_moves_release_stops_and_esc_latches_estop(server):
    url, hub = server
    with pw.sync_playwright() as p:
        try:
            chrome = os.environ.get('HUB_CHROME') or shutil.which('google-chrome') or shutil.which('chromium')
            browser = p.chromium.launch(executable_path=chrome, headless=True, args=ARGS)
        except Exception as exc:                                   # noqa: BLE001
            pytest.skip(f'tarayıcı açılamadı: {exc}')
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.goto(url + '/#/jog')
        page.wait_for_selector('#p-keys')
        page.wait_for_timeout(2000)
        assert page.is_checked('#jp-kb-en')                        # SİM'de varsayılan açık
        x0 = _tcp_x(page)
        page.keyboard.down('w')
        page.wait_for_timeout(1500)
        assert _tcp_x(page) > x0 + 0.5                             # basılıyken ilerledi
        page.keyboard.up('w')
        page.wait_for_timeout(600)
        stopped = _tcp_x(page)
        page.wait_for_timeout(800)
        assert _tcp_x(page) == stopped                             # bırakınca durdu
        page.focus('#jp-tz')                                       # metin kutusu: tuş yok sayılır
        before = _tcp_x(page)
        page.keyboard.down('w')
        page.wait_for_timeout(600)
        page.keyboard.up('w')
        assert _tcp_x(page) == before
        page.evaluate('document.activeElement.blur()')
        page.keyboard.down('w')
        page.wait_for_timeout(500)
        page.keyboard.press('Escape')                              # ACİL DUR: üst çubuk düğmesiyle aynı yol
        page.wait_for_timeout(800)
        assert hub.estopped
        frozen = _tcp_x(page)
        page.wait_for_timeout(600)
        assert _tcp_x(page) == frozen
        status = page.evaluate(
            "fetch('/api/jog', {method: 'POST', headers: {'Content-Type': 'application/json'},"
            " body: JSON.stringify({lease: 'x', twist: [0.01,0,0,0,0,0], ttl_ms: 250})}).then(r => r.status)")
        assert status == 423
        browser.close()
