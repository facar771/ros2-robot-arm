"""Faz 3 pot telemetrisi (NodeMCU, TCP 8771): AYRI çözücü + tazelik kapısı (ROS'suz).

Çerçeve şeması: firmware/esp_relay/lib/k9telem/k9_telem.h (ver 0x54, 14 bayt, COBS+0x00, CRC-16).
``codec.py`` (Uno protokolü, ver 1) buraya karışmaz ve ona dokunulmadı: iki çözücü birbirinin çerçevesini
"yabancı çerçeve" diye atar.

KURALLAR (Robot_1):
* Bir kanalın verisi ``STALE_S`` (500 ms) içinde gelmediyse, GEÇERSİZ bayrağı taşıyorsa, ya da iki örneği
  arasındaki ESP zamanı (t_ms) 500 ms'yi aşıyorsa o kanal "veri yok" sayılır.
  "Veri yok" denetim kapalı demek DEĞİLDİR.
* ``required`` kanal "veri yok" ise kapı KAPANIR: köprü kontrollü durdurur, yeni hareket komutlarını
  reddeder (homed korunur; /api/stop ve API kilidi yok). Kapı, kanal ``REOPEN_S`` (1 s)
  KESİNTİSİZ taze+geçerli gelince açılır; açılma kendiliğinden hareket başlatmaz.
* ``required`` varsayılan False: o kanal yalnız görünür (state), kapıyı etkilemez.
* Telemetri yalnız DURDURMA yönünde etki eder; hiçbir koruma devre dışı kalmaz, hiçbir sınır genişlemez.

Bu bir ÖLÇÜMDÜR ama güvenlik düzeneği değildir; yazılım durdurması donanım acil durdurmanın yerine geçmez."""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import cobs
from .crc import crc16

VER = 0x54
TYPE_POT_SAMPLE = 0x20
FLAG_INVALID = 0x01
RAW_LEN = 14
MAX_WIRE = 64                 # bundan uzun, ayraçsız bayt yığını ayraç kaybıdır: atılır
STALE_S = 0.5                 # SABİT: parametre değil, gevşetilemez
REOPEN_S = 1.0                # kapı bu kadar KESİNTİSİZ taze+geçerli veriden sonra açılır
GAP_MS = 500                  # aynı kanalın iki örneği arası ESP zamanı sınırı (TCP birikmesi önlemi)
PORT = 8771


@dataclass(frozen=True)
class PotSample:
    seq: int
    t_ms: int
    ch: int
    flags: int
    raw: int

    @property
    def invalid(self) -> bool:
        return bool(self.flags & FLAG_INVALID)


class PotDecoder:
    """Bayt akışından pot örneği çıkarır. Bozuk/yabancı çerçeve atılır ve neden sayacına yazılır."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.stats: Dict[str, int] = {'frames': 0, 'bad_cobs': 0, 'bad_len': 0, 'bad_ver': 0, 'bad_type': 0,
                                      'bad_crc': 0, 'overflow': 0}

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> List[PotSample]:
        out: List[PotSample] = []
        for b in data:
            if b != 0:
                self._buf.append(b)
                if len(self._buf) > MAX_WIRE:
                    self._buf.clear()
                    self.stats['overflow'] += 1
                continue
            if not self._buf:
                continue
            wire = bytes(self._buf)
            self._buf.clear()
            sample = self._decode(wire)
            if sample is not None:
                self.stats['frames'] += 1
                out.append(sample)
        return out

    def _decode(self, wire: bytes) -> Optional[PotSample]:
        try:
            raw = cobs.decode(wire)
        except cobs.CobsError:
            self.stats['bad_cobs'] += 1
            return None
        if len(raw) != RAW_LEN:
            self.stats['bad_len'] += 1
            return None
        if raw[0] != VER:
            self.stats['bad_ver'] += 1
            return None
        if raw[1] != TYPE_POT_SAMPLE:
            self.stats['bad_type'] += 1
            return None
        if crc16(raw[:12]) != struct.unpack_from('<H', raw, 12)[0]:
            self.stats['bad_crc'] += 1
            return None
        seq, t_ms, ch, flags, val = struct.unpack_from('<HIBBH', raw, 2)
        return PotSample(seq, t_ms, ch, flags, val)


@dataclass(frozen=True)
class PotChannelCfg:
    channel: int
    joint: str
    required: bool = False


def pot_channels_from_profile(hw: Any) -> List[PotChannelCfg]:
    """Profilden pot kanallarını OKUR (profil Robot_2'nin; bu köprü yazmaz, şema icat etmez).

    Beklenen sözleşme: bir eksende ``pot`` özniteliği varsa ve ``channel`` (int) ile ``required`` (bool,
    yoksa False) taşıyorsa kanal vardır. Alan profilde henüz yoksa liste boş döner: özellik etkisizdir."""
    out: List[PotChannelCfg] = []
    for ax in getattr(hw, 'axes', ()):
        pot = getattr(ax, 'pot', None)
        ch = getattr(pot, 'channel', None)
        if isinstance(ch, int):
            out.append(PotChannelCfg(ch, ax.joint, bool(getattr(pot, 'required', False))))
    return out


class _Channel:
    def __init__(self, cfg: PotChannelCfg):
        self.cfg = cfg
        self.last_rx: Optional[float] = None
        self.last_t_ms: Optional[int] = None
        self.last_raw: Optional[int] = None
        self.last_invalid = False
        self.good_since: Optional[float] = None
        self.lost_reason = 'veri hiç gelmedi'

    def link_down(self) -> None:
        self.last_rx = self.last_t_ms = None
        self.good_since = None
        self.last_invalid = False
        self.lost_reason = 'telemetri bağlantısı yok'

    def on_sample(self, s: PotSample, now: float, stale_s: float) -> None:
        bad: Optional[str] = None
        if s.invalid:
            bad = 'GEÇERSİZ bayrağı (kablo kopuk / uçta)'
        elif self.last_t_ms is not None and ((s.t_ms - self.last_t_ms) & 0xFFFFFFFF) > GAP_MS:
            bad = 'örnekler arası ESP zamanı >500 ms (kesinti ya da birikmiş çerçeve)'
        elif self.last_rx is not None and now - self.last_rx > stale_s:
            bad = f'veri {now - self.last_rx:.2f} s kesildi'
        self.last_rx, self.last_t_ms, self.last_raw = now, s.t_ms, s.raw
        self.last_invalid = s.invalid
        if bad is not None:
            self.good_since = None
            self.lost_reason = bad
        elif self.good_since is None:
            self.good_since = now

    def evaluate(self, now: float, stale_s: float, reopen_s: float) -> Optional[str]:
        """None = kanal OK (kapıyı açabilir); aksi halde "veri yok" nedeni."""
        if self.last_rx is None:
            self.good_since = None
            return self.lost_reason
        age = now - self.last_rx
        if age > stale_s:
            self.good_since = None
            self.lost_reason = f'bayat ({age:.2f} s > {stale_s:g} s)'
            return self.lost_reason
        if self.last_invalid:
            self.good_since = None
            return self.lost_reason
        if self.good_since is None:
            return self.lost_reason
        held = now - self.good_since
        if held < reopen_s:
            return f'yeniden açılma bekleniyor (kesintisiz {held:.1f}/{reopen_s:g} s)'
        return None


class PotMonitor:
    """Kanal başına tazelik/geçerlilik; ``required`` kanallar için kapı. İş parçacığı güvenlidir."""

    def __init__(self, channels: List[PotChannelCfg], *, clock: Callable[[], float] = time.monotonic,
                 stale_s: float = STALE_S, reopen_s: float = REOPEN_S):
        if stale_s > STALE_S or reopen_s < REOPEN_S:
            raise ValueError('telemetri eşikleri gevşetilemez (en çok 0.5 s bayatlık, en az 1 s açılma)')
        self._clock, self._stale_s, self._reopen_s = clock, stale_s, reopen_s
        self._lock = threading.Lock()
        self._ch: Dict[int, _Channel] = {c.channel: _Channel(c) for c in channels}
        self.unknown_channel = 0
        self.connected = False

    @property
    def has_required(self) -> bool:
        return any(c.cfg.required for c in self._ch.values())

    def link_up(self) -> None:
        with self._lock:
            self.connected = True

    def link_down(self) -> None:
        with self._lock:
            self.connected = False
            for c in self._ch.values():
                c.link_down()

    def on_sample(self, s: PotSample) -> None:
        now = self._clock()
        with self._lock:
            ch = self._ch.get(s.ch)
            if ch is None:
                self.unknown_channel += 1
                return
            ch.on_sample(s, now, self._stale_s)

    def blocked(self) -> Optional[str]:
        """Hareket komutlarını durduran ``required`` kanalların açıklaması; None = kapı açık."""
        now = self._clock()
        with self._lock:
            why = []
            for c in self._ch.values():
                if not c.cfg.required:
                    continue
                reason = c.evaluate(now, self._stale_s, self._reopen_s)
                if reason is not None:
                    why.append(f'{c.cfg.joint} (kanal {c.cfg.channel}): {reason}')
        if not why:
            return None
        return 'pot telemetrisi gerekli ve yok: ' + '; '.join(why)

    def snapshot(self) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            chans = {}
            for c in self._ch.values():
                reason = c.evaluate(now, self._stale_s, self._reopen_s)
                chans[c.cfg.joint] = {
                    'channel': c.cfg.channel, 'required': c.cfg.required,
                    'ok': reason is None, 'reason': reason, 'raw': c.last_raw,
                    'age_s': None if c.last_rx is None else round(now - c.last_rx, 3)}
            return {'connected': self.connected, 'channels': chans, 'unknown_channel': self.unknown_channel}


class PotLink:
    """TCP 8771'e SALT OKUNUR bağlanır (hiçbir bayt göndermez), kopunca yeniden bağlanır."""

    def __init__(self, host: str, port: int, monitor: PotMonitor, *, connect_timeout: float = 2.0,
                 retry_s: float = 1.0):
        self.host, self.port, self.monitor = host, port, monitor
        self.connect_timeout, self.retry_s = connect_timeout, retry_s
        self.decoder = PotDecoder()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='pot-telemetry', daemon=True)

    def start(self) -> 'PotLink':
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(3)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            except OSError:
                self.monitor.link_down()
                self._stop.wait(self.retry_s)
                continue
            sock.settimeout(0.1)
            self.decoder.reset()
            self.monitor.link_up()
            try:
                while not self._stop.is_set():
                    try:
                        data = sock.recv(512)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    for s in self.decoder.feed(data):
                        self.monitor.on_sample(s)
            except OSError:
                pass
            finally:
                sock.close()
                self.monitor.link_down()
