"""``--agent-selftest`` (.exe içi öz-sınama) kaynaktan da çalışır; spec/akış kaydı bozulmasın."""
import re
from pathlib import Path

from control_hub.agent import selftest
from control_hub.desktop import launcher

ROOT = Path(__file__).resolve().parents[1]


def test_agent_selftest_passes_and_is_loud_about_real_keyring(capsys):
    assert launcher.main(['--agent-selftest']) == 0
    out = capsys.readouterr().out
    assert 'AGENT-SELFTEST OK' in out and 'KEYRING-MEMORY: OK' in out
    assert re.search(r'KEYRING-REAL: (OK|SKIPPED) ', out)          # asla sessiz atlanmaz


def test_real_keyring_check_skips_loudly_without_backend(monkeypatch):
    import keyring
    from keyring.backends import fail
    monkeypatch.setattr(keyring, 'get_keyring', lambda: fail.Keyring())
    assert selftest.keyring_real_check('x').startswith('SKIPPED ')


def test_spec_packages_agent_and_keyring():
    spec = (ROOT / 'hub.spec').read_text(encoding='utf-8')
    needles = ("collect_submodules('robot_agent')", "copy_metadata('keyring')",
               "collect_submodules('keyring')", "collect_submodules('win32ctypes')",
               'src/robot_agent', 'src/robot_core')
    for needle in needles:
        assert needle in spec, needle
    excludes = re.search(r'excludes=\[(.*?)\]', spec).group(1)
    assert 'robot_core' not in excludes and 'robot_agent' not in excludes
