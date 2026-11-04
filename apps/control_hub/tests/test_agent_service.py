import sys

import pytest
from robot_agent.llm import LLMResponse, ScriptedLLM, call

from control_hub.agent import AgentConfig, AgentError, AgentService, KeyStore
from control_hub.hub_core import AccessControl, FakeBackend, Hub
from tests.helpers_agent import CANARY, MemoryKeyring, wait_for


@pytest.fixture
def hub(scenarios, ui):
    h = Hub(FakeBackend(time_scale=0.02), scenarios, ui, AccessControl('open', '123456'))
    yield h
    h.close()


def make(hub, *steps, **cfg):
    svc = AgentService(hub, AgentConfig(**cfg), KeyStore(MemoryKeyring()),
                       llm_factory=lambda c, k: ScriptedLLM(*steps))
    hub.agent = svc
    return svc


def done(svc, timeout=10):
    assert wait_for(lambda: not svc.active, timeout), 'tur bitmedi'


def decide_next(svc, approve):
    assert wait_for(lambda: svc.approvals.pending()), 'onay istenmedi'
    assert svc.approvals.decide(svc.approvals.pending()[0]['id'], approve)


def roles(svc):
    return [(m['role'], m['text']) for m in svc.chat()]


def test_provider_without_a_key_is_refused_before_anything_runs(hub):
    svc = AgentService(hub, AgentConfig(provider='openai', model='m', base_url='https://x.example/v1'),
                       KeyStore(MemoryKeyring()))
    with pytest.raises(AgentError) as exc:
        svc.send('home')
    assert exc.value.code == 'no_key' and not svc.active


def test_read_tools_need_no_approval_and_dry_run_never_moves(hub):
    svc = make(hub, LLMResponse('bakıyorum', [call('list_objects')]),
               LLMResponse('', [call('go_home', id='t2')]), LLMResponse('tamam'), dry_run=True)
    svc.send('home git')
    done(svc)
    assert hub.backend.history == ['objects']                    # home robota GİTMEDİ
    tool_lines = [t for r, t in roles(svc) if r == 'tool']
    assert any('list_objects' in t and 'ok' in t for t in tool_lines)
    assert any('go_home' in t and 'dry_run' in t for t in tool_lines)
    assert svc.approvals.pending() == []                          # kuru çalıştırmada onay istenmez


def test_motion_waits_for_approval_then_runs(hub):
    svc = make(hub, LLMResponse('', [call('go_home')]), LLMResponse('gittim'))
    svc.send('home')
    assert wait_for(lambda: svc.approvals.pending())
    assert hub.backend.history == []                              # onaysız hareket yok
    assert svc.status()['approvals'][0]['tool'] == 'go_home'
    decide_next(svc, True)
    done(svc)
    assert 'home' in hub.backend.history
    assert ('assistant', 'gittim') in roles(svc)


def test_denied_and_timed_out_approvals_reject_the_motion(hub):
    svc = make(hub, LLMResponse('', [call('go_home')]), LLMResponse('olmadı'))
    svc.send('home')
    decide_next(svc, False)
    done(svc)
    assert hub.backend.history == []
    assert any('onaylamadı' in t for r, t in roles(svc) if r == 'tool')
    # zaman aşımı: sessizlik onay değildir
    svc2 = make(hub, LLMResponse('', [call('go_home')]), LLMResponse('olmadı'))
    svc2.approvals.timeout_s = 0.3
    svc2.send('home')
    done(svc2)
    assert hub.backend.history == []


def test_per_turn_motion_cap_blocks_the_next_motion_but_not_the_stop(hub):
    svc = make(hub, LLMResponse('', [call('go_home', id='a')]), LLMResponse('', [call('go_home', id='b')]),
               LLMResponse('', [call('emergency_stop', {'reason': 'test'}, id='c')]), max_motions=1)
    svc.send('iki kez home')
    decide_next(svc, True)
    assert wait_for(lambda: any('sınırı' in t for r, t in roles(svc) if r == 'tool'))
    done(svc)
    assert hub.backend.history.count('home') == 1                 # ikinci hareket reddedildi
    assert hub.estopped                                           # tavan acil durdu ENGELLEMEDİ


def test_emergency_stop_works_with_cap_zero_and_without_any_approval(hub):
    svc = make(hub, LLMResponse('', [call('emergency_stop', {'reason': 'sınır'})]), max_motions=0)
    svc.send('dur')
    done(svc)
    assert hub.estopped and svc.approvals.pending() == []
    assert any('emergency_stop' in t and 'estopped' in t for r, t in roles(svc) if r == 'tool')


def test_daily_request_limit_ends_the_turn_with_a_clear_message(hub):
    steps = [LLMResponse('', [call('list_objects', id=str(i))]) for i in range(5)]
    svc = make(hub, *steps, daily_limit=2)
    svc.send('bak')
    done(svc)
    assert svc.counter.snapshot() == {'daily_limit': 2, 'used_today': 2}
    assert any('sınırı doldu' in t for r, t in roles(svc) if r == 'system')
    svc.send('bir daha')                                          # sınır dolu: yeni tur da kesilir
    done(svc)
    limit_msgs = [t for r, t in roles(svc) if r == 'system' and 'sınırı doldu' in t]
    assert len(limit_msgs) == 2 and svc.counter.snapshot()['used_today'] == 2


def test_hub_estop_while_waiting_for_approval_cancels_the_turn_without_moving(hub):
    svc = make(hub, LLMResponse('', [call('go_home')]), LLMResponse('geç kaldın'))
    svc.send('home')
    assert wait_for(lambda: svc.approvals.pending())
    hub.estop('test')                                             # ajandan bağımsız yol
    done(svc)
    assert hub.backend.history == [] and svc.approvals.pending() == []
    assert hub.estopped


def test_cancel_denies_the_pending_approval_and_stops_the_loop(hub):
    svc = make(hub, LLMResponse('', [call('go_home')]), LLMResponse('', [call('go_home', id='x')]))
    svc.send('home')
    assert wait_for(lambda: svc.approvals.pending())
    assert svc.cancel() is True
    done(svc)
    assert hub.backend.history == [] and not hub.estopped        # iptal ACİL DUR değildir


def test_one_turn_at_a_time_and_message_validation(hub):
    svc = make(hub, LLMResponse('', [call('go_home')]), LLMResponse('ok'))
    svc.send('home')
    assert wait_for(lambda: svc.approvals.pending())
    with pytest.raises(AgentError) as exc:
        svc.send('ikinci')
    assert exc.value.code == 'busy'
    with pytest.raises(AgentError):
        svc.configure(dry_run=True)                               # tur sürerken ayar değişmez
    svc.cancel()
    done(svc)
    for bad in ('', '   ', 'x' * 2001):
        with pytest.raises(AgentError) as e2:
            svc.send(bad)
        assert e2.value.code == 'bad_request'


def test_configure_validates_and_rejects_unsafe_endpoints(hub):
    svc = make(hub)
    cfg = svc.configure(provider='openai', model='m1', base_url='https://api.example.com/v1',
                        daily_limit=10, max_motions=2, dry_run=True)
    assert cfg.model == 'm1' and svc.counter.limit == 10
    for bad in ({'base_url': 'http://evil.example/v1'}, {'base_url': 'https://u:p@x.example'},
                {'provider': 'gemini'}, {'daily_limit': 0}, {'max_motions': 99}, {'nope': 1},
                {'model': 'has space'}):
        with pytest.raises(AgentError):
            svc.configure(**bad)
    svc.configure(base_url='http://127.0.0.1:9/v1')               # yerel makinede http serbest


def test_user_text_is_scrubbed_before_it_is_stored_or_sent(hub):
    seen = []
    svc = AgentService(hub, AgentConfig(), KeyStore(MemoryKeyring()),
                       llm_factory=lambda c, k: ScriptedLLM(
                           lambda msgs: (seen.append(str(msgs)), LLMResponse('tamam'))[1]))
    svc.send(f'anahtarım {CANARY} ve Bearer abcdef123456')
    done(svc)
    assert CANARY not in str(svc.chat()) and CANARY not in ''.join(seen)
    assert 'abcdef123456' not in ''.join(seen)


def test_agent_is_disabled_cleanly_when_robot_agent_is_missing(hub, monkeypatch):
    monkeypatch.setitem(sys.modules, 'robot_agent.agent', None)
    svc = AgentService(hub, AgentConfig(), KeyStore(MemoryKeyring()))
    assert not svc.enabled and 'robot_agent' in svc.unavailable
    with pytest.raises(AgentError) as exc:
        svc.send('home')
    assert exc.value.code == 'unavailable'
    assert svc.status()['enabled'] is False
