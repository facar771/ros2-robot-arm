"""Tarayıcı duman testi (YEREL; Playwright + Chrome/Chromium yoksa ATLANIR, CI'de koşmaz).

Gerçek tarayıcıda: iskelet açılıyor, menü sayfaları, 3B tuval WebGL ile kuruluyor (yazılım GL yeter), eklem
hareketi 3B sahneye geliyor, ACİL DUR üst çubuktan çalışıyor. WSL'de:
    pip install playwright && python3 -m pytest tests/test_ui_browser.py -q
Chrome yolu: HUB_CHROME=/usr/bin/google-chrome (yoksa Playwright'ın kendi Chromium'u denenir).
"""
import os
import shutil
import socket
import threading
import time

import pytest
import uvicorn

from control_hub.server import create_app

pw = pytest.importorskip('playwright.sync_api')

ARGS = ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
        '--use-gl=angle']


def _chrome():
    return os.environ.get('HUB_CHROME') or shutil.which('google-chrome') or shutil.which('chromium') or None


@pytest.fixture
def server(hub):
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


def test_skeleton_renders_3d_and_estop_works(server):
    url, hub = server
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=_chrome(), headless=True, args=ARGS)
        except Exception as exc:                                   # noqa: BLE001
            pytest.skip(f'tarayıcı açılamadı: {exc}')
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(url + '/#/robot')
        page.wait_for_selector('#nav a[data-page=robot]', timeout=10000)
        assert [a.get_attribute('data-page') for a in page.query_selector_all('#nav a') if a.is_visible()] \
            == ['robot', 'jog', 'parts']                         # ajan/tezgâh kapalı: menüde yok (parts: S2)
        pills = page.eval_on_selector_all('.pill', 'els => els.map(e => e.textContent)')
        assert pills[0].startswith('Canlı') and pills[1].startswith('SİM') and 'ERİŞİM' in pills[2]
        page.wait_for_function('() => window.Scene3D && Scene3D._V.nodes.size === 7', timeout=15000)
        assert page.evaluate('() => Scene3D._V.renderer.getContext() !== null')
        assert page.is_hidden('.v3d-msg')                        # "kullanılamıyor" mesajı yok: çiziliyor
        page.click('#nav a[data-page=jog]')
        page.wait_for_selector('#p-jog', timeout=10000)
        assert page.evaluate("() => document.querySelector('.v3d').closest('.page').id") == 'page-jog'
        page.click('#btn-estop')
        page.wait_for_function(
            "() => document.getElementById('estop-label').textContent === 'ACİL DUR AKTİF'", timeout=5000)
        assert hub.estopped
        assert not errors, errors
        browser.close()
