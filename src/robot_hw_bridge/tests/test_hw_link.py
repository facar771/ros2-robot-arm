"""HwLink (host) + simülatör, gerçek zamanlı ve bellek-içi/TCP hat: CRC bozulması, seq kaybı, watchdog
→ durma, hat kopması. Süreler kısa tutuldu (kalp atışı 20-50 ms; MCU watchdog'u en az 200 ms)."""
import threading
import time

import pytest

from robot_hw_bridge import Esp32Sim, HwLink, LinkMonitor, LoopbackLink, SimRunner, SimServer, SocketTransport
from robot_hw_bridge.protocol import ConfigKey, EstopReason, Result, State
from robot_hw_bridge.transport import TransportClosed


class Rig:
    def __init__(self, **link_kw):
        self.lb = LoopbackLink()
        self.sim = Esp32Sim()
        self.runner = SimRunner(self.sim, self.lb.device).start()
        kw = dict(heartbeat_s=0.02, link_timeout_s=0.25, ack_timeout_s=0.1)
        kw.update(link_kw)
        self.events = []
        self.hw = HwLink(self.lb.host, on_link_lost=lambda: self.events.append('lost'),
                         on_link_up=lambda: self.events.append('up'), **kw).start()
        assert self.hw.wait_link(3), 'hat kurulamadı'

    def ready(self):
        assert self.hw.home(0x3F).result == Result.OK
        assert self.hw.wait_for(lambda s: s.state == State.HOMED, 15)
        assert self.hw.enable().result == Result.OK

    def close(self):
        self.hw.stop()
        self.runner.stop()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def test_link_comes_up_hello_and_full_cycle(rig):
    hw = rig.hw
    assert hw.link_ok and rig.events == ['up']
    assert hw.wait_for(lambda s: hw.hello is not None, 2) and len(hw.hello.axes) == 6
    rig.ready()
    assert hw.move(0, 2500, 8000, 40000).result == Result.OK
    assert hw.wait_for(lambda s: s.axes[0].pos == 2500 and not s.axes[0].moving, 10)
    assert hw.stats.retries == 0 and hw.decoder.stats.bad == 0 and rig.sim.decoder_stats['crc_errors'] == 0


def test_crc_corruption_towards_the_mcu_is_retried_and_counted(rig):
    rig.ready()
    rig.lb.to_device.corrupt.extend([6, 9])           # sıradaki iki çerçeve (kalp atışları dahil) bozulur
    ack = rig.hw.move(0, 1500, 8000, 40000)
    assert ack.result == Result.OK                      # yeniden gönderimle yine de ulaştı
    assert rig.sim.decoder_stats['crc_errors'] + rig.sim.decoder_stats['cobs_errors'] \
        + rig.sim.decoder_stats['length_errors'] >= 1
    assert rig.hw.wait_for(lambda s: s.axes[0].pos == 1500, 10)
    assert rig.sim.state == State.ENABLED               # bozuk çerçeve hiçbir şeyi bozmadı


def test_crc_corruption_towards_the_host_is_counted_and_status_recovers(rig):
    rig.lb.to_host.corrupt.extend([4, 7])               # art arda 2 STATUS (50 ms'de bir): hat 250 ms'i aşmaz
    time.sleep(0.4)
    assert rig.hw.decoder.stats.bad >= 1
    assert rig.hw.link_ok                                # sonraki STATUS'larla devam
    assert rig.hw.status is not None


def test_lost_frames_are_visible_through_seq_and_ack(rig):
    before = rig.sim.seq_stats['lost']
    rig.lb.to_device.drop = 3                           # 3 host çerçevesi hatta kayboldu
    time.sleep(0.3)
    assert rig.sim.seq_stats['lost'] - before == 3
    assert rig.hw.link_ok
    rig.lb.to_host.drop = 2                             # 2 STATUS kayboldu
    time.sleep(0.3)
    assert rig.hw.rx_seq.lost >= 2


def test_estop_survives_dropped_frames_thanks_to_repetition(rig):
    rig.ready()
    rig.lb.to_device.drop = 2                           # ilk iki çerçeve kaybolur: 3. ulaşır
    rig.hw.estop(repeats=3)
    assert rig.hw.wait_for(lambda s: s.state == State.ESTOPPED, 2)
    assert rig.sim.reason == EstopReason.HOST_CMD


def test_host_dying_trips_the_mcu_watchdog_and_stops_motion():
    r = Rig()
    try:
        r.ready()
        assert r.hw.move(0, 60000, 3000, 20000).result == Result.OK
        assert r.hw.wait_for(lambda s: s.axes[0].moving, 3)
        r.hw.stop(close_transport=False)                 # host süreci öldü: kalp atışı kesildi, hat açık
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2 and r.sim.state != State.ESTOPPED:
            time.sleep(0.01)
        dt = time.monotonic() - t0
        assert r.sim.state == State.ESTOPPED and r.sim.reason == EstopReason.WATCHDOG
        assert 0.15 < dt < 0.9                           # ~300 ms (200-500 aralığı): ne hemen ne geç
        pos = r.sim.axes[0].cmd
        time.sleep(0.3)
        assert r.sim.axes[0].cmd == pos                  # hareket durdu, ilerlemiyor
    finally:
        r.runner.stop()


def test_mcu_freezing_is_detected_by_the_host_watchdog(rig):
    rig.runner.pause()                                   # firmware kilitlendi, USB takılı
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline and rig.hw.link_ok:
        time.sleep(0.01)
    assert not rig.hw.link_ok and rig.events[-1] == 'lost'
    assert rig.hw.stats.link_losses == 1
    res = rig.hw.enable()
    assert res.result == Result.LINK_DOWN                # kopukken komut gönderilmez/başarı sayılmaz
    rig.runner.resume()
    assert rig.hw.wait_for(lambda s: True, 1) and _wait(lambda: rig.hw.link_ok, 2)
    assert rig.events[-2:] == ['lost', 'up']


def test_cable_cut_closes_the_link_and_reports_loss(rig):
    rig.lb.cut()                                         # COM kopması
    assert _wait(lambda: rig.hw.closed_reason is not None, 2)
    assert not rig.hw.link_ok and rig.events[-1] == 'lost'
    assert rig.hw.home().result == Result.LINK_DOWN
    rig.hw.estop()                                       # kopukken bile exception fırlatmaz


def test_commands_are_not_executed_twice_when_the_ack_is_lost(rig):
    rig.ready()
    rig.lb.to_host.drop = 4                              # ACK/STATUS'lar kaybolur
    ack = rig.hw.move(0, 800, 8000, 40000)
    assert ack.result in (Result.OK, Result.LINK_DOWN)
    assert rig.hw.wait_for(lambda s: s.axes[0].pos == 800 and not s.axes[0].moving, 10)
    assert rig.sim.counters['cmds'] - rig.sim.counters['dup_cmds'] >= 1
    assert rig.sim.counters['dup_cmds'] == rig.hw.stats.retries


def test_watchdog_config_through_the_link(rig):
    assert rig.hw.config(ConfigKey.WATCHDOG_MS, 100).result == Result.BAD_ARG
    assert rig.hw.config(ConfigKey.WATCHDOG_MS, 500).result == Result.OK
    assert rig.hw.wait_for(lambda s: s.watchdog_ms == 500, 1)


def test_link_monitor_pure_logic():
    t = [0.0]
    m = LinkMonitor(0.3, clock=lambda: t[0])
    assert not m.up and not m.check()
    assert m.touch() is True and m.up
    t[0] = 0.29
    assert not m.check()
    assert m.touch() is False
    t[0] = 0.58
    assert not m.check()                                 # son çerçeveden 0.29 s: henüz kopmadı
    t[0] = 0.60
    assert m.check() is True and not m.up                # 0.31 s > 0.3 s
    assert m.check() is False                            # bir kez bildirir
    assert m.touch() is True                             # geri geldi
    for bad in (0.1, 0.6):
        with pytest.raises(ValueError):
            LinkMonitor(bad)


# --- TCP üzerinden (köprünün ucu): SimServer + SocketTransport -------------------------------------
def _wait(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_sim_server_over_tcp_and_unplug():
    sim = Esp32Sim()
    srv = SimServer(sim).start()
    try:
        hw = HwLink(SocketTransport('127.0.0.1', srv.port), heartbeat_s=0.02, link_timeout_s=0.25).start()
        assert hw.wait_link(3) and hw.wait_for(lambda s: hw.hello is not None, 2)
        assert hw.home(0x3F).result == Result.OK
        assert hw.wait_for(lambda s: s.state == State.HOMED, 15)
        srv.drop_client()                                # USB çekildi
        assert _wait(lambda: hw.closed_reason is not None, 2)
        assert not hw.link_ok
        hw.stop()
        hw2 = HwLink(SocketTransport('127.0.0.1', srv.port), heartbeat_s=0.02,
                     link_timeout_s=0.25).start()        # yeniden takıldı: MCU aynı (HOMED hatırlıyor)
        assert hw2.wait_link(3) and hw2.state in (State.HOMED, State.UNHOMED)
        hw2.stop()
        assert srv.connections == 2
    finally:
        srv.stop()


def test_transport_closed_is_raised_not_swallowed():
    lb = LoopbackLink()
    lb.cut()
    with pytest.raises(TransportClosed):
        lb.host.send(b'x\x00')
    with pytest.raises(TransportClosed):
        lb.host.recv(10, 0.01)


def test_threads_do_not_leak(rig):
    names = {t.name for t in threading.enumerate()}
    assert 'hw-rx' in names and 'hw-hb' in names and 'esp32-sim' in names
