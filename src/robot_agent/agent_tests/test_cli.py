"""The robot_agent CLI against a real Robot API v1 server (fake skills)."""
import io

import pytest

from agent_tests.rig import Rig
from robot_agent import cli


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def run(rig, *args):
    out = io.StringIO()
    code = cli.main(['--url', f'http://127.0.0.1:{rig.view.port}', *args], out=out)
    return code, out.getvalue()


def above(rig):
    return [c for c in rig.skills.calls if c[0] == 'go_above']


def test_offline_with_yes_moves_the_robot(rig):
    code, out = run(rig, '--offline', '--yes', 'yeşil nesnenin üstüne git')
    assert code == 0 and above(rig) == [('go_above', 'green', 0.35)]
    assert '[+] go_above_object' in out and '[onay otomatik]' in out and 'Tamam' in out


def test_without_a_confirmation_nothing_moves(rig):
    code, out = run(rig, '--offline', 'yeşil nesnenin üstüne git')      # not a tty, no --yes
    assert code == 1 and above(rig) == [] and 'onay gerekli' in out


def test_dry_run_moves_nothing(rig):
    code, out = run(rig, '--offline', '--dry-run', '--yes', 'kırmızı kutunun 30 cm üstüne git')
    assert code == 0 and above(rig) == [] and 'dry_run' in out and 'above red 0.300' in out


def test_nothing_understood_means_no_motion(rig):
    code, out = run(rig, '--offline', '--yes', 'hava nasıl')
    assert code == 0 and rig.skills.calls == [] and 'anlayamadım' in out


def test_read_only_requests_need_no_confirmation(rig):
    code, out = run(rig, '--offline', 'nesneleri listele')
    assert code == 0 and 'list_objects' in out


def test_exit_code_3_when_the_run_ends_in_an_emergency_stop(rig):
    rig.supervisor.stop('insan')
    code, out = run(rig, '--offline', '--yes', 'home')
    assert code == 3 and 'acil dur' in out.lower() and rig.skills.calls == []


def test_the_cli_can_stop_the_robot(rig):
    code, out = run(rig, '--offline', 'acil dur')
    assert code == 3 and rig.estop.is_set


def test_no_api_key_means_the_llm_layer_is_off(rig, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    code, out = run(rig, 'home')
    assert code == 2 and 'ANTHROPIC_API_KEY' in out and '--offline' in out
    assert rig.skills.calls == []


def test_only_loopback_urls(rig):
    out = io.StringIO()
    assert cli.main(['--offline', '--url', 'http://10.0.0.5:8080', 'home'], out=out) == 2
    assert '127.0.0.1' in out.getvalue()


def test_max_motions_is_enforced(rig):
    code, out = run(rig, '--offline', '--yes', '--max-motions', '0', 'home')
    assert code == 1 and 'sınırı' in out and rig.skills.calls == []
