"""Hub "Tezgâh" (KM3): ``tools/bench/bench.py`` mantığını Hub'dan sürer. GERÇEK ROBOT DEĞİL.

Kurallar (Robot_1, tezgâh modu; docs/YOL_HARITASI.md K9):

* **Yeniden kullanım, kopya yok:** kapılar (BENCH_MODE, döngü, durum), eksen limitleri, ilk-hareket sınırı ve
  mesajlar ``tools/bench/bench.py``'daki ``Bench`` sınıfından gelir; bu modül yalnızca onu sarar (ağ üstünden
  çağrılabilir yapar, konsol ``ask`` yerine onay kutusu verir). Hub limitleri GENİŞLETEMEZ: ``Bench`` ve
  firmware aynen uygular. Hub'ın eklediği tek şey **daha dar** kapılardır (varsayılan kapalı, PIN kipi,
  köprü açıkken ret, girdi doğrulama).
* **COM tek sahipli:** ESP32 köprüsü (``hw.SerialTcpBridge``) açıkken Tezgâh açılmaz (net mesaj); Tezgâh
  açıkken köprü zaten başlatılmaz (Hub başlarken ya biri ya öteki). Bench CLI ile aynı anda açılamaz çünkü
  işletim sistemi portu tek sahibe verir; Hub bunu "port meşgul" mesajıyla bildirir.
* **Güvenli çıkış:** Hub kapanırken ve arayüz ``IDLE_TIMEOUT_S`` boyunca sorgulamayı bırakırsa (telefon
  uyudu, sekme kapandı) ``stop`` + ``DISABLE`` gider; hat koparsa port bırakılır. Kalp atışı kesilirse
  firmware watchdog'u (300 ms) zaten keser: burası ikinci katmandır, ilkinin yerine GEÇMEZ.
* Konum bir KESTİRİMDİR (enkoder yok).
"""
from __future__ import annotations

import importlib.util
import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger('control_hub.bench')
IDLE_TIMEOUT_S = 5.0
MAX_MESSAGES = 40
# Hub'ın kendi girdi süzgeci: bench.parse_amount'tan ÖNCE; "30", "-30", "5deg" (en çok 5 hane)
_AMOUNT_OK = re.compile(r'^[+-]?\d{1,5}(\.\d{1,3})?\s*(deg|d|°)?$', re.IGNORECASE)
COMMANDS = ('zero', 'enable', 'disable', 'move', 'stop', 'clear')


class BenchError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def bench_candidates(here: Path, frozen: Optional[str] = None) -> List[Path]:
    """bench.py'nin olası yerleri. ``here`` bu dosyanın yolu: Docker imajında ``/app/control_hub/hw/...``
    gibi sığ yollarda depo kökü YOKTUR (``parents[4]`` IndexError verirdi: Hub açılışta çökerdi)."""
    out: List[Path] = []
    if frozen:
        out.append(Path(frozen) / 'tools' / 'bench' / 'bench.py')
    parents = here.parents
    if len(parents) > 4:
        out.append(parents[4] / 'tools' / 'bench' / 'bench.py')
    return out


def load_bench_module() -> Any:
    """``tools/bench/bench.py``'yi yoldan yükler (paket değil). .exe'de ``sys._MEIPASS`` altında gömülü."""
    here = bench_candidates(Path(__file__).resolve(), getattr(sys, '_MEIPASS', None))
    for path in here:
        if path.is_file():
            spec = importlib.util.spec_from_file_location('k9_bench_tool', path)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            sys.modules['k9_bench_tool'] = mod
            spec.loader.exec_module(mod)
            return mod
    raise BenchError(503, 'bench_unavailable', 'Tezgâh aracı (tools/bench/bench.py) bu kurulumda yok')


class BenchService:
    def __init__(self, *, enabled: bool = False, serial_url: str = '', baud: int = 115200,
                 bridge_active: Callable[[], bool] = lambda: False,
                 link_factory: Optional[Callable[[], Any]] = None,
                 loader: Callable[[], Any] = load_bench_module,
                 on_event: Optional[Callable[[str, str, str], None]] = None,
                 clock: Callable[[], float] = time.monotonic, idle_timeout_s: float = IDLE_TIMEOUT_S):
        self.enabled, self.serial_url, self.baud = enabled, serial_url, baud
        self._bridge_active, self._link_factory, self._loader = bridge_active, link_factory, loader
        self._on_event, self._clock, self.idle_timeout_s = on_event, clock, idle_timeout_s
        self._lock = threading.RLock()              # durum/bağlantı
        self._cmd_lock = threading.Lock()           # aynı anda tek hareketli komut (stop/estop bunu BEKLEMEZ)
        self._mod: Any = None
        self._bench: Any = None
        self._link: Any = None
        self._messages: List[str] = []
        self._tls = threading.local()               # komut başına yakalama + onay (eşzamanlı stop karışmasın)
        self._seen = clock()
        self._watch: Optional[threading.Thread] = None
        self._halt = threading.Event()
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ yardımcılar
    def _event(self, kind: str, msg: str, level: str = 'info') -> None:
        log.log(30 if level != 'info' else 20, '%s: %s', kind, msg)
        if self._on_event:
            try:
                self._on_event(kind, msg, level)
            except Exception:                        # noqa: BLE001
                log.exception('olay alıcısı hata verdi')

    def _say(self, line: str) -> None:
        with self._lock:
            self._messages.append(line)
            del self._messages[:-MAX_MESSAGES]
        buf = getattr(self._tls, 'buf', None)
        if buf is not None:
            buf.append(line)

    def _ask(self, _prompt: str) -> str:
        """Konsol sorusu yerine onay kutusu: yalnızca bu çağrı ``confirm`` verdiyse bench'in 'EVET'i gider."""
        return self._mod.CONFIRM if getattr(self._tls, 'confirm', False) else ''

    @property
    def is_open(self) -> bool:
        return self._bench is not None

    def summary(self) -> Dict[str, Any]:
        """Hub durumuna girer (G/Ç yok, ucuz): arayüz sekmeyi göstermek için kullanır."""
        return {'enabled': self.enabled, 'open': self.is_open}

    def touch(self) -> None:
        self._seen = self._clock()

    def _status(self) -> Optional[Any]:
        link = self._link
        return link.status if link is not None and link.link_ok else None

    # ------------------------------------------------------------------ açma/kapama
    def open(self) -> Dict[str, Any]:
        with self._lock:
            if not self.enabled:
                raise BenchError(403, 'bench_disabled',
                                 'Tezgâh kapalı (Hub ayarı: Tezgâh varsayılan KAPALIDIR)')
            if self.is_open:
                raise BenchError(409, 'bench_open', 'Tezgâh zaten açık')
            if self._bridge_active():
                raise BenchError(409, 'bridge_active', 'ESP32 köprüsü açık: COM portu tek sahiplidir. '
                                 'Önce köprüyü kapat (Hub\'u köprüsüz başlat), sonra Tezgâh\'ı aç.')
            if not self.serial_url and self._link_factory is None:
                raise BenchError(400, 'no_port', 'Tezgâh için seri port ayarlı değil (--bench-port COM3)')
            mod = self._mod = self._mod or self._loader()
            try:
                link = self._link_factory() if self._link_factory else \
                    mod.HwLink(mod.SerialTransport(self.serial_url, self.baud))
            except Exception as exc:                 # noqa: BLE001 - port yok/meşgul/izin
                raise BenchError(409, 'port_busy', f'Seri port açılamadı ({self.serial_url}): {exc}. '
                                 'Başka bir program (bench CLI, Arduino IDE, seri monitör) tutuyor.') \
                    from exc
            link.start()
            if not link.wait_link(5.0):
                link.stop()
                raise BenchError(504, 'no_link', 'Hat kurulamadı: STATUS gelmiyor (port/baud/kablo, '
                                 'firmware yüklü mü?)')
            self._link = link
            self._bench = mod.Bench(link, self._say, self._ask, None)
            self._messages.clear()
            self.touch()
            self._start_watch()
            bench_mode = self._bench_mode()
            self._event('bench_open', f'Tezgâh açıldı: {self.serial_url or "(test hattı)"}'
                        + ('' if bench_mode else ' — BENCH_MODE YOK: hareket komutu gönderilmez'),
                        'info' if bench_mode else 'warning')
        return self.info()

    def _bench_mode(self) -> bool:
        st = self._status()
        return bool(st is not None and st.flags & self._mod.StatusFlag.BENCH_MODE)

    def close(self) -> None:
        """Hub kapanırken/kullanıcı kapatırken: stop + DISABLE, sonra hat ve port bırakılır."""
        self._halt.set()
        with self._lock:
            bench, self._bench, self._link = self._bench, None, None
        if bench is not None:
            try:
                bench.close()          # stop + DISABLE (olmazsa watchdog), her zaman HwLink.stop()
            except Exception:                         # noqa: BLE001
                log.exception('bench kapanışı hata verdi')
            self._event('bench_close', 'Tezgâh kapatıldı (stop + DISABLE gönderildi)')
        t = self._watch
        if t is not None and t is not threading.current_thread():
            t.join(2)
        self._watch = None

    # ------------------------------------------------------------------ komutlar
    def command(self, cmd: str, axis: Optional[str] = None, amount: Optional[str] = None,
                confirm: bool = False) -> Dict[str, Any]:
        if cmd not in COMMANDS:
            raise BenchError(400, 'bad_command', f'Bilinmeyen komut: {cmd!r}')
        if cmd in ('zero', 'move', 'stop') and axis is not None and axis.lower() not in ('j1', 'j2', 'j7'):
            raise BenchError(400, 'bad_axis', "Eksen j1, j2 ya da j7 olmalı")
        if cmd in ('zero', 'move') and axis is None:
            raise BenchError(400, 'bad_axis', 'Eksen gerekli')
        if cmd == 'move' and (not isinstance(amount, str) or not _AMOUNT_OK.match(amount.strip())):
            raise BenchError(400, 'bad_amount', "Miktar '30', '-30' ya da '5deg' biçiminde olmalı")
        bench = self._bench
        if bench is None:
            raise BenchError(409, 'bench_closed', 'Tezgâh açık değil')
        words = [cmd] + [w for w in (axis, amount) if w]
        if cmd == 'stop' and not axis:
            words = ['stop']
        safety = cmd in ('stop', 'clear', 'disable')
        if not self._cmd_lock.acquire(blocking=False):
            if cmd == 'stop':          # stop hareket sürerken de geçer (bench.stop kapıya takılmaz)
                return self._run(bench, words, confirm)
            raise BenchError(409, 'busy', 'Önceki komut sürüyor (hareket?): önce STOP')
        try:
            return self._run(bench, words, confirm)
        finally:
            self._cmd_lock.release()
            if not safety:
                self.touch()

    def _run(self, bench: Any, words: List[str], confirm: bool) -> Dict[str, Any]:
        self._tls.buf, self._tls.confirm = [], confirm
        try:
            bench.dispatch(words)
            lines = list(self._tls.buf)
        finally:
            self._tls.buf, self._tls.confirm = None, False
        refused = [ln for ln in lines if ln.startswith('REDDEDİLDİ') or ': RED ->' in ln
                   or ln.startswith('Onay verilmedi') or ln.startswith('hat yok')]
        return {'ok': not refused, 'messages': lines, **self.info()}

    def estop(self) -> bool:
        """ACİL DUR: kilitlere, PIN'e ve komut kilidine TAKILMAZ. Bağlantı yoksa hiçbir şey yapmaz."""
        link = self._link
        if link is None:
            return False
        try:
            link.estop()
        except Exception:                             # noqa: BLE001
            log.exception('bench ESTOP gönderilemedi')
            return False
        self._say('ESTOP 3 kez gönderildi (ACK beklenmez). Kilidi açmak için: clear, sonra zero.')
        self._event('bench_estop', 'Tezgâh ACİL DUR gönderildi', 'warning')
        return True

    # ------------------------------------------------------------------ durum
    def info(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {'enabled': self.enabled, 'open': self.is_open, 'serial': self.serial_url,
                               'baud': self.baud, 'bridge_active': bool(self._bridge_active()),
                               'bench_mode': False, 'link_ok': False, 'state': None, 'reason': None,
                               'flags': [], 'axes': [], 'messages': list(self._messages[-MAX_MESSAGES:]),
                               'error': self.last_error, 'idle_timeout_s': self.idle_timeout_s}
        if not self.is_open or self._mod is None:
            return out
        mod, st = self._mod, self._status()
        out['link_ok'] = st is not None
        if st is None:
            return out
        sf = mod.StatusFlag
        names = (('HW_LOOP_OPEN', sf.HW_LOOP_OPEN), ('WATCHDOG_TRIPPED', sf.WATCHDOG_TRIPPED),
                 ('OPEN_LOOP_ANY', sf.OPEN_LOOP_ANY), ('HOST_LINK_OK', sf.HOST_LINK_OK),
                 ('BENCH_MODE', sf.BENCH_MODE))
        out.update(bench_mode=bool(st.flags & mod.StatusFlag.BENCH_MODE), state=st.state.name,
                   reason=st.reason.name, watchdog_ms=st.watchdog_ms,
                   flags=[n for n, b in names if st.flags & b])
        for ax in self._bench.axes.values():
            a = st.axes[ax.idx] if ax.idx < len(st.axes) else None
            if a is None:
                continue
            out['axes'].append({'name': ax.name, 'idx': ax.idx, 'pos': a.pos, 'moving': a.moving,
                                'homed': a.homed, 'enabled': a.enabled, 'lo': ax.lo, 'hi': ax.hi,
                                'spr': ax.spr, 'first_move_cap': mod.first_move_cap(ax),
                                'estimate': True})
        return out

    # ------------------------------------------------------------------ bekçi (ikinci katman)
    def _start_watch(self) -> None:
        self._halt.clear()
        self._watch = threading.Thread(target=self._watch_loop, name='bench-watch', daemon=True)
        self._watch.start()

    def _watch_loop(self) -> None:
        while not self._halt.wait(0.5):
            self.tick()

    def tick(self) -> None:
        """Arayüz sorgulamayı bıraktıysa ya da hat koptuysa güvenli kapanış (testte elle çağrılır)."""
        if not self.is_open:
            return
        st = self._status()
        if st is None:
            self._event('bench_link_lost', 'Tezgâh hattı koptu: port bırakıldı (firmware watchdog\'u keser)',
                        'warning')
            self.close()
            return
        if self._clock() - self._seen > self.idle_timeout_s and st.state.name in ('ENABLED', 'HOMING'):
            self._event('bench_idle', 'Arayüz yanıt vermiyor: stop + DISABLE gönderiliyor', 'warning')
            self.close()
