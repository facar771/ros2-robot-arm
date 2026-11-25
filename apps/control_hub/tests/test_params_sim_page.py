# ruff: noqa: F811  (pytest fixture, params_support.py içinden içe aktarılır)
"""P4b ön yüz: "SİM'e uygula / Sıfırla" kartı + üst çubuk rozeti (dosya kuralları + YEREL tarayıcı akışı)."""
import math
import re
import time
from pathlib import Path

import pytest

pytest.importorskip('numpy')
pytest.importorskip('yaml')

from control_hub.hub_core import AccessControl, Hub  # noqa: E402
from control_hub.hub_core.sim_backend import SimBackend  # noqa: E402
from tests.params_support import data_dir, repo_hashes  # noqa: E402,F401
from tests.test_params_page import SAVE, serve  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'


def test_page_has_the_sim_card_and_the_badge_without_innerhtml():
    page = (WEB / 'pages' / 'params.js').read_text(encoding='utf-8')
    for needle in ("SİM'e uygula", 'Sıfırla (gerçek profile dön)', 'TASLAK PROFİL (SİM)',
                   '/api/params/sim/apply', '/api/params/sim/reset', 'pill-profile'):
        assert needle in page, needle
    assert not re.search(r'\.innerHTML|insertAdjacentHTML|\beval\(|style\s*=', page)
    assert len(page.splitlines()) < 400                         # K12
    css = (WEB / 'pages' / 'params.css').read_text(encoding='utf-8')
    assert '.pr-sim' in css and '@import' not in css and 'url(' not in css   # yalnız params.css'e yazıldı


def test_browser_flow_apply_badge_reject_and_reset_on_the_real_sim(scenarios, ui, data_dir):
    """YEREL (Playwright + Chrome yoksa atlanır, CI'de koşmaz): kayıtlı taslak yokken düğme kapalı + neden,
    kaydedince açılır, uygulayınca üst çubukta rozet, Git yeni limitte reddedilir, Sıfırla rozeti kaldırır."""
    import os
    pw = pytest.importorskip('playwright.sync_api')
    backend = SimBackend()                                       # gerçek saat: arka plan iş parçacığı çalışır
    hub = Hub(backend, scenarios, ui, AccessControl('open', '123456'))
    srv, th, url = serve(hub)
    before = repo_hashes()
    far = {'type': 'joint', 'joints_deg': [25] + [math.degrees(v) for v in backend.home_q][1:]}
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=os.environ.get('HUB_CHROME'), headless=True,
                                            args=['--no-sandbox'])
            except Exception as exc:                              # noqa: BLE001
                pytest.skip(f'tarayıcı açılamadı: {exc}')
            page = browser.new_context(viewport={'width': 1280, 'height': 900}).new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(url + '/#/params')
            page.wait_for_selector('#page-params .pr-sim', timeout=15000)
            apply_btn = page.get_by_role('button', name="SİM'e uygula")
            reset_btn = page.get_by_role('button', name='Sıfırla (gerçek profile dön)')
            page.wait_for_selector('#page-params .pr-sim .pr-why:not(:empty)', timeout=8000)
            assert apply_btn.is_disabled() and reset_btn.is_disabled()
            assert 'Taslak kaydet' in page.inner_text('#page-params .pr-sim .pr-why')
            assert page.locator('#pill-profile').count() == 0      # henüz rozet yok

            def box(key):
                return page.locator(f'#page-params input[data-key="{key}"]')
            box('axes.joint_1.min_deg').fill('-5')
            box('axes.joint_1.max_deg').fill('5')
            page.wait_for_selector(f'{SAVE}:enabled', timeout=5000)
            assert apply_btn.is_disabled()                         # kayıtlı taslak henüz yok
            page.locator(SAVE).click()
            page.wait_for_selector('#page-params .pr-sim .pr-sim-btns button.primary:enabled', timeout=8000)
            vel = box('axes.joint_1.vel_dps')
            old = vel.input_value()
            vel.fill('3')                                  # kayıtlıdan farklı: SİM KAYITLI taslağı yükler
            page.wait_for_selector('#page-params .pr-sim .pr-why:not(:empty)', timeout=8000)
            why = page.inner_text('#page-params .pr-sim .pr-why')
            assert apply_btn.is_disabled() and 'Kaydedilmemiş' in why
            vel.fill(old)
            page.wait_for_selector('#page-params .pr-sim .pr-sim-btns button.primary:enabled', timeout=8000)
            apply_btn.click()
            page.wait_for_selector('#pill-profile:not([hidden])', timeout=8000)
            assert page.inner_text('#pill-profile') == 'TASLAK PROFİL (SİM)'
            assert 'ETKİN' in page.inner_text('#page-params .pr-sim [role=status]')
            page.goto(url + '/#/robot')                            # rozet HER sayfada görünür
            page.wait_for_selector('#pill-profile:not([hidden])', timeout=8000)
            limited = backend.move_submit(far)
            assert (limited[0], limited[1]['error']) == (409, 'joint_limit')   # yeni limitte reddedildi
            page.goto(url + '/#/params')
            page.wait_for_selector('#page-params .pr-sim', timeout=8000)
            reset_btn.click()
            page.wait_for_selector('#pill-profile[hidden]', state='attached', timeout=8000)
            assert 'Gerçek profil kullanılıyor' in page.inner_text('#page-params .pr-sim [role=status]')
            assert backend.move_check(far)[1]['ok']
            time.sleep(0.2)
            assert errors == []
            browser.close()
    finally:
        srv.should_exit = True
        th.join(5)
        hub.close()
    assert repo_hashes() == before
