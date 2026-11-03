"""COBS (Consistent Overhead Byte Stuffing): çerçeve içinde 0x00 bulunmaz, 0x00 çerçeve ayracıdır.

Bir bayt akışında bozulma/kayıp sonrası bir sonraki 0x00'da yeniden senkronize olunur.
"""
from __future__ import annotations


class CobsError(ValueError):
    pass


def encode(data: bytes) -> bytes:
    out = bytearray([0])           # kod baytı yer tutucusu
    code_idx, code = 0, 1
    for b in data:
        if b == 0:
            out[code_idx] = code
            code_idx, code = len(out), 1
            out.append(0)
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_idx] = code
                code_idx, code = len(out), 1
                out.append(0)
    out[code_idx] = code
    return bytes(out)


def decode(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise CobsError('COBS içinde 0x00')
        end = i + code
        if end > n:
            raise CobsError('COBS bloğu çerçeveden uzun')
        out += data[i + 1:end]
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)
