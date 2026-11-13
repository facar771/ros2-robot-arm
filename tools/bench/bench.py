#!/usr/bin/env python3
"""K9 Faz 2a tezgâh aracı, komut satırı. Mantığın hepsi `bench_core.py`'de (içe aktarılabilir); bu dosya yalnız CLI'dır.

YALNIZ yükten ayrık motorla, operatör başında, güç kesmeye hazırken kullan.

  PowerShell'de:   python tools\\bench\\bench.py --port COM3 status
                   python tools\\bench\\bench.py --port COM3 shell          # zero/enable/move zinciri için oturum
  Komutlar: status | zero <j1|j2|j7> | enable | move <eksen> <adım|derece> | stop [eksen] | estop | clear | disable | shell

Başka koddan kullanım (arayüz sabit): `from bench_core import Bench, SerialTransport, AXES, parse_amount, first_move_cap`
(`tools/bench` yolda olmalı) ya da `tools.bench.bench_core`. `Bench(link, say, ask, j7_spr)`; `say` ve `ask` enjekte edilir.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_core import (AXES, BENCH_MASK, CONFIRM, FIRST_MOVE_DEG, FIRST_MOVE_STEPS_UNKNOWN, Axis,  # noqa: E402,F401
                        Bench, HwLink, SerialTransport, first_move_cap, parse_amount)


def connect(args, factory: Optional[Callable[[], HwLink]] = None) -> HwLink:
    link = factory() if factory else HwLink(SerialTransport(args.port, args.baud))
    link.start()
    if not link.wait_link(5.0):
        link.stop()
        raise SystemExit('Hat kurulamadı: STATUS gelmiyor. Port/baud/kablo (TX-RX), ESP telleri D0/D1\'den SÖKÜLÜ mü, firmware yüklü mü?')
    return link


def main(argv=None, link_factory: Optional[Callable[[], HwLink]] = None,
         say: Callable[[str], None] = print, ask: Callable[[str], str] = input) -> int:
    ap = argparse.ArgumentParser(description='K9 tezgâh aracı (yalnız yükten ayrık motor, operatör başında)')
    ap.add_argument('--port', help='seri port, ör. COM3')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--j7-steps-per-rev', type=int, default=None, help='J7 motoru adım/devir (bilinirse derece kullanılabilir)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status')
    sub.add_parser('shell')
    sub.add_parser('enable')
    sub.add_parser('estop')
    sub.add_parser('clear')
    sub.add_parser('disable')
    z = sub.add_parser('zero')
    z.add_argument('axis')
    m = sub.add_parser('move')
    m.add_argument('axis')
    m.add_argument('amount')
    s = sub.add_parser('stop')
    s.add_argument('axis', nargs='?')
    args = ap.parse_args(argv)
    if link_factory is None and not args.port:
        ap.error('--port gerekli (ör. --port COM3)')
    if args.cmd in ('enable', 'move'):
        say("Bu komut tek başına anlamsız: çıkışta DISABLE gider ve watchdog kalp atışı ister. "
            f"'python tools\\bench\\bench.py --port {args.port or 'COM3'} shell' içinde çalıştır.")
        return 2
    link = connect(args, link_factory)
    bench = Bench(link, say, ask, args.j7_steps_per_rev)
    try:
        if args.cmd == 'shell':
            bench.shell()
        else:
            bench.dispatch([args.cmd] + [a for a in (getattr(args, 'axis', None), getattr(args, 'amount', None)) if a])
    except BaseException:
        say('HATA: oturum kapatılıyor (stop + DISABLE).')
        raise
    finally:
        bench.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
