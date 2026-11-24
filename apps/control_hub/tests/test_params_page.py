# ruff: noqa: F811  (pytest fixture, params_support.py içinden içe aktarılır)
"""P4 Parametreler sayfası (ön yüz): saf mantık (node), sayfa dosyaları (CSP, K12), gerçek tarayıcı akışı."""
import os
from pathlib import Path

import pytest

pytest.importorskip('numpy')
pytest.importorskip('yaml')

from tests.params_support import data_dir, repo_hashes  # noqa: E402,F401

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
PAGE_FILES = ('pages/params.js', 'pages/params.css', 'params_form.js', 'pot_logic.js', 'bench.js',
              'pages/bench.js')
SAVE = '#page-params .pr-bar button.primary'


def test_params_logic_in_node():
    import shutil
    import subprocess
    if shutil.which('node') is None:
        pytest.skip('node yok')
    script = Path(__file__).parent / 'js' / 'params_logic_check.js'
    r = subprocess.run(['node', str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'PARAMS_LOGIC_JS_OK' in r.stdout, r.stdout + r.stderr


def test_params_page_is_discovered_and_follows_the_template(make_client):
    c, _ = make_client('open')
    assert 'params' in [p['id'] for p in c.get('/api/pages').json()['pages']]
    page = (WEB / 'pages' / 'params.js').read_text(encoding='utf-8')
    assert "id: 'params'" in page and page.index("'/pot_logic.js'") < page.index("'/params_form.js'")
    assert "styles: ['/pages/params.css']" in page
    for name in ('/params_form.js', '/pot_logic.js', '/pages/params.css'):
        assert c.get(name).status_code == 200


def test_page_files_are_csp_clean_and_under_k12_limit():
    import re
    bad = re.compile(r'\.innerHTML|\.outerHTML|insertAdjacentHTML|\beval\(|new Function|document\.write'
                     r'|\bon[a-z]+\s*=\s*["\']|https?://(?!www\.w3\.org/2000/svg)')
    for name in PAGE_FILES:
        text = (WEB / name).read_text(encoding='utf-8')
        assert not bad.search(text), f'{name}: CSP/innerHTML kuralı'
        assert len(text.splitlines()) < 400, f'{name}: K12 (< 400 satır)'


def test_server_side_files_are_under_k12_limit():
    root = Path(__file__).resolve().parents[1] / 'control_hub'
    for rel in ('hub_core/profile_fields.py', 'hub_core/profile_rules.py', 'hub_core/profile_view.py',
                'server/params_routes.py'):
        assert len((root / rel).read_text(encoding='utf-8').splitlines()) < 400, rel


def serve(hub):
    import socket
    import threading
    import time

    import uvicorn

    from control_hub.server import create_app
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(hub), host='127.0.0.1', port=port, log_level='warning'))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _i in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    return srv, th, f'http://127.0.0.1:{port}'


def test_browser_flow_errors_on_field_diff_save_and_download(make_client, data_dir):
    """Gerçek tarayıcıda (YEREL; Playwright + Chrome yoksa atlanır, CI'de koşmaz): saçma değer alanda hata
    verir ve kaydı kapatır, geçerli değişiklik diff'e düşer, kaydet + indir çalışır, taslak yeniden yüklenir,
    telefonda yatay taşma yok, depo dosyaları değişmez."""
    import time
    pw = pytest.importorskip('playwright.sync_api')
    _, hub = make_client('open')
    srv, th, url = serve(hub)
    before = repo_hashes()
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch(executable_path=os.environ.get('HUB_CHROME'), headless=True,
                                            args=['--no-sandbox'])
            except Exception as exc:                                  # noqa: BLE001
                pytest.skip(f'tarayıcı açılamadı: {exc}')
            ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, accept_downloads=True)
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(url + '/#/params')
            page.wait_for_selector('#page-params .pr-card', timeout=15000)

            def box(key):
                return page.locator(f'#page-params input[data-key="{key}"]')

            def wait(selector):                   # CSP açık kalsın: eval gerektirmeyen seçici beklemesi
                page.wait_for_selector(selector, timeout=5000)
            save = page.get_by_role('button', name='Taslak kaydet')
            box('axes.joint_2.min_deg').fill('100')
            box('axes.joint_2.max_deg').fill('50')
            wait('#page-params input[data-key="axes.joint_2.min_deg"].bad')
            assert save.is_disabled()
            box('axes.joint_2.min_deg').fill('2.865')
            box('axes.joint_2.max_deg').fill('150')
            box('safety.x_max_mm').fill('230')
            wait(f'{SAVE}:enabled')
            assert 'değişiklik' in page.inner_text('#page-params')
            box('tcp.z_mm').fill('900')                          # home kutu dışına çıkar -> kayıt kapanır
            wait('#page-params .say.bad')                        # doğrulama yanıtı geldi: home kutu dışında
            assert 'DIŞINDA' in page.inner_text('#page-params') and page.locator(SAVE).is_disabled()
            box('tcp.z_mm').fill('0')
            wait(f'{SAVE}:enabled')
            save.click()
            draft = data_dir / 'profiles' / 'karma_robot' / 'draft.yaml'
            for _i in range(60):
                if draft.is_file():
                    break
                time.sleep(0.1)
            assert draft.is_file()
            with page.expect_download() as dl:
                page.get_by_role('button', name='YAML indir').click()
            assert dl.value.suggested_filename == 'karma_robot-taslak.yaml'
            page.reload()
            page.wait_for_selector('#page-params .pr-card', timeout=15000)
            for _i in range(50):
                if box('safety.x_max_mm').input_value() == '230':
                    break
                time.sleep(0.1)
            assert box('safety.x_max_mm').input_value() == '230'
            phone = ctx.new_page()
            phone.set_viewport_size({'width': 390, 'height': 844})
            phone.goto(url + '/#/params')
            phone.wait_for_selector('#page-params .pr-card', timeout=15000)
            assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert errors == []
            browser.close()
    finally:
        srv.should_exit = True
        th.join(5)
    assert repo_hashes() == before
