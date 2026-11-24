"""P4 Günlük sayfası (sunucu): süzgeçler, sıralama, CSV ve SIZINTI KURALI (PIN/anahtar/yol çıktıda yok)."""
import csv
import io
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from control_hub.hub_core.events import LEVELS as HUB_LEVELS
from control_hub.server import log_routes
from control_hub.server.log_routes import MASK, PATH_MASK, redact, select, to_csv

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
PIN = '123456'
SECRETS = ('123456', 'SUPERSECRET123', 'abc123def456ghi', 'ferhat', 'Ferhat Demir', 'pw123',
           'hub_session=abcd', 'ANTHROPIC-KEY')


def test_levels_match_the_hub_event_log():
    assert log_routes.LEVELS == HUB_LEVELS


@pytest.mark.parametrize('text, leaked', [
    ('Giriş: pin=123456 başarılı', '123456'),
    ('PIN 123456 kabul', '123456'),
    ('Authorization: Bearer abc123def456ghi', 'abc123def456ghi'),
    ('anahtar sk-ant-api03-ANTHROPIC-KEY', 'ANTHROPIC-KEY'),
    ('api_key=SUPERSECRET123 ile çağrı', 'SUPERSECRET123'),
    ('profil /home/ferhat/proje/x.yaml okunamadı', 'ferhat'),
    (r'C:\Users\Ferhat Demir\AppData\Roaming\ControlHub\config.json yazılamadı', 'Ferhat Demir'),
    (r'\\NAS\paylasim\robot\log.txt açılamadı', 'paylasim'),
    ('dosya ~/proje/a.txt', 'proje'),
    ('url https://user:pw123@host/x', 'pw123'),
    ('cookie: hub_session=abcd', 'hub_session=abcd'),
    ('tok ' + 'a' * 40, 'a' * 40),
    ('PIN düz yazıldı: 123456.', '123456'),
])
def test_redact_masks_secrets(text, leaked):
    out = redact(text, [PIN])
    assert leaked not in out, out
    assert MASK in out or PATH_MASK in out


@pytest.mark.parametrize('text', ['Hatalı PIN: 127.0.0.1', 'POST /api/move 409', 'Senaryo bitti: 3 adım',
                                  'GET /api/state 200', 'Bağlandı: 192.168.1.20:8080'])
def test_redact_keeps_ordinary_text_readable(text):
    assert redact(text, [PIN]) == text


def test_redact_pin_inside_longer_number_is_not_cut():
    assert redact('adım 1234567', [PIN]) == 'adım 1234567'
    assert redact('sayaç 123456', [PIN]) == f'sayaç {MASK}'


def emit_samples(hub):
    ev = hub.events
    ev.emit('login', 'Giriş: pin=123456 başarılı')
    ev.emit('scenario', 'Senaryo başladı: demo')
    ev.emit('config', 'profil /home/ferhat/x.yaml okunamadı', 'warning', pin=PIN, path='/home/ferhat/x.yaml',
            scenario='demo')
    ev.emit('robot', 'Bağlantı koptu', 'error', token='tok-abc', call={'url': 'http://u:pw123@h/'})
    ev.emit('safety', '=cmd|calc', 'critical')
    return ev


def blob(resp):
    return resp.text


def test_log_endpoint_has_no_secret_anywhere(make_client):
    c, hub = make_client('open')
    emit_samples(hub)
    r = c.get('/api/log')
    assert r.status_code == 200 and r.json()['ok'] is True
    for secret in SECRETS:
        assert secret not in blob(r), secret
    assert PIN not in blob(c.get('/api/log.csv'))
    rows = r.json()['events']
    detail = next(x for x in rows if x['source'] == 'config')['detail']
    assert f'pin={MASK}' in detail and f'path={MASK}' in detail and 'scenario=demo' in detail


def test_pin_in_event_data_value_is_masked_even_when_key_is_innocent(make_client):
    c, hub = make_client('open')
    hub.events.emit('misc', 'x', 'info', note='kod 123456 gönderildi', hata='http://u:pw123@h/')
    text = blob(c.get('/api/log'))
    assert '123456' not in text and 'pw123' not in text


def test_search_runs_on_masked_text_and_cannot_reveal_secrets(make_client):
    c, hub = make_client('open')
    emit_samples(hub)
    for needle in ('123456', 'ferhat', 'SUPERSECRET', 'pw123', 'tok-abc'):
        r = c.get('/api/log', params={'q': needle}).json()
        assert r['shown'] == 0, needle
    assert c.get('/api/log', params={'q': MASK}).json()['shown'] >= 1


def test_newest_first_filters_and_limit(make_client):
    c, hub = make_client('open')
    emit_samples(hub)
    rows = c.get('/api/log').json()['events']
    assert [r['seq'] for r in rows] == sorted((r['seq'] for r in rows), reverse=True)
    assert rows[0]['source'] == 'safety'
    assert {r['level'] for r in c.get('/api/log', params={'level': 'error'}).json()['events']} \
        == {'error', 'critical'}
    only = c.get('/api/log', params={'source': 'scenario'}).json()
    assert [r['source'] for r in only['events']] == ['scenario'] and 'scenario' in only['sources']
    assert c.get('/api/log', params={'q': 'BAŞLADI'}).json()['shown'] == 1
    assert c.get('/api/log', params={'q': 'basladi'}).json()['shown'] == 1      # ASCII klavye de bulur
    assert c.get('/api/log', params={'limit': '2'}).json()['shown'] == 2
    assert c.get('/api/log', params={'limit': '0'}).json()['shown'] == 1
    assert c.get('/api/log', params={'limit': '99999'}).json()['shown'] == 5


def test_only_last_500_events_are_served(make_client):
    c, hub = make_client('open')
    for i in range(620):
        hub.events.emit('bulk', f'olay {i}')
    r = c.get('/api/log').json()
    assert r['shown'] == 500 and r['total'] == 500 and r['capacity'] == 500
    assert r['events'][0]['message'] == 'olay 619' and r['events'][-1]['message'] == 'olay 120'


def test_bad_parameters_are_400_with_a_message(make_client):
    c, _ = make_client('open')
    bad = c.get('/api/log', params={'level': 'nonsense'})
    assert bad.status_code == 400 and bad.json()['error'] == 'bad_level'
    bad = c.get('/api/log', params={'limit': 'abc'})
    assert bad.status_code == 400 and bad.json()['error'] == 'bad_limit'
    assert c.get('/api/log.csv', params={'level': 'x'}).status_code == 400


def test_csv_download_bom_header_and_formula_guard(make_client):
    c, hub = make_client('open')
    emit_samples(hub)
    r = c.get('/api/log.csv', params={'level': 'warning'})
    assert r.status_code == 200 and r.headers['content-type'].startswith('text/csv')
    assert r.headers['content-disposition'].startswith('attachment; filename="gunluk-')
    assert r.content.startswith('\ufeff'.encode('utf-8'))
    rows = list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'))))
    assert rows[0] == list(log_routes.CSV_HEAD) and len(rows) == 4          # başlık + warning/error/critical
    assert rows[1][3] == 'safety' and rows[1][4] == "'=cmd|calc"            # en yeni üstte, formül etkisiz
    assert not any(cell.startswith(('=', '+', '@')) for row in rows[1:] for cell in row)


def test_select_and_to_csv_are_pure():
    rows = [{'seq': i, 'ts': 0.0, 'time': 't', 'level': lv, 'source': 's', 'message': m, 'detail': ''}
            for i, (lv, m) in enumerate((('info', 'a'), ('error', 'b'), ('warning', 'c')), 1)]
    assert [r['seq'] for r in select(rows, 'warning')] == [3, 2]
    assert select(rows, 'info', q='B')[0]['seq'] == 2 and select(rows, source='yok') == []
    assert to_csv([]).count('\r\n') == 1 and to_csv([]).startswith('\ufeff')


def test_pin_mode_requires_login_and_then_serves_a_redacted_log(make_client):
    c, hub = make_client('pin')
    emit_samples(hub)
    for path in ('/api/log', '/api/log.csv'):
        r = c.get(path)
        assert r.status_code == 401 and r.json()['error'] == 'login_required'
    assert c.post('/api/login', json={'pin': PIN}).status_code == 200
    r = c.get('/api/log')
    assert r.status_code == 200 and PIN not in r.text and 'pin=' in r.text


def test_locked_mode_still_allows_viewing_the_log(make_client):
    c, hub = make_client('locked')
    emit_samples(hub)
    assert c.get('/api/log').status_code == 200
    assert c.get('/api/log.csv').status_code == 200


# ----------------------------------------------------------------------------------------------- ön yüz

def test_log_logic_and_page_in_node():
    if shutil.which('node') is None:
        pytest.skip('node yok')
    script = Path(__file__).parent / 'js' / 'log_logic_check.js'
    r = subprocess.run(['node', str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'LOG_LOGIC_JS_OK' in r.stdout, r.stdout + r.stderr


def test_log_page_is_discovered_and_files_are_served(make_client):
    c, _ = make_client('open')
    pages = {p['id']: p for p in c.get('/api/pages').json()['pages']}
    assert 'log' in pages
    assert c.get('/pages/log.js').status_code == 200 and c.get('/pages/log.css').status_code == 200


def test_log_page_files_are_csp_clean_and_under_k12_limit():
    bad = re.compile(r'\.innerHTML|\.outerHTML|insertAdjacentHTML|\beval\(|new Function|document\.write'
                     r'|\bon[a-z]+\s*=\s*["\']|https?://')
    for name in ('pages/log.js', 'pages/log.css'):
        text = (WEB / name).read_text(encoding='utf-8')
        assert not bad.search(text), f'{name}: CSP/innerHTML kuralı'
        assert len(text.splitlines()) < 400, f'{name}: K12 (< 400 satır)'
    assert len((Path(log_routes.__file__)).read_text(encoding='utf-8').splitlines()) < 400


def test_log_page_never_renders_raw_websocket_events():
    code = re.sub(r'/\*.*?\*/', '', (WEB / 'pages' / 'log.js').read_text(encoding='utf-8'), flags=re.S)
    assert 'S.events' not in code and '/api/events' not in code, 'ham olaylar ekrana yazılmamalı'
    assert "api(LogLogic.url(L.f))" in code


def serve(hub):
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


def test_browser_flow_filters_live_update_csv_and_no_leak(make_client, tmp_path):
    """Gerçek tarayıcıda (YEREL; Playwright + Chrome yoksa atlanır, CI'de koşmaz): satırlar yeni üstte ve
    maskeli, süzgeç/arama çalışır, canlı olay gelince liste yenilenir (ham PIN ekrana HİÇ düşmez), CSV iner,
    telefonda yatay taşma yok."""
    pw = pytest.importorskip('playwright.sync_api')
    _, hub = make_client('open')
    emit_samples(hub)
    srv, th, url = serve(hub)
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
            page.goto(url + '/#/log')
            page.wait_for_selector('#page-log tr.lg-row', timeout=15000)
            rows = page.locator('#page-log tr.lg-row')
            assert rows.count() == 5 and 'safety' in rows.first.inner_text()      # en yeni üstte
            text = page.inner_text('#page-log')
            for secret in SECRETS:
                assert secret not in text, secret
            assert '5 / 5 olay' in text

            page.select_option('#page-log select[data-role=level]', 'error')
            page.wait_for_selector('#page-log .lg-count:has-text("2 / 5")', timeout=5000)
            page.select_option('#page-log select[data-role=level]', 'info')
            page.wait_for_selector('#page-log .lg-count:has-text("5 / 5")', timeout=5000)
            page.fill('#page-log input[data-role=q]', 'basladi')
            page.wait_for_selector('#page-log .lg-count:has-text("1 / 5")', timeout=5000)
            page.fill('#page-log input[data-role=q]', '123456')
            page.wait_for_selector('#page-log .lg-count:has-text("0 / 5")', timeout=5000)
            assert page.locator('#page-log .lg-empty').is_visible()
            page.fill('#page-log input[data-role=q]', '')
            page.wait_for_selector('#page-log .lg-count:has-text("5 / 5")', timeout=5000)

            hub.events.emit('canli', 'yeni canlı olay pin=123456 /home/ferhat/x', 'warning', pin=PIN)
            page.wait_for_selector('#page-log td.lg-msg:has-text("yeni canlı olay")', timeout=10000)
            assert 'canli' in page.locator('#page-log tr.lg-row').first.inner_text()
            assert '123456' not in page.inner_text('body') and 'ferhat' not in page.inner_text('body')

            page.select_option('#page-log select[data-role=level]', 'warning')
            page.wait_for_selector('#page-log .lg-count:has-text("4 / 6")', timeout=5000)
            with page.expect_download() as dl:
                page.click('#page-log a[data-role=csv]')
            assert dl.value.suggested_filename.startswith('gunluk-')
            path = tmp_path / 'g.csv'
            dl.value.save_as(str(path))
            raw = path.read_bytes()
            assert raw.startswith(b'\xef\xbb\xbf') and PIN.encode() not in raw and b'ferhat' not in raw

            phone = ctx.new_page()
            phone.set_viewport_size({'width': 390, 'height': 844})
            phone.goto(url + '/#/log')
            phone.wait_for_selector('#page-log tr.lg-row', timeout=15000)
            assert phone.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert errors == []
            browser.close()
    finally:
        srv.should_exit = True
        th.join(5)
