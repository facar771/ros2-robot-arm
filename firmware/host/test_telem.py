"""Pot telemetri çekirdeğinin (firmware/esp_relay/lib/k9telem) bilgisayarda, SİMÜLE ADC ile testi.

Bu test ESP8266'da ya da gerçek potlarda ÇALIŞMAZ; yalnız çerçeve şemasını, sıra/CRC'yi, geçersiz bayrağını ve pot sürme
sırasını (aynı anda tek pot, bekleme sonrası okuma) gösterir. Referans çözücü YALNIZ burada (src/ içine dokunulmadı).
Çalıştır:  cd <repo> && PYTHONPATH=src/robot_hw_bridge python3 -m pytest firmware/host -q
"""
import importlib.util
import shutil
import socket
import struct
import subprocess
import threading
from pathlib import Path

import pytest

from robot_hw_bridge import cobs
from robot_hw_bridge.codec import FrameDecoder
from robot_hw_bridge.crc import crc16

HERE = Path(__file__).resolve().parent
LIB = HERE.parent / 'esp_relay' / 'lib' / 'k9telem'
ROOT = HERE.parent.parent

pytestmark = pytest.mark.skipif(shutil.which('g++') is None, reason='g++ yok')


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('k9t') / 'k9_telem'
    subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror', '-O1', f'-I{LIB}',
                    str(HERE / 'k9_telem_driver.cpp'), '-o', str(out)], check=True)
    return out


class Sim:
    def __init__(self, binary):
        self.p = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.frames = []
        self.viol = 0

    def cmd(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()
        while True:
            r = self.p.stdout.readline().strip()
            if r.startswith('F '):
                self.frames.append(bytes.fromhex(r[2:]))
            elif r.startswith('OK '):
                self.viol = int(r[3:])
                return

    def run(self, ms):
        self.cmd(f'R {ms}')

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)


@pytest.fixture
def sim(binary):
    s = Sim(binary)
    yield s
    s.close()


def ref_decode(wire):
    """Referans çözücü (yalnız test): COBS + 0x00 ayraç, CRC-16, ver 0x54."""
    assert wire[-1] == 0 and 0 not in wire[:-1]
    raw = cobs.decode(wire[:-1])
    assert len(raw) == 14
    assert crc16(raw[:12]) == struct.unpack_from('<H', raw, 12)[0]
    ver, typ, seq, t_ms, ch, flags, val = struct.unpack_from('<BBHIBBH', raw)
    assert (ver, typ) == (0x54, 0x20)
    return {'seq': seq, 't_ms': t_ms, 'ch': ch, 'flags': flags, 'raw': val, 'invalid': bool(flags & 1)}


def test_frames_seq_crc_channels_rate(sim):
    sim.cmd('V 0 100')
    sim.cmd('V 1 512')
    sim.cmd('V 2 900')
    sim.run(1000)
    fr = [ref_decode(w) for w in sim.frames]
    assert sim.viol == 0
    assert 13 <= len(fr) <= 16                              # ~15 Hz toplam
    assert [f['seq'] for f in fr] == list(range(len(fr)))   # sıra numarası kesintisiz
    assert [f['ch'] for f in fr][:6] == [1, 2, 7, 1, 2, 7]  # döngüsel sıra
    by = {f['ch']: f['raw'] for f in fr}
    assert by == {1: 100, 2: 512, 7: 900}
    assert all(not f['invalid'] for f in fr)
    t = [f['t_ms'] for f in fr]
    assert t == sorted(t) and all(65 <= b - a <= 70 for a, b in zip(t, t[1:]))


@pytest.mark.parametrize('val,invalid', [(0, True), (8, True), (9, False), (512, False), (1014, False),
                                         (1015, True), (1023, True), (5000, True)])
def test_invalid_flag_thresholds(sim, val, invalid):
    sim.cmd(f'V 0 {val}')
    sim.cmd('V 1 512')
    sim.cmd('V 2 512')
    sim.run(400)
    f1 = [ref_decode(w) for w in sim.frames if ref_decode(w)['ch'] == 1]
    assert f1 and all(f['invalid'] == invalid for f in f1)
    assert all(f['raw'] <= 1023 for f in f1)                 # taşan değer kırpılır
    others = [ref_decode(w) for w in sim.frames if ref_decode(w)['ch'] != 1]
    assert others and all(not f['invalid'] for f in others)  # bir potun hatası diğerini geçersiz yapmaz


def test_one_pot_driven_at_a_time_and_settle(sim):
    for s in ('V 0 200', 'V 1 600', 'V 2 800'):
        sim.cmd(s)
    sim.run(2000)
    assert sim.viol == 0     # sürücü: >1 pot sürülürse, okuma sırasında pot sürülmüyorsa ya da bekleme dolmadan okursa sayar
    # bekleme süresi yapılandırılabilir ve yine uyulur
    sim.cmd('C 10 5000')
    sim.cmd('V 0 300')
    sim.run(1000)
    assert sim.viol == 0
    n = len([w for w in sim.frames])
    assert n > 20


def test_rate_configurable_and_clamped(sim):
    sim.cmd('C 5 1000')
    sim.frames.clear()
    sim.run(2000)
    assert 9 <= len(sim.frames) <= 11
    sim.cmd('C 200 1000')    # 20 Hz'e kırpılır
    sim.frames.clear()
    sim.run(1000)
    assert 18 <= len(sim.frames) <= 21
    assert sim.viol == 0


def test_time_wrap_safe(sim):
    sim.cmd('Z 4294967200 4294960000')   # ms ve µs sayaçları 32 bit sınırına yakın
    sim.run(600)
    fr = [ref_decode(w) for w in sim.frames]
    assert sim.viol == 0 and len(fr) >= 8
    assert [f['seq'] for f in fr] == list(range(len(fr)))
    assert any(f['t_ms'] < 1000 for f in fr)      # ms sayacı sarıldı, çerçeveler akmaya devam etti


def test_reset_releases_pins_and_keeps_seq(sim):
    sim.run(100)
    sim.cmd('X')
    sim.frames.clear()
    sim.run(300)
    fr = [ref_decode(w) for w in sim.frames]
    assert sim.viol == 0 and fr and fr[0]['seq'] >= 1   # seq sıfırlanmaz: PC kayıp/yeniden bağlanmayı görür


def test_corruption_detected(sim):
    sim.run(100)
    w = sim.frames[0]
    for i in range(len(w) - 1):
        for bit in range(8):
            bad = bytearray(w)
            bad[i] ^= 1 << bit
            if bad[i] == 0:
                continue        # ayraç yaratırdı; COBS testi ayrı
            with pytest.raises(AssertionError):
                try:
                    ref_decode(bytes(bad))
                except cobs.CobsError:
                    raise AssertionError  # COBS bozulması da reddedilmektir
    # başka bir telgraf türü: aynı CRC şemasıyla bile ver/type reddedilir
    raw = bytearray(cobs.decode(w[:-1]))
    raw[1] = 0x21
    raw[12:14] = struct.pack('<H', crc16(bytes(raw[:12])))
    with pytest.raises(AssertionError):
        ref_decode(cobs.encode(bytes(raw)) + b'\x00')


def test_never_confusable_with_uno_protocol(sim):
    """Telemetri çerçevesi Uno protokol çözücüsünde 'geçerli çerçeve' olmaz (ver 0x54 != 1). PC köprüsü ve Uno
    çekirdeği bunu hata sayar; kalp atışına benzeyen çerçeve üretilmez."""
    sim.run(300)
    dec = FrameDecoder()
    for w in sim.frames:
        assert dec.feed(w) == []
    assert dec.stats.frames_ok == 0 and dec.stats.bad == len(sim.frames) > 0   # uzunluk/ver hatası: hangisi önce bakılırsa
    for w in sim.frames:
        raw = cobs.decode(w[:-1])
        assert raw[0] != 1 and raw[1] not in (0x01, 0x02, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
                                              0x81, 0x82, 0x83, 0x84)


def test_pin_table_safe():
    txt = (LIB / 'k9_telem.h').read_text()
    assert '{1, 16, 5}' in txt and '{2, 4, 14}' in txt and '{7, 12, 13}' in txt
    pins = [16, 5, 4, 14, 12, 13]
    assert len(set(pins)) == 6
    assert not set(pins) & {0, 1, 2, 3, 15, 6, 7, 8, 9, 10, 11}   # boot / UART0 / flash pinleri kullanılmaz


def _load_pot_view():
    spec = importlib.util.spec_from_file_location('pot_view', ROOT / 'tools' / 'pot_view.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_pot_view_decoder_matches_reference_and_rejects_junk(sim):
    pv = _load_pot_view()
    sim.cmd('V 0 1020')
    sim.run(300)
    d = pv.Decoder()
    stream = b''.join(sim.frames)
    got = []
    for i in range(0, len(stream), 5):          # parça parça besle
        got += d.feed(stream[i:i + 5])
    ref = [ref_decode(w) for w in sim.frames]
    assert [(g['seq'], g['ch'], g['raw'], g['invalid']) for g in got] == \
           [(r['seq'], r['ch'], r['raw'], r['invalid']) for r in ref]
    assert d.bad == 0 and any(g['invalid'] for g in got)
    # çöp + bozuk çerçeve + Uno kalp atışı: hiçbiri çerçeve olmaz
    bad = bytearray(sim.frames[0])
    bad[5] ^= 0x10
    uno_like = cobs.encode(bytes([1, 0, 1, 0, 0, 0, 0, 0]) + struct.pack('<H', crc16(bytes([1, 0, 1, 0, 0, 0, 0, 0])))) + b'\x00'
    d2 = pv.Decoder()
    assert d2.feed(b'\x07\x55\x00' + bytes(bad) + uno_like) == [] and d2.bad >= 2


def test_pot_view_socket_loop(sim, capsys):
    pv = _load_pot_view()
    sim.run(300)
    payload = b''.join(sim.frames)
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = {}

    def serve():
        c, _ = srv.accept()
        c.settimeout(2)
        c.sendall(payload)
        try:
            got['rx'] = c.recv(16)      # araç hiçbir şey göndermemeli
        except socket.timeout:
            got['rx'] = b''
        c.close()

    th = threading.Thread(target=serve)
    th.start()
    rc = pv.main(['127.0.0.1', '--port', str(port), '--log', '--seconds', '0.5'])
    th.join(5)
    srv.close()
    out = capsys.readouterr().out
    assert rc == 0 and 'J1:' in out and 'J2:' in out and 'J7:' in out and 'kayıp=0' in out
    assert got['rx'] == b''


def test_uno_core_ignores_telemetry_and_watchdog_not_fed(sim, tmp_path):
    """Telemetri çerçeveleri (kazara Uno'ya ulaşsa bile) Uno çekirdeğinde bozuk sayılır: kalp atışı yerine geçmez,
    300 ms watchdog yine açar. (Telemetri Uno'ya zaten GİTMEZ; bu, tasarımın ek güvencesidir.)"""
    from k9_hostlib import Dev, build
    from robot_hw_bridge.protocol import EstopReason, MsgType, State
    uno = Dev(build(tmp_path / 'k9_host'))
    try:
        sim.run(600)
        uno.send(MsgType.HEARTBEAT)       # t=0
        uno.at(0)
        uno._io('X 3')                     # ENABLED'a zorla (yalnız host testi)
        for i, w in enumerate(sim.frames[:8]):
            uno.at(40 * (i + 1))
            uno.raw(w)                     # telemetri çerçevesi
        c = uno.counters()
        assert c['ok'] == 1 and c['bad'] >= 8        # ok=1: yalnız baştaki gerçek kalp atışı
        st = uno.status(uno.at(400))
        assert (st.state, st.reason) == (State.ESTOPPED, EstopReason.WATCHDOG)
    finally:
        uno.close()
