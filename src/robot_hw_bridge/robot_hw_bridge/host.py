"""Host tarafı: ``HwLink`` (B2'de ROS düğümü bunu sarmalar; ROS'a bağımlılığı YOK).

* Kalp atışı (100 ms) gönderir; MCU'dan STATUS gelmezse (``link_timeout``, 200-500 ms) hat KOPTU sayılır
  ve ``on_link_lost`` çağrılır: üst katman bunu yazılım ACİL DUR'u olarak ele almalıdır.
* Komutlar cmd_id + ACK ile güvenilir hale getirilir (zaman aşımında aynı cmd_id RETRY bayrağıyla
  yeniden gönderilir; MCU yinelemeyi yeniden işlemez, aynı ACK'i yeniden verir).
* ACİL DUR komutu ACK beklemeden **hemen ve art arda** gönderilir.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from robot_hw_bridge.codec import Frame, FrameDecoder, SeqTracker, encode_frame
from robot_hw_bridge.protocol import (
    HEARTBEAT_PERIOD_MS,
    WATCHDOG_MAX_MS,
    WATCHDOG_MIN_MS,
    Ack,
    ConfigKey,
    Fault,
    Flag,
    Hello,
    MsgType,
    ProtocolError,
    Result,
    State,
    Status,
    pack_cmd,
)
from robot_hw_bridge.transport import Transport, TransportClosed


class LinkMonitor:
    """Karşı taraftan geçerli çerçeve gelmeyince ``timeout`` sonra hattı kopuk ilan eder (saf mantık)."""

    def __init__(self, timeout_s: float, clock: Callable[[], float] = time.monotonic):
        if not WATCHDOG_MIN_MS / 1000 <= timeout_s <= WATCHDOG_MAX_MS / 1000:
            raise ValueError(f'hat zaman aşımı {WATCHDOG_MIN_MS}-{WATCHDOG_MAX_MS} ms olmalı')
        self.timeout, self._clock = timeout_s, clock
        self._last: Optional[float] = None
        self.up = False

    def touch(self) -> bool:
        """Çerçeve geldi. Hat bu çerçeveyle (yeniden) kurulduysa True."""
        self._last = self._clock()
        was_up, self.up = self.up, True
        return not was_up

    def check(self) -> bool:
        """Zaman aşımı oldu mu? Hat az önce koptuysa True (bir kez)."""
        if self.up and self._last is not None and self._clock() - self._last > self.timeout:
            self.up = False
            return True
        return False

    def age(self) -> Optional[float]:
        return None if self._last is None else self._clock() - self._last


@dataclass
class LinkStats:
    tx_frames: int = 0
    rx_frames: int = 0
    retries: int = 0
    cmd_timeouts: int = 0
    link_losses: int = 0


class HwLink:
    def __init__(self, transport: Transport, *, heartbeat_s: float = HEARTBEAT_PERIOD_MS / 1000,
                 link_timeout_s: float = 0.3, ack_timeout_s: float = 0.15, retries: int = 3,
                 key: Optional[bytes] = None, clock: Callable[[], float] = time.monotonic,
                 on_status: Optional[Callable[[Status], None]] = None,
                 on_fault: Optional[Callable[[Fault], None]] = None,
                 on_link_lost: Optional[Callable[[], None]] = None,
                 on_link_up: Optional[Callable[[], None]] = None):
        self.transport, self.key = transport, key
        self.heartbeat_s, self.ack_timeout, self.retries = heartbeat_s, ack_timeout_s, retries
        self.monitor = LinkMonitor(link_timeout_s, clock)
        self.on_status, self.on_fault = on_status, on_fault
        self.on_link_lost, self.on_link_up = on_link_lost, on_link_up
        self.decoder = FrameDecoder(key)
        self.rx_seq = SeqTracker(strict=key is not None)
        self.stats = LinkStats()
        self.hello: Optional[Hello] = None
        self.status: Optional[Status] = None
        self.faults: List[Fault] = []
        self.peer_ack: int = 0
        self._tx_seq = 0
        self._cmd_id = 0
        self._tx_lock = threading.Lock()
        self._cv = threading.Condition()
        self._acks: Dict[int, Ack] = {}
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self.closed_reason: Optional[str] = None

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self) -> 'HwLink':
        self._threads = [threading.Thread(target=self._rx_loop, name='hw-rx', daemon=True),
                         threading.Thread(target=self._tx_loop, name='hw-hb', daemon=True)]
        for t in self._threads:
            t.start()
        return self

    def stop(self, close_transport: bool = True) -> None:
        """Host tarafını durdurur. ``close_transport=False``: iş parçacıkları (kalp atışı dahil) durur ama
        hat açık kalır; "host süreci öldü" senaryosunu (MCU watchdog'u) denemek için."""
        self._stop.set()
        if close_transport:
            try:
                self.transport.close()
            except Exception:                              # noqa: BLE001
                pass
        for t in self._threads:
            t.join(1)

    @property
    def link_ok(self) -> bool:
        return self.monitor.up

    @property
    def state(self) -> Optional[State]:
        return self.status.state if self.status else None

    # ------------------------------------------------------------------ gönderim
    def _send(self, mtype: MsgType, payload: bytes = b'', flags: int = 0) -> None:
        with self._tx_lock:
            ack = self.rx_seq.last if self.rx_seq.last is not None else 0
            data = encode_frame(Frame(mtype, self._tx_seq, ack, payload, flags), self.key)
            self._tx_seq = (self._tx_seq + 1) & 0xFFFF
            self.stats.tx_frames += 1
            self.transport.send(data)

    def _tx_loop(self) -> None:
        try:
            self._send(MsgType.HELLO_REQ, bytes([1, 0, 0, 0, 0]))
            while not self._stop.is_set():
                self._send(MsgType.HEARTBEAT)
                self._check_link()
                self._stop.wait(self.heartbeat_s)
        except TransportClosed as exc:
            self._closed(str(exc))

    def _check_link(self) -> None:
        if self.monitor.check():
            self.stats.link_losses += 1
            if self.on_link_lost:
                self.on_link_lost()

    def _closed(self, reason: str) -> None:
        if self.closed_reason is None:
            self.closed_reason = reason
            if self.monitor.up:
                self.monitor.up = False
                self.stats.link_losses += 1
                if self.on_link_lost:
                    self.on_link_lost()
            with self._cv:
                self._cv.notify_all()

    # ------------------------------------------------------------------ alım
    def _rx_loop(self) -> None:
        try:
            while not self._stop.is_set():
                data = self.transport.recv(512, 0.02)
                if data:
                    for f in self.decoder.feed(data):
                        self._on_frame(f)
                self._check_link()
        except TransportClosed as exc:
            self._closed(str(exc))

    def _on_frame(self, f: Frame) -> None:
        _gap, fresh = self.rx_seq.observe(f.seq)
        if not fresh:
            return
        self.stats.rx_frames += 1
        self.peer_ack = f.ack
        try:
            t = MsgType(f.type)
            if t == MsgType.STATUS:
                self.status = Status.unpack(f.payload)
                if self.monitor.touch() and self.on_link_up:
                    self.on_link_up()
                if self.on_status:
                    self.on_status(self.status)
            elif t == MsgType.HELLO:
                self.hello = Hello.unpack(f.payload)
                self.rx_seq = SeqTracker(strict=self.key is not None)    # MCU yeniden başlamış olabilir
                self.rx_seq.observe(f.seq)
            elif t == MsgType.ACK:
                a = Ack.unpack(f.payload)
                with self._cv:
                    self._acks[a.cmd_id] = a
                    self._cv.notify_all()
            elif t == MsgType.FAULT:
                fault = Fault.unpack(f.payload)
                self.faults.append(fault)
                if self.on_fault:
                    self.on_fault(fault)
        except (ValueError, ProtocolError):
            return                                          # bozuk yük: yok say (sayaç: decoder dışı)

    # ------------------------------------------------------------------ komutlar
    def _next_id(self) -> int:
        self._cmd_id = (self._cmd_id + 1) & 0xFFFF
        return self._cmd_id

    def command(self, mtype: MsgType, **kw: int) -> Ack:
        """Komutu gönderir, ACK bekler (zaman aşımında RETRY ile yeniden dener). Bağlantı yoksa
        ``Result.LINK_DOWN`` döner; hiçbir zaman exception fırlatmaz."""
        if not self.link_ok:
            return Ack(0, Result.LINK_DOWN)
        cid = self._next_id()
        payload = pack_cmd(mtype, cid, **kw)
        for attempt in range(self.retries + 1):
            try:
                self._send(mtype, payload, Flag.RETRY if attempt else 0)
            except TransportClosed:
                return Ack(cid, Result.LINK_DOWN)
            if attempt:
                self.stats.retries += 1
            end = time.monotonic() + self.ack_timeout
            with self._cv:
                while cid not in self._acks and self.closed_reason is None:
                    left = end - time.monotonic()
                    if left <= 0:
                        break
                    self._cv.wait(left)
                if cid in self._acks:
                    return self._acks.pop(cid)
                if self.closed_reason is not None:
                    return Ack(cid, Result.LINK_DOWN)
        self.stats.cmd_timeouts += 1
        return Ack(cid, Result.LINK_DOWN, 1)               # detail=1: ACK gelmedi (zaman aşımı)

    def home(self, mask: int = 0x3F) -> Ack:
        return self.command(MsgType.CMD_HOME, mask=mask)

    def enable(self) -> Ack:
        return self.command(MsgType.CMD_ENABLE)

    def disable(self) -> Ack:
        return self.command(MsgType.CMD_DISABLE)

    def move(self, axis: int, target: int, vmax: int, accel: int) -> Ack:
        return self.command(MsgType.CMD_MOVE, axis=axis, target=target, vmax=vmax, accel=accel)

    def stop_axes(self, mask: int = 0xFF) -> Ack:
        return self.command(MsgType.CMD_STOP, mask=mask)

    def clear(self) -> Ack:
        return self.command(MsgType.CMD_CLEAR)

    def config(self, key: ConfigKey, value: int, axis: int = 0) -> Ack:
        return self.command(MsgType.CMD_CONFIG, key=int(key), axis=axis, value=value)

    def estop(self, repeats: int = 3) -> None:
        """Yazılım ACİL DUR: ACK beklemeden, art arda ``repeats`` kez (kayıp çerçeveye karşı).
        Hat kopmuşsa yine dener; MCU'nun kendi watchdog'u son çaredir."""
        cid = self._next_id()
        payload = pack_cmd(MsgType.CMD_ESTOP, cid)
        for i in range(repeats):
            try:
                self._send(MsgType.CMD_ESTOP, payload, Flag.RETRY if i else 0)
            except TransportClosed:
                return

    # ------------------------------------------------------------------ yardımcı
    def wait_for(self, pred: Callable[[Status], bool], timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = self.status
            if st is not None and pred(st):
                return True
            time.sleep(0.01)
        return False

    def wait_link(self, timeout: float = 3.0) -> bool:
        return self.wait_for(lambda s: True, timeout) and self.link_ok
