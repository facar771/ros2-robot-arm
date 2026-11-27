"""P1e sınırsız hız/ivme kartı, gerçek Chromium'da (YEREL; Playwright/Chrome yoksa ATLANIR).
% kutusu 400 kabul eder (kaydırıcı uca yaslanır, yanında gerçek değer yazar), ivme sütunu/kutusu,
aşan satır SARI, üst çubukta rozet (limit içine dönünce kalkar), gerçek/tezgâhta kontroller kilitli.
Ekran görüntüsü: HUB_SHOTS."""
import os
from pathlib import Path

import pytest

from control_hub.hub_core import AccessControl, FakeBackend, Hub, load_scenarios, load_ui_config
from control_hub.server import create_app
from tests.conftest import CONFIG
from tests.test_speed_card_browser import ARGS, J2_EFF_DEG, _open, _speed, pw, server  # noqa: F401  (fikstür)

OVER = 'SİM: hız profil sınırının ÜSTÜNDE (varsayımsal)'


def _fire(page, sel, value):
    page.fill(sel, value)
    page.dispatch_event(sel, 'change')


def _shots(page, stem, wide):
    out = os.environ.get('HUB_SHOTS')
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    kind = 'masaustu' if wide else 'telefon'
    page.evaluate("window.scrollTo(0, 0)")
    bar = Path(out) / f'{stem}_{kind}_ust_cubuk_rozet.png'
    page.locator('.topbar').screenshot(path=str(bar))                                   # rozet görünür
    page.screenshot(path=str(Path(out) / f'{stem}_{kind}_sayfa.png'))
    page.evaluate("document.querySelector('.jog-view').style.position = 'static'")   # 3B kart örtmesin
    page.locator('#p-speed').screenshot(path=str(Path(out) / f'{stem}_{kind}_kart.png'))


def test_percent_box_takes_400_slider_clamps_rows_turn_yellow_and_the_badge_comes_and_goes(server):  # noqa: F811
    url, hub = server
    with pw.sync_playwright() as p:
        browser, page = _open(p, url, 1280, 900)
        page.wait_for_timeout(1200)
        assert not page.is_visible('#pill-over') and page.inner_text('#jp-sp-real') == ''
        # ölçek 1,0 + %400 → profil limitinin 4 katı
        page.fill('#jp-sc-num', '1')
        page.click('#jp-sc-apply')
        page.wait_for_function('() => /Hız ölçeği 1,/.test(document.getElementById("jp-sc-msg").innerText)')
        _fire(page, '#jp-sp-num', '400')
        assert _speed(page) == 400
        assert float(page.input_value('#jp-sp-range')) == 100                         # kaydırıcı uca yaslandı
        assert '400' in page.inner_text('#jp-sp-real')                                # yanında gerçek değer
        page.wait_for_function(
            f'() => document.querySelector("{J2_EFF_DEG}").textContent.trim() === "18,3"')   # 4,58 × 4
        rows = page.query_selector_all('#jp-sp-table tbody tr')
        assert all(r.get_attribute('data-over') == '1' for r in rows)           # aşan satırlar sarı
        assert all('over' in (r.get_attribute('class') or '') for r in rows)
        assert page.is_visible('#jp-sp-over') and 'ÜSTÜNDE' in page.inner_text('#jp-sp-over')
        accel = page.inner_text('#jp-sp-table tbody tr:nth-child(2) td:nth-child(6)')   # J2 ivme: 0,1 × 4
        assert accel.replace(',', '.') == '0.400'
        # rozet (sunucu anlık görüntüsünden) ve sunucu hızı
        page.wait_for_selector('#pill-over:not([hidden])', timeout=6000)
        assert OVER in page.inner_text('#pill-over')
        assert hub.backend.speed_pct == 400 and hub.backend.sm.velocity_cap == 1.0
        assert any(e.kind == 'sim_over_profile' for e in hub.events.since(0))         # Günlük olayı
        _shots(page, 'p1e', True)
        # limit içine dönünce rozet ve sarı satırlar kalkar
        _fire(page, '#jp-sp-num', '100')
        page.wait_for_function('() => document.getElementById("pill-over").hidden', timeout=6000)
        assert page.query_selector('#jp-sp-table tbody tr.over') is None
        assert not page.is_visible('#jp-sp-over')
        browser.close()


def test_acceleration_scale_box_is_linked_by_default_and_independent_when_unchecked(server):  # noqa: F811
    url, hub = server
    with pw.sync_playwright() as p:
        browser, page = _open(p, url, 1280, 900)
        page.wait_for_timeout(1200)
        assert page.is_checked('#jp-ac-link') and page.is_disabled('#jp-ac-num')      # bağlı: kutu kapalı
        page.fill('#jp-sc-num', '2')
        page.click('#jp-sc-apply')
        page.wait_for_function('() => document.getElementById("jp-ac-num").value === "2"')   # birlikte gitti
        assert hub.backend.sm.accel_linked
        page.uncheck('#jp-ac-link')
        assert not page.is_disabled('#jp-ac-num')
        page.fill('#jp-ac-num', '0.5')
        page.click('#jp-sc-apply')
        page.wait_for_function('() => /ivme ölçeği 0.5/.test(document.getElementById("jp-sc-msg").innerText)')
        assert hub.backend.sm.accel_cap == 0.5 and hub.backend.sm.velocity_cap == 2.0
        page.fill('#jp-ac-num', '0')
        page.click('#jp-sc-apply')
        assert 'İvme ölçeği' in page.inner_text('#jp-sc-msg')
        assert hub.backend.sm.accel_cap == 0.5                                       # reddedildi
        browser.close()


def test_percent_box_rejects_garbage_and_overflow_with_a_reason(server):  # noqa: F811
    url, hub = server
    with pw.sync_playwright() as p:
        browser, page = _open(p, url, 1280, 900)
        page.wait_for_timeout(1200)
        before = _speed(page)
        for bad in ('', '0', '-3', '100001'):       # type=number harf kabul etmez (sunucu yine de 400 verir)
            _fire(page, '#jp-sp-num', bad)
            assert 'Hız' in page.inner_text('#jp-sc-msg') and _speed(page) == before, bad
        _fire(page, '#jp-sp-num', '100000')
        assert _speed(page) == 100000
        browser.close()


def test_phone_view_with_the_badge_fits_without_overflow(server):  # noqa: F811
    url, _ = server
    with pw.sync_playwright() as p:
        browser, page = _open(p, url, 390, 800)
        page.wait_for_timeout(1200)
        page.fill('#jp-sc-num', '1')
        page.click('#jp-sc-apply')
        page.wait_for_function('() => /Hız ölçeği 1,/.test(document.getElementById("jp-sc-msg").innerText)')
        _fire(page, '#jp-sp-num', '400')
        page.wait_for_selector('#pill-over:not([hidden])', timeout=6000)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        rect = 'e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }'
        box = page.eval_on_selector('#p-speed', rect)
        assert box[0] >= 0 and box[1] <= 391
        _shots(page, 'p1e', False)
        browser.close()


def test_real_mode_card_is_locked_and_never_offers_more_than_100(tmp_path):
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    hub = Hub(FakeBackend(time_scale=0.02), scen, ui, AccessControl('open', '123456'))
    import socket
    import threading
    import time

    import uvicorn
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
    try:
        with pw.sync_playwright() as p:
            try:
                import shutil
                chrome = (os.environ.get('HUB_CHROME') or shutil.which('google-chrome')
                          or shutil.which('chromium'))
                browser = p.chromium.launch(executable_path=chrome, headless=True, args=ARGS)
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f'tarayıcı açılamadı: {exc}')
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(f'http://127.0.0.1:{port}/#/jog')
            page.wait_for_selector('#p-speed')
            page.wait_for_function('() => document.getElementById("jp-sc-apply").disabled')
            assert all(page.is_disabled(i) for i in ('#jp-sc-num', '#jp-ac-num', '#jp-ac-link'))
            assert page.get_attribute('#jp-sp-num', 'max') == '100'
            _fire(page, '#jp-sp-num', '400')
            assert 'gerçek' in page.inner_text('#jp-sc-msg') or 'tezgâh' in page.inner_text('#jp-sc-msg')
            assert _speed(page) <= 100
            assert page.query_selector('#pill-over:not([hidden])') is None
            browser.close()
    finally:
        srv.should_exit = True
        t.join(5)
        hub.close()
