"""Çerçeveleme: başlık + CRC16 (+ isteğe bağlı HMAC) + COBS, akış çözücü ve seq izleyici.

Çözücü bozuk girdide **asla exception fırlatmaz**: kötü çerçeve sayılır (sayaçlar) ve bir sonraki 0x00'da
yeniden senkronize olunur. Seq kaybı/yinelemesi ``SeqTracker`` ile ayrıca izlenir.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from robot_hw_bridge import cobs
from robot_hw_bridge.crc import crc16
from robot_hw_bridge.protocol import (
    HEADER,
    HEADER_LEN,
    MAX_PAYLOAD,
    PROTO_VERSION,
    Flag,
    ProtocolError,
)

HMAC_LEN = 8
MAX_WIRE = HEADER_LEN + MAX_PAYLOAD + 2 + HMAC_LEN + 8     # kodlanmış çerçeve üst sınırı (COBS payı dahil)


@dataclass(frozen=True)
class Frame:
    type: int                  # MsgType (bilinmeyen sayı da olabilir; üst katman UNKNOWN_CMD der)
    seq: int
    ack: int = 0
    payload: bytes = b''
    flags: int = 0

    @property
    def retry(self) -> bool:
        return bool(self.flags & Flag.RETRY)


def _mac(key: bytes, body: bytes) -> bytes:
    return hmac.new(key, body, hashlib.sha256).digest()[:HMAC_LEN]


def encode_frame(frame: Frame, key: Optional[bytes] = None) -> bytes:
    """Telde gönderilecek baytlar (COBS + sonunda 0x00)."""
    if len(frame.payload) > MAX_PAYLOAD:
        raise ProtocolError(f'yük {MAX_PAYLOAD} baytı aşıyor')
    if not 0 <= int(frame.type) <= 0xFF:
        raise ProtocolError('tür 0-255 olmalı')
    flags = (frame.flags & Flag.KNOWN & ~Flag.AUTH) | (Flag.AUTH if key else 0)
    body = HEADER.pack(PROTO_VERSION, flags, int(frame.type), len(frame.payload),
                       frame.seq & 0xFFFF, frame.ack & 0xFFFF) + frame.payload
    raw = body + crc16(body).to_bytes(2, 'little')
    if key:
        raw += _mac(key, body)
    return cobs.encode(raw) + b'\x00'


@dataclass
class DecoderStats:
    frames_ok: int = 0
    crc_errors: int = 0
    cobs_errors: int = 0
    length_errors: int = 0
    version_errors: int = 0
    flag_errors: int = 0
    auth_errors: int = 0         # HMAC yok/yanlış
    oversize: int = 0            # ayraçsız çok uzun bayt dizisi (kayıp 0x00)

    @property
    def bad(self) -> int:
        return (self.crc_errors + self.cobs_errors + self.length_errors + self.version_errors
                + self.flag_errors + self.auth_errors + self.oversize)

    def as_dict(self) -> Dict[str, int]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class FrameDecoder:
    """Bayt akışından çerçeve çıkarır. ``key`` verilmişse HMAC'siz/yanlış HMAC'li çerçeve reddedilir."""

    def __init__(self, key: Optional[bytes] = None):
        self.key = key
        self.stats = DecoderStats()
        self._buf = bytearray()
        self._overflow = False

    def feed(self, data: bytes) -> List[Frame]:
        out: List[Frame] = []
        for b in data:
            if b == 0:
                if self._overflow:
                    self._overflow = False        # taşan çerçevenin sonu: zaten sayıldı
                elif self._buf:
                    frame = self._decode(bytes(self._buf))
                    if frame is not None:
                        out.append(frame)
                self._buf.clear()
                continue
            if self._overflow:
                continue
            self._buf.append(b)
            if len(self._buf) > MAX_WIRE:
                self.stats.oversize += 1
                self._buf.clear()
                self._overflow = True
        return out

    def _decode(self, wire: bytes) -> Optional[Frame]:
        st = self.stats
        try:
            raw = cobs.decode(wire)
        except cobs.CobsError:
            st.cobs_errors += 1
            return None
        if len(raw) < HEADER_LEN + 2:
            st.length_errors += 1
            return None
        ver, flags, mtype, length, seq, ack = HEADER.unpack_from(raw)
        has_mac = bool(flags & Flag.AUTH)
        expected = HEADER_LEN + length + 2 + (HMAC_LEN if has_mac else 0)
        if len(raw) != expected:
            st.length_errors += 1
            return None
        body = raw[:HEADER_LEN + length]
        crc = int.from_bytes(raw[HEADER_LEN + length:HEADER_LEN + length + 2], 'little')
        if crc != crc16(body):
            st.crc_errors += 1
            return None
        if ver != PROTO_VERSION:
            st.version_errors += 1
            return None
        if flags & ~Flag.KNOWN:
            st.flag_errors += 1
            return None
        if self.key is not None:
            if not has_mac or not hmac.compare_digest(raw[-HMAC_LEN:], _mac(self.key, body)):
                st.auth_errors += 1
                return None
        elif has_mac:
            st.auth_errors += 1                   # anahtarımız yok: doğrulayamayız, kabul etme
            return None
        st.frames_ok += 1
        return Frame(mtype, seq, ack, body[HEADER_LEN:], flags & ~Flag.AUTH)


@dataclass
class SeqTracker:
    """Karşı tarafın seq'ini izler: kayıp, yineleme, eski çerçeve.

    ``observe(seq)`` -> ``(gap, fresh)``: ``gap`` beklenenden kaç çerçeve atlandığı (kayıp), ``fresh``
    çerçevenin işlenmesi gerekip gerekmediği (yineleme/eski seq ``False``). ``strict=True`` (HMAC'li
    taşımalarda): seq ilerlemeyen çerçeve = tekrar saldırısı sayılır.
    """
    strict: bool = False
    expected: Optional[int] = None
    lost: int = 0
    duplicates: int = 0
    replays: int = 0
    last: Optional[int] = field(default=None)

    def observe(self, seq: int) -> tuple:
        seq &= 0xFFFF
        if self.expected is None:                 # ilk çerçeve: referans noktası
            self.expected, self.last = (seq + 1) & 0xFFFF, seq
            return 0, True
        diff = (seq - self.expected) & 0xFFFF
        if diff < 0x8000:                         # ileri (ya da tam beklenen)
            self.lost += diff
            self.expected, self.last = (seq + 1) & 0xFFFF, seq
            return diff, True
        # geri: yineleme/eski/yeniden sıralama
        if seq == self.last:
            self.duplicates += 1
        elif self.strict:
            self.replays += 1
        else:
            self.duplicates += 1
        return 0, False
