"""P2 Program sayfası uçları: UI_CONTRACT §5 şablonu, güvenlik değişmezleri, uçtan uca (SAHTE arka uç)."""
import json
import time

import pytest

from control_hub.server import registry

J0 = {'kind': 'move_j', 'joints_deg': [0, 10, 20, 0, 0, 0]}
L0 = {'kind': 'move_l', 'pos_m': [0.3, 0.0, 0.4], 'rpy_deg': [0, 0, 0]}


def prog(steps=None, **kw):
    doc = {'version': 1, 'name': 'Deneme', 'waypoints': [
        {'name': 'A', 'joints_deg': [0, 10, 20, 0, 0, 0], 'pos_m': [0.3, 0.0, 0.4], 'rpy_deg': [0, 0, 0]}],
        'steps': steps if steps is not None else [J0, L0]}
    doc.update(kw)
    return doc


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Testler gerçek kullanıcı klasörüne ASLA yazmaz."""
    monkeypatch.setenv('HUB_DATA_DIR', str(tmp_path / 'data'))
    return tmp_path / 'data'


@pytest.fixture
def client(make_client):
    c, hub = make_client('open')
    yield c, hub
    c.app.state.program_runner.cancel('test sonu')


def wait_for(c, pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = c.get('/api/program/state').json()['runner']
        if pred(st):
            return st
        time.sleep(0.02)
    raise AssertionError(f'zaman aşımı: {c.get("/api/program/state").json()["runner"]}')


def beat(c):
    c.post('/api/program/heartbeat', json={})


# --- UI_CONTRACT §5 şablonu: rota otomatik kurulur, oturumsuz görünmez ---------------------------------
def test_program_routes_are_auto_installed_and_hidden_without_login(make_client):
    assert 'program_routes' in registry.route_module_names()
    c, _ = make_client('open')
    assert c.get('/api/program').json() == {'ok': True, 'programs': []}
    pin, _ = make_client('pin')
    for method, url in (('GET', '/api/program'), ('GET', '/api/program/state'), ('GET', '/api/program/x'),
                        ('POST', '/api/program/run'), ('POST', '/api/program/save')):
        assert pin.request(method, url, json={} if method == 'POST' else None).status_code == 401, url


def test_locked_mode_is_read_only(make_client):
    c, _ = make_client('locked')
    assert c.get('/api/program').status_code == 200
    for url in ('save', 'run', 'check', 'goto', 'check_move', 'import', 'stop', 'pause', 'resume', 'step',
                'heartbeat'):
        assert c.post(f'/api/program/{url}', json={}).status_code == 403, url
    assert c.request('DELETE', '/api/program/x', json={}).status_code == 403


def test_post_needs_json_content_type_like_every_api_post(client):
    c, _ = client
    assert c.post('/api/program/save', content='{}').status_code == 415


# --- kayıt: kaydet / aç / sil / dışa-içe aktar -----------------------------------------------------
def test_save_list_open_export_import_delete(client, data_dir):
    c, _ = client
    r = c.post('/api/program/save', json={'program': prog()})
    assert r.status_code == 200 and r.json()['slug'] == 'deneme'
    assert (data_dir / 'programs' / 'deneme.json').is_file()
    assert [p['slug'] for p in c.get('/api/program').json()['programs']] == ['deneme']
    assert c.get('/api/program/deneme').json()['program']['steps'][1]['kind'] == 'move_l'
    ex = c.get('/api/program/deneme/export')
    assert ex.status_code == 200 and 'attachment; filename="deneme.json"' == ex.headers['content-disposition']
    imp = c.post('/api/program/import', json={'text': ex.text}).json()
    assert imp['ok'] and imp['program'] == c.get('/api/program/deneme').json()['program']
    assert c.request('DELETE', '/api/program/deneme', json={}).json() == {'ok': True}
    assert c.get('/api/program/deneme').status_code == 404
    assert c.request('DELETE', '/api/program/deneme', json={}).status_code == 404


def test_save_rejects_invalid_programs_with_paths(client, data_dir):
    c, _ = client
    r = c.post('/api/program/save', json={'program': prog([{'kind': 'wait', 's': -3}, {'kind': 'tool'}])})
    body = r.json()
    assert r.status_code == 422 and body['error'] == 'invalid_program' and not body['ok']
    assert {e['path'] for e in body['errors']} == {'steps[0].s', 'steps[1].open_pct'}
    assert not (data_dir / 'programs').exists()                  # geçersiz program diske hiç yazılmadı
    assert c.post('/api/program/save', json={}).status_code == 422
    assert c.post('/api/program/save', content='{bad', headers={'content-type': 'application/json'}
                  ).json()['error'] == 'bad_request'
    nan = '{"program": {"version":1,"name":"n","steps":[{"kind":"wait","s":NaN}]}}'
    assert c.post('/api/program/save', content=nan, headers={'content-type': 'application/json'}
                  ).status_code == 400


def test_import_rejects_bad_files_and_does_not_write(client, data_dir):
    c, _ = client
    for body, code in (({'text': '{'}, 'bad_json'), ({'text': '[]'}, 'invalid_program'),
                       ({'text': 'x' * 1_000_001}, 'too_large'), ({}, 'bad_request'),
                       ({'text': 5}, 'bad_request')):
        r = c.post('/api/program/import', json=body)
        assert r.status_code in (400, 413, 422) and r.json()['error'] == code, body
    assert not (data_dir / 'programs').exists()


def test_program_names_never_become_paths(client, data_dir):
    c, _ = client
    assert c.get('/api/program/Bad_Slug').json()['error'] == 'bad_name'
    assert c.get('/api/program/..%2Fetc').status_code in (400, 404)
    assert c.request('DELETE', '/api/program/..', json={}).status_code in (400, 404, 405)
    r = c.post('/api/program/save', json={'program': prog(name='../../etc/passwd')})
    assert r.status_code == 422                       # ad doğrulaması yolu daha kaydetmeden reddeder
    c.post('/api/program/save', json={'program': prog(name='Çıkış Pozu 2')})
    assert [p.name for p in (data_dir / 'programs').iterdir()] == ['cikis-pozu-2.json']


# --- kuru çalıştırma -------------------------------------------------------------------------------
def test_check_reports_violations_row_by_row_without_moving(client):
    c, _ = client
    before = c.get('/api/program/state').json()['pose']
    steps = [J0, {'kind': 'move_j', 'joints_deg': [0, 200, 0, 0, 0, 0]},
             {'kind': 'move_l', 'pos_m': [2, 0, 0.4], 'rpy_deg': [0, 0, 0]},
             {'kind': 'move_l', 'pos_m': [0.1, 0.0, 0.05], 'rpy_deg': [0, 0, 0]},
             {'kind': 'move_j', 'waypoint': 'Silinmiş'}]
    res = c.post('/api/program/check', json={'program': prog(steps), 'speed_pct': 30}).json()
    kinds = {r['line']: [v['kind'] for v in r['violations']] for r in res['rows']}
    assert not res['ok'] and res['violations'] == 5
    assert kinds == {0: [], 1: ['joint_limit'], 2: ['outside_safe_zone', 'unreachable'],
                     3: ['path_blocked'], 4: ['missing_waypoint']}
    assert next(v for r in res['rows'] if r['line'] == 3 for v in r['violations'])['at_pct'] is not None
    assert c.get('/api/program/state').json()['pose'] == before                         # robot oynamadı
    assert c.post('/api/program/check', json={'program': prog(), 'speed_pct': 0}).status_code == 400


def test_check_move_and_goto_use_the_same_validation(client):
    c, _ = client
    ok = c.post('/api/program/check_move', json={'step': L0, 'speed_pct': 50}).json()
    assert ok['ok'] and ok['violations'] == []
    far = {'kind': 'move_l', 'pos_m': [0.9, 0.9, 0.1], 'rpy_deg': [0, 0, 0]}
    assert not c.post('/api/program/check_move', json={'step': far}).json()['ok']
    for bad in ({'step': {'kind': 'wait', 's': 1}}, {'step': {'kind': 'move_j', 'waypoint': 'A'}}, {}):
        assert c.post('/api/program/check_move', json=bad).status_code in (400, 422), bad
    nan = '{"step": {"kind": "move_j", "joints_deg": [NaN]}}'
    assert c.post('/api/program/check_move', content=nan,
                  headers={'content-type': 'application/json'}).status_code == 400
    r = c.post('/api/program/goto', json={'step': {'kind': 'move_j', 'joints_deg': [0, 999, 0, 0, 0, 0]}})
    assert r.status_code == 422                                  # aralık dışı: doğrulayıcı reddeder
    r = c.post('/api/program/goto', json={'step': {'kind': 'move_j', 'joints_deg': [0, 175, 0, 0, 0, 0]}})
    assert r.status_code == 409 and r.json()['error'] == 'joint_limit'        # arka uç limiti
    r = c.post('/api/program/goto', json={'step': {'kind': 'move_j', 'joints_deg': [0, 30, 0, 0, 0, 0]},
                                          'speed_pct': 100})
    assert r.status_code == 202 and r.json()['job_id']
    end = time.monotonic() + 3
    while time.monotonic() < end and c.get('/api/program/state').json()['pose']['joints_deg'][1] != 30:
        time.sleep(0.02)
    assert c.get('/api/program/state').json()['pose']['joints_deg'][1] == 30


# --- çalıştırma ------------------------------------------------------------------------------------
def test_run_to_the_end_with_the_fake_backend(client):
    c, _ = client
    assert c.get('/api/program/state').json()['backend'] == {'label': 'SAHTE (mock)', 'measured': False,
                                                                 'mock': True}
    steps = [J0, {'kind': 'wait', 's': 0.01}, L0, {'kind': 'tool', 'open_pct': 50}]
    r = c.post('/api/program/run', json={'program': prog(steps), 'speed_pct': 80})
    assert r.status_code == 202 and r.json()['runner']['state'] in ('running', 'finished')
    st = wait_for(c, lambda s: s['state'] == 'finished')
    assert st['done'] == 4 and not st['running']
    pose = c.get('/api/program/state').json()['pose']
    assert pose['pos_m'] == [0.3, 0.0, 0.4] and pose['open_pct'] == 50 and pose['mock'] is True


def test_run_refuses_what_the_dry_run_refuses_and_other_bad_requests(client):
    c, _ = client
    bad = prog([J0, {'kind': 'move_j', 'joints_deg': [0, 200, 0, 0, 0, 0]}])
    r = c.post('/api/program/run', json={'program': bad})
    assert r.status_code == 409 and r.json()['error'] == 'program_invalid'
    assert r.json()['check']['rows'][1]['violations'][0]['kind'] == 'joint_limit'
    assert c.get('/api/program/state').json()['runner']['state'] == 'idle'      # hiçbir şey başlamadı
    assert c.post('/api/program/run', json={'program': prog([])}).json()['error'] == 'empty_program'
    only_comment = prog([{'kind': 'comment', 'text': 'x'}])
    assert c.post('/api/program/run', json={'program': only_comment}).json()['error'] == 'empty_program'
    assert c.post('/api/program/run', json={'program': prog(), 'mode': 'fly'}).status_code == 400
    assert c.post('/api/program/run', json={'program': prog(), 'speed_pct': 101}).status_code == 400
    assert c.post('/api/program/run', json={'program': prog([{'kind': 'fly'}])}).status_code == 422


def test_second_run_is_busy_and_pause_resume_stop(client):
    c, _ = client
    long = prog([{'kind': 'loop', 'count': 1000, 'steps': [{'kind': 'tool', 'open_pct': 10},
                                                           {'kind': 'tool', 'open_pct': 90}]}])
    assert c.post('/api/program/run', json={'program': long, 'speed_pct': 100}).status_code == 202
    r = c.post('/api/program/run', json={'program': long})
    assert r.status_code == 409 and r.json()['error'] == 'busy'
    assert c.post('/api/program/goto', json={'step': J0}).json()['error'] == 'busy'
    wait_for(c, lambda s: s['done'] >= 2)
    assert c.post('/api/program/pause', json={}).json()['runner']['state'] == 'paused'
    assert c.post('/api/program/pause', json={}).status_code == 200            # tekrar duraklatmak zararsız
    n = c.get('/api/program/state').json()['runner']['done']
    time.sleep(0.3)
    assert c.get('/api/program/state').json()['runner']['done'] <= n + 1       # duraklayınca ilerlemez
    assert c.post('/api/program/resume', json={}).json()['runner']['state'] == 'running'
    assert c.post('/api/program/resume', json={}).json()['error'] == 'not_paused'
    r = c.post('/api/program/stop', json={}).json()
    assert r['cancelled'] and r['runner']['state'] == 'cancelled'
    assert c.post('/api/program/stop', json={}).json()['cancelled'] is False


def test_step_mode_over_http(client):
    c, _ = client
    r = c.post('/api/program/run', json={'program': prog([J0, L0]), 'mode': 'step', 'speed_pct': 100})
    assert r.json()['runner']['state'] == 'paused'
    for n in (1, 2):
        assert c.post('/api/program/step', json={}).status_code == 200
        wait_for(c, lambda s, n=n: s['done'] >= n and s['state'] in ('paused', 'finished'))
        beat(c)
    assert c.get('/api/program/state').json()['runner']['state'] == 'finished'


def test_browser_silence_pauses_the_program(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(c.app.state.program_runner, '_hb_timeout', 0.25)
    long = prog([{'kind': 'loop', 'count': 1000, 'steps': [{'kind': 'tool', 'open_pct': 10},
                                                           {'kind': 'tool', 'open_pct': 90}]}])
    c.post('/api/program/run', json={'program': long, 'speed_pct': 100})
    st = wait_for(c, lambda s: s['state'] == 'paused')              # kalp atışı yok: kendiliğinden duraklar
    assert 'bağlantısı' in st['pause_reason']
    assert c.post('/api/program/resume', json={}).json()['runner']['state'] == 'running'


# --- ACİL DUR (K3) ---------------------------------------------------------------------------------
def test_estop_cancels_a_running_program_and_blocks_every_motion_path(client):
    c, hub = client
    long = prog([{'kind': 'loop', 'count': 1000, 'steps': [{'kind': 'tool', 'open_pct': 10},
                                                           {'kind': 'tool', 'open_pct': 90}]}])
    c.post('/api/program/run', json={'program': long, 'speed_pct': 100})
    wait_for(c, lambda s: s['done'] >= 1)
    assert c.post('/api/estop').status_code == 200
    st = wait_for(c, lambda s: s['state'] == 'cancelled')
    assert st['estop'] is True and 'ACİL DUR' in st['say']
    for url, body in (('run', {'program': prog()}), ('goto', {'step': J0}), ('step', {}), ('resume', {})):
        r = c.post(f'/api/program/{url}', json=body)
        assert r.status_code == 423 and r.json()['error'] == 'estop_latched', url
    assert c.post('/api/program/check', json={'program': prog()}).status_code == 200   # hareket değildir
    assert c.post('/api/estop/reset', json={}).status_code == 200
    time.sleep(0.4)
    assert c.get('/api/program/state').json()['runner']['state'] == 'cancelled'   # Reset sonrası devam YOK
    assert not hub.estopped


def test_real_mode_needs_the_confirmation_flag(client, monkeypatch):
    c, hub = client
    snap = hub.backend.snapshot()
    monkeypatch.setattr(hub.backend, 'snapshot', lambda: {**snap, 'mode': 'real'})
    r = c.post('/api/program/run', json={'program': prog()})
    assert r.status_code == 409 and r.json()['error'] == 'confirm_required'
    assert c.get('/api/program/state').json()['runner']['state'] == 'idle'
    assert c.post('/api/program/run', json={'program': prog(), 'confirm_real': True}).status_code == 202


def test_events_carry_no_secrets_or_paths(client, data_dir):
    c, hub = client
    c.post('/api/program/save', json={'program': prog()})
    c.post('/api/program/run', json={'program': prog(), 'speed_pct': 100})
    wait_for(c, lambda s: s['state'] == 'finished')
    text = json.dumps([e.as_dict() for e in hub.events.since(0)], ensure_ascii=False)
    assert 'program_started' in text and 'program_finished' in text
    assert str(data_dir) not in text and hub.access.pin not in text
