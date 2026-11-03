"""COM ↔ TCP köprüsü: seri portu (ESP32, USB) açar ve TEK bir TCP istemcisine bayt-bayt aktarır.

Neden: Windows'ta COM portunu yalnızca .exe açabilir; Docker'daki ROS düğümü (B2) ona
``host.docker.internal:<port>`` ile TCP üzerinden bağlanır. Köprü **çerçeve çözmez** (CRC/seq/watchdog
uçlar arasındadır: ROS düğümü ↔ ESP32); yalnızca baytları taşır ve hat durumunu doğru bildirir:

* COM koparsa (USB çekildi) TCP istemcisi HEMEN kapatılır (istemci "hat koptu" görsün) ve köprü portu
  yeniden açmayı dener.
* TCP istemcisi koparsa ESP32'ye hiçbir şey eklenmez: kalp atışı kesildiği için ESP32'nin kendi
  watchdog'u (200-500 ms) motorları durdurur. Köprü kendiliğinden kalp atışı ÜRETMEZ.
* Tek istemci: ikinci bağlantı reddedilir (iki denetleyici aynı motorlara komut veremez).
* Güvenlik: varsayılan yalnızca ``127.0.0.1``. Başka adreste dinlemek için ``token`` ZORUNLU (ilk satır
  ``HUBBRIDGE-AUTH <token>\\n``); token şifrelenmeden gider: yalnızca güvenilir ağda kullan.
"""
from __future__ import annotations

import hmac
import logging
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger('control_hub.hw')
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
AUTH_PREFIX = b'HUBBRIDGE-AUTH '
DEFAULT_BAUD = 921600
DEFAULT_PORT = 8770
EventFn = Callable[[str, str, str], None]          # (tür, mesaj, seviye)


class BridgeError(Exception):
    """Köprü başlatılamadı (geçersiz ayar, port dolu...)."""


def parse_listen(text: str) -> Tuple[str, int]:
    host, _, port = text.rpartition(':')
    if not port.isdigit():
        raise ValueError(f'HOST:PORT bekleniyor: {text!r}')
    return host or '127.0.0.1', int(port)


def list_serial_ports() -> List[str]:
    """Sistemdeki seri portlar (Windows: ``COM3``...). pyserial yoksa/okunamazsa boş liste."""
    try:
        from serial.tools import list_ports
        return sorted(p.device for p in list_ports.comports())
    except Exception:                                  # noqa: BLE001
        return []


def _default_opener(url: str, baud: int, timeout: float) -> Any:
    import serial
    return serial.serial_for_url(url, baudrate=baud, timeout=timeout, write_timeout=1.0)


class SerialTcpBridge:
    def __init__(self, serial_url: str, *, baud: int = DEFAULT_BAUD,
                 listen: Tuple[str, int] = ('127.0.0.1', DEFAULT_PORT), token: Optional[str] = None,
                 reopen_s: float = 1.0, read_timeout_s: float = 0.05,
                 opener: Callable[[str, int, float], Any] = _default_opener,
                 on_event: Optional[EventFn] = None, auth_timeout_s: float = 3.0):
        if not serial_url:
            raise BridgeError('seri port boş')
        host, port = listen
        if host not in LOOPBACK and not token:
            raise BridgeError(f'{host} üzerinde dinlemek için token zorunlu (motor komutları açığa çıkar)')
        if not 0 <= port <= 65535 or not 300 <= baud <= 4_000_000:
            raise BridgeError('port ya da baud geçersiz')
        self.serial_url, self.baud, self.listen_host = serial_url, baud, host
        self._port_req, self.token = port, token
        self.reopen_s, self.read_timeout = reopen_s, read_timeout_s
        self._opener, self._on_event, self._auth_timeout = opener, on_event, auth_timeout_s
        self._stop = threading.Event()
        self._lock = threading.Lock()                  # istemci + durum
        self._ser_lock = threading.Lock()              # seri yazma
        self._ser: Any = None
        self._client: Optional[socket.socket] = None
        self._srv: Optional[socket.socket] = None
        self._threads: List[threading.Thread] = []
        self.listen_port = port
        self.stats: Dict[str, Any] = {
            'serial_connected': False, 'client_connected': False, 'bytes_to_serial': 0,
            'bytes_to_client': 0, 'dropped_no_client': 0, 'reopens': 0, 'clients_total': 0,
            'refused': 0, 'auth_failures': 0, 'last_error': None}

    # ------------------------------------------------------------------ yaşam döngüsü
    def start(self) -> 'SerialTcpBridge':
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if _reuse_ok():
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.listen_host if self.listen_host != 'localhost' else '127.0.0.1',
                      self._port_req))
        except OSError as exc:
            srv.close()
            raise BridgeError(f'köprü portu açılamadı ({self.listen_host}:{self._port_req}): {exc}') \
                from exc
        srv.listen(2)
        srv.settimeout(0.2)
        self._srv = srv
        self.listen_port = srv.getsockname()[1]
        for name, fn in (('hw-bridge-serial', self._serial_loop), ('hw-bridge-accept', self._accept_loop)):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self._event('bridge_started', f'Köprü: {self.serial_url} ↔ {self.listen_host}:{self.listen_port}')
        return self

    def stop(self) -> None:
        self._stop.set()
        self._drop_client('köprü durduruluyor', quiet=True)
        with self._ser_lock:
            self._close_serial()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(2)
        self._event('bridge_stopped', 'Köprü durduruldu')

    def status(self) -> Dict[str, Any]:
        with self._lock:
            out = dict(self.stats)
        out.update(serial=self.serial_url, baud=self.baud,
                   listen=f'{self.listen_host}:{self.listen_port}', authenticated=bool(self.token))
        return out

    def _event(self, kind: str, message: str, level: str = 'info') -> None:
        log.log(30 if level == 'warning' else 20, '%s: %s', kind, message)
        if self._on_event:
            try:
                self._on_event(kind, message, level)
            except Exception:                          # noqa: BLE001 - olay alıcısı köprüyü düşürmez
                log.exception('olay alıcısı hata verdi')

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self.stats.update(kw)

    def _bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] += n

    # ------------------------------------------------------------------ seri taraf
    def _close_serial(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:                          # noqa: BLE001
                pass

    def _serial_loop(self) -> None:
        first = True
        while not self._stop.is_set():
            try:
                ser = self._opener(self.serial_url, self.baud, self.read_timeout)
            except Exception as exc:                   # noqa: BLE001 - port yok/meşgul/izin
                self._set(last_error=f'seri port açılamadı: {exc}', serial_connected=False)
                if first:
                    self._event('serial_open_failed', f'{self.serial_url} açılamadı: {exc}', 'warning')
                    first = False
                self._stop.wait(self.reopen_s)
                continue
            first = False
            with self._ser_lock:
                self._ser = ser
            self._set(serial_connected=True, last_error=None)
            self._event('serial_open', f'{self.serial_url} açıldı ({self.baud} baud)')
            reason = self._pump_serial(ser)
            with self._ser_lock:
                self._close_serial()
            self._set(serial_connected=False, last_error=reason)
            self._drop_client(f'seri hat koptu: {reason}')
            if self._stop.is_set():
                break
            self._bump('reopens')
            self._event('serial_lost', f'{self.serial_url} koptu ({reason}); yeniden denenecek', 'warning')
            self._stop.wait(self.reopen_s)

    def _pump_serial(self, ser: Any) -> str:
        """Seri porttan okur, istemciye iletir. Hat kopunca nedenini döndürür."""
        while not self._stop.is_set():
            try:
                data = ser.read(max(1, getattr(ser, 'in_waiting', 0) or 1))
            except Exception as exc:                   # noqa: BLE001 - SerialException/OSError/TypeError
                return str(exc) or exc.__class__.__name__
            if not data:
                continue
            with self._lock:
                client = self._client
            if client is None:
                self._bump('dropped_no_client', len(data))
                continue
            try:
                client.sendall(data)
                self._bump('bytes_to_client', len(data))
            except OSError:
                self._drop_client('istemciye yazılamadı')
        return 'durduruldu'

    # ------------------------------------------------------------------ TCP taraf
    def _drop_client(self, why: str, quiet: bool = False) -> None:
        with self._lock:
            c, self._client = self._client, None
            was = c is not None
            self.stats['client_connected'] = False
        if c is not None:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()
        if was and not quiet:
            self._event('client_disconnected', f'İstemci bağlantısı kapandı: {why}', 'warning')

    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle_new, args=(conn, addr), daemon=True,
                             name='hw-bridge-client').start()

    def _handle_new(self, conn: socket.socket, addr: Any) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if not self.stats['serial_connected']:
            self._event('client_refused', f'{addr[0]} reddedildi: seri hat bağlı değil', 'warning')
            self._bump('refused')
            conn.close()
            return
        leftover = b''
        if self.token:
            leftover = self._authenticate(conn, addr)
            if leftover is None:
                return
        with self._lock:
            if self._client is not None:
                self.stats['refused'] += 1
                busy = True
            else:
                self._client = conn
                self.stats['client_connected'] = True
                self.stats['clients_total'] += 1
                busy = False
        if busy:
            self._event('client_refused', f'{addr[0]} reddedildi: zaten bir denetleyici bağlı', 'warning')
            conn.close()
            return
        self._event('client_connected', f'İstemci bağlandı: {addr[0]}:{addr[1]}')
        if leftover:
            self._to_serial(leftover)
        self._pump_client(conn)

    def _authenticate(self, conn: socket.socket, addr: Any) -> Optional[bytes]:
        conn.settimeout(self._auth_timeout)
        buf = b''
        try:
            while b'\n' not in buf and len(buf) < 256:
                chunk = conn.recv(256)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        line, _, rest = buf.partition(b'\n')
        ok = line.startswith(AUTH_PREFIX) and hmac.compare_digest(
            line[len(AUTH_PREFIX):].strip(), (self.token or '').encode())
        conn.settimeout(None)
        if not ok:
            self._bump('auth_failures')
            self._event('auth_failed', f'{addr[0]} yetkilendirilemedi', 'warning')
            conn.close()
            return None
        return rest

    def _to_serial(self, data: bytes) -> bool:
        with self._ser_lock:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write(data)
            except Exception as exc:                   # noqa: BLE001 - yazma hatası = hat koptu
                self._set(last_error=f'seri yazma hatası: {exc}')
                self._close_serial()                   # okuyucu iş parçacığı da hata alıp yeniden açar
                return False
        self._bump('bytes_to_serial', len(data))
        return True

    def _pump_client(self, conn: socket.socket) -> None:
        conn.settimeout(0.2)
        while not self._stop.is_set():
            with self._lock:
                if self._client is not conn:
                    return                              # seri koptu / başka nedenle bırakıldı
            try:
                data = conn.recv(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            if not self._to_serial(data):
                time.sleep(0.01)                        # seri yok: veri atılır (ESP32 watchdog'u bilir)
        with self._lock:
            mine = self._client is conn
        if mine:
            self._drop_client('istemci ayrıldı')


def _reuse_ok() -> bool:
    import sys
    return sys.platform != 'win32'                      # Windows'ta SO_REUSEADDR portu çalmaya izin verir
