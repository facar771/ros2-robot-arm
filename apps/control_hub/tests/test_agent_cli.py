import pytest

from control_hub import cli
from tests.helpers_agent import CANARY


def build(argv):
    return cli.build_hub(cli.build_parser().parse_args(['--fake', *argv]))


def test_agent_is_on_by_default_with_safe_defaults():
    hub = build([])
    try:
        cfg = hub.agent.config
        assert hub.agent.enabled and (cfg.provider, cfg.max_motions, cfg.dry_run) == ('anthropic', 3, False)
    finally:
        hub.close()


def test_flags_configure_the_agent_and_no_agent_turns_it_off():
    hub = build(['--agent-provider', 'openai', '--agent-model', 'm', '--agent-base-url',
                 'https://api.example.com/v1', '--agent-daily-limit', '9', '--agent-max-motions', '1',
                 '--agent-dry-run'])
    try:
        c = hub.agent.config
        assert (c.provider, c.model, c.daily_limit, c.max_motions, c.dry_run) == ('openai', 'm', 9, 1, True)
    finally:
        hub.close()
    off = build(['--no-agent'])
    try:
        assert off.agent is None
    finally:
        off.close()


def test_bad_agent_values_are_a_clear_error_not_a_crash(capsys):
    assert cli.main(['--fake', '--check-config', '--agent-daily-limit', '0']) == 0      # check-config atlar
    with pytest.raises(ValueError, match='ajan ayarı'):
        build(['--agent-daily-limit', '0'])


def test_key_prompt_reads_hidden_and_keeps_it_in_memory_only(monkeypatch):
    monkeypatch.setattr('getpass.getpass', lambda prompt='': CANARY)
    hub = build(['--agent-provider', 'openai', '--agent-model', 'm', '--agent-base-url',
                 'https://api.example.com/v1', '--agent-key-prompt', 'openai'])
    try:
        assert hub.agent.keys.get('openai') == CANARY
        assert hub.agent.keys.status()['openai']['remembered'] is False
        assert CANARY not in str(hub.snapshot())
    finally:
        hub.close()
