import sys
import types

from control_hub import cli
from control_hub.desktop import launcher


def test_arguments_go_to_the_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, 'main', lambda argv=None: seen.append(argv) or 0)
    assert launcher.main(['--check-config', '--gui']) == 0
    assert seen == [['--check-config']]                 # --gui ayıklanır


def test_no_arguments_opens_the_window_and_hides_owned_console(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, 'hide_console_if_owned', lambda: calls.append('hide'))
    monkeypatch.setattr(launcher, '_setup_file_log', lambda: calls.append('log'))
    fake_gui = types.ModuleType('control_hub.desktop.gui')       # Tk gerekmesin
    fake_gui.run_gui = lambda: calls.append('gui') or 0          # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'control_hub.desktop.gui', fake_gui)
    assert launcher.main([]) == 0
    assert calls == ['hide', 'log', 'gui']


def test_missing_tk_falls_back_to_cli(monkeypatch, capsys):
    monkeypatch.setattr(launcher, 'hide_console_if_owned', lambda: None)
    monkeypatch.setattr(launcher, '_setup_file_log', lambda: None)
    monkeypatch.setitem(sys.modules, 'control_hub.desktop.gui', None)       # import -> ImportError
    seen = []
    monkeypatch.setattr(cli, 'main', lambda argv=None: seen.append(argv) or 0)
    assert launcher.main([]) == 0
    assert seen == [[]] and 'komut satırı' in capsys.readouterr().out


def test_firewall_help_prints_commands_and_changes_nothing(capsys, monkeypatch):
    from control_hub.desktop import firewall as fw
    def boom(*a, **k):
        raise AssertionError('çalıştırma!')
    monkeypatch.setattr(fw, 'run_elevated', boom)
    assert launcher.main(['--firewall-help', '--port', '9000']) == 0
    out = capsys.readouterr().out
    assert 'HİÇBİR ŞEY EKLEMEZ' in out and '-LocalPort 9000' in out
    assert 'New-NetFirewallRule' in out and 'Remove-NetFirewallRule' in out


def test_check_config_via_cli_entry(capsys):
    assert launcher.main(['--fake', '--check-config']) == 0
    assert 'geçerli' in capsys.readouterr().out


def test_turkish_output_survives_a_cp1252_console(monkeypatch):
    """CI'daki gerçek hata: Windows konsolu cp1252 iken 'ı' yazmak .exe'yi çökertiyordu."""
    import io

    from control_hub.desktop.winconsole import ensure_utf8_output
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding='cp1252', errors='strict')
    monkeypatch.setattr(sys, 'stdout', stream)
    monkeypatch.setattr(sys, 'stderr', io.TextIOWrapper(io.BytesIO(), encoding='cp437'))
    try:
        print('Yapılandırma geçerli')
        raise AssertionError('cp1252 ı yazamamalıydı (test geçerli değil)')
    except UnicodeEncodeError:
        pass
    ensure_utf8_output(platform='linux')
    print('Yapılandırma geçerli: şğüöç İ')
    sys.stdout.flush()
    assert 'Yapılandırma geçerli: şğüöç İ' in raw.getvalue().decode('utf-8')
    print('ekstra', file=sys.stderr)                 # stderr de çökmemeli


def test_bridge_cli_flags(capsys):
    assert launcher.main(['--list-serial-ports']) == 0
    assert launcher.main(['--bridge-only']) == 2                        # port verilmedi
    assert 'serial-bridge' in capsys.readouterr().err
    with __import__('pytest').raises(SystemExit, match='token'):
        launcher.main(['--bridge-only', '--serial-bridge', 'COM3', '--bridge-listen', '0.0.0.0:8770'])


def test_cli_builds_hub_with_bridge_but_does_not_open_it(monkeypatch):
    args = cli.build_parser().parse_args(['--fake', '--serial-bridge', 'COM5', '--pin', '123456'])
    hub = cli.build_hub(args)
    try:
        assert hub.bridge is not None and hub.snapshot()['bridge']['serial_connected'] is False
        assert hub.bridge.serial_url == 'COM5'
    finally:
        hub.close()
    args = cli.build_parser().parse_args(['--fake', '--serial-bridge', 'COM5', '--check-config'])
    assert cli.build_hub(args).bridge is None                           # --check-config köprüyü kurmaz


def test_bridge_selftest_runs_in_process(capsys):
    assert launcher.main(['--bridge-selftest']) == 0
    assert 'BRIDGE-SELFTEST OK' in capsys.readouterr().out
