"""Altın gövde testi (docs/MIMARI.md §7 R5): hata yanıtı BİÇİMİ (durum + ham JSON + Retry-After) sabit.
Hata üretimi ``server/errors.py``'de toplanınca hiçbir yanıt tek bayt değişmemeli. ``GOLDEN`` refaktörden önce
mevcut kodla üretilip dondurulmuştur; bilerek değişen gövde yalnız burada, gerekçesiyle güncellenir."""
import re

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, FakeBackend, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG

JSON = {'Content-Type': 'application/json'}
JOG_BODY = {'lease': 'goldenlease01', 'twist': [0.01, 0, 0, 0, 0, 0], 'ttl_ms': 250}
HUGE = b'{"x":"' + b'a' * (80 * 1024 * 1024) + b'"}'
BIG = b'{"x":"' + b'a' * (6 * 1024 * 1024) + b'"}'      # her rotanın gövde sınırının üstünde


def _hub(mode, backend):
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    return Hub(backend, scen, ui, AccessControl(mode, '123456'))


def _client(hub, host='127.0.0.1'):
    return TestClient(create_app(hub), base_url='http://localhost', client=(host, 50000))


@pytest.fixture(scope='module')
def envs():
    made = {
        'sim': _hub('open', SimBackend(clock=SimClock(), autostart=False)),
        'fake': _hub('open', FakeBackend()),
        'pin': _hub('pin', FakeBackend()),
        'locked': _hub('locked', FakeBackend()),
    }
    clients = {k: _client(h) for k, h in made.items()}
    clients['remote'] = _client(made['fake'], host='203.0.113.9')
    yield clients
    for h in made.values():
        h.close()


# (ad, istemci, yöntem, yol, gönderilecek kwargs)
CASES = [
    ('api_error_login_required', 'pin', 'POST', '/api/estop/reset', {'json': {}}),
    ('api_error_locked', 'locked', 'POST', '/api/estop/reset', {'json': {}}),
    ('validation_bad_request', 'fake', 'POST', '/api/login', {'json': {}}),
    ('move_bad_json', 'sim', 'POST', '/api/move', {'content': b'{x', 'headers': JSON}),
    ('move_too_large', 'sim', 'POST', '/api/move', {'content': BIG, 'headers': JSON}),
    ('sim_bad_json', 'sim', 'POST', '/api/sim/speed', {'content': b'{x', 'headers': JSON}),
    ('algo_bad_json', 'sim', 'POST', '/api/algo/generate', {'content': b'{x', 'headers': JSON}),
    ('jog_bad_json', 'sim', 'POST', '/api/jog', {'content': b'{x', 'headers': JSON}),
    ('jog_too_large', 'sim', 'POST', '/api/jog', {'content': BIG, 'headers': JSON}),
    ('jog_not_local', 'remote', 'POST', '/api/jog', {'json': JOG_BODY}),
    ('log_bad_level', 'fake', 'GET', '/api/log?level=zzz', {}),
    ('log_bad_limit', 'fake', 'GET', '/api/log?limit=abc', {}),
    ('params_bad_json', 'fake', 'POST', '/api/params/validate', {'content': b'{x', 'headers': JSON}),
    ('params_too_big', 'fake', 'POST', '/api/params/validate', {'content': BIG, 'headers': JSON}),
    ('program_bad_json', 'fake', 'POST', '/api/program/check', {'content': b'{x', 'headers': JSON}),
    ('program_not_object', 'fake', 'POST', '/api/program/check', {'content': b'[1]', 'headers': JSON}),
    ('program_too_large', 'fake', 'POST', '/api/program/check', {'content': BIG, 'headers': JSON}),
    ('parts_bad_json', 'fake', 'POST', '/api/parts', {'content': b'{x', 'headers': JSON}),
    ('parts_too_large', 'fake', 'POST', '/api/parts', {'content': HUGE, 'headers': JSON}),
    ('login_rate_limited', 'pin', 'POST', '/api/login', {'json': {'pin': '000000'}, 'repeat': 12}),
    ('bad_host', 'fake', 'GET', '/api/state', {'headers': {'host': 'evil.example'}}),
    ('cross_origin', 'fake', 'POST', '/api/estop/reset',
     {'json': {}, 'headers': {'origin': 'http://evil.example'}}),
]


def observe(clients, case):
    _, who, method, path, kw = case
    kw = dict(kw)
    for _ in range(kw.pop('repeat', 1)):
        r = clients[who].request(method, path, **kw)
    text = re.sub(r'"retry_after_s":[0-9.]+', '"retry_after_s":N', r.text)      # saat bağımlı sayı sabitlenir
    wait = r.headers.get('retry-after')
    return {'status': r.status_code, 'text': text, 'retry_after': None if wait is None else 'var'}


# (durum, ham gövde, Retry-After): refaktörden ÖNCE mevcut kodla üretildi
GOLDEN = {'api_error_login_required': (401,
                              '{"ok":false,"error":"login_required","message":"PIN ile giriş gerekli"}',
                              None),
 'api_error_locked': (403,
                      '{"ok":false,"error":"locked","message":"Arayüz salt okunur kipte: kontroller '
                      'kilitli"}',
                      None),
 'validation_bad_request': (400,
                            '{"ok":false,"error":"bad_request","message":"Geçersiz istek gövdesi"}',
                            None),
 'move_bad_json': (400, '{"ok":false,"error":"bad_request","message":"Geçersiz JSON"}', None),
 'move_too_large': (413, '{"ok":false,"error":"too_large","message":"Gövde çok büyük"}', None),
 'sim_bad_json': (400, '{"ok":false,"error":"bad_request","message":"Geçersiz JSON"}', None),
 'algo_bad_json': (400, '{"ok":false,"error":"bad_request","message":"Geçersiz JSON"}', None),
 'jog_bad_json': (400, '{"ok":false,"error":"bad_request","message":"Geçersiz JSON"}', None),
 'jog_too_large': (413, '{"ok":false,"error":"too_large","message":"Gövde çok büyük"}', None),
 'jog_not_local': (403,
                   '{"ok":false,"error":"hold_local_only","message":"Basılı-tut jog yalnızca bu '
                   'bilgisayardan yapılır; telefondan artımlı jog kullan","state":"hold_denied"}',
                   None),
 'log_bad_level': (400,
                   '{"ok":false,"error":"bad_level","message":"level şunlardan biri olmalı: info, warning, '
                   'error, critical"}',
                   None),
 'log_bad_limit': (400, '{"ok":false,"error":"bad_limit","message":"limit tam sayı olmalı (1–500)"}', None),
 'params_bad_json': (400,
                     '{"ok":false,"error":"bad_json","message":"geçerli bir JSON gövdesi gerekli"}',
                     None),
 'params_too_big': (413, '{"ok":false,"error":"too_big","message":"istek çok büyük"}', None),
 'program_bad_json': (400, '{"ok":false,"error":"bad_request","message":"Geçersiz JSON"}', None),
 'program_not_object': (400, '{"ok":false,"error":"bad_request","message":"Gövde nesne olmalı"}', None),
 'program_too_large': (413, '{"ok":false,"error":"too_large","message":"Gövde çok büyük"}', None),
 'parts_bad_json': (400, '{"ok":false,"error":"bad_request","message":"Geçersiz JSON"}', None),
 'parts_too_large': (413, '{"ok":false,"error":"too_large","message":"Gövde çok büyük"}', None),
 'login_rate_limited': (429,
                        '{"ok":false,"error":"rate_limited","message":"Çok fazla deneme; biraz '
                        'bekle","retry_after_s":N}',
                        'var'),
 'bad_host': (403, '{"ok":false,"error":"bad_host","message":"Host reddedildi"}', None),
 'cross_origin': (403,
                  '{"ok":false,"error":"cross_origin","message":"Başka kaynaktan istek reddedildi"}',
                  None)}


def test_error_bodies_are_byte_identical(envs):
    problems = []
    for case in CASES:
        got = observe(envs, case)
        want = GOLDEN[case[0]]
        if (got['status'], got['text'], got['retry_after']) != tuple(want):
            problems.append(f"{case[0]}: {got} != {want}")
    assert problems == []


def test_golden_covers_every_case():
    assert set(GOLDEN) == {c[0] for c in CASES}
