import os
import random

import pytest

from robot_hw_bridge import cobs
from robot_hw_bridge.codec import Frame, FrameDecoder, SeqTracker, encode_frame
from robot_hw_bridge.crc import crc16
from robot_hw_bridge.protocol import (
    HEADER_LEN,
    MAX_PAYLOAD,
    Ack,
    AxisInfo,
    AxisStatus,
    EncoderMode,
    EstopReason,
    Fault,
    FaultCode,
    Hello,
    MsgType,
    ProtocolError,
    Result,
    State,
    Status,
    pack_cmd,
    unpack_cmd,
)


def test_crc16_known_vector():
    assert crc16(b'123456789') == 0x29B1          # CRC-16/CCITT-FALSE
    assert crc16(b'') == 0xFFFF


@pytest.mark.parametrize('data', [b'', b'\x00', b'abc', b'\x00\x00\x01', bytes(range(256)) * 2,
                                  b'\x01' * 254, b'\x01' * 255, b'\x01' * 300])
def test_cobs_roundtrip_has_no_zero(data):
    enc = cobs.encode(data)
    assert 0 not in enc and cobs.decode(enc) == data


def test_cobs_rejects_garbage():
    with pytest.raises(cobs.CobsError):
        cobs.decode(b'\x05ab')                     # blok çerçeveden uzun
    with pytest.raises(cobs.CobsError):
        cobs.decode(b'\x02a\x00b')


def test_frame_roundtrip_all_fields():
    f = Frame(MsgType.CMD_MOVE, seq=65535, ack=7, payload=bytes(range(1, 40)), flags=2)
    dec = FrameDecoder()
    out = dec.feed(encode_frame(f))
    assert out == [f] and dec.stats.frames_ok == 1 and dec.stats.bad == 0


def test_wire_has_single_trailing_delimiter():
    wire = encode_frame(Frame(MsgType.HEARTBEAT, 1))
    assert wire.endswith(b'\x00') and wire.count(b'\x00') == 1


def test_payload_limit():
    encode_frame(Frame(MsgType.STATUS, 0, payload=b'x' * MAX_PAYLOAD))
    with pytest.raises(ProtocolError):
        encode_frame(Frame(MsgType.STATUS, 0, payload=b'x' * (MAX_PAYLOAD + 1)))


def test_every_single_bit_flip_is_detected():
    """CRC bozulması: tek bitlik her değişiklik ya reddedilir ya da (COBS bayt yapısını bozarsa) sayılır;
    yanlış içerikli çerçeve ASLA kabul edilmez."""
    good = Frame(MsgType.CMD_MOVE, 513, 12, pack_cmd(MsgType.CMD_MOVE, 9, axis=2, target=-5000,
                                                      vmax=8000, accel=40000))
    wire = bytearray(encode_frame(good))
    accepted = 0
    for i in range(len(wire) - 1):                 # son 0x00 ayracı hariç
        for bit in range(8):
            bad = bytearray(wire)
            bad[i] ^= 1 << bit
            if bad[i] == 0:                         # ayraç oluştu: iki parçaya bölünür, yine kabul edilmemeli
                pass
            dec = FrameDecoder()
            out = dec.feed(bytes(bad) + b'\x00')
            for fr in out:
                assert fr == good, 'bozuk çerçeve farklı içerikle kabul edildi!'
                accepted += 1
    assert accepted == 0
    dec = FrameDecoder()
    bad = bytearray(wire)
    bad[10] ^= 0x01
    dec.feed(bytes(bad))
    assert dec.stats.crc_errors + dec.stats.cobs_errors + dec.stats.length_errors == 1


def test_decoder_never_raises_on_noise_and_resyncs():
    rnd = random.Random(1234)
    dec = FrameDecoder()
    good = Frame(MsgType.HEARTBEAT, 42)
    got = []
    for _ in range(300):
        noise = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 60)))
        got += dec.feed(noise)
    got = [f for f in got if f != good]            # rastgele gürültüden çerçeve çıkmaz (CRC16 payı: ~1/65k)
    assert len(got) <= 1
    dec2 = FrameDecoder()
    dec2.feed(os.urandom(500))                      # bozuk + ayraçsız çöp
    out = dec2.feed(b'\x00' + encode_frame(good) + encode_frame(Frame(MsgType.HEARTBEAT, 43)))
    assert [f.seq for f in out] == [42, 43]         # çöpten sonra bir sonraki ayraçta kendine geldi


def test_oversize_unterminated_run_is_dropped_and_counted():
    dec = FrameDecoder()
    dec.feed(b'\x01' * 1000)
    assert dec.stats.oversize == 1
    out = dec.feed(b'\x00' + encode_frame(Frame(MsgType.HEARTBEAT, 1)))
    assert len(out) == 1


def test_wrong_version_and_unknown_flags_rejected():
    from robot_hw_bridge.protocol import HEADER
    for ver, flags, attr in ((2, 0, 'version_errors'), (1, 0x80, 'flag_errors')):
        body = HEADER.pack(ver, flags, 1, 0, 1, 0)
        raw = body + crc16(body).to_bytes(2, 'little')
        dec = FrameDecoder()
        assert dec.feed(cobs.encode(raw) + b'\x00') == [] and getattr(dec.stats, attr) == 1


def test_truncated_frame_is_a_length_error():
    wire = encode_frame(Frame(MsgType.CMD_ENABLE, 1, payload=b'\x01\x00'))
    raw = cobs.decode(wire[:-1])[:-3]
    dec = FrameDecoder()
    dec.feed(cobs.encode(raw) + b'\x00')
    assert dec.stats.length_errors == 1 and dec.stats.frames_ok == 0


def test_hmac_required_and_verified():
    key = b'k' * 16
    f = Frame(MsgType.CMD_ESTOP, 5, payload=b'\x01\x00')
    signed = encode_frame(f, key)
    assert FrameDecoder(key).feed(signed)[0].payload == f.payload
    d = FrameDecoder(key)
    assert d.feed(encode_frame(f)) == [] and d.stats.auth_errors == 1          # imzasız reddedilir
    d = FrameDecoder(b'x' * 16)
    assert d.feed(signed) == [] and d.stats.auth_errors == 1                   # yanlış anahtar
    d = FrameDecoder()
    assert d.feed(signed) == [] and d.stats.auth_errors == 1                   # anahtarsız taraf kabul etmez
    tampered = bytearray(cobs.decode(signed[:-1]))
    tampered[HEADER_LEN] ^= 1                                                  # yükü değiştir, CRC'yi düzelt
    body = bytes(tampered[:-10])
    tampered[-10:-8] = crc16(body).to_bytes(2, 'little')
    d = FrameDecoder(key)
    assert d.feed(cobs.encode(bytes(tampered)) + b'\x00') == [] and d.stats.auth_errors == 1


def test_seq_tracker_loss_wrap_duplicates():
    t = SeqTracker()
    assert t.observe(10) == (0, True)
    assert t.observe(11) == (0, True)
    assert t.observe(14) == (2, True) and t.lost == 2            # 12, 13 kayıp
    assert t.observe(14) == (0, False) and t.duplicates == 1     # yineleme
    assert t.observe(12) == (0, False)                           # eski (yeniden sıralanmış) çerçeve
    t2 = SeqTracker()
    t2.observe(65534)
    assert t2.observe(1) == (2, True) and t2.lost == 2           # 65535 ve 0 kayıp, sarma
    s = SeqTracker(strict=True)
    s.observe(100)
    s.observe(101)
    assert s.observe(100) == (0, False) and s.replays == 1       # HMAC'li hatta geri seq = tekrar saldırısı


def test_message_payloads_roundtrip():
    h = Hello(1, 0, 3, 7, b'ESP32-A1', (AxisInfo(EncoderMode.NONE, True, True, False),
                                         AxisInfo(EncoderMode.ABSOLUTE, False)))
    assert Hello.unpack(h.pack()) == h
    s = Status(State.ENABLED, EstopReason.NONE, 0x0C, 123456, 300,
               (AxisStatus(-5, 7, 0x23), AxisStatus(2 ** 31 - 1, -2 ** 31, 0)))
    assert Status.unpack(s.pack()) == s
    assert Ack.unpack(Ack(65535, Result.LIMIT, 3).pack()) == Ack(65535, Result.LIMIT, 3)
    fl = Fault(FaultCode.WATCHDOG, 255, -1, 99)
    assert Fault.unpack(fl.pack()) == fl
    for mt, kw in ((MsgType.CMD_HOME, {'mask': 63}), (MsgType.CMD_STOP, {'mask': 1}),
                   (MsgType.CMD_ENABLE, {}), (MsgType.CMD_ESTOP, {}),
                   (MsgType.CMD_MOVE, {'axis': 3, 'target': -9, 'vmax': 100, 'accel': 5}),
                   (MsgType.CMD_CONFIG, {'key': 1, 'axis': 0, 'value': 300})):
        assert unpack_cmd(mt, pack_cmd(mt, 77, **kw)) == {'cmd_id': 77, **kw}


@pytest.mark.parametrize('bad', [b'', b'\x01', b'\x00' * 50])
def test_bad_payloads_raise_protocol_error_only(bad):
    for fn in (Hello.unpack, Status.unpack, Ack.unpack, Fault.unpack):
        with pytest.raises(ProtocolError):
            fn(bad)
    with pytest.raises(ProtocolError):
        unpack_cmd(MsgType.CMD_MOVE, bad)
