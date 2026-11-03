"""CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, yansıtma yok, xorout 0).

Doğrulama değeri: ``crc16(b"123456789") == 0x29B1``. Firmware'de aynı tablo/algoritma kullanılır
(ESP32 ROM'undaki ``crc16_be`` ile eşleştirmek yerine bilerek elle yazıldı: tek tanım, test vektörü var).
"""
from __future__ import annotations


def _table() -> tuple:
    out = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        out.append(c)
    return tuple(out)


_TABLE = _table()


def crc16(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc
