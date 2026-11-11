"""Uno çekirdeğinin (firmware/uno/lib/k9core) bilgisayarda, `robot_hw_bridge` kodeğiyle karşılaştırmalı testi.

C++ çekirdeği `k9_host_driver` olarak derlenir (g++), stdin/stdout ile sürülür. Çerçeveler ve çözüm
`robot_hw_bridge.{codec,protocol}` ile yapılır: firmware ile host aynı şemayı konuşuyor mu?
Bu test AVR'de ya da gerçek donanımda ÇALIŞMAZ; yalnız mantık/şema uyumunu gösterir.
Çalıştır:  cd <repo> && PYTHONPATH=src/robot_hw_bridge python3 -m pytest firmware/host -q
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from k9_hostlib import CORE, HERE, Dev, build  # noqa: F401
from robot_hw_bridge import cobs
from robot_hw_bridge.codec import Frame, FrameDecoder, encode_frame
from robot_hw_bridge.crc import crc16
from robot_hw_bridge.protocol import (
    Ack, ConfigKey, EstopReason, Fault, Hello, MsgType, Result, State, Status, StatusFlag, pack_cmd,
)


pytestmark = pytest.mark.skipif(shutil.which('g++') is None, reason='g++ yok')


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
    return build(tmp_path_factory.mktemp('k9') / 'k9_host')


@pytest.fixture
def dev(binary):
    d = Dev(binary)
    yield d
    d.close()


def test_crc_and_cobs_match_reference():
    assert crc16(b'123456789') == 0x29B1          # firmware aynı algoritma; host testi aşağıda çerçeveyle kanıtlar
    for data in (b'', b'\x00', b'abc', bytes(range(0, 120)), b'\x00\x00\x01\x00'):
        assert cobs.decode(cobs.encode(data)) == data


def test_hello_matches_codec(dev):
    frames = dev.send(MsgType.HELLO_REQ, bytes([1, 0, 0, 0, 0]))
    hello = [Hello.unpack(f.payload) for f in frames if f.type == MsgType.HELLO]
    assert len(hello) == 1
    h = hello[0]
    assert (h.proto, h.fw_major, h.fw_minor, h.boot_count, h.device_id) == (1, 0, 1, 7, b'K9-UNO')
    assert len(h.axes) == 7
    assert all(a.encoder == 0 and not a.has_home_switch and not a.referenceable for a in h.axes)


def test_status_every_50ms_and_flags(dev):
    first = dev.at(0)
    st = dev.status(first)
    assert st.state == State.UNHOMED and st.reason == EstopReason.NONE
    assert st.watchdog_ms == 300 and len(st.axes) == 7
    assert st.open_loop_any and not (st.flags & StatusFlag.HOST_LINK_OK)
    assert all(a.open_loop and a.pos == 0 and not a.moving and not a.enabled for a in st.axes)
    assert dev.at(49) == []
    st2 = dev.status(dev.at(50))
    assert st2 is not None and st2.uptime_ms == 50
    dev.send(MsgType.HEARTBEAT)
    assert dev.status(dev.at(100)).flags & StatusFlag.HOST_LINK_OK


def test_tx_seq_increments_and_ack_field(dev):
    frames = dev.at(0) + dev.send(MsgType.HEARTBEAT, seq=40) + dev.at(50) + dev.at(100)
    seqs = [f.seq for f in frames]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    assert frames[-1].ack == 40


def test_no_motion_possible(dev):
    """Faz 1: hiçbir komut hareket/enable üretmez; referanslanamayan eksen kuralı (HARDWARE.md §7)."""
    dev.send(MsgType.HEARTBEAT)
    assert dev.result(MsgType.CMD_HOME, mask=0x01).result == Result.NO_REFERENCE
    assert dev.result(MsgType.CMD_HOME, mask=0x7F).result == Result.NO_REFERENCE
    assert dev.result(MsgType.CMD_HOME, mask=0x80).result == Result.BAD_ARG       # 7 eksen: bit 7 yok
    assert dev.result(MsgType.CMD_HOME, mask=0).result == Result.BAD_ARG
    assert dev.result(MsgType.CMD_ENABLE).result == Result.NO_REFERENCE
    assert dev.result(MsgType.CMD_MOVE, axis=0, target=1, vmax=10, accel=10).result == Result.BAD_STATE
    assert dev.result(MsgType.CMD_STOP, mask=0xFF).result == Result.OK
    assert dev.result(MsgType.CMD_DISABLE).result == Result.BAD_STATE
    st = dev.status(dev.at(60))
    assert st.state == State.UNHOMED and not any(a.moving or a.enabled for a in st.axes)


def test_config_rules(dev):
    dev.send(MsgType.HEARTBEAT)
    assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.WATCHDOG_MS, axis=0, value=250).result == Result.OK
    assert dev.status(dev.at(10)).watchdog_ms == 250
    for bad in (150, 199, 501, 5000):
        assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.WATCHDOG_MS, axis=0, value=bad).result == Result.BAD_ARG
    # gömülü limit (Faz 1: 0..0) PC'den GENİŞLETİLEMEZ
    assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=0, value=1).result == Result.LIMIT
    assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MIN, axis=0, value=-1).result == Result.LIMIT
    assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=0, value=0).result == Result.OK
    assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=7, value=0).result == Result.BAD_ARG
    assert dev.result(MsgType.CMD_CONFIG, key=ConfigKey.FOLLOW_ERR_STEPS, axis=0, value=10).result == Result.BAD_ARG
    assert dev.result(MsgType.CMD_CONFIG, key=99, axis=0, value=1).result == Result.BAD_ARG


def test_unknown_and_malformed_commands(dev):
    dev.send(MsgType.HEARTBEAT)
    frames = dev.send(0x1F, (5).to_bytes(2, 'little'))
    ack = [Ack.unpack(f.payload) for f in frames if f.type == MsgType.ACK][0]
    assert (ack.cmd_id, ack.result) == (5, Result.UNKNOWN_CMD)
    frames = dev.send(MsgType.CMD_MOVE, b'\x09\x00\x01')            # kısa yük
    ack = [Ack.unpack(f.payload) for f in frames if f.type == MsgType.ACK][0]
    assert (ack.cmd_id, ack.result) == (9, Result.BAD_ARG)
    frames = dev.send(MsgType.CMD_ESTOP, b'\x07')                    # cmd_id bile yok
    ack = [Ack.unpack(f.payload) for f in frames if f.type == MsgType.ACK][0]
    assert (ack.cmd_id, ack.result) == (0xFFFF, Result.BAD_ARG)
    assert dev.status(dev.at(0)).state == State.UNHOMED              # bozuk ESTOP etkisiz


def test_duplicate_cmd_is_not_reprocessed(dev):
    dev.send(MsgType.HEARTBEAT)
    payload = pack_cmd(MsgType.CMD_CONFIG, 77, key=ConfigKey.WATCHDOG_MS, axis=0, value=400)
    first = dev.send(MsgType.CMD_CONFIG, payload)
    again = dev.send(MsgType.CMD_CONFIG, payload, flags=2)           # RETRY, yeni seq
    a1 = [Ack.unpack(f.payload) for f in first if f.type == MsgType.ACK][0]
    a2 = [Ack.unpack(f.payload) for f in again if f.type == MsgType.ACK][0]
    assert a1 == a2 and a1.result == Result.OK
    c = dev.counters()
    assert c['cmds'] == 1 and c['dup'] == 1


def test_cache_holds_last_32(dev):
    dev.send(MsgType.HEARTBEAT)
    for _ in range(40):
        dev.result(MsgType.CMD_STOP, mask=1)
    assert dev.counters()['cmds'] == 40
    # id 40 hâlâ önbellekte (yineleme); id 1 düşmüş (yeniden işlenir)
    dev.send(MsgType.CMD_STOP, pack_cmd(MsgType.CMD_STOP, 40, mask=1))
    assert dev.counters()['dup'] == 1
    dev.send(MsgType.CMD_STOP, pack_cmd(MsgType.CMD_STOP, 1, mask=1))
    assert dev.counters()['cmds'] == 41


def test_estop_lock_and_clear(dev):
    dev.send(MsgType.HEARTBEAT)
    assert dev.result(MsgType.CMD_ESTOP).result == Result.OK
    st = dev.status(dev.at(0))
    assert (st.state, st.reason) == (State.ESTOPPED, EstopReason.HOST_CMD)
    assert dev.result(MsgType.CMD_HOME, mask=1).result == Result.ESTOP_ACTIVE
    assert dev.result(MsgType.CMD_MOVE, axis=0, target=0, vmax=1, accel=1).result == Result.ESTOP_ACTIVE
    assert dev.result(MsgType.CMD_CLEAR).result == Result.OK
    assert dev.status(dev.at(60)).state == State.UNHOMED


def test_watchdog_logic_trips_only_when_driving(dev):
    """Faz 1'de ENABLED'a ulaşılamaz; mantık, test kancasıyla (X) ENABLED'a zorlanarak sınanır."""
    dev.send(MsgType.HEARTBEAT)                    # t=0: host görüldü
    dev.at(0)
    dev.at(200)
    st = dev.status(dev.at(299))
    assert st.state == State.UNHOMED and st.flags & StatusFlag.HOST_LINK_OK
    st = dev.status(dev.at(350))                   # sürücü yok: kilitlenmez, yalnız HOST_LINK_OK düşer
    assert st.state == State.UNHOMED and not st.flags & StatusFlag.HOST_LINK_OK
    dev.send(MsgType.HEARTBEAT)                    # t=350
    dev._io('X 3')                                  # ENABLED'a zorla (yalnız host testi)
    frames = dev.at(350 + 299)
    assert dev.status(frames).state == State.ENABLED
    frames = dev.at(350 + 301)
    st = dev.status(frames)
    assert (st.state, st.reason) == (State.ESTOPPED, EstopReason.WATCHDOG)
    faults = [Fault.unpack(f.payload) for f in frames if f.type == MsgType.FAULT]
    assert len(faults) == 1 and faults[0].code == 2


def test_late_frame_does_not_erase_silence(dev):
    """Sessizlik süresi dolmuşken gelen çerçeve, işlenmeden önce watchdog'u tetikler (sim ile aynı sıra)."""
    dev.send(MsgType.HEARTBEAT)
    dev.at(0)
    dev._io('X 3')
    dev._io('N 400')                                # poll'suz zaman ilerlet
    frames = dev.send(MsgType.HEARTBEAT)
    assert dev.status(frames).state == State.ESTOPPED
    assert len([f for f in frames if f.type == MsgType.FAULT]) == 1


def test_corrupt_frames_are_dropped(dev):
    dev.send(MsgType.HEARTBEAT)
    wire = encode_frame(Frame(MsgType.CMD_HOME, 50, 0, pack_cmd(MsgType.CMD_HOME, 123, mask=1)))
    before = dev.counters()['cmds']
    for i in range(len(wire) - 1):                  # ayraç hariç her bayt, her bit
        for bit in range(8):
            bad = bytearray(wire)
            bad[i] ^= 1 << bit
            out = dev.raw(bytes(bad) + b'\x00')
            assert not [f for f in out if f.type == MsgType.ACK], (i, bit)
    assert dev.counters()['cmds'] == before
    c = dev.counters()
    assert c['bad'] > 0


def test_garbage_and_oversize_then_resync(dev):
    dev.raw(bytes([0x55] * 400) + b'\x00')           # ayraçsız uzun diziyi at, sonra yeniden senkron
    frames = dev.send(MsgType.HELLO_REQ, bytes(5))
    assert any(f.type == MsgType.HELLO for f in frames)
    dev.raw(b'\x00\x00\x00')                         # boş çerçeveler zararsız
    frames = dev.send(MsgType.HELLO_REQ, bytes(5))
    assert any(f.type == MsgType.HELLO for f in frames)


def test_oversize_valid_frame_is_dropped(dev):
    """Uno sınırı 96 B yük; HARDWARE.md 200 B'a izin verir -> Uno düşürür (sayaç), çökmez."""
    dev.send(MsgType.HEARTBEAT)
    big = encode_frame(Frame(MsgType.CMD_CONFIG, 60, 0, bytes(150)))
    assert dev.raw(big) == []
    assert dev.send(MsgType.HELLO_REQ, bytes(5))


def test_auth_flag_is_rejected(dev):
    out = dev.raw(encode_frame(Frame(MsgType.HELLO_REQ, 0, 0, bytes(5)), key=b'k' * 16))
    assert out == []


def test_seq_gap_duplicate_and_old(dev):
    dev.send(MsgType.HEARTBEAT, seq=10)
    dev.send(MsgType.HEARTBEAT, seq=13)             # 2 kayıp
    dev.send(MsgType.HEARTBEAT, seq=13)             # yineleme
    dev.send(MsgType.HEARTBEAT, seq=11)             # eski
    c = dev.counters()
    assert (c['lost'], c['seqdup']) == (2, 2)
    # yinelenen/eski çerçevedeki komut İŞLENMEZ
    frames = dev.send(MsgType.CMD_STOP, pack_cmd(MsgType.CMD_STOP, 5, mask=1), seq=12)
    assert not [f for f in frames if f.type == MsgType.ACK]


def test_seq_wraparound_is_not_loss(dev):
    dev.send(MsgType.HEARTBEAT, seq=0xFFFE)
    dev.send(MsgType.HEARTBEAT, seq=0xFFFF)
    dev.send(MsgType.HEARTBEAT, seq=0)
    c = dev.counters()
    assert (c['lost'], c['seqdup'], c['ok']) == (0, 0, 3)


# --- gerçek HwLink (host) <-> C++ çekirdek: uçtan uca uyum --------------------------------------------
import threading  # noqa: E402
import time  # noqa: E402
import sys  # noqa: E402

from robot_hw_bridge.host import HwLink  # noqa: E402
from robot_hw_bridge.transport import TransportClosed  # noqa: E402


class DriverTransport:
    """HwLink'in Transport'u: gerçek zamanı sürücünün sanal saatine çevirir (seri hat gibi)."""

    def __init__(self, binary):
        self.p = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.closed = False

    def _ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def _io(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()
        return bytes.fromhex(self.p.stdout.readline().strip()[2:])

    def send(self, data):
        with self.lock:
            if self.closed:
                raise TransportClosed('kapalı')
            self._io(f'N {self._ms()}')
            self.pending = getattr(self, 'pending', b'') + self._io('F ' + data.hex())

    def recv(self, max_bytes, timeout):
        time.sleep(0.01)
        with self.lock:
            if self.closed:
                raise TransportClosed('kapalı')
            out = getattr(self, 'pending', b'') + self._io(f'T {self._ms()}')
            self.pending = b''
        return out[:max_bytes] if out else b''

    def close(self):
        with self.lock:
            self.closed = True
        try:
            self.p.stdin.close()
            self.p.wait(2)
        except Exception:  # noqa: BLE001
            pass


def test_real_hwlink_against_core(binary):
    sys.path.insert(0, str(HERE.parent / 'tools'))
    import k9_probe
    link = HwLink(DriverTransport(binary)).start()
    try:
        lines = []
        assert k9_probe.run_checks(link, say=lines.append, wait_s=3.0), '\n'.join(lines)
        # gerçek komut yolu: ACK/yeniden deneme zinciri de çalışıyor
        assert link.home(0x01).result == Result.NO_REFERENCE
        assert link.enable().result == Result.NO_REFERENCE
        assert link.move(0, 100, 100, 100).result == Result.BAD_STATE
        link.estop()
        assert link.wait_for(lambda s: s.state == State.ESTOPPED, 2.0)
        assert link.clear().result == Result.OK
        assert link.wait_for(lambda s: s.state == State.UNHOMED, 2.0)
        assert link.stats.cmd_timeouts == 0
    finally:
        link.stop()
