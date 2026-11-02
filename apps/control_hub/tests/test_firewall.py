import pytest

from control_hub.desktop import firewall as fw


def test_add_rule_is_narrow():
    args = fw.add_rule_args(8765)
    assert args[:4] == ['advfirewall', 'firewall', 'add', 'rule']
    assert 'dir=in' in args and 'action=allow' in args and 'protocol=TCP' in args
    assert 'localport=8765' in args and 'profile=private' in args       # tek port, yalnızca Özel ağ
    assert f'name={fw.RULE_NAME}' in args
    assert not any(a.startswith(('remoteip', 'program', 'protocol=any')) for a in args)


def test_command_lines_quote_the_rule_name():
    line = fw.netsh_command_line(fw.add_rule_args(8765))
    assert line.startswith('netsh advfirewall firewall add rule "name=Robot Kontrol Merkezi (Hub)"')
    assert line.endswith('localport=8765 profile=private')
    assert fw.netsh_command_line(fw.delete_rule_args()).endswith(
        'delete rule "name=Robot Kontrol Merkezi (Hub)"')


def test_powershell_commands():
    add = fw.powershell_add(8765)
    assert add == ('New-NetFirewallRule -DisplayName "Robot Kontrol Merkezi (Hub)" -Direction Inbound '
                   '-Action Allow -Protocol TCP -LocalPort 8765 -Profile Private')
    assert fw.powershell_remove() == 'Remove-NetFirewallRule -DisplayName "Robot Kontrol Merkezi (Hub)"'
    assert 'Get-NetFirewallRule' in fw.powershell_show()
    assert fw.powershell_add(9000, profile='any').endswith('-Profile Any')


@pytest.mark.parametrize('port', [0, 80, 1023, 65536, -1, '8765', None, True, 8765.0])
def test_bad_ports_are_refused(port):
    with pytest.raises(fw.FirewallError):
        fw.add_rule_args(port)                                           # type: ignore[arg-type]
    with pytest.raises(fw.FirewallError):
        fw.powershell_add(port)                                          # type: ignore[arg-type]


@pytest.mark.parametrize('name', ['', 'a"b', "a'b", 'a;b', 'a|b', 'a&b', 'a`b', '$x', 'a\nb', 'x' * 81])
def test_unsafe_names_are_refused(name):
    with pytest.raises(fw.FirewallError):
        fw.add_rule_args(8765, name=name)
    with pytest.raises(fw.FirewallError):
        fw.delete_rule_args(name)


def test_bad_profile_refused():
    with pytest.raises(fw.FirewallError):
        fw.add_rule_args(8765, profile='yok')


def test_confirmation_says_exactly_what_will_happen():
    t = fw.confirmation_text(8765)
    for needle in ('Robot Kontrol Merkezi (Hub)', 'TCP', '8765', 'Özel', 'UAC', 'kaldırabilirsin'):
        assert needle in t
    assert 'TÜM' in fw.confirmation_text(8765, profile='any')            # geniş kapsam açıkça uyarılır


def test_run_elevated_uses_runas_and_reports_uac_refusal():
    calls = []

    def fake_shell(hwnd, verb, exe, params, cwd, show):
        calls.append((verb, exe, params, show))
        return 42 if len(calls) == 1 else 5                              # >32 başarı; 5 = erişim reddedildi
    assert fw.run_elevated(fw.add_rule_args(8765), fake_shell) is True
    assert calls[0][:2] == ('runas', 'netsh.exe') and calls[0][3] == 0
    assert 'localport=8765' in calls[0][2] and '"name=Robot Kontrol Merkezi (Hub)"' in calls[0][2]
    assert fw.run_elevated(fw.delete_rule_args(), fake_shell) is False   # UAC reddedildi


def test_nothing_happens_off_windows(monkeypatch):
    monkeypatch.setattr(fw, 'is_windows', lambda: False)
    assert fw.run_elevated(fw.add_rule_args(8765)) is False              # shell_execute enjekte edilmedi
    assert fw.rule_exists() is None


def test_rule_exists_reads_netsh_exit_code(monkeypatch):
    monkeypatch.setattr(fw, 'is_windows', lambda: True)

    class R:
        def __init__(self, rc):
            self.returncode = rc
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return R(0 if len(seen) == 1 else 1)
    assert fw.rule_exists(runner=runner) is True and fw.rule_exists(runner=runner) is False
    assert seen[0][:5] == ['netsh', 'advfirewall', 'firewall', 'show', 'rule']
    assert fw.rule_exists(runner=lambda *a, **k: (_ for _ in ()).throw(OSError())) is None
