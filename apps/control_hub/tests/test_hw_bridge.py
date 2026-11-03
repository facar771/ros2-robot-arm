"""Hub COM↔TCP köprüsü, simüle ESP32'ye karşı. 'COM portu' = simülatörün TCP sunucusu (``socket://``):
pyserial'in gerçek kod yolu, Windows dahil her yerde çalışır; gerçek COM/USB bu testlerde YOK."""
import socket
import time

import pytest
from robot_hw_bridge import Esp32Sim, HwLink, SimServer, SocketTransport
from robot_hw_bridge.protocol import EstopReason, Result, State

from control_hub.hw import BridgeError, SerialTcpBridge, list_serial_ports


def wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class Rig:
    def __init__(self, **bridge_kw):
        self.sim = Esp32Sim()
        self.srv = SimServer(self.sim).start()
        self.events = []
        kw = dict(listen=('127.0.0.1', 0), reopen_s=0.1,
                  on_event=lambda k, m, lvl: self.events.append(k))
        kw.update(bridge_kw)
        self.bridge = SerialTcpBridge(f'socket://127.0.0.1:{self.srv.port}', **kw).start()
        assert wait(lambda: self.bridge.status()['serial_connected'], 3), 'köprü seriyi açamadı'
        self.links = []

    def link(self, **kw):
        t = SocketTransport('127.0.0.1', self.bridge.listen_port, **kw)
        hw = HwLink(t, heartbeat_s=0.02, link_timeout_s=0.25, ack_timeout_s=0.1).start()
        self.links.append(hw)
        return hw

    def close(self):
        for hw in self.links:
            hw.stop()
        self.bridge.stop()
        self.srv.stop()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def test_full_session_through_the_bridge(rig):
    hw = rig.link()
    assert hw.wait_link(3) and wait(lambda: hw.hello is not None, 2)
    assert hw.home(0x3F).result == Result.OK
    assert hw.wait_for(lambda s: s.state == State.HOMED, 15)
    assert hw.enable().result == Result.OK
    assert hw.move(1, 1200, 8000, 40000).result == Result.OK
    assert hw.wait_for(lambda s: s.axes[1].pos == 1200 and not s.axes[1].moving, 10)
    st = rig.bridge.status()
    assert st['client_connected'] and st['bytes_to_serial'] > 0 and st['bytes_to_client'] > 0
    assert hw.decoder.stats.bad == 0 and rig.sim.decoder_stats['crc_errors'] == 0   # bayt bayt şeffaf
    hw.estop()
    assert hw.wait_for(lambda s: s.state == State.ESTOPPED, 2)


def test_bridge_is_byte_transparent(rig):
    rig.sim.feed(b'')                                   # sim hazır
    c = socket.create_connection(('127.0.0.1', rig.bridge.listen_port))
    c.settimeout(2)
    junk = bytes(range(1, 255)) + b'\x00'
    c.sendall(junk)
    assert wait(lambda: rig.bridge.status()['bytes_to_serial'] >= len(junk), 2)
    assert rig.sim.decoder_stats['frames_ok'] == 0       # çöp çerçeve değil: sim sayar, köprü yorumlamaz
    c.close()


def test_usb_unplug_closes_the_client_at_once_and_bridge_reconnects(rig):
    hw = rig.link()
    assert hw.wait_link(3)
    rig.srv.drop_client()                                # USB çekildi (COM koptu)
    assert wait(lambda: hw.closed_reason is not None, 2), 'istemci hat kopmasını göremedi'
    assert not hw.link_ok
    assert wait(lambda: rig.bridge.status()['reopens'] >= 1, 2)
    assert 'serial_lost' in rig.events and 'client_disconnected' in rig.events
    # yeniden takıldı: köprü kendiliğinden bağlanır, yeni istemci kabul edilir
    assert wait(lambda: rig.bridge.status()['serial_connected'], 3)
    hw2 = rig.link()
    assert hw2.wait_link(3)


def test_client_death_leaves_the_stopping_to_the_mcu_watchdog(rig):
    """Köprü istemci koparsa ESP32'ye komut enjekte ETMEZ; kalp atışı kesilir, MCU watchdog'u durdurur."""
    hw = rig.link()
    assert hw.wait_link(3)
    assert hw.home(0x3F).result == Result.OK
    assert hw.wait_for(lambda s: s.state == State.HOMED, 15)
    assert hw.enable().result == Result.OK
    assert hw.move(0, 60000, 3000, 20000).result == Result.OK
    assert hw.wait_for(lambda s: s.axes[0].moving, 3)
    t0 = time.monotonic()
    hw.stop()                                            # ROS düğümü öldü: TCP kapandı
    assert wait(lambda: rig.sim.state == State.ESTOPPED, 2)
    assert rig.sim.reason == EstopReason.WATCHDOG
    assert 0.15 < time.monotonic() - t0 < 1.0
    assert wait(lambda: not rig.bridge.status()['client_connected'], 2)


def test_second_client_is_refused_and_serial_down_refuses(rig):
    hw = rig.link()
    assert hw.wait_link(3)
    c2 = socket.create_connection(('127.0.0.1', rig.bridge.listen_port))
    c2.settimeout(2)
    assert c2.recv(10) == b''                            # hemen kapatıldı
    assert rig.bridge.status()['refused'] == 1 and hw.link_ok
    rig.srv.stop()                                       # simülatör tamamen gitti
    assert wait(lambda: not rig.bridge.status()['serial_connected'], 3)
    c3 = socket.create_connection(('127.0.0.1', rig.bridge.listen_port))
    c3.settimeout(2)
    assert c3.recv(10) == b''
    assert 'client_refused' in rig.events


def test_data_without_a_client_is_dropped_and_counted(rig):
    """İstemci yokken MCU'nun STATUS'ları (20 Hz) köprüye gelir: birikmez, atılır ve sayılır."""
    assert wait(lambda: rig.bridge.status()['dropped_no_client'] > 0, 3)
    assert rig.bridge.status()['bytes_to_client'] == 0


def test_token_is_required_off_loopback_and_checked():
    with pytest.raises(BridgeError, match='token'):
        SerialTcpBridge('socket://127.0.0.1:1', listen=('0.0.0.0', 0))
    r = Rig(token='s3cret')
    try:
        c = socket.create_connection(('127.0.0.1', r.bridge.listen_port))
        c.settimeout(2)
        c.sendall(b'HUBBRIDGE-AUTH wrong\n')
        assert c.recv(10) == b''
        c = socket.create_connection(('127.0.0.1', r.bridge.listen_port))
        c.settimeout(2)
        c.sendall(b'garbage without newline')
        c.shutdown(socket.SHUT_WR)
        assert c.recv(10) == b''
        assert r.bridge.status()['auth_failures'] == 2 and not r.bridge.status()['client_connected']
        hw = r.link(auth_token='s3cret')
        assert hw.wait_link(3) and r.bridge.status()['client_connected']
    finally:
        r.close()


def test_bad_settings_and_busy_port():
    for kw in ({'baud': 10}, {'listen': ('127.0.0.1', 70000)}):
        with pytest.raises(BridgeError):
            SerialTcpBridge('COM3', **kw)
    with pytest.raises(BridgeError):
        SerialTcpBridge('')
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen()
    try:
        with pytest.raises(BridgeError, match='açılamadı'):
            SerialTcpBridge('COM3', listen=('127.0.0.1', s.getsockname()[1])).start()
    finally:
        s.close()


def test_missing_com_port_is_reported_and_retried_then_stops_cleanly():
    events = []
    b = SerialTcpBridge('socket://127.0.0.1:9', listen=('127.0.0.1', 0), reopen_s=0.05,
                        on_event=lambda k, m, lvl: events.append((k, lvl))).start()
    try:
        # Windows'ta reddedilen TCP bağlantısı ~2 sn sürer (CI'da görüldü): payı geniş tut
        assert wait(lambda: b.status()['last_error'] is not None, 10)
        assert not b.status()['serial_connected'] and 'açılamadı' in b.status()['last_error']
        assert events.count(('serial_open_failed', 'warning')) == 1      # günlük taşmaz: bir kez
    finally:
        b.stop()


class FlakySerial:
    """Yazma hatası veren sahte port: köprü hattı kopuk sayıp yeniden açmalı."""
    opened = 0

    def __init__(self):
        FlakySerial.opened += 1
        self.in_waiting = 0
        self.closed = False

    def read(self, n):
        time.sleep(0.01)
        if self.closed:
            raise OSError('port kapandı')
        return b''

    def write(self, data):
        raise OSError('device disconnected')

    def close(self):
        self.closed = True


def test_serial_write_error_drops_client_and_reopens():
    FlakySerial.opened = 0
    b = SerialTcpBridge('COM9', listen=('127.0.0.1', 0), reopen_s=0.05,
                        opener=lambda u, baud, t: FlakySerial()).start()
    try:
        assert wait(lambda: b.status()['serial_connected'], 2)
        c = socket.create_connection(('127.0.0.1', b.listen_port))
        c.settimeout(2)
        c.sendall(b'abc')
        assert c.recv(10) == b''                       # istemci kapatıldı
        assert wait(lambda: FlakySerial.opened >= 2, 3)     # yeniden açıldı
        assert 'seri yazma hatası' in (b.status()['last_error'] or '') or b.status()['reopens'] >= 1
    finally:
        b.stop()


def test_list_serial_ports_never_raises():
    assert isinstance(list_serial_ports(), list)


@pytest.mark.skipif(__import__('sys').platform == 'win32', reason='pty yalnızca POSIX')
def test_real_pyserial_path_through_a_pty():
    """socket:// yerine pyserial'in GERÇEK ``Serial`` sınıfı (POSIX) bir sözde-terminalde: simüle ESP32
    pty'nin ana ucunda, köprü alt ucunu açar. (Gerçek USB/COM değil; Windows'ta bu test atlanır.)"""
    import os
    import select
    import threading

    master, slave = os.openpty()
    path = os.ttyname(slave)
    import termios
    import tty
    tty.setraw(slave)
    attrs = termios.tcgetattr(master)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(master, termios.TCSANOW, attrs)
    sim = Esp32Sim()
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            r, _, _ = select.select([master], [], [], 0.005)
            if r:
                try:
                    sim.feed(os.read(master, 512))
                except OSError:
                    return
            out = sim.poll()
            if out:
                try:
                    os.write(master, out)
                except OSError:
                    return

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    b = SerialTcpBridge(path, baud=115200, listen=('127.0.0.1', 0), reopen_s=0.2).start()
    try:
        assert wait(lambda: b.status()['serial_connected'], 3)
        hw = HwLink(SocketTransport('127.0.0.1', b.listen_port), heartbeat_s=0.02,
                    link_timeout_s=0.25).start()
        assert hw.wait_link(3) and wait(lambda: hw.hello is not None, 2)
        assert hw.home(0x3F).result == Result.OK
        assert hw.wait_for(lambda s: s.state == State.HOMED, 15)
        assert hw.decoder.stats.bad == 0
        hw.stop()
    finally:
        b.stop()
        stop.set()
        os.close(slave)
        os.close(master)
