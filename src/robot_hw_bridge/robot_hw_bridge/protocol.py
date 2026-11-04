"""ESP32 hattı mesaj şeması v1 (ayrıntı ve gerekçe: docs/HARDWARE.md). ROS'suz, yalnızca standart kütüphane.

Çerçeve (COBS ile kodlanır, ardından 0x00 ayracı):
    sürüm u8 | bayraklar u8 | tür u8 | uzunluk u8 | seq u16 | ack u16 | yük[uzunluk] | crc16 u16 [| hmac8]
Tamsayılar little-endian. ``crc16`` ve ``hmac`` başlık+yük üzerindedir.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Tuple

PROTO_VERSION = 1
MAX_PAYLOAD = 200
MAX_AXES = 8
HEADER = struct.Struct('<BBBBHH')           # ver, flags, type, len, seq, ack
HEADER_LEN = HEADER.size                    # 8

# Watchdog: iki yönlü, 200-500 ms (belge §5)
WATCHDOG_MIN_MS, WATCHDOG_MAX_MS, WATCHDOG_DEFAULT_MS = 200, 500, 300
HEARTBEAT_PERIOD_MS = 50


class Flag:
    AUTH = 0x01          # sonda 8 baytlık HMAC var (Wi-Fi/TCP için; USB-seri'de kullanılmaz)
    RETRY = 0x02         # aynı cmd_id'nin yeniden gönderimi
    KNOWN = AUTH | RETRY


class MsgType(IntEnum):
    # host -> MCU
    HEARTBEAT = 0x01
    HELLO_REQ = 0x02
    CMD_HOME = 0x10
    CMD_ENABLE = 0x11
    CMD_DISABLE = 0x12
    CMD_MOVE = 0x13
    CMD_STOP = 0x14
    CMD_ESTOP = 0x15
    CMD_CLEAR = 0x16
    CMD_CONFIG = 0x17
    # MCU -> host
    HELLO = 0x81
    STATUS = 0x82
    ACK = 0x83
    FAULT = 0x84


CMD_TYPES = frozenset(t for t in MsgType if 0x10 <= t <= 0x17)


class State(IntEnum):
    UNHOMED = 0
    HOMING = 1
    HOMED = 2
    ENABLED = 3
    ESTOPPED = 4


class EstopReason(IntEnum):
    NONE = 0
    HW_BUTTON = 1        # NC döngü açıldı (buton/kablo kopması)
    HOST_CMD = 2
    WATCHDOG = 3         # host sustu
    FOLLOWING_ERROR = 4  # encoder: komut ile ölçüm ayrıştı
    LIMIT_VIOLATION = 5  # homing dışında limit anahtarı
    INTERNAL = 6
    BOOT = 7             # açılışta döngü zaten açıktı


class Result(IntEnum):
    OK = 0
    BAD_STATE = 1
    BAD_ARG = 2
    LIMIT = 3            # yazılım limiti dışı hedef
    NO_REFERENCE = 4     # eksen referanslanamıyor (homing anahtarı/mutlak encoder yok)
    ESTOP_ACTIVE = 5
    HW_LOOP_OPEN = 6     # NC döngü açıkken kilit açılamaz
    BUSY = 7
    UNKNOWN_CMD = 8
    LINK_DOWN = 9        # (yalnız host tarafında üretilir) bağlantı yok


class FaultCode(IntEnum):
    HW_ESTOP_PRESSED = 1
    WATCHDOG = 2
    FOLLOWING_ERROR = 3
    LIMIT_HIT = 4


class EncoderMode(IntEnum):
    NONE = 0
    INCREMENTAL = 1
    ABSOLUTE = 2


class ConfigKey(IntEnum):
    WATCHDOG_MS = 1         # genel (eksen yok sayılır), 200-500
    SOFT_MIN = 2            # eksen başına, adım
    SOFT_MAX = 3
    FOLLOW_ERR_STEPS = 4    # encoder'lı eksenlerde izin verilen sapma
    FOLLOW_ERR_MS = 5       # sapmanın sürmesine izin verilen süre


class StatusFlag:
    HW_LOOP_OPEN = 0x0001   # NC acil dur döngüsü açık
    WATCHDOG_TRIPPED = 0x0002
    OPEN_LOOP_ANY = 0x0004  # en az bir eksen encoder'sız: adım kaybı ALGILANAMAZ
    HOST_LINK_OK = 0x0008


class AxisFlag:
    MOVING = 0x01
    HOMED = 0x02
    ENABLED = 0x04
    LIMIT_MIN = 0x08
    LIMIT_MAX = 0x10
    OPEN_LOOP = 0x20
    FAULT = 0x40


class ProtocolError(ValueError):
    """Geçersiz/bozuk mesaj (yük uzunluğu, değer aralığı...)."""


# --- yük tanımları ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class AxisInfo:
    encoder: EncoderMode
    has_home_switch: bool
    has_limit_min: bool = False
    has_limit_max: bool = False

    @property
    def referenceable(self) -> bool:
        """Eksen homing yapabilir mi? (anahtar ya da mutlak encoder). Yoksa güvenli çalıştırılamaz."""
        return self.has_home_switch or self.encoder == EncoderMode.ABSOLUTE


@dataclass(frozen=True)
class Hello:
    proto: int
    fw_major: int
    fw_minor: int
    boot_count: int
    device_id: bytes
    axes: Tuple[AxisInfo, ...]

    _S = struct.Struct('<BBBBH8s')

    def pack(self) -> bytes:
        out = bytearray(self._S.pack(self.proto, self.fw_major, self.fw_minor, len(self.axes),
                                     self.boot_count, self.device_id.ljust(8, b'\0')[:8]))
        for a in self.axes:
            flags = (1 if a.has_home_switch else 0) | (2 if a.has_limit_min else 0) \
                | (4 if a.has_limit_max else 0)
            out += bytes([int(a.encoder), flags])
        return bytes(out)

    @classmethod
    def unpack(cls, data: bytes) -> 'Hello':
        if len(data) < cls._S.size:
            raise ProtocolError('HELLO çok kısa')
        proto, major, minor, n, boots, dev = cls._S.unpack_from(data)
        if n > MAX_AXES or len(data) != cls._S.size + 2 * n:
            raise ProtocolError('HELLO eksen sayısı/uzunluk uyuşmuyor')
        axes = []
        for i in range(n):
            enc, flags = data[cls._S.size + 2 * i], data[cls._S.size + 2 * i + 1]
            if enc not in (0, 1, 2):
                raise ProtocolError('HELLO bilinmeyen encoder kipi')
            axes.append(AxisInfo(EncoderMode(enc), bool(flags & 1), bool(flags & 2),
                                 bool(flags & 4)))
        return cls(proto, major, minor, boots, dev.rstrip(b'\0'), tuple(axes))


@dataclass(frozen=True)
class AxisStatus:
    pos: int            # komut edilen/sayılan adım
    enc: int            # encoder ölçümü (encoder yoksa pos ile aynı DEĞİL anlamlı: 0 ve OPEN_LOOP bayrağı)
    flags: int

    @property
    def moving(self) -> bool:
        return bool(self.flags & AxisFlag.MOVING)

    @property
    def homed(self) -> bool:
        return bool(self.flags & AxisFlag.HOMED)

    @property
    def enabled(self) -> bool:
        return bool(self.flags & AxisFlag.ENABLED)

    @property
    def open_loop(self) -> bool:
        return bool(self.flags & AxisFlag.OPEN_LOOP)


@dataclass(frozen=True)
class Status:
    state: State
    reason: EstopReason
    flags: int
    uptime_ms: int
    watchdog_ms: int
    axes: Tuple[AxisStatus, ...] = field(default_factory=tuple)

    _S = struct.Struct('<BBHIHB')
    _A = struct.Struct('<iiB')

    @property
    def hw_loop_open(self) -> bool:
        return bool(self.flags & StatusFlag.HW_LOOP_OPEN)

    @property
    def open_loop_any(self) -> bool:
        return bool(self.flags & StatusFlag.OPEN_LOOP_ANY)

    def pack(self) -> bytes:
        out = bytearray(self._S.pack(int(self.state), int(self.reason), self.flags, self.uptime_ms,
                                     self.watchdog_ms, len(self.axes)))
        for a in self.axes:
            out += self._A.pack(a.pos, a.enc, a.flags)
        return bytes(out)

    @classmethod
    def unpack(cls, data: bytes) -> 'Status':
        if len(data) < cls._S.size:
            raise ProtocolError('STATUS çok kısa')
        st, reason, flags, up, wd, n = cls._S.unpack_from(data)
        if n > MAX_AXES or len(data) != cls._S.size + cls._A.size * n:
            raise ProtocolError('STATUS eksen sayısı/uzunluk uyuşmuyor')
        try:
            state, rsn = State(st), EstopReason(reason)
        except ValueError as exc:
            raise ProtocolError(f'STATUS bilinmeyen değer: {exc}') from exc
        axes = tuple(AxisStatus(*cls._A.unpack_from(data, cls._S.size + cls._A.size * i))
                     for i in range(n))
        return cls(state, rsn, flags, up, wd, axes)


@dataclass(frozen=True)
class Ack:
    cmd_id: int
    result: Result
    detail: int = 0
    _S = struct.Struct('<HBB')

    def pack(self) -> bytes:
        return self._S.pack(self.cmd_id, int(self.result), self.detail)

    @classmethod
    def unpack(cls, data: bytes) -> 'Ack':
        if len(data) != cls._S.size:
            raise ProtocolError('ACK uzunluğu')
        cid, res, det = cls._S.unpack(data)
        try:
            return cls(cid, Result(res), det)
        except ValueError as exc:
            raise ProtocolError(f'ACK bilinmeyen sonuç {res}') from exc


@dataclass(frozen=True)
class Fault:
    code: FaultCode
    axis: int
    detail: int
    uptime_ms: int
    _S = struct.Struct('<BBiI')

    def pack(self) -> bytes:
        return self._S.pack(int(self.code), self.axis, self.detail, self.uptime_ms)

    @classmethod
    def unpack(cls, data: bytes) -> 'Fault':
        if len(data) != cls._S.size:
            raise ProtocolError('FAULT uzunluğu')
        code, axis, detail, up = cls._S.unpack(data)
        try:
            return cls(FaultCode(code), axis, detail, up)
        except ValueError as exc:
            raise ProtocolError(f'FAULT bilinmeyen kod {code}') from exc


# --- komut yükleri (host -> MCU) -------------------------------------------------------------------
_CID = struct.Struct('<H')
_MOVE = struct.Struct('<HBiII')
_MASK = struct.Struct('<HB')
_CFG = struct.Struct('<HBBi')


def pack_cmd(mtype: MsgType, cmd_id: int, **kw: int) -> bytes:
    """Komut yükünü üretir. Alanlar: HOME/STOP ``mask``; MOVE ``axis,target,vmax,accel``;
    CONFIG ``key,axis,value``; ENABLE/DISABLE/ESTOP/CLEAR yalnız cmd_id."""
    if mtype in (MsgType.CMD_ENABLE, MsgType.CMD_DISABLE, MsgType.CMD_ESTOP, MsgType.CMD_CLEAR):
        return _CID.pack(cmd_id)
    if mtype in (MsgType.CMD_HOME, MsgType.CMD_STOP):
        return _MASK.pack(cmd_id, kw['mask'])
    if mtype == MsgType.CMD_MOVE:
        return _MOVE.pack(cmd_id, kw['axis'], kw['target'], kw['vmax'], kw['accel'])
    if mtype == MsgType.CMD_CONFIG:
        return _CFG.pack(cmd_id, kw['key'], kw['axis'], kw['value'])
    raise ProtocolError(f'komut türü değil: {mtype!r}')


def unpack_cmd(mtype: MsgType, data: bytes) -> dict:
    """``pack_cmd``'in tersi: ``{'cmd_id': ..., ...}``; uzunluk yanlışsa ``ProtocolError``."""
    try:
        if mtype in (MsgType.CMD_ENABLE, MsgType.CMD_DISABLE, MsgType.CMD_ESTOP, MsgType.CMD_CLEAR):
            (cid,) = _CID.unpack(data)
            return {'cmd_id': cid}
        if mtype in (MsgType.CMD_HOME, MsgType.CMD_STOP):
            cid, mask = _MASK.unpack(data)
            return {'cmd_id': cid, 'mask': mask}
        if mtype == MsgType.CMD_MOVE:
            cid, axis, target, vmax, accel = _MOVE.unpack(data)
            return {'cmd_id': cid, 'axis': axis, 'target': target, 'vmax': vmax, 'accel': accel}
        if mtype == MsgType.CMD_CONFIG:
            cid, key, axis, value = _CFG.unpack(data)
            return {'cmd_id': cid, 'key': key, 'axis': axis, 'value': value}
    except struct.error as exc:
        raise ProtocolError(f'{mtype.name} yükü bozuk: {exc}') from exc
    raise ProtocolError(f'komut türü değil: {mtype!r}')


def cmd_id_of(data: bytes) -> int:
    """Her komut yükü cmd_id ile başlar (geçersizse -1: yine de ACK BAD_ARG döndürülebilsin)."""
    return _CID.unpack_from(data)[0] if len(data) >= 2 else -1


def axes_in_mask(mask: int, n_axes: int) -> List[int]:
    return [i for i in range(n_axes) if mask & (1 << i)]
