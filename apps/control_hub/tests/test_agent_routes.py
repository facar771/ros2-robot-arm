import json
import logging

import pytest
from fastapi.testclient import TestClient
from robot_agent.llm import LLMResponse, ScriptedLLM, call

from control_hub.agent import AgentConfig, AgentService, KeyStore
from control_hub.hub_core import AccessControl, FakeBackend, Hub
from control_hub.server import create_app
from tests.helpers_agent import CANARY, MemoryKeyring, Provider, text_reply, tool_reply, wait_for

LOCAL, PHONE = ('127.0.0.1', 50000), ('192.168.1.23', 50000)


@pytest.fixture
def env(scenarios, ui):
    made = []

    def build(mode='pin', steps=(), cfg=None, keyring=None, factory=True):
        hub = Hub(FakeBackend(time_scale=0.02), scenarios, ui, AccessControl(mode, '123456'))
        kr = keyring or MemoryKeyring()
        svc = AgentService(hub, cfg or AgentConfig(), KeyStore(kr),
                           llm_factory=(lambda c, k: ScriptedLLM(*steps)) if factory else None)
        hub.agent = svc
        app = create_app(hub)
        made.append(hub)
        return hub, svc, app, kr

    def client(app, peer=LOCAL, base='http://localhost', login=True):
        c = TestClient(app, base_url=base, client=peer)
        if login:
            assert c.post('/api/login', json={'pin': '123456'}).status_code == 200
        return c

    build.client = client
    yield build
    for h in made:
        h.close()


def csrf(c):
    return {'X-CSRF-Token': c.get('/api/agent/status').json()['csrf']}


def test_status_needs_a_session_and_hides_everything_secret(env):
    hub, svc, app, _ = env()
    anon = env.client(app, login=False)
    assert anon.get('/api/agent/status').status_code == 401
    c = env.client(app)
    st = c.get('/api/agent/status').json()
    assert st['enabled'] and st['can_configure'] is True and st['csrf']
    assert st['keys']['openai'] == {'set': False, 'remembered': False}
    assert 'key' not in {k for k in st if k.endswith('key')}      # yalnızca 'keys' bayrakları


def test_every_post_needs_the_csrf_token_of_its_own_session(env):
    hub, svc, app, _ = env(steps=[LLMResponse('merhaba')])
    c = env.client(app)
    for path, body in (('/api/agent/message', {'text': 'selam'}), ('/api/agent/cancel', {}),
                       ('/api/agent/approvals/a1', {'approve': True}),
                       ('/api/agent/key', {'provider': 'openai', 'key': 'abcdefgh'}),
                       ('/api/agent/key/clear', {'provider': 'openai'}), ('/api/agent/config', {})):
        r = c.post(path, json=body)
        assert r.status_code == 403 and r.json()['error'] == 'csrf', path
        r = c.post(path, json=body, headers={'X-CSRF-Token': 'x' * 64})
        assert r.status_code == 403, path
    other = env.client(app)                                      # başka oturumun belirteci geçmez
    assert c.post('/api/agent/message', json={'text': 'selam'}, headers=csrf(other)).status_code == 403
    assert c.post('/api/agent/message', json={'text': 'selam'}, headers=csrf(c)).status_code == 202


def test_foreign_origin_and_wrong_content_type_are_still_blocked(env):
    hub, svc, app, _ = env()
    c = env.client(app)
    h = {**csrf(c), 'Origin': 'http://evil.example'}
    assert c.post('/api/agent/message', json={'text': 'x'}, headers=h).status_code == 403
    r = c.post('/api/agent/message', content='text=x', headers={
        **csrf(c), 'Content-Type': 'application/x-www-form-urlencoded'})
    assert r.status_code == 415


def test_phone_can_chat_and_approve_but_cannot_touch_keys_or_endpoints(env):
    hub, svc, app, _ = env(steps=[LLMResponse('', [call('go_home')]), LLMResponse('bitti')])
    phone = env.client(app, PHONE, base='http://192.168.1.2:8765')
    st = phone.get('/api/agent/status').json()
    assert st['can_configure'] is False
    h = {'X-CSRF-Token': st['csrf']}
    for path, body in (('/api/agent/key', {'provider': 'openai', 'key': 'abcdefgh'}),
                       ('/api/agent/key/clear', {'provider': 'openai'}),
                       ('/api/agent/config', {'base_url': 'https://evil.example/v1'})):
        r = phone.post(path, json=body, headers=h)
        assert r.status_code == 403 and r.json()['error'] == 'local_only', path
    assert svc.keys.get('openai') is None and svc.config.base_url == ''
    assert phone.post('/api/agent/message', json={'text': 'home'}, headers=h).status_code == 202
    assert wait_for(lambda: svc.approvals.pending())
    pid = svc.approvals.pending()[0]['id']
    assert phone.post(f'/api/agent/approvals/{pid}', json={'approve': True}, headers=h).status_code == 200
    assert wait_for(lambda: not svc.active)
    assert 'home' in hub.backend.history


@pytest.mark.parametrize('peer,base,extra', [
    (LOCAL, 'http://192.168.1.2:8765', {}),                       # Host loopback değil (DNS rebinding)
    (LOCAL, 'http://localhost', {'X-Forwarded-For': '203.0.113.9'}),     # vekil arkası
    (('172.17.0.1', 4000), 'http://localhost', {}),               # Docker kapısı: yerel sayılmaz
])
def test_key_entry_fails_closed_unless_truly_local(env, peer, base, extra):
    hub, svc, app, _ = env()
    c = env.client(app, peer, base=base)
    r = c.post('/api/agent/key', json={'provider': 'openai', 'key': CANARY},
               headers={**csrf(c), **extra})
    assert r.status_code == 403 and r.json()['error'] == 'local_only'
    assert svc.keys.get('openai') is None


def test_locked_mode_blocks_writes_and_open_mode_works_without_login(env):
    hub, svc, app, _ = env(mode='locked')
    c = env.client(app, login=False)
    assert c.post('/api/agent/message', json={'text': 'x'}, headers=csrf(c)).status_code == 403
    hub2, svc2, app2, _ = env(mode='open', steps=[LLMResponse('merhaba')])
    c2 = env.client(app2, login=False)
    assert c2.post('/api/agent/message', json={'text': 'x'}, headers=csrf(c2)).status_code == 202


def test_status_when_agent_is_off_or_missing_just_says_disabled(env):
    hub, svc, app, _ = env()
    hub.agent = None
    c = env.client(app)
    st = c.get('/api/agent/status').json()
    assert st['enabled'] is False and st['can_configure'] is False
    assert c.post('/api/agent/message', json={'text': 'x'}, headers={'X-CSRF-Token': st['csrf']}
                  ).status_code == 503


def test_estop_works_without_session_while_the_agent_waits_and_clears_approvals(env):
    hub, svc, app, _ = env(steps=[LLMResponse('', [call('go_home')]), LLMResponse('x')])
    c = env.client(app)
    assert c.post('/api/agent/message', json={'text': 'home'}, headers=csrf(c)).status_code == 202
    assert wait_for(lambda: svc.approvals.pending())
    anon = env.client(app, PHONE, base='http://192.168.1.2:8765', login=False)     # oturumsuz telefon
    assert anon.post('/api/estop', content='').json()['estop'] is True
    assert wait_for(lambda: not svc.active)
    assert svc.approvals.pending() == [] and hub.backend.history == []
    r = c.post('/api/agent/approvals/a1', json={'approve': True}, headers=csrf(c))
    assert r.status_code in (404, 423)
    assert c.get('/api/state').json()['estop']['latched'] is True


def test_cannot_approve_while_estopped(env):
    hub, svc, app, _ = env()
    c = env.client(app)
    hub.estop('test')
    r = c.post('/api/agent/approvals/a1', json={'approve': True}, headers=csrf(c))
    assert r.status_code == 423


def test_validation_errors_are_400_and_unknown_fields_rejected(env):
    hub, svc, app, _ = env()
    c = env.client(app)
    h = csrf(c)
    assert c.post('/api/agent/message', json={'text': 'x', 'extra': 1}, headers=h).status_code == 400
    assert c.post('/api/agent/config', json={'daily_limit': 0}, headers=h).json()['error'] == 'bad_config'
    assert c.post('/api/agent/key', json={'provider': 'gemini', 'key': 'abcdefgh'},
                  headers=h).json()['error'] == 'bad_key'
    assert c.post('/api/agent/config', json={'dry_run': True, 'max_motions': 1}, headers=h).status_code == 200
    assert svc.config.dry_run and svc.config.max_motions == 1


# ---- anahtar canary: tüm yığın boyunca, gerçek HTTP ile sahte sağlayıcıya ----------------------------
def surfaces(c, hub, extra=()):
    """Anahtarın sızabileceği her HTTP yüzeyi + Hub olay günlüğü + durum."""
    texts = [c.get(p).text + str(dict(c.get(p).headers)) for p in (
        '/api/agent/status', '/api/agent/chat', '/api/state', '/api/events', '/api/session', '/api/ui')]
    texts.append(json.dumps([e.as_dict() for e in hub.events.since(0, 1000)]))
    texts.append(json.dumps(hub.snapshot(), default=str))
    return '\n'.join(texts + list(extra))


def test_canary_never_appears_in_http_events_logs_or_state(env, caplog):
    caplog.set_level(logging.DEBUG)
    echo = lambda auth: (401, json.dumps({'error': {'message': f'Incorrect API key provided: {auth}'}}))  # noqa: E731
    provider = Provider(fail=echo)
    try:
        hub, svc, app, kr = env(factory=False, cfg=AgentConfig(provider='openai', model='m',
                                                                base_url=provider.base_url))
        c = env.client(app)
        h = csrf(c)
        collected = []
        r = c.post('/api/agent/key', json={'provider': 'openai', 'key': CANARY, 'remember': False}, headers=h)
        collected.append(r.text + str(dict(r.headers)))
        assert r.status_code == 200 and r.json()['remembered'] is False
        r = c.post('/api/agent/message', json={'text': f'selam {CANARY}'}, headers=h)
        collected.append(r.text)
        assert wait_for(lambda: not svc.active)
        assert wait_for(lambda: provider.requests)
        # anahtar yalnızca sağlayıcıya, yalnızca Authorization başlığında gitti
        path, auth, body = provider.requests[0]
        assert path == '/v1/chat/completions' and auth == f'Bearer {CANARY}' and CANARY not in body
        assert any('HTTP 401' in m['text'] for m in svc.chat())    # hata kullanıcıya gösterildi...
        r = c.post('/api/agent/config', json={'dry_run': True}, headers=h)
        collected.append(r.text)
        everything = surfaces(c, hub, collected) + caplog.text
        assert CANARY not in everything
        assert kr.data == {}                                       # hatırla işaretsiz: depoya yazılmadı
    finally:
        provider.close()


def test_remember_goes_to_the_credential_store_only_and_clear_removes_it(env, tmp_path):
    hub, svc, app, kr = env()
    c = env.client(app)
    h = csrf(c)
    r = c.post('/api/agent/key', json={'provider': 'openai', 'key': CANARY, 'remember': True}, headers=h)
    assert r.json()['remembered'] is True and CANARY not in r.text
    assert list(kr.data.values()) == [CANARY]                      # tek yer: kimlik deposu
    assert CANARY not in surfaces(c, hub)
    assert c.get('/api/agent/status').json()['keys']['openai'] == {'set': True, 'remembered': True}
    c.post('/api/agent/key/clear', json={'provider': 'openai', 'forget': True}, headers=h)
    assert kr.data == {} and c.get('/api/agent/status').json()['keys']['openai']['set'] is False


def test_provider_tool_loop_over_real_http_reaches_the_robot_only_after_approval(env):
    provider = Provider([tool_reply('go_home', {}), text_reply('eve gittim')])
    try:
        hub, svc, app, _ = env(factory=False, cfg=AgentConfig(provider='openai', model='m',
                                                              base_url=provider.base_url))
        svc.keys.set('openai', CANARY)
        c = env.client(app)
        assert c.post('/api/agent/message', json={'text': 'eve git'}, headers=csrf(c)).status_code == 202
        assert wait_for(lambda: svc.approvals.pending())
        assert hub.backend.history == []
        svc.approvals.decide(svc.approvals.pending()[0]['id'], True)
        assert wait_for(lambda: not svc.active)
        assert 'home' in hub.backend.history
        assert any(m['role'] == 'assistant' and 'eve gittim' in m['text'] for m in svc.chat())
        assert all(CANARY not in body for _, _, body in provider.requests)
        assert CANARY not in surfaces(c, hub)
    finally:
        provider.close()
