#!/usr/bin/env python3
"""K9 Faz 2a tezgâh aracı: Uno'nun `K9_BENCH` firmware'ine seri portla bağlanır; J1, J2 (28BYJ-48) ve J7 (A4988) için
sıfırla / enerjile / hareket ettir / durdur. YEREL araçtır: Robot API, ROS, `mode: real`, servo YOK.

YALNIZ yükten ayrık motorla, operatör başında, güç kesmeye hazırken kullan.

  PowerShell'de:   python tools\\bench\\bench.py --port COM3 status
                   python tools\\bench\\bench.py --port COM3 shell          # zero/enable/move zinciri için oturum
  Komutlar: status | zero <j1|j2|j7> | enable | move <eksen> <adım|derece> | stop [eksen] | estop | clear | disable | shell
  (`clear`: acil dur sonrası kilidi açar; sonra yeniden `zero` gerekir.)

Neden `shell`: firmware watchdog'u 300 ms'dir ve kalp atışı sürekli gitmelidir; araç kapanınca DISABLE gönderir ve hattı keser.
Tek komutluk çağrıda `enable` ve `move` bu yüzden anlamsızdır, yalnız `shell` içinde çalışır.

Konum bir KESTİRİMDİR (açık döngü, enkoder yok); adım kaybı algılanmaz.
Gereken: `pip install pyserial`. Yalnız `robot_hw_bridge` (host/protocol/transport) içe aktarılır, ROS gerekmez.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src' / 'robot_hw_bridge'))

from robot_hw_bridge.host import HwLink                                      # noqa: E402
from robot_hw_bridge.protocol import Result, State, Status, StatusFlag       # noqa: E402
from robot_hw_bridge.transport import TransportClosed                        # noqa: E402

CONFIRM = 'EVET'
FIRST_MOVE_DEG = 5.0          # her eksende oturumdaki ilk hareket en çok bu kadar
FIRST_MOVE_STEPS_UNKNOWN = 10  # adım/devir bilinmiyorsa (J7) ilk hareket en çok bu kadar adım
BENCH_MASK = 0x43


class Axis(NamedTuple):
    name: str
    idx: int           # protokol eksen indeksi
    lo: int            # firmware'deki gömülü limitle AYNI (adım)
    hi: int
    vmax: int          # gömülü azami hız (adım/sn) ve ivme (adım/sn^2)
    accel: int
    def_vmax: int      # varsayılanlar gömülü sınırdan DAHA DAR
    def_accel: int
    spr: Optional[int]  # çıkış mili adım/devir (28BYJ-48 yarım adım: ~4096 VARSAYIM); J7 için bilinmiyor


AXES: Dict[str, Axis] = {
    'j1': Axis('j1', 0, -256, 256, 300, 300, 100, 100, 4096),
    'j2': Axis('j2', 1, -256, 256, 300, 300, 100, 100, 4096),
    'j7': Axis('j7', 6, -50, 50, 100, 100, 50, 50, None),
}


class SerialTransport:
    """pyserial üzerinden HwLink taşıyıcısı. DTR/RTS kapalı açılır; Uno açılışta resetlenebilir diye 2 sn beklenir."""

    def __init__(self, port: str, baud: int = 115200):
        import serial                                                         # pyserial
        self._serial = serial
        self._ser = serial.Serial()
        self._ser.port, self._ser.baudrate = port, baud
        self._ser.dtr = False
        self._ser.rts = False
        self._ser.open()
        time.sleep(2.0)
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


_AMOUNT = re.compile(r'^([+-]?\d+(?:\.\d+)?)\s*(deg|d|°)?$', re.IGNORECASE)


def parse_amount(text: str, ax: Axis) -> int:
    """'30' -> 30 adım; '5deg' / '5d' / '5°' -> derece (çıkış mili, MAFSAL değil). Adıma çevirir. Hata: ValueError."""
    m = _AMOUNT.match(text.strip())
    if not m:
        raise ValueError(f"'{text}' anlaşılmadı: adım için '30', derece için '5deg'")
    num, unit = float(m.group(1)), m.group(2)
    if unit:
        if ax.spr is None:
            raise ValueError(f'{ax.name.upper()} için adım/devir bilinmiyor: derece kullanılamaz, adım gir')
        return int(num * ax.spr / 360.0)   # sıfıra doğru kırp: istenen dereceyi ASLA aşma
    if num != int(num):
        raise ValueError('adım tam sayı olmalı')
    return int(num)


def first_move_cap(ax: Axis) -> int:
    """Oturumdaki ilk hareketin azami adımı: <=5 derece (adım/devir biliniyorsa) ya da 10 adım."""
    if ax.spr is None:
        return FIRST_MOVE_STEPS_UNKNOWN
    return max(1, int(FIRST_MOVE_DEG * ax.spr / 360.0))


class Bench:
    def __init__(self, link: HwLink, say: Callable[[str], None] = print,
                 ask: Callable[[str], str] = input, j7_spr: Optional[int] = None):
        self.link, self.say, self.ask = link, say, ask
        self.axes = dict(AXES)
        if j7_spr:
            self.axes['j7'] = AXES['j7']._replace(spr=j7_spr)
        self._moved: set = set()   # bu oturumda en az bir kez hareket ettirilen eksenler

    # ------------------------------------------------------------------ yardımcılar
    def _status(self) -> Optional[Status]:
        return self.link.status if self.link.link_ok else None

    def _need_bench(self) -> Optional[Status]:
        """Hareket komutundan önce kapı: BENCH_MODE görülmeden ve döngü kapalı değilken hiçbir şey GÖNDERİLMEZ."""
        st = self._status()
        if st is None:
            self.say('REDDEDİLDİ: hat yok / STATUS gelmiyor. Komut gönderilmedi.')
            return None
        if not st.flags & StatusFlag.BENCH_MODE:
            self.say('REDDEDİLDİ: STATUS\'ta BENCH_MODE yok (firmware tezgâh derlemesi değil). Hareket komutu gönderilmedi.')
            return None
        if st.hw_loop_open:
            self.say('REDDEDİLDİ: acil dur döngüsü açık (D13). Jumper/NC butonu kontrol et. Komut gönderilmedi.')
            return None
        return st

    def _axis(self, name: str) -> Axis:
        try:
            return self.axes[name.lower()]
        except KeyError:
            raise ValueError(f"eksen '{name}' yok; tezgâhta yalnız j1, j2, j7") from None

    def _report(self, what: str, ack) -> bool:
        ok = ack.result == Result.OK
        extra = {Result.NO_REFERENCE: ' (önce zero)', Result.BAD_STATE: ' (durum uygun değil; status bak)',
                 Result.ESTOP_ACTIVE: ' (kilitli: clear, sonra zero)', Result.HW_LOOP_OPEN: ' (D13 döngüsü açık)',
                 Result.BUSY: ' (hareket var: önce stop)', Result.LINK_DOWN: ' (hat/ACK yok)',
                 Result.LIMIT: f' (gömülü sınır aşıldı, detay {ack.detail}: 2=hız 3=ivme)'}.get(ack.result, '')
        self.say(f"{what}: {'TAMAM' if ok else 'RED'} -> {ack.result.name}{extra}")
        return ok

    # ------------------------------------------------------------------ komutlar
    def status(self) -> None:
        st = self._status()
        if st is None:
            self.say('hat yok / STATUS gelmiyor')
            return
        fl = [n for n, b in (('HW_LOOP_OPEN', StatusFlag.HW_LOOP_OPEN), ('WATCHDOG_TRIPPED', StatusFlag.WATCHDOG_TRIPPED),
                             ('OPEN_LOOP_ANY', StatusFlag.OPEN_LOOP_ANY), ('HOST_LINK_OK', StatusFlag.HOST_LINK_OK),
                             ('BENCH_MODE', StatusFlag.BENCH_MODE)) if st.flags & b]
        self.say(f'durum {st.state.name}  neden {st.reason.name}  watchdog {st.watchdog_ms} ms  bayraklar {", ".join(fl) or "-"}')
        if not st.flags & StatusFlag.BENCH_MODE:
            self.say('UYARI: BENCH_MODE yok; bu araç hareket komutu göndermez.')
        for ax in self.axes.values():
            a = st.axes[ax.idx] if ax.idx < len(st.axes) else None
            if a is None:
                continue
            flags = [n for n, on in (('moving', a.moving), ('homed', a.homed), ('enabled', a.enabled)) if on]
            deg = f' ≈ {a.pos * 360.0 / ax.spr:.1f}°' if ax.spr else ''
            self.say(f'  {ax.name.upper()}: konum {a.pos} adım{deg} (KESTİRİM, enkoder yok)  [{", ".join(flags) or "-"}]')

    def zero(self, name: str) -> None:
        ax = self._axis(name)
        st = self._need_bench()
        if st is None:
            return
        if st.state not in (State.UNHOMED, State.HOMED):
            self.say(f'REDDEDİLDİ: durum {st.state.name}; sıfırlama yalnız UNHOMED/HOMED iken (ESTOPPED: clear; ENABLED: disable). Komut gönderilmedi.')
            return
        if st.axes[ax.idx].moving:
            self.say('REDDEDİLDİ: eksen hareket ediyor. Komut gönderilmedi.')
            return
        self.say(f'{ax.name.upper()} tezgâh sıfırı: mevcut konum 0 sayılacak. Bu bir referans DEĞİL, yalnız başlangıç noktasıdır.')
        if self.ask(f'motor yükten ayrık, güç kesmeye hazırım: {CONFIRM} yaz > ').strip() != CONFIRM:
            self.say('Onay verilmedi. Komut gönderilmedi.')
            return
        self._report(f'zero {ax.name}', self.link.home(mask=1 << ax.idx))

    def enable(self) -> None:
        st = self._need_bench()
        if st is None:
            return
        if st.state != State.HOMED and st.state != State.ENABLED:
            self.say(f'REDDEDİLDİ: durum {st.state.name}; önce zero. Komut gönderilmedi.')
            return
        if self._report('enable', self.link.enable()):
            self.say('  yalnız sıfırlanan eksenler enerjilendi (bobinler tutuyor, ısınabilir).')

    def move(self, name: str, amount: str) -> None:
        ax = self._axis(name)
        st = self._need_bench()
        if st is None:
            return
        if st.state != State.ENABLED or not st.axes[ax.idx].enabled:
            self.say(f'REDDEDİLDİ: {ax.name.upper()} enerjili değil (durum {st.state.name}); zero + enable gerekir. Komut gönderilmedi.')
            return
        if st.axes[ax.idx].moving:
            self.say('REDDEDİLDİ: eksen hareket ediyor; önce stop. Komut gönderilmedi.')
            return
        try:
            delta = parse_amount(amount, ax)
        except ValueError as exc:
            self.say(f'REDDEDİLDİ: {exc}')
            return
        if delta == 0:
            self.say('REDDEDİLDİ: 0 adım.')
            return
        target = st.axes[ax.idx].pos + delta
        if not ax.lo <= target <= ax.hi:
            self.say(f'REDDEDİLDİ: hedef {target} adım, sınır {ax.lo}..{ax.hi} (firmware ile aynı). Komut gönderilmedi.')
            return
        cap = first_move_cap(ax)
        if ax.idx not in self._moved and abs(delta) > cap:
            self.say(f'REDDEDİLDİ: bu oturumda {ax.name.upper()} ilk hareketi en çok {cap} adım '
                     f'(' + (f'{FIRST_MOVE_DEG:g}°' if ax.spr else 'adım/devir bilinmiyor') + f'); {abs(delta)} istendi. Komut gönderilmedi.')
            return
        if not self._report(f'move {ax.name} {delta:+d} adım -> hedef {target}',
                            self.link.move(ax.idx, target, ax.def_vmax, ax.def_accel)):
            return
        self._moved.add(ax.idx)
        self._wait_done(ax, target, abs(delta))

    def _wait_done(self, ax: Axis, target: int, dist: int) -> None:
        end = time.monotonic() + dist / ax.def_vmax + 2.0 * ax.def_vmax / ax.def_accel + 3.0
        try:
            while time.monotonic() < end:
                st = self._status()
                if st is None:
                    self.say('  hat koptu; firmware watchdog 300 ms içinde keser.')
                    return
                if st.state != State.ENABLED:
                    self.say(f'  durum {st.state.name} (neden {st.reason.name}): hareket kesildi, konum kestirimdir.')
                    return
                a = st.axes[ax.idx]
                if not a.moving and a.pos == target:
                    self.say(f'  vardı: konum {a.pos} adım (KESTİRİM).')
                    return
                time.sleep(0.02)
            self.say('  zaman aşımı: hedefe varış görülmedi; status bak, gerekirse stop/estop.')
        except KeyboardInterrupt:
            self.stop(None)
            self.say('  Ctrl-C: stop gönderildi.')

    def stop(self, name: Optional[str]) -> None:
        """Güvenlik komutu: kapıya takılmaz (hat varsa her zaman gider)."""
        mask = BENCH_MASK if not name else 1 << self._axis(name).idx
        self._report('stop', self.link.stop_axes(mask=mask))

    def estop(self) -> None:
        self.link.estop()
        self.say('ESTOP 3 kez gönderildi (ACK beklenmez). Kilidi açmak için: clear, sonra zero.')

    def clear(self) -> None:
        self._report('clear', self.link.clear())

    def disable(self) -> None:
        self._report('disable', self.link.disable())

    def close(self) -> None:
        """Çıkış/hata: hareketi durdur, DISABLE gönder; olmazsa hattı keserek watchdog'a bırak. Her zaman HwLink'i durdurur."""
        try:
            st = self._status()
            if st is not None and st.state == State.ENABLED:
                self.link.stop_axes(mask=BENCH_MASK)
                for _ in range(20):                      # ivmeli durma <= ~1 sn; BUSY bitene kadar dene
                    if self.link.disable().result in (Result.OK, Result.BAD_STATE, Result.ESTOP_ACTIVE):
                        break
                    time.sleep(0.2)
                else:
                    self.say('UYARI: DISABLE onaylanmadı; hat kesilince firmware watchdog (300 ms) bobinleri keser. Gerekirse GÜCÜ KES.')
        except Exception as exc:                          # noqa: BLE001
            self.say(f'UYARI: kapanışta hata ({exc}); firmware watchdog (300 ms) keser. Gerekirse GÜCÜ KES.')
        finally:
            self.link.stop()

    # ------------------------------------------------------------------ kabuk
    def dispatch(self, words: list) -> bool:
        """Bir komut satırı çalıştırır. False: oturumu kapat."""
        if not words:
            return True
        cmd, args = words[0].lower(), words[1:]
        try:
            if cmd in ('quit', 'exit', 'q'):
                return False
            if cmd == 'help':
                self.say('status | zero <j1|j2|j7> | enable | move <eksen> <adım|derece> | stop [eksen] | estop | clear | disable | quit')
            elif cmd == 'status' and not args:
                self.status()
            elif cmd == 'zero' and len(args) == 1:
                self.zero(args[0])
            elif cmd == 'enable' and not args:
                self.enable()
            elif cmd == 'move' and len(args) == 2:
                self.move(args[0], args[1])
            elif cmd == 'stop' and len(args) <= 1:
                self.stop(args[0] if args else None)
            elif cmd == 'estop' and not args:
                self.estop()
            elif cmd == 'clear' and not args:
                self.clear()
            elif cmd == 'disable' and not args:
                self.disable()
            else:
                self.say(f"anlaşılmadı: {' '.join(words)}  (help)")
        except ValueError as exc:
            self.say(f'REDDEDİLDİ: {exc}')
        return True

    def shell(self, read: Callable[[str], str] = input) -> None:
        self.say('tezgâh kabuğu (help, quit). Çıkışta stop + DISABLE gönderilir.')
        while True:
            try:
                line = read('bench> ')
            except (EOFError, KeyboardInterrupt):
                break
            if not self.dispatch(line.split()):
                break


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
