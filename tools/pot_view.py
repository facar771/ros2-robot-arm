#!/usr/bin/env python3
"""Pot telemetrisini canlı gösterir (Faz 3, ilk donanım denemesi için). Yalnız Python standart kütüphanesi.

NodeMCU'nun telemetri portuna (varsayılan 8771) bağlanır; kanal başına HAM ADC (0..1023) ve GEÇERSİZ bayrağını yazar.
SALT OKUNUR: hiçbir şey göndermez. Kalibrasyon yapmaz (ham değer; kalibrasyon profilde). Bu araç bir güvenlik düzeneği değildir.

  PowerShell'de:  python tools\\pot_view.py 192.168.1.50            (canlı tek satır; Ctrl+C ile çık)
                  python tools\\pot_view.py 192.168.1.50 --log      (her çerçeve bir satır)
                  python tools\\pot_view.py 192.168.1.50 --seconds 20 --log

Çerçeve şeması firmware/esp_relay/lib/k9telem/k9_telem.h başlığındadır (ver 0x54, Uno protokolünden ayrı).
"""
import argparse
import socket
import struct
import sys
import time

VER = 0x54
TYPE_POT = 0x20
FLAG_INVALID = 0x01
RAW_LEN = 14
NAMES = {1: 'J1', 2: 'J2', 7: 'J7'}


def _crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def _cobs_decode(data):
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        if code == 0 or i + code > len(data):
            return None
        out += data[i + 1:i + code]
        i += code
        if code != 0xFF and i < len(data):
            out.append(0)
    return bytes(out)


class Decoder:
    """Bayt akışından telemetri çerçevesi çıkarır. Bozuk/yabancı çerçeve atılır ve sayılır (bad)."""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0

    def feed(self, data):
        out = []
        for b in data:
            if b != 0:
                self.buf.append(b)
                if len(self.buf) > 64:       # ayraç kaybı: at
                    self.buf.clear()
                    self.bad += 1
                continue
            if not self.buf:
                continue
            raw = _cobs_decode(bytes(self.buf))
            self.buf.clear()
            if raw is None or len(raw) != RAW_LEN or raw[0] != VER or raw[1] != TYPE_POT:
                self.bad += 1
                continue
            if _crc16(raw[:12]) != struct.unpack_from('<H', raw, 12)[0]:
                self.bad += 1
                continue
            seq, t_ms, ch, flags, val = struct.unpack_from('<HIBBH', raw, 2)
            out.append({'seq': seq, 't_ms': t_ms, 'ch': ch, 'invalid': bool(flags & FLAG_INVALID),
                        'flags': flags, 'raw': val})
        return out


def _fmt(ch, s):
    if s is None:
        return f'{NAMES.get(ch, ch)}: ---'
    return f"{NAMES.get(ch, ch)}: {s['raw']:4d}{' GECERSIZ' if s['invalid'] else '         '}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('host', help="NodeMCU IP adresi")
    ap.add_argument('--port', type=int, default=8771)
    ap.add_argument('--log', action='store_true', help='her çerçeve için bir satır yaz')
    ap.add_argument('--seconds', type=float, default=0, help='bu kadar sn sonra çık (0 = Ctrl+C)')
    a = ap.parse_args(argv)
    try:
        sock = socket.create_connection((a.host, a.port), timeout=5)
    except OSError as e:
        print(f'Bağlanılamadı {a.host}:{a.port}: {e}', file=sys.stderr)
        return 2
    sock.settimeout(1.0)
    dec = Decoder()
    last = {1: None, 2: None, 7: None}
    exp_seq, lost, total = None, 0, 0
    t_end = time.monotonic() + a.seconds if a.seconds else None
    try:
        while t_end is None or time.monotonic() < t_end:
            try:
                data = sock.recv(256)
            except socket.timeout:
                continue
            if not data:
                print('\nBağlantı kapandı.', file=sys.stderr)
                break
            for s in dec.feed(data):
                total += 1
                if exp_seq is not None and s['seq'] != exp_seq:
                    lost += (s['seq'] - exp_seq) & 0xFFFF
                exp_seq = (s['seq'] + 1) & 0xFFFF
                last[s['ch']] = s
                if a.log:
                    print(f"seq={s['seq']} t={s['t_ms']}ms {_fmt(s['ch'], s)}", flush=True)
            if not a.log:
                line = '  '.join(_fmt(c, last[c]) for c in (1, 2, 7))
                print(f'\r{line}  | çerçeve={total} kayıp={lost} bozuk={dec.bad}', end='', flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    print(f'\nBitti: çerçeve={total} kayıp={lost} bozuk={dec.bad}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
