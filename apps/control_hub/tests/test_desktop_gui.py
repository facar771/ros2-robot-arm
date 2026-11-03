"""Tk penceresi: ekran/Tk yoksa atlanır (Linux CI'da genelde; Windows işinde ve sanal ekranda koşar)."""
import socket

import pytest

tk = pytest.importorskip('tkinter')

from control_hub.desktop import firewall as fw  # noqa: E402
from control_hub.desktop.gui import HubWindow, qr_png_base64  # noqa: E402
from control_hub.desktop.settings import Settings  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'ekran yok: {exc}')
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


class Spy:
    def __init__(self, answer=False):
        self.answer, self.asked, self.elevated, self.errors, self.infos = answer, [], [], [], []

    def ask(self, title, text):
        self.asked.append((title, text))
        return self.answer

    def elevate(self, args):
        self.elevated.append(list(args))
        return True

    def error(self, title, text):
        self.errors.append((title, text))

    def info(self, title, text):
        self.infos.append((title, text))


def make(root, spy, **settings):
    s = Settings(fake=True, port=free_port(), pin='123456', **settings)
    return HubWindow(root, settings=s, persist=False, ask=spy.ask, elevate=spy.elevate, error=spy.error,
                     info=spy.info, rule_exists=lambda: False, open_dir=lambda p: None)


def test_qr_is_a_png():
    b64 = qr_png_base64('http://192.168.1.20:8765')
    assert b64 and __import__('base64').b64decode(b64)[:8] == b'\x89PNG\r\n\x1a\n'


def test_start_shows_urls_pin_qr_and_stop_clears(root):
    spy = Spy()
    w = make(root, spy)
    w.start()
    try:
        assert w.service and w.service.running and spy.errors == []
        assert 'PIN: 123456' in w.l_pin.cget('text') and 'localhost' in w.l_local.cget('text')
        assert w._photo is not None
        assert 'disabled' in w.b_start.state() and 'disabled' not in w.b_stop.state()
        root.update()
        assert 'SAHTE' in w.l_robot.cget('text')
    finally:
        w.stop()
    assert w.service is None and w.l_pin.cget('text') == '' and 'disabled' in w.b_stop.state()


def test_bad_settings_show_an_error_and_do_not_start(root):
    spy = Spy()
    w = make(root, spy)
    w.v_pin.set('12')
    w.start()
    assert w.service is None and spy.errors and 'PIN' in spy.errors[0][1]


def test_firewall_add_asks_first_and_declining_changes_nothing(root):
    spy = Spy(answer=False)
    w = make(root, spy)
    w.firewall_add()
    assert len(spy.asked) == 1 and spy.elevated == []                   # onay istendi, hiçbir şey çalışmadı
    text = spy.asked[0][1]
    assert 'localport=' in text and 'UAC' in text and 'Özel' in text and fw.RULE_NAME in text


def test_firewall_add_elevates_only_after_yes_with_exact_args(root):
    spy = Spy(answer=True)
    w = make(root, spy)
    w.start()
    try:
        w.firewall_add()
        assert spy.elevated == [fw.add_rule_args(w.service.port)]
    finally:
        w.stop()


def test_firewall_remove_is_also_confirmed(root):
    spy = Spy(answer=False)
    w = make(root, spy)
    w.firewall_remove()
    assert len(spy.asked) == 1 and spy.elevated == []
    spy.answer = True
    w.firewall_remove()
    assert spy.elevated == [fw.delete_rule_args()]


def test_starting_the_window_never_touches_the_firewall(root):
    spy = Spy()
    w = make(root, spy)
    w.start()
    w.stop()
    assert spy.asked == [] and spy.elevated == []


def test_copy_button_puts_the_powershell_command_on_the_clipboard(root):
    spy = Spy()
    w = make(root, spy)
    w.firewall_copy()
    assert root.clipboard_get().startswith('New-NetFirewallRule -DisplayName')
    assert spy.infos and 'PowerShell' in spy.infos[0][1]


def test_bridge_section_feeds_settings_and_shows_status(root):
    from robot_hw_bridge import Esp32Sim, SimServer
    srv = SimServer(Esp32Sim()).start()
    try:
        spy = Spy()
        bport = free_port()
        w = make(root, spy, serial_port=f'socket://127.0.0.1:{srv.port}', bridge_port=bport)
        s = w.read_settings()
        assert s.serial_port.startswith('socket://') and s.bridge_port == bport and not s.bridge_open
        w.v_baud.set('abc')
        assert w.read_settings().serial_baud == -1 and w.read_settings().problems()
        w.v_baud.set('921600')
        w.start()
        try:
            import time
            for _ in range(60):
                root.update()
                if 'seri BAĞLI' in w.l_bridge.cget('text'):
                    break
                time.sleep(0.05)
            assert 'seri BAĞLI' in w.l_bridge.cget('text') and 'istemci yok' in w.l_bridge.cget('text')
            assert 'disabled' in w.e_bport.state() and 'disabled' in w.cb_serial.state()
        finally:
            w.stop()
        assert w.l_bridge.cget('text') == '' and 'disabled' not in w.cb_serial.state()
    finally:
        srv.stop()


def test_open_bridge_shows_the_token_in_the_log(root):
    spy = Spy()
    w = make(root, spy, serial_port='socket://127.0.0.1:9', bridge_port=free_port(), bridge_open=True)
    w.start()
    try:
        assert 'token' in w.t_log.get('1.0', 'end') and w.service.bridge_token in w.t_log.get('1.0', 'end')
    finally:
        w.stop()
