"""Faz 3 pot telemetrisi: AYRI çözücü + tazelik kapısı (PR 1: yalnız varlık/tazelik).

Çerçeve vektörleri GERÇEK C++ kodlayıcıdan (k9telem/k9_telem.h ``encodeSample``) üretildi:
  g++ -std=c++11 -Ifirmware/esp_relay/lib/k9telem gen.cpp && ./a.out   (gen.cpp: encodeSample(...) -> hex)
Gerçek donanım (ESP8266, Wi-Fi, potlar) BURADA denenmedi; yalnız çözücü + sim + köprü kapısı."""
import socket
import struct
import threading
import time

import pytest
from test_bridge_core import NAMES, ZERO, Rig, at

from robot_hw_bridge import cobs
from robot_hw_bridge.codec import Frame, encode_frame
from robot_hw_bridge.core import INVALID_GOAL, Point
from robot_hw_bridge.crc import crc16
from robot_hw_bridge.protocol import MsgType, State
from robot_hw_bridge.telemetry import (
    FLAG_INVALID,
    PotChannelCfg,
    PotDecoder,
    PotLink,
    PotMonitor,
    PotSample,
    pot_channels_from_profile,
)

# (seq, t_ms, ch, flags, raw, hex)  <- C++ encodeSample çıktısı
GOLDEN = [
    (1, 1000, 1, 0, 512, '0454200103e803010201010402954600'),
    (65535, 4294967295, 7, 1, 1023, '0f5420ffffffffffff0701ff03d14200'),
    (0, 0, 2, 0, 0, '035420010101010102020101032f2400'),
    (258, 100000, 2, 0, 300, '0854200201a086010202052c01b98b00'),
    (300, 500, 1, 0, 870, '0754202c01f401010201056603cde100'),
]


def wire(seq, t_ms, ch, flags, raw):
    body = bytes([0x54, 0x20]) + struct.pack('<HIBBH', seq & 0xFFFF, t_ms & 0xFFFFFFFF, ch, flags, raw)
    body += struct.pack('<H', crc16(body))
    return cobs.encode(body) + b'\x00'


# ---- çözücü -------------------------------------------------------------------------------------
@pytest.mark.parametrize('seq,t_ms,ch,flags,raw,hexs', GOLDEN)
def test_decoder_reads_frames_made_by_the_real_firmware_encoder(seq, t_ms, ch, flags, raw, hexs):
    assert wire(seq, t_ms, ch, flags, raw).hex() == hexs      # Python kodlayıcı C++ ile aynı baytlar
    got = PotDecoder().feed(bytes.fromhex(hexs))
    assert got == [PotSample(seq, t_ms, ch, flags, raw)]


def test_decoder_handles_split_and_glued_stream():
    blob = b''.join(bytes.fromhex(g[5]) for g in GOLDEN)
    d = PotDecoder()
    out = []
    for i in range(0, len(blob), 3):                           # 3'er baytlık parçalar
        out += d.feed(blob[i:i + 3])
    assert [(s.seq, s.ch) for s in out] == [(g[0], g[2]) for g in GOLDEN]
    assert sum(d.stats[k] for k in d.stats if k != 'frames') == 0


def test_bad_crc_and_bit_flips_are_dropped_and_counted():
    good = bytearray(bytes.fromhex(GOLDEN[0][5]))
    d = PotDecoder()
    for i in range(len(good) - 1):                             # her bayt için bir bit çevir
        bad = bytearray(good)
        bad[i] ^= 0x04
        assert d.feed(bytes(bad)) == [], i
    assert d.stats['frames'] == 0
    assert d.feed(bytes(good)) == [PotSample(1, 1000, 1, 0, 512)]   # akış bozulmadan toparlar


def test_uno_frame_and_other_versions_are_not_telemetry():
    uno = encode_frame(Frame(MsgType.HEARTBEAT, 1, 0, b''))
    d = PotDecoder()
    assert d.feed(uno) == []
    assert d.stats['bad_ver'] + d.stats['bad_len'] + d.stats['bad_cobs'] >= 1 and d.stats['frames'] == 0
    other = bytearray(bytes.fromhex(GOLDEN[0][5]))
    raw = bytearray(cobs.decode(bytes(other[:-1])))
    raw[0] = 0x55                                              # başka sürüm, CRC yeniden hesaplanmış
    raw[12:14] = struct.pack('<H', crc16(bytes(raw[:12])))
    assert PotDecoder().feed(cobs.encode(bytes(raw)) + b'\x00') == []
    raw[0], raw[1] = 0x54, 0x21                                # tür farklı
    raw[12:14] = struct.pack('<H', crc16(bytes(raw[:12])))
    d2 = PotDecoder()
    assert d2.feed(cobs.encode(bytes(raw)) + b'\x00') == [] and d2.stats['bad_type'] == 1


def test_garbage_without_separator_is_bounded_and_resyncs():
    d = PotDecoder()
    assert d.feed(b'\x07' * 500) == []
    assert d.stats['overflow'] >= 1
    assert d.feed(b'\x00' + bytes.fromhex(GOLDEN[3][5])) == [PotSample(258, 100000, 2, 0, 300)]


def test_decoder_is_not_the_uno_codec():
    from robot_hw_bridge.codec import FrameDecoder
    fd = FrameDecoder()
    assert fd.feed(bytes.fromhex(GOLDEN[0][5])) == []          # Uno çözücü telemetriyi çerçeve saymaz


# ---- tazelik kapısı (sahte saat) ----------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Feeder:
    def __init__(self, mon, clock, ch=1):
        self.mon, self.clock, self.ch, self.seq, self.t_ms = mon, clock, ch, 0, 5000

    def sample(self, invalid=False, raw=512):
        self.seq += 1
        self.mon.on_sample(PotSample(self.seq, self.t_ms, self.ch, FLAG_INVALID if invalid else 0, raw))

    def run(self, seconds, step=0.125, invalid=False):   # 0.125 = 2^-3: sahte saatte yuvarlama hatası yok
        """``seconds`` boyunca her ``step``'te bir örnek (saat ve ESP zamanı birlikte ilerler)."""
        n = int(round(seconds / step))
        for _ in range(n):
            self.clock.t += step
            self.t_ms += int(step * 1000)
            self.sample(invalid)

    def silence(self, seconds):
        self.clock.t += seconds
        self.t_ms += int(seconds * 1000)


def monitor(required=True, clock=None):
    clock = clock or Clock()
    mon = PotMonitor([PotChannelCfg(1, 'joint_1', required), PotChannelCfg(2, 'joint_2', False)], clock=clock)
    return mon, clock, Feeder(mon, clock)


def test_required_channel_is_blocked_until_one_second_of_continuous_good_data():
    mon, clock, f = monitor()
    assert 'veri hiç gelmedi' in mon.blocked()                 # hiç veri yok: denetim kapalı SAYILMAZ
    f.sample()
    f.run(0.75)
    assert 'yeniden açılma' in mon.blocked()
    f.run(0.25)
    assert mon.blocked() is None


def test_stale_beyond_500ms_closes_the_gate_and_500ms_does_not():
    mon, clock, f = monitor()
    f.sample()
    f.run(1.25)
    assert mon.blocked() is None
    f.silence(0.5)
    assert mon.blocked() is None                               # tam 500 ms: hâlâ taze
    f.silence(0.125)
    assert 'bayat' in mon.blocked()


def test_invalid_flag_closes_the_gate_and_restarts_the_one_second_window():
    mon, clock, f = monitor()
    f.sample()
    f.run(1.25)
    assert mon.blocked() is None
    f.run(0.125, invalid=True)
    assert 'GEÇERSİZ' in mon.blocked()
    f.run(0.875)                                               # geçerli ama 1 s dolmadı
    assert 'yeniden açılma' in mon.blocked()
    f.run(0.25)
    assert mon.blocked() is None


def test_one_gap_inside_the_reopen_window_restarts_it():
    mon, clock, f = monitor()
    f.sample()
    f.run(0.625)
    f.silence(0.625)                                           # kesinti
    assert mon.blocked() is not None
    f.run(0.125)
    assert mon.blocked() is not None
    f.run(1.125)
    assert mon.blocked() is None
    f.silence(0.625)
    f.run(0.5)
    assert mon.blocked() is not None                           # 1 s kesintisiz yok


def test_esp_time_gap_over_500ms_counts_as_a_gap_even_if_bytes_arrive_together():
    """TCP birikmesi: eski çerçeveler art arda gelir (taze görünür); ESP zamanı boşluğu ele verir."""
    mon, clock, f = monitor()
    f.sample()
    f.run(1.25)
    assert mon.blocked() is None
    f.t_ms += 700                                              # ESP tarafında 700 ms örnek yoktu
    f.sample()
    assert 'ESP zamanı' in mon.blocked()
    f.run(1.125)
    assert mon.blocked() is None


def test_link_down_closes_the_gate_at_once():
    mon, clock, f = monitor()
    f.sample()
    f.run(1.25)
    assert mon.blocked() is None
    mon.link_down()
    assert 'bağlantısı yok' in mon.blocked()


def test_optional_channel_never_blocks_but_is_visible():
    mon, clock, f = monitor(required=False)
    assert mon.blocked() is None and not mon.has_required
    snap = mon.snapshot()
    assert snap['channels']['joint_1']['ok'] is False and snap['channels']['joint_1']['required'] is False


def test_thresholds_cannot_be_loosened():
    with pytest.raises(ValueError):
        PotMonitor([PotChannelCfg(1, 'joint_1', True)], stale_s=0.6)
    with pytest.raises(ValueError):
        PotMonitor([PotChannelCfg(1, 'joint_1', True)], reopen_s=0.5)


def test_profile_without_a_pot_field_means_no_channels():
    class Ax:
        joint = 'joint_1'

    class Hw:
        axes = [Ax()]
    assert pot_channels_from_profile(Hw()) == []

    class Pot:
        channel, required = 1, True
    Ax.pot = Pot()
    assert pot_channels_from_profile(Hw()) == [PotChannelCfg(1, 'joint_1', True)]


# ---- köprü: kontrollü durdurma, komut reddi, histerezis -----------------------------------------
def gated_rig(required=True):
    r = Rig()
    mon, clock, f = monitor(required=required)
    r.bridge.pot = mon
    return r, mon, clock, f


def open_gate(f):
    f.sample()
    f.run(1.25)


def test_without_telemetry_a_required_channel_rejects_homing_with_a_clear_message():
    r, mon, clock, f = gated_rig()
    try:
        r.configured()
        ok, msg = r.bridge.home()
        assert not ok and 'pot telemetrisi' in msg and 'joint_1' in msg
        assert r.sim.state == State.UNHOMED
        open_gate(f)
        ok, msg = r.bridge.home()
        assert ok, msg
    finally:
        r.close()


def test_loss_during_motion_is_a_controlled_stop_not_an_estop():
    r, mon, clock, f = gated_rig()
    try:
        open_gate(f)
        r.homed()
        box = []
        t = threading.Thread(target=lambda: box.append(r.run([(0.0, ZERO), (3.0, at(0.5))])))
        t.start()
        assert r.wait(lambda: r.bridge.executing)
        time.sleep(0.4)
        f.silence(0.625)                                       # telemetri 625 ms kesildi
        r.bridge._pot_guard()                                  # bekçi döngüsünün tek adımı
        t.join(10)
        assert box[0].code == INVALID_GOAL and 'pot telemetrisi' in box[0].message
        assert r.wait(lambda: not any(a.moving for a in r.sim.axes))      # eksenler durdu
        assert r.bridge.homed                                  # homed KORUNUR
        assert r.estops == [] and r.bridge.reports == []       # /api/stop YOK
        assert r.sim.state != State.ESTOPPED and r.bridge.fault is None
        assert r.bridge.state_doc()['pot_gate'] is not None    # kapı kapalı ve görünür
    finally:
        r.close()


def test_new_motion_commands_are_rejected_while_lost_and_rearm_needs_one_second():
    r, mon, clock, f = gated_rig()
    try:
        open_gate(f)
        r.homed()
        f.silence(0.625)
        for out in (r.run([(0.0, ZERO), (1.0, at(0.1))]),
                    r.bridge.setpoint(NAMES, [Point(0.0, ZERO), Point(0.05, at(0.01))])):
            assert out.code == INVALID_GOAL and 'pot telemetrisi' in out.message
        ok, msg = r.bridge.home()
        assert not ok and 'pot telemetrisi' in msg
        before = r.bridge.positions()
        f.sample()
        f.run(0.125)
        f.run(0.75)                                            # geri geldi ama histerezis dolmadı
        assert r.run([(0.0, ZERO), (1.0, at(0.1))]).code == INVALID_GOAL
        f.run(0.25)
        assert mon.blocked() is None                           # kapı açıldı
        r.bridge._pot_guard()
        assert not r.bridge.executing and not any(a.moving for a in r.sim.axes)   # kendiliğinden hareket YOK
        assert max(abs(a - b) for a, b in zip(before, r.bridge.positions())) < 1e-9
        assert r.run([(0.0, ZERO), (1.0, at(0.1))]).ok         # artık komut kabul edilir
    finally:
        r.close()


def test_required_false_has_no_effect_at_all():
    r, mon, clock, f = gated_rig(required=False)
    try:
        r.homed()                                              # hiç telemetri yok, yine de homing olur
        box = []
        t = threading.Thread(target=lambda: box.append(r.run([(0.0, ZERO), (1.5, at(0.4))])))
        t.start()
        assert r.wait(lambda: r.bridge.executing)
        f.silence(5.0)
        r.bridge._pot_guard()
        t.join(10)
        assert box[0].ok, box[0].message
        assert r.bridge.state_doc()['pot_gate'] is None
    finally:
        r.close()


def test_background_guard_thread_stops_motion_without_a_manual_step():
    r, mon, clock, f = gated_rig()
    try:
        open_gate(f)
        r.homed()
        r.bridge.tick()                                        # bekçi iş parçacığını başlatır
        box = []
        t = threading.Thread(target=lambda: box.append(r.run([(0.0, ZERO), (3.0, at(0.5))])))
        t.start()
        assert r.wait(lambda: r.bridge.executing)
        f.silence(0.625)
        for _ in range(60):                                    # düğüm tick'i sürerken bekçi kendi karar verir
            r.bridge.tick()
            time.sleep(0.02)
            if not t.is_alive():
                break
        t.join(5)
        assert box and box[0].code == INVALID_GOAL and 'pot telemetrisi' in box[0].message
        assert r.bridge.homed and r.estops == []
    finally:
        r.close()


# ---- TCP bağlantısı -----------------------------------------------------------------------------
def test_pot_link_reads_from_tcp_never_sends_and_closes_the_gate_when_the_server_goes_away():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    mon = PotMonitor([PotChannelCfg(1, 'joint_1', True)])
    link = PotLink('127.0.0.1', port, mon, retry_s=0.05).start()
    try:
        conn, _ = srv.accept()
        conn.settimeout(0.4)
        t_ms = 1000
        end = time.monotonic() + 1.3
        while time.monotonic() < end:
            t_ms += 100
            conn.sendall(wire(len(str(t_ms)), t_ms, 1, 0, 600))
            time.sleep(0.1)
        assert mon.blocked() is None
        with pytest.raises(socket.timeout):
            conn.recv(16)                                      # istemci HİÇBİR ŞEY göndermedi
        conn.close()
        srv.close()                                            # dinleyici yok: yeniden bağlanamaz
        deadline = time.monotonic() + 4
        while (mon.snapshot()['connected'] or mon.blocked() is None) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert mon.blocked() is not None and not mon.snapshot()['connected']
    finally:
        link.stop()
        srv.close()
