"""Robot API ile iki yönlü acil dur bağı (ROS'suz, yalnızca 127.0.0.1).

* ESP32 → Robot API: köprü bir sorun bildirince (telemetri kesildi, MCU acil dur, FAULT) ``POST /api/stop``
  atılır: Robot API ``estopped`` olur ve çalışan işler iptal edilir. İstek ulaşana kadar yeniden denenir
  (Robot API o an kapalı olabilir); ulaşmazsa ``unreached`` ile görünür kalır.
* Robot API → ESP32: ``GET /api/state`` sorgulanır; ``robot.estop.tripped`` açılınca donanıma ACİL DUR
  yollanır (sürücüler kesilir; sonrasında clear + homing gerekir). Robot API bir süre hiç yanıt
  vermezse ve yörünge çalışıyorsa eksenler kontrollü durdurulur.

v1 sözleşmesine ekleme yoktur: yalnızca mevcut ``/api/stop`` ve ``/api/state`` kullanılır."""
from __future__ import annotations

import http.client
import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

LOOPBACK = ('127.0.0.1', 'localhost', '::1')


class RobotApiClient:
    def __init__(self, base_url: str, timeout: float = 1.0):
        parts = urlsplit(base_url)
        if parts.scheme != 'http' or parts.hostname not in LOOPBACK or not parts.port:
            raise ValueError('robot API adresi http://127.0.0.1:<port> biçiminde olmalı (yalnızca yerel)')
        self.host, self.port, self.timeout = parts.hostname, parts.port, timeout

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None
                 ) -> Tuple[int, Optional[Dict[str, Any]]]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            data = None if body is None else json.dumps(body).encode()
            conn.request(method, path, body=data,
                         headers={'Content-Type': 'application/json'} if data else {})
            resp = conn.getresponse()
            raw = resp.read()
            try:
                doc = json.loads(raw) if raw else None
            except ValueError:
                doc = None
            return resp.status, doc if isinstance(doc, dict) else None
        finally:
            conn.close()

    def stop(self, reason: str) -> bool:
        status, _ = self._request('POST', '/api/stop', {'reason': reason[:100]})
        return status == 200

    def estop_tripped(self) -> Optional[bool]:
        """True/False; None = ulaşılamadı ya da beklenmeyen yanıt."""
        try:
            status, doc = self._request('GET', '/api/state')
        except (OSError, http.client.HTTPException):
            return None
        robot = (doc or {}).get('robot') if status == 200 else None
        info = (robot or {}).get('estop') if isinstance(robot, dict) else None
        if not isinstance(info, dict) or 'tripped' not in info:
            return None
        return bool(info['tripped'])


class EstopRelay:
    """İki yönü yürüten iş parçacığı: ``report(reason)`` ESP32 → Robot API; polling Robot API → ESP32."""

    def __init__(self, client: RobotApiClient, *, hw_estop: Callable[[str], None],
                 hw_stop: Callable[[str], None], moving: Callable[[], bool],
                 poll_s: float = 0.1, api_lost_s: float = 1.0, retry_s: float = 0.25,
                 give_up_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._client, self._hw_estop, self._hw_stop, self._moving = client, hw_estop, hw_stop, moving
        self.poll_s, self.api_lost_s, self.retry_s, self.give_up_s = poll_s, api_lost_s, retry_s, give_up_s
        self._clock = clock
        self._pending: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._was_tripped = False
        self._last_ok: Optional[float] = None
        self._api_lost_acted = False
        self.delivered: list = []
        self.unreached: list = []
        self._thread = threading.Thread(target=self._run, name='hw-estop-relay', daemon=True)

    def start(self) -> 'EstopRelay':
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)

    def report(self, reason: str) -> None:
        """ESP32 tarafı bir sorun gördü: Robot API'yi durdur (yeniden denenir)."""
        with self._lock:
            self._pending.append((reason, self._clock()))

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            self._deliver()
            self._poll()

    def _deliver(self) -> None:
        with self._lock:
            items, self._pending = self._pending, []
        for reason, since in items:
            try:
                ok = self._client.stop(f'hw_bridge: {reason}')
            except (OSError, http.client.HTTPException):
                ok = False
            if ok:
                self.delivered.append(reason)
            elif self._clock() - since < self.give_up_s:
                with self._lock:
                    self._pending.append((reason, since))
                self._stop.wait(self.retry_s)
            else:
                self.unreached.append(reason)

    def _poll(self) -> None:
        tripped = self._client.estop_tripped()
        now = self._clock()
        if tripped is None:
            if self._last_ok is not None and now - self._last_ok > self.api_lost_s \
                    and not self._api_lost_acted and self._moving():
                self._api_lost_acted = True
                self._hw_stop('robot API yanıt vermiyor')
            return
        self._last_ok, self._api_lost_acted = now, False
        if tripped and not self._was_tripped:
            self._hw_estop('robot API acil dur')
        self._was_tripped = tripped
