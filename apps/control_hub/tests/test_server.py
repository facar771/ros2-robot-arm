import time

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import J

WS = {'host': 'localhost'}      # TestClient WebSocket'te varsayılan 'testserver' gönderir


def wait_idle(hub, t=10):
    assert hub.runner.wait_idle(t)


def login(client, pin='123456'):
    r = client.post('/api/login', json={'pin': pin})
    assert r.status_code == 200, r.text
    return r


def test_health_and_static(make_client):
    c, _ = make_client('pin')
    assert c.get('/health').json()['ok'] is True
    page = c.get('/')
    assert page.status_code == 200 and 'Robot Kontrol Merkezi' in page.text
    assert c.get('/app.js').status_code == 200 and c.get('/style.css').status_code == 200
    assert c.get('/manifest.webmanifest').json()['display'] == 'standalone'
    assert 'frame-ancestors' in page.headers['content-security-policy']
    assert page.headers['x-content-type-options'] == 'nosniff'


def test_open_mode_runs_scenario_end_to_end(make_client):
    c, hub = make_client('open')
    assert c.get('/api/session').json()['can_control'] is True
    ui = c.get('/api/ui').json()
    assert 'demo_tour' in ui['scenarios'] and ui['layout']['sections']
    r = c.post('/api/scenarios/demo_tour/run', json={})
    assert r.status_code == 202
    wait_idle(hub)
    st = c.get('/api/state').json()
    assert st['runner']['last']['ok'] and st['robot']['connected'] and st['me']['can_control']
    assert 'pin' not in str(st).lower().replace('pinned', '')       # durumda PIN yok
    assert any(e['kind'] == 'scenario_finished' for e in c.get('/api/events').json()['events'])


def test_unknown_busy_and_validation_errors(make_client):
    c, hub = make_client('open', scale=1.0)
    assert c.post('/api/scenarios/yok/run', json={}).status_code == 404
    assert c.post('/api/scenarios/demo_tour/run', json={}).status_code == 202
    b = c.post('/api/scenarios/home/run', json={})
    assert b.status_code == 409 and b.json()['error'] == 'busy'
    assert c.post('/api/scenarios/cancel', json={}).json()['cancelled'] is True
    wait_idle(hub)
    assert c.post('/api/scenarios/home/run', content='{bad', headers=J).status_code == 400


def test_pin_mode_requires_login_and_cookie_flags(make_client):
    c, _ = make_client('pin')
    for path in ('/api/state', '/api/ui', '/api/events'):
        r = c.get(path)
        assert r.status_code == 401 and r.json()['error'] == 'login_required'
    assert c.post('/api/scenarios/home/run', json={}).status_code == 401
    assert c.get('/api/session').json()['authenticated'] is False
    r = login(c)
    cookie = r.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    assert c.get('/api/state').status_code == 200
    assert c.post('/api/scenarios/home/run', json={}).status_code == 202
    c.post('/api/logout', json={})
    assert c.get('/api/state').status_code == 401


def test_login_wrong_pin_and_rate_limit(make_client):
    c, hub = make_client('pin')
    r = c.post('/api/login', json={'pin': '000000'})
    assert r.status_code == 401 and r.json()['attempts_left'] == 4
    for _ in range(3):
        c.post('/api/login', json={'pin': '000000'})
    r = c.post('/api/login', json={'pin': '000000'})
    assert r.status_code == 429 and 'retry-after' in r.headers
    # kilitliyken doğru PIN de geçmez
    assert c.post('/api/login', json={'pin': '123456'}).status_code == 429
    assert c.post('/api/login', json={}).status_code == 400
    kinds = [e.kind for e in hub.events.since(0)]
    assert 'login_failed' in kinds and 'login_throttled' in kinds


def test_login_not_needed_outside_pin_mode(make_client):
    c, _ = make_client('open')
    assert c.post('/api/login', json={'pin': '123456'}).status_code == 400


def test_locked_mode_is_read_only_but_estop_works(make_client):
    c, hub = make_client('locked')
    s = c.get('/api/session').json()
    assert s['read_only'] and not s['can_control']
    assert c.get('/api/state').status_code == 200          # izleme serbest
    for path in ('/api/scenarios/home/run', '/api/scenarios/cancel', '/api/estop/reset'):
        r = c.post(path, json={})
        assert r.status_code == 403 and r.json()['error'] == 'locked', path
    assert not hub.backend.history
    assert c.post('/api/estop', json={}).status_code == 200 and hub.estopped
    assert c.post('/api/estop/reset', json={}).status_code == 403    # kilidi açmak yine kontrol


def test_estop_without_session_without_body_and_reset_needs_auth(make_client):
    c, hub = make_client('pin')
    r = c.post('/api/estop')                                # gövdesiz, içerik türsüz
    assert r.status_code == 200 and r.json()['interrupts_motion'] is True and hub.estopped
    assert c.post('/api/estop/reset', json={}).status_code == 401
    login(c)
    assert c.post('/api/scenarios/home/run', json={}).status_code == 423
    assert c.post('/api/estop/reset', json={}).status_code == 200
    assert c.post('/api/scenarios/home/run', json={}).status_code == 202


def test_estop_interrupts_running_scenario_via_api(make_client):
    c, hub = make_client('open', scale=1.0)
    c.post('/api/scenarios/demo_tour/run', json={})
    time.sleep(0.15)
    t0 = time.monotonic()
    c.post('/api/estop', json={})
    wait_idle(hub, 3)
    assert time.monotonic() - t0 < 1.0
    st = c.get('/api/state').json()
    assert st['estop']['latched'] and st['runner']['last']['cancelled']
    assert c.post('/api/estop/reset', json={}).status_code == 200


def test_csrf_and_host_protections(make_client):
    c, hub = make_client('open')
    # başka sitenin formu (ön uçuşsuz): içerik türü
    r = c.post('/api/scenarios/home/run', content='{}', headers={'content-type': 'text/plain'})
    assert r.status_code == 415
    # yabancı Origin / cross-site
    assert c.post('/api/scenarios/home/run', json={},
                  headers={'origin': 'http://evil.example'}).status_code == 403
    assert c.post('/api/scenarios/home/run', json={},
                  headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    assert c.post('/api/estop', headers={'origin': 'http://evil.example'}).status_code == 403
    # aynı kaynak Origin geçer
    assert c.post('/api/scenarios/home/run', json={},
                  headers={'origin': 'http://localhost'}).status_code == 202
    wait_idle(hub)
    assert not any(h for h in hub.backend.history if 'evil' in h)
    # DNS rebinding: Host bir alan adı
    for host in ('evil.example', 'evil.example:8765'):
        assert c.get('/api/state', headers={'host': host}).status_code == 403
        assert c.get('/', headers={'host': host}).status_code == 403
    # LAN IP, IPv6, .local, localhost:port geçer
    for host in ('192.168.1.20:8765', '[::1]:8765', 'robot.local', 'localhost:8765'):
        assert c.get('/health', headers={'host': host}).status_code == 200, host


def test_no_api_docs_exposed(make_client):
    c, _ = make_client('open')
    for p in ('/docs', '/redoc', '/openapi.json'):
        assert c.get(p).status_code == 404


def test_websocket_state_events_and_auth(make_client):
    c, hub = make_client('pin', scale=1.0)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect('/ws', headers=WS):
            pass
    assert e.value.code == 4401
    login(c)
    # WS istemcisi çerez kavanozunu kullanmaz: çerezi elle ver
    cookie = {'cookie': f"hub_session={c.cookies.get('hub_session')}"}
    with c.websocket_connect('/ws', headers={**WS, **cookie}) as ws:
        first = ws.receive_json()
        assert first['type'] == 'state' and first['state']['me']['can_control'] is True
        assert first['state']['runner']['running'] is False
        c.post('/api/scenarios/demo_tour/run', json={})
        seen_running = seen_finished = False
        for _ in range(80):                       # sınırlı: engelleyici receive asılı kalmasın
            m = ws.receive_json()
            if m['type'] == 'state' and m['state']['runner']['running']:
                if not seen_running:
                    seen_running = True
                    c.post('/api/scenarios/cancel', json={})
            if m['type'] == 'events' and any(e['kind'] == 'scenario_finished' for e in m['events']):
                seen_finished = True
                break
        assert seen_running and seen_finished
    wait_idle(hub)


def test_websocket_rejects_foreign_origin_and_host(make_client):
    c, _ = make_client('open')
    for hdr in ({'origin': 'http://evil.example'}, {'host': 'evil.example'}):
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect('/ws', headers={**WS, **hdr}):
                pass
        assert e.value.code == 4403
