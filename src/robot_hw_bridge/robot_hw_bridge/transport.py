"""Taşıma katmanı: bayt akışı arayüzü + soket, bellek-içi (hata enjeksiyonlu) ve simülatör sunucusu.

Host tarafı (``HwLink``) yalnızca ``Transport`` arayüzünü bilir; aynı kod USB-seri köprüsünün TCP ucuna
(``SocketTransport``), testlerdeki bellek-içi hatta ya da ileride Wi-Fi'ya bağlanır.
"""
from __future__ import annotations

import queue
import socket
import threading
import time
from collections import deque
from typing import Deque, Optional, Protocol, Tuple

from robot_hw_bridge.sim import Esp32Sim


class TransportClosed(Exception):
    """Karşı uç kapandı / hat koptu."""


class Transport(Protocol):
    def send(self, data: bytes) -> None: ...

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """En çok ``max_bytes`` bayt; zaman aşımında ``b''``; hat kopunca ``TransportClosed``."""

    def close(self) -> None: ...


class SocketTransport:
    def __init__(self, host: str, port: int, connect_timeout: float = 3.0,
                 auth_token: Optional[str] = None):
        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.settimeout(None)
        self._lock = threading.Lock()
        if auth_token is not None:
            self.send(f'HUBBRIDGE-AUTH {auth_token}\n'.encode())

    def send(self, data: bytes) -> None:
        try:
            with self._lock:
                self._sock.sendall(data)
        except OSError as exc:
            raise TransportClosed(str(exc)) from exc

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(max_bytes)
        except socket.timeout:
            return b''
        except OSError as exc:
            raise TransportClosed(str(exc)) from exc
        if not data:
            raise TransportClosed('karşı uç kapattı')
        return data

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


class _Pipe:
    """Tek yönlü bayt borusu; çerçeve (0x00 ayraçlı) düzeyinde hata enjekte edebilir (test için)."""

    def __init__(self) -> None:
        self._q: 'queue.Queue[Optional[bytes]]' = queue.Queue()
        self._pending = bytearray()
        self.corrupt: Deque[int] = deque()      # sıradaki çerçevede bozulacak bayt konumları
        self.drop = 0                           # sıradaki bu kadar çerçeveyi at
        self.closed = False
        self.frames = 0

    def write(self, data: bytes) -> None:
        if self.closed:
            raise TransportClosed('hat kapalı')
        self._pending += data
        while 0 in self._pending:
            i = self._pending.index(0)
            frame = bytes(self._pending[:i + 1])
            del self._pending[:i + 1]
            self.frames += 1
            if self.drop > 0:
                self.drop -= 1
                continue
            if self.corrupt and len(frame) > 2:
                pos = self.corrupt.popleft() % (len(frame) - 1)
                frame = frame[:pos] + bytes([frame[pos] ^ 0x5A or 1]) + frame[pos + 1:]
            self._q.put(frame)

    def read(self, max_bytes: int, timeout: float) -> bytes:
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            if self.closed:
                raise TransportClosed('hat kapalı') from None
            return b''
        if item is None:
            raise TransportClosed('hat kapalı')
        return item

    def close(self) -> None:
        self.closed = True
        self._q.put(None)


class _End:
    def __init__(self, tx: _Pipe, rx: _Pipe):
        self._tx, self._rx = tx, rx

    def send(self, data: bytes) -> None:
        self._tx.write(data)

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        return self._rx.read(max_bytes, timeout)

    def close(self) -> None:
        self._tx.close()
        self._rx.close()


class LoopbackLink:
    """Bellek-içi çift yönlü hat: ``host`` ve ``device`` uçları. ``to_device`` / ``to_host`` boruları
    ``drop`` ve ``corrupt`` ile çerçeve kaybı/bozulması enjekte eder."""

    def __init__(self) -> None:
        self.to_device, self.to_host = _Pipe(), _Pipe()
        self.host: Transport = _End(self.to_device, self.to_host)
        self.device: Transport = _End(self.to_host, self.to_device)

    def cut(self) -> None:
        """USB çekildi: iki yön de kapanır."""
        self.to_device.close()
        self.to_host.close()


class SimRunner:
    """``Esp32Sim``'i gerçek zamanda bir ``Transport`` uç noktasına bağlar (arka plan iş parçacığı)."""

    def __init__(self, sim: Esp32Sim, transport: Transport, tick_s: float = 0.005):
        self.sim, self.transport, self.tick = sim, transport, tick_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='esp32-sim', daemon=True)
        self.error: Optional[str] = None
        self._paused = threading.Event()

    def start(self) -> 'SimRunner':
        self._thread.start()
        return self

    def pause(self) -> None:
        """Simüle MCU'yu dondur (USB takılı ama firmware kilitlendi): hiç yanıt/STATUS yok."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self.transport.close()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    time.sleep(self.tick)
                    continue
                data = self.transport.recv(512, self.tick)
                if data:
                    self.sim.feed(data)
                out = self.sim.poll()
                if out:
                    self.transport.send(out)
        except TransportClosed as exc:
            self.error = str(exc)


class SimServer:
    """Simülatörü TCP sunucusu olarak yayınlar: bir istemci bağlanır, baytlar çerçeve akışıdır.

    Hub köprüsü buna ``socket://host:port`` seri adresiyle bağlanır (gerçek COM portu yerine); Windows'ta
    da çalışır. İstemci kopsa MCU çalışmaya devam eder (gerçek USB gibi): watchdog devreye girer.
    ``drop_client()`` USB çekmeyi taklit eder."""

    def __init__(self, sim: Esp32Sim, host: str = '127.0.0.1', port: int = 0, tick_s: float = 0.005):
        self.sim, self.tick = sim, tick_s
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(1)
        self._srv.settimeout(0.1)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._client: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self.connections = 0
        self._thread = threading.Thread(target=self._run, name='esp32-sim-server', daemon=True)

    def start(self) -> 'SimServer':
        self._thread.start()
        return self

    def drop_client(self) -> None:
        with self._lock:
            c, self._client = self._client, None
        if c is not None:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def stop(self) -> None:
        self._stop.set()
        self.drop_client()
        self._thread.join(2)
        self._srv.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                client = self._client
            if client is None:
                try:
                    conn, _ = self._srv.accept()
                except (socket.timeout, OSError):
                    self._tick(None)
                    continue
                conn.settimeout(self.tick)
                with self._lock:
                    self._client = conn
                self.connections += 1
                continue
            self._tick(client)

    def _tick(self, client: Optional[socket.socket]) -> None:
        if client is None:
            self.sim.feed(b'')
            self.sim.poll()                       # MCU istemci olmasa da yaşar (watchdog, fizik)
            return
        try:
            data = client.recv(512)
            if data == b'':
                self.drop_client()
                return
            self.sim.feed(data)
        except socket.timeout:
            pass
        except OSError:
            self.drop_client()
            return
        out = self.sim.poll()
        if out:
            try:
                client.sendall(out)
            except OSError:
                self.drop_client()


def parse_hostport(text: str, default_host: str = '127.0.0.1') -> Tuple[str, int]:
    host, _, port = text.rpartition(':')
    if not port.isdigit():
        raise ValueError(f'host:port bekleniyor: {text!r}')
    return host or default_host, int(port)


__all__ = ['LoopbackLink', 'SimRunner', 'SimServer', 'SocketTransport', 'Transport',
           'TransportClosed', 'parse_hostport']
