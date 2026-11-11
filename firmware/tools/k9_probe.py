#!/usr/bin/env python3
"""K9 Faz 1 duman testi: Uno'ya (USB-seri ya da ESP aktarıcısı üzerinden TCP) bağlanır, YALNIZ OKUR.

Gönderdiği tek şey kalp atışı ve HELLO isteğidir (`robot_hw_bridge.HwLink`). Hareket/enable/home komutu GÖNDERMEZ.

  Windows (PowerShell'de):  python firmware\\tools\\k9_probe.py --port COM3
  Wi-Fi (ESP aktarıcı):      python firmware/tools/k9_probe.py --connect 192.168.1.50:8770

Gereken: `pip install pyserial` (yalnız --port için). Uno USB açılınca resetlenir: 2 sn beklenir.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src' / 'robot_hw_bridge'))

from robot_hw_bridge.host import HwLink                       # noqa: E402
from robot_hw_bridge.protocol import State, StatusFlag        # noqa: E402
from robot_hw_bridge.transport import SocketTransport, TransportClosed, parse_hostport  # noqa: E402


class SerialTransport:
    def __init__(self, port: str, baud: int = 115200):
        import serial                                          # pyserial
        self._serial = serial
        self._ser = serial.Serial()
        self._ser.port, self._ser.baudrate = port, baud
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()
        time.sleep(2.0)                                        # Uno açılışta resetlenebilir
        self._ser.reset_input_buffer()

    def send(self, data: bytes) -> None:
        try:
            self._ser.write(data)
        except (self._serial.SerialException, OSError) as exc:
            raise TransportClosed(str(exc)) from exc

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        try:
            self._ser.timeout = timeout
            first = self._ser.read(1)
            if not first:
                return b''
            return first + self._ser.read(min(max_bytes - 1, self._ser.in_waiting))
        except (self._serial.SerialException, OSError) as exc:
            raise TransportClosed(str(exc)) from exc

    def close(self) -> None:
        self._ser.close()


def run_checks(link: HwLink, say: Callable[[str], None] = print, wait_s: float = 5.0) -> bool:
    """Hareketsiz Faz 1 firmware'inden beklenenler. Her satır GEÇTİ/KALDI yazar."""
    results: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = '') -> None:
        results.append((name, ok, detail))
        say(f"  [{'GEÇTİ' if ok else 'KALDI'}] {name}" + (f': {detail}' if detail else ''))

    up = link.wait_link(wait_s)
    check('hat kuruldu (STATUS geliyor)', up)
    if not up:
        say('  İpucu: kablo/TX-RX, baud 115200, ESP Wi-Fi bağlantısı; ESP bağlıyken USB ile aynı anda KONUŞMA.')
        return False
    link.wait_for(lambda s: link.hello is not None, 3.0)
    h = link.hello
    check('HELLO alındı', h is not None, f'fw {h.fw_major}.{h.fw_minor}, boot #{h.boot_count}, {len(h.axes)} eksen' if h else '')
    check('7 eksen bildirildi', h is not None and len(h.axes) == 7)
    time.sleep(0.5)
    st = link.status
    check('durum UNHOMED (açılışta hareketsiz)', st is not None and st.state == State.UNHOMED,
          st.state.name if st else '')
    check('açık döngü uyarısı var (encoder yok)', st is not None and st.open_loop_any)
    check('hiçbir eksen hareket etmiyor/enable değil',
          st is not None and not any(a.moving or a.enabled for a in st.axes))
    check('HOST_LINK_OK bayrağı', st is not None and bool(st.flags & StatusFlag.HOST_LINK_OK))
    check('watchdog 300 ms', st is not None and st.watchdog_ms == 300, str(st.watchdog_ms) if st else '')
    say(f'  istatistik: gönderilen={link.stats.tx_frames} alınan={link.stats.rx_frames} '
        f'bozuk={link.decoder.stats.bad} seq-kayıp={link.rx_seq.lost}')
    return all(ok for _, ok, _ in results)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--port', help='seri port, örn. COM3 ya da /dev/ttyUSB0')
    g.add_argument('--connect', help='ESP aktarıcısı host:port (örn. 192.168.1.50:8770)')
    ap.add_argument('--baud', type=int, default=115200)
    args = ap.parse_args(argv)
    if args.port:
        transport = SerialTransport(args.port, args.baud)
    else:
        host, port = parse_hostport(args.connect)
        transport = SocketTransport(host, port)
    link = HwLink(transport).start()
    try:
        ok = run_checks(link)
    finally:
        link.stop()
    print('SONUÇ:', 'HEPSİ GEÇTİ' if ok else 'KALDI')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
