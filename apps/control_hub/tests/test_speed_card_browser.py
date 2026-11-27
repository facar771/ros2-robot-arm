"""P1d hassas hız kartı, gerçek Chromium'da (YEREL; Playwright/Chrome yoksa ATLANIR).
Kart + tablo görünür, % kutusu/−+ düğmeleri/klavye, tavan ölçeği SİM'de ayarlanır ve ACİL DUR'da kilitlenir,
telefonda kart yatay taşmaz. Ekran görüntüleri: HUB_SHOTS klasörü verilirse oraya yazılır."""
import os
import shutil
import socket
import threading
import time
from pathlib import Path

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


J2_EFF_DEG = '#jp-sp-table tbody tr:nth-child(2) td:nth-child(5)'            # J2 satırı, etkin °/s


def _open(p, url, width, height):
    try:
        chrome = os.environ.get('HUB_CHROME') or shutil.which('google-chrome') or shutil.which('chromium')
        browser = p.chromium.launch(executable_path=chrome, headless=True, args=ARGS)
    except Exception as exc:                                       # noqa: BLE001
        pytest.skip(f'tarayıcı açılamadı: {exc}')
    page = browser.new_page(viewport={'width': width, 'height': height})
    page.goto(url + '/#/jog')
    page.wait_for_selector('#jp-sp-table tbody tr')
    return browser, page


def _speed(page):
    return page.evaluate('JogPanel.speed()')


def _shot(page, name):
    out = os.environ.get('HUB_SHOTS')
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        # yapışkan 3B kartı görüntüyü örtmesin (CSSOM: CSP'ye uygun); kartın tamamı çekilir
        page.evaluate("document.querySelector('.jog-view').style.position = 'static'")
        page.locator('#p-speed').screenshot(path=str(Path(out) / name))


def test_card_table_percent_controls_keyboard_and_scale(server):
    url, hub = server
    with pw.sync_playwright() as p:
        browser, page = _open(p, url, 1280, 900)
        page.wait_for_timeout(1500)
        rows = page.query_selector_all('#jp-sp-table tbody tr')
        assert len(rows) == 6
        assert [r.get_attribute('class') or '' for r in rows].count('lim') == 1
        assert 'lim' in (rows[1].get_attribute('class') or '')                      # J2 sınırlayıcı
        assert 'en yavaş eklem belirleyici' in page.inner_text('#jp-sp-note')
        head = page.inner_text('#jp-sp-table thead')
        assert head.count('rad/s') == 3 and head.count('°/s') == 2           # P1e: + ivme sütunu
        assert 'Etkin ivme rad/s²' in head
        # % kutusu 0,1; düğmeler ±1 ve ±5; tek kaynak JogPanel hızı
        page.fill('#jp-sp-num', '12.3')
        page.dispatch_event('#jp-sp-num', 'change')
        assert _speed(page) == 12.3 and float(page.input_value('#jp-sp-range')) == 12.3
        page.click('button.jp-nb[data-delta="5"]')
        page.click('button.jp-nb[data-delta="-1"]')
        assert _speed(page) == 16.3
        page.wait_for_timeout(600)                                 # tablo yeni % ile sunucudan yenilenir
        eff_j2 = float(page.inner_text(J2_EFF_DEG).replace(',', '.'))
        assert eff_j2 == pytest.approx(4.58 * 0.3 * 0.163, abs=0.06)
        # klavye: ↑/↓ ±5, Shift ±1 (odak sayfada, metin kutusunda değil)
        page.evaluate('document.activeElement.blur()')
        page.keyboard.press('ArrowUp')
        assert _speed(page) == 21.3
        page.keyboard.press('Shift+ArrowDown')
        assert _speed(page) == 20.3
        page.keyboard.press('ArrowDown')
        assert _speed(page) == 15.3
        # tavan ölçeği: SİM'de ayarlanır; P1e: 1,01 artık kabul, tek sınır 1000 (istemci ve sunucu reddeder)
        page.fill('#jp-sc-num', '1001')
        page.click('#jp-sc-apply')
        assert 'en çok 1000' in page.inner_text('#jp-sc-msg')
        assert hub.backend.sm.velocity_cap == pytest.approx(0.3)
        page.fill('#jp-sc-num', '1')
        page.click('#jp-sc-apply')
        page.wait_for_function('() => /Hız ölçeği 1,/.test(document.getElementById("jp-sc-msg").innerText)')
        assert hub.backend.sm.velocity_cap == 1.0
        page.fill('#jp-sp-num', '100')
        page.dispatch_event('#jp-sp-num', 'change')
        page.wait_for_function(f'() => document.querySelector("{J2_EFF_DEG}").textContent.trim() === "4,6"')
        _shot(page, 'p1d_masaustu_hiz_karti.png')
        if os.environ.get('HUB_SHOTS'):
            page.screenshot(path=str(Path(os.environ['HUB_SHOTS']) / 'p1d_masaustu_sayfa.png'))
        # ACİL DUR: ölçek uygulanamaz (423 sunucuda, düğme arayüzde kapalı)
        page.keyboard.press('Escape')
        page.wait_for_function('() => document.getElementById("jp-sc-apply").disabled')
        assert hub.estopped
        status = page.evaluate("fetch('/api/sim/speed', {method: 'POST', body: JSON.stringify({scale: 0.5}),"
                               " headers: {'Content-Type': 'application/json'}}).then(r => r.status)")
        assert status == 423 and hub.backend.sm.velocity_cap == 1.0
        browser.close()


def test_phone_card_fits_without_horizontal_overflow(server):
    url, _ = server
    with pw.sync_playwright() as p:
        browser, page = _open(p, url, 390, 800)
        page.wait_for_timeout(1500)
        assert page.is_visible('#p-speed') and page.is_visible('#jp-sp-table')
        rect = 'e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }'
        box = page.eval_on_selector('#p-speed', rect)
        assert box[0] >= 0 and box[1] <= 390 + 1                                      # kart ekrana sığar
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        _shot(page, 'p1d_telefon_hiz_karti.png')
        browser.close()
