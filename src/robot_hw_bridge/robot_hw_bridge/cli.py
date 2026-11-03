"""Komut satırı: ``python -m robot_hw_bridge sim`` (simüle ESP32, TCP) ve ``demo`` (örnek istemci)."""
from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from robot_hw_bridge.host import HwLink
from robot_hw_bridge.protocol import ConfigKey, State
from robot_hw_bridge.sim import Esp32Sim
from robot_hw_bridge.transport import SimServer, SocketTransport, parse_hostport


def _sim(args: argparse.Namespace) -> int:
    host, port = parse_hostport(args.listen)
    sim = Esp32Sim(watchdog_ms=args.watchdog_ms, hw_loop_closed=not args.loop_open)
    srv = SimServer(sim, host, port).start()
    print(f'Simüle ESP32 dinliyor: {host}:{srv.port} (6 eksen, karışık donanım). Ctrl-C ile durdur.\n'
          f'Hub köprüsü için seri adres:  socket://{host}:{srv.port}')
    try:
        while True:
            time.sleep(1)
            print(f'  durum={sim.state.name} bağlantı={srv.connections} '
                  f'çerçeve={sim.counters["frames_rx"]} kayıp={sim.seq_stats["lost"]} '
                  f'crc={sim.decoder_stats["crc_errors"]}')
    except KeyboardInterrupt:
        srv.stop()
    return 0


def _demo(args: argparse.Namespace) -> int:
    host, port = parse_hostport(args.connect)
    print(f'Bağlanılıyor: {host}:{port}')
    link = HwLink(SocketTransport(host, port, auth_token=args.token)).start()
    ok = True

    def step(name: str, cond: bool, detail: str = '') -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  [{'GEÇTİ' if cond else 'KALDI'}] {name}" + (f': {detail}' if detail else ''))

    try:
        step('hat kuruldu (STATUS geldi)', link.wait_link(5.0))
        step('HELLO alındı', link.wait_for(lambda s: link.hello is not None, 3.0),
             f'{len(link.hello.axes)} eksen' if link.hello else '')
        if link.status and link.status.state == State.ESTOPPED:
            step('kilit aç (clear)', link.clear().result == 0)
        # watchdog: 200-500 ms arasında olmalı
        step('watchdog ayarı 300 ms', link.config(ConfigKey.WATCHDOG_MS, 300).result == 0)
        a = link.home(0x3F)
        step('home kabul edildi', a.result == 0, a.result.name)
        step('hepsi homed', link.wait_for(lambda s: s.state == State.HOMED, 20.0),
             str(link.state))
        step('enable', link.enable().result == 0)
        a = link.move(0, 5000, 8000, 40000)
        step('move eksen 0 → 5000', a.result == 0, a.result.name)
        step('hedefe varıldı', link.wait_for(
            lambda s: abs(s.axes[0].pos - 5000) <= 1 and not s.axes[0].moving, 10.0))
        if link.status and link.status.open_loop_any:
            print('  UYARI: encoder\'sız eksen(ler) var: ADIM KAYBI ALGILANAMAZ (açık döngü).')
        link.estop()
        step('ACİL DUR', link.wait_for(lambda s: s.state == State.ESTOPPED, 3.0))
        print(f'  istatistik: {link.stats}')
    finally:
        link.stop()
    print('SONUÇ:', 'HEPSİ GEÇTİ' if ok else 'KALDI')
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog='robot_hw_bridge', description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('sim', help='simüle ESP32 (TCP sunucusu)')
    s.add_argument('--listen', default='127.0.0.1:8771')
    s.add_argument('--watchdog-ms', type=int, default=300)
    s.add_argument('--loop-open', action='store_true', help='NC döngü açık başla (ESTOPPED/BOOT)')
    s.set_defaults(fn=_sim)
    d = sub.add_parser('demo', help='örnek istemci: home → enable → move → ACİL DUR')
    d.add_argument('--connect', default='127.0.0.1:8770', help='Hub köprüsü ya da simülatör host:port')
    d.add_argument('--token', default=None, help='köprü --bridge-token değeri (varsa)')
    d.set_defaults(fn=_demo)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
