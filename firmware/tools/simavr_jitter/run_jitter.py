#!/usr/bin/env python3
"""Seri trafiğin step darbesi zamanlamasına etkisi (simavr benzetimi, GERÇEK bench firmware ELF'i).

Üç senaryo, aynı hareket (J1: 256 yarım adım, 300/300; J7: 50 adım, 100/100, ikisi birlikte):
  seyrek  : kalp atışı 250 ms'de bir (watchdog 300 ms'nin hemen altı; neredeyse hiç seri iş yok)
  normal  : kalp atışı 50 ms (HwLink'in gerçek hızı)
  zorlama : 50 ms kalp atışı + her 20 ms'de bir CONFIG komutu (her biri ACK+STATUS üretir, TX hattı ~%55 dolu)
  doygun  : 50 ms kalp atışı + her 10 ms'de bir CONFIG (TX hattı ~%100: Serial.write tamponu dolar, döngü BEKLER)
Çıktı: seyrek koşuya göre adım zamanı sapmaları. Kullanım:
  python3 run_jitter.py <fw.elf> <jitter_ikilisi>
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src' / 'robot_hw_bridge'))
from robot_hw_bridge.codec import Frame, encode_frame      # noqa: E402
from robot_hw_bridge.protocol import ConfigKey, MsgType, pack_cmd  # noqa: E402

BYTE_US = 1e6 / 115200 * 10
END_US = 3_500_000


def schedule(hb_ms: int, stress_ms: int = 0):
    frames, cid = [], 0

    def add(t_us, mtype, payload=b''):
        frames.append((t_us, mtype, payload))

    def nid():
        nonlocal cid
        cid += 1
        return cid

    add(400_000, MsgType.HEARTBEAT)
    add(405_000, MsgType.CMD_HOME, pack_cmd(MsgType.CMD_HOME, nid(), mask=0x43))
    add(410_000, MsgType.CMD_ENABLE, pack_cmd(MsgType.CMD_ENABLE, nid()))
    add(500_000, MsgType.CMD_MOVE, pack_cmd(MsgType.CMD_MOVE, nid(), axis=0, target=256, vmax=300, accel=300))
    add(500_100, MsgType.CMD_MOVE, pack_cmd(MsgType.CMD_MOVE, nid(), axis=6, target=50, vmax=100, accel=100))
    for t in range(450_000, END_US, hb_ms * 1000):
        add(t + 7_000, MsgType.HEARTBEAT)
    if stress_ms:
        for t in range(520_000, END_US, stress_ms * 1000):
            add(t, MsgType.CMD_CONFIG, pack_cmd(MsgType.CMD_CONFIG, nid(), key=int(ConfigKey.WATCHDOG_MS), axis=0, value=300))
    frames.sort(key=lambda x: x[0])
    out, free = [], 0.0
    for seq, (t, mtype, payload) in enumerate(frames):  # seq zaman sırasına göre (MCU eski seq'i atar)
        data = encode_frame(Frame(mtype, seq, 0, payload))
        for b in data:
            free = max(free, t)
            out.append((free, b))
            free += BYTE_US
    return out


def run(elf, jit, hb_ms, stress_ms=0):
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        for t, b in schedule(hb_ms, stress_ms):
            f.write(f'{t:.1f} {b}\n')
        path = f.name
    res = subprocess.run([jit, elf, path, str(END_US)], capture_output=True, text=True, check=True)
    j1 = [float(l.split()[1]) for l in res.stdout.splitlines() if l.startswith('J1')]
    j7 = [float(l.split()[1]) for l in res.stdout.splitlines() if l.startswith('J7')]
    j1 = [t for t in j1 if t >= 500_000]            # yalnız MOVE sonrası; ENABLE/açılış bobin değişimleri hariç
    merged = []
    for t in j1:                                      # bir adımdaki 4 pin değişimi (<200 us) tek adım sayılır
        if not merged or t - merged[-1][-1] > 200:
            merged.append([t])
        else:
            merged[-1].append(t)
    j1 = [m[0] for m in merged]
    return j1, [t for t in j7 if t >= 500_000]


def iv(ts):
    return [b - a for a, b in zip(ts, ts[1:])]


def main():
    elf, jit = sys.argv[1], sys.argv[2]
    runs = {'seyrek': run(elf, jit, 250), 'normal': run(elf, jit, 50), 'zorlama': run(elf, jit, 50, 20), 'doygun': run(elf, jit, 50, 10)}
    base = runs['seyrek']
    for name, label in (('J1', 0), ('J7', 1)):
        print(f'--- {name}')
        b = base[label]
        print(f'  seyrek : {len(b)} adım, aralık min/maks {min(iv(b)):.0f}/{max(iv(b)):.0f} us')
        for k in ('normal', 'zorlama', 'doygun'):
            t = runs[k][label]
            n = min(len(t), len(b))
            d = [abs(x - y) for x, y in zip(iv(t)[:n], iv(b)[:n])]
            off = [abs(x - y) for x, y in zip(t[:n], b[:n])]
            print(f'  {k:7}: {len(t)} adım, aralık min/maks {min(iv(t)):.0f}/{max(iv(t)):.0f} us, '
                  f'seyrek koşuya göre en büyük aralık sapması {max(d):.0f} us, en büyük birikmiş zaman farkı {max(off):.0f} us')


if __name__ == '__main__':
    main()
