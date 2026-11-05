"""RobotApiBackend: Hub'ı robotun HTTP API v1'ine bağlar (``docs/INTERFACES.md``, "Robot API v1").

Kullanılan uç noktalar: ``GET /api/state`` (arka plan yoklaması), ``POST /api/jobs`` +
``GET /api/jobs/<id>`` + ``POST /api/jobs/<id>/cancel`` (komutlar), ``POST /api/stop`` (ACİL DUR),
``POST /api/reset``. ``POST /api/command`` kullanımdan kaldırıldığı için **kullanılmaz** (iş API'si onun
üst kümesidir). Yalnızca standart kütüphane (``http.client``): Windows .exe'ye ek bağımlılık girmez.

* ``snapshot()`` **asla ağa çıkmaz**: yoklama iş parçacığının önbelleğini döner (arayüz 5 Hz okur).
* Robot API'si yalnızca ``127.0.0.1``'e bağlanır ve kimlik doğrulaması yoktur; Hub bunu kendi
  PIN/kip katmanıyla ağa açar. ``Host`` loopback değilse robot ``403`` verir (bkz. README).
* Sürüm denetimi: ``api_version`` 1 değilse (eski robot ya da v2) bağlantı "uyumsuz" sayılır ve komut
  gönderilmez; sessizce yanlış sözleşmeyle konuşulmaz.
"""
from __future__ import annotations

import http.client
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from control_hub.hub_core.backend import BackendResult, ResetResult, StopReport

log = logging.getLogger('control_hub.robot_api')
SUPPORTED_API_VERSION = 1
DEFAULT_URL = 'http://127.0.0.1:8080'
LOOPBACK = {'127.0.0.1', 'localhost', '::1'}


class RobotApiError(Exception):
    """Ağ/protokol hatası (HTTP durum kodları hata sayılmaz; çağıran bakar)."""


class RobotApiBackend:
    name = 'robot-api'

    def __init__(self, base_url: str = DEFAULT_URL, *, poll_s: float = 0.5, job_poll_s: float = 0.25,
                 request_timeout_s: float = 5.0, stale_after_s: float = 3.0,
                 max_poll_failures: int = 20, cancel_grace_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic, autostart: bool = True):
        parts = urlsplit(base_url if '//' in base_url else f'http://{base_url}')
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError(f'robot adresi http://host:port olmalı: {base_url!r}')
        self.host, self.port, self.https = parts.hostname, parts.port or 80, parts.scheme == 'https'
        self.loopback = self.host in LOOPBACK
        self._poll_s, self._job_poll_s = poll_s, job_poll_s
        self._timeout, self._stale = request_timeout_s, stale_after_s
        self._max_fail, self._cancel_grace = max_poll_failures, cancel_grace_s
        self._clock = clock
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {}
        self._last_ok: Optional[float] = None
        self._problem: Optional[str] = 'robot henüz yoklanmadı'
        self._compatible = False
        self._stop_evt = threading.Event()
        self._first = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if autostart:
            self.start()

    # --- yaşam döngüsü -------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._poll_loop, name='robot-api-poll', daemon=True)
            self._thread.start()

    def wait_first_poll(self, timeout: float = 3.0) -> bool:
        return self._first.wait(timeout)

    def close(self) -> None:
        self._stop_evt.set()

    # --- HTTP ----------------------------------------------------------------------------------
    def _request(self, method: str, path: str, payload: Optional[dict] = None,
                 timeout: Optional[float] = None) -> Tuple[int, Dict[str, Any]]:
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        conn = cls(self.host, self.port, timeout=timeout or self._timeout)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            conn.request(method, path, body=body,
                         headers={'Content-Type': 'application/json'} if payload is not None else {})
            resp = conn.getresponse()
            raw = resp.read()
            try:
                doc = json.loads(raw) if raw else {}
            except ValueError:
                doc = {'error': f'JSON olmayan yanıt (HTTP {resp.status})'}
            return resp.status, doc if isinstance(doc, dict) else {'value': doc}
        except (OSError, http.client.HTTPException) as exc:
            raise RobotApiError(f'robot API\'sine ulaşılamıyor ({self.host}:{self.port}): {exc}') from exc
        finally:
            conn.close()

    # --- yoklama -------------------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            self._poll_once()
            self._stop_evt.wait(self._poll_s)

    def _poll_once(self) -> None:
        try:
            status, doc = self._request('GET', '/api/state')
            if status != 200:
                raise RobotApiError(f'/api/state HTTP {status}: {doc.get("error", "")}')
        except RobotApiError as exc:
            with self._lock:
                self._problem = str(exc)
            self._first.set()
            return
        ver = (doc.get('robot') or {}).get('api_version', doc.get('api_version'))
        enabled = (doc.get('commands') or {}).get('enabled', False)
        problem = None
        if ver != SUPPORTED_API_VERSION:
            problem = (f'robot API sürümü {ver!r}; Hub yalnızca v{SUPPORTED_API_VERSION} konuşur '
                       '(robot tarafı güncel mi?)')
        elif not enabled:
            problem = 'robotun komut ucu kapalı (web_view commands:=true gerekli)'
        with self._lock:
            self._state, self._last_ok = doc, self._clock()
            self._problem, self._compatible = problem, problem is None
        self._first.set()

    def _fresh(self) -> bool:
        return self._last_ok is not None and self._clock() - self._last_ok <= self._stale

    @property
    def can_interrupt(self) -> bool:                       # type: ignore[override]
        with self._lock:
            return (self._state.get('robot') or {}).get('api_version') == SUPPORTED_API_VERSION

    @property
    def compatible(self) -> bool:
        with self._lock:
            return self._compatible

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            st, problem, last_ok = self._state, self._problem, self._last_ok
        robot = st.get('robot') or {}
        return {
            'name': self.name, 'connected': self._fresh(),
            'can_interrupt': robot.get('api_version') == SUPPORTED_API_VERSION,
            'compatible': self.compatible, 'problem': problem,
            'stale_s': None if last_ok is None else round(self._clock() - last_ok, 1),
            'api_version': robot.get('api_version'), 'mode': robot.get('mode'),
            'state': robot.get('state'), 'moving': robot.get('moving'),
            'estop': robot.get('estop') or {'tripped': False, 'reason': None, 'since': None},
            'current_job': robot.get('current_job'), 'last_error': robot.get('last_error'),
            'safety': robot.get('safety'),
            'tool': (st.get('tool') or {}).get('position'),
            'tcp': st.get('tcp'), 'jog': robot.get('jog'),
            'joints': st.get('joints') or [],
            'objects': [{'name': o.get('name'), 'label': o.get('label'), 'position': o.get('position'),
                         'size': o.get('size')} for o in st.get('objects') or []],
        }

    # --- komutlar ------------------------------------------------------------------------------
    def execute(self, command: str, cancel: threading.Event) -> BackendResult:
        with self._lock:
            compatible, problem = self._compatible, self._problem
        if not compatible:
            return BackendResult(False, problem or 'robot API uyumlu değil', {'unavailable': True})
        try:
            status, doc = self._request('POST', '/api/jobs', {'command': command})
        except RobotApiError as exc:
            return BackendResult(False, str(exc), {'unavailable': True})
        if status == 423:
            return BackendResult(False, 'robot ACİL DUR kilidinde: önce kilidi aç', {'estop': True})
        if status == 409:
            return BackendResult(False, 'robot başka bir işle meşgul', {'busy': True,
                                                                        'job_id': doc.get('job_id')})
        if status != 202 or 'id' not in doc:
            return BackendResult(False, doc.get('error') or f'robot işi kabul etmedi (HTTP {status})',
                                 {'http_status': status})
        return self._follow(doc['id'], cancel)

    def _follow(self, job_id: str, cancel: threading.Event) -> BackendResult:
        failures, cancel_sent_at = 0, None
        while True:
            cancel.wait(self._job_poll_s)
            if cancel.is_set() and cancel_sent_at is None:
                cancel_sent_at = self._clock()
                try:
                    self._request('POST', f'/api/jobs/{job_id}/cancel', {})
                except RobotApiError:
                    pass                                   # ACİL DUR ayrı yoldan gider
            try:
                status, job = self._request('GET', f'/api/jobs/{job_id}')
                failures = 0
            except RobotApiError as exc:
                failures += 1
                if failures >= self._max_fail:
                    return BackendResult(False, f'bağlantı koptu; iş durumu bilinmiyor ({exc})',
                                         {'job_id': job_id, 'unknown': True})
                continue
            if status != 200:
                return BackendResult(False, job.get('error') or f'iş okunamadı (HTTP {status})',
                                     {'job_id': job_id, 'http_status': status})
            if job.get('state') != 'running':
                return self._finished(job_id, job)
            if cancel_sent_at is not None and self._clock() - cancel_sent_at > self._cancel_grace:
                return BackendResult(False, 'iptal istendi ama robot işi bitirmedi',
                                     {'job_id': job_id, 'interrupted': True, 'unconfirmed': True})

    @staticmethod
    def _finished(job_id: str, job: Dict[str, Any]) -> BackendResult:
        result = job.get('result') or {}
        data = {**(result.get('data') or {}), 'job_id': job_id}
        state = job.get('state')
        if state == 'done':
            return BackendResult(True, result.get('message') or 'tamam', data)
        msg = job.get('error') or result.get('message') or 'başarısız'
        if state == 'cancelled':
            data.update(interrupted=True, cancelled_by=job.get('cancelled_by'))
            return BackendResult(False, f'iptal edildi: {msg}', data)
        if job.get('timed_out'):
            data['timed_out'] = True
        return BackendResult(False, msg, data)

    # --- jog / çerçeveler (J2): Hub yalnızca geçirir; kurallar (TTL, kiralama, 409/423) robotta ----
    def _passthrough(self, method: str, path: str, payload: Optional[dict] = None,
                     timeout: float = 2.0) -> Tuple[int, Dict[str, Any]]:
        """(HTTP kodu, gövde). Jog kısa zaman aşımı kullanır: yavaş yanıt, bayat bir komut demektir.
        Ağ hatası ``RobotApiError`` fırlatır; robot TTL'i dolunca kendiliğinden durur."""
        return self._request(method, path, payload, timeout=timeout)

    def jog_command(self, body: Any, *, remote: bool = False) -> Tuple[int, Dict[str, Any]]:
        return self._passthrough('POST', '/api/jog', body)

    def jog_status(self) -> Tuple[int, Dict[str, Any]]:
        return self._passthrough('GET', '/api/jog')

    def frames_get(self) -> Tuple[int, Dict[str, Any]]:
        return self._passthrough('GET', '/api/frames')

    def frames_put(self, body: Any) -> Tuple[int, Dict[str, Any]]:
        return self._passthrough('PUT', '/api/frames', body, timeout=5.0)

    # --- ACİL DUR ------------------------------------------------------------------------------
    def emergency_stop(self) -> StopReport:
        """``POST /api/stop``: robot kilidi koyar ve kolun durduğunu hız verisinden doğrular (≤ 5 s).
        Ağ hatasında 3 kez dener; yine de ulaşamazsa ``stopped=False``."""
        last = 'bilinmeyen hata'
        for attempt in range(3):
            try:
                status, doc = self._request('POST', '/api/stop', {'reason': 'Hub ACİL DUR'},
                                            timeout=12.0)
            except RobotApiError as exc:
                last = str(exc)
                time.sleep(0.3 * (attempt + 1))
                continue
            if status != 200:
                return StopReport(False, doc.get('error') or f'/api/stop HTTP {status}',
                                  {'http_status': status})
            details = {k: doc.get(k) for k in ('stop_time_s', 'max_joint_speed', 'was_running')}
            self._poll_once()
            return StopReport(bool(doc.get('stopped')), doc.get('message') or '', details)
        return StopReport(False, f'robota ulaşılamadı, durduğu doğrulanamadı: {last}')

    def reset_emergency_stop(self) -> ResetResult:
        try:
            status, doc = self._request('POST', '/api/reset', {}, timeout=8.0)
        except RobotApiError as exc:
            return ResetResult(False, str(exc))
        if status == 200 and doc.get('ok'):
            self._poll_once()          # önbellek bayat "kilitli" göstermesin (Hub hemen komut verebilir)
            return ResetResult(True, doc.get('message') or 'sıfırlandı')
        return ResetResult(False, doc.get('error') or f'robot sıfırlamayı reddetti (HTTP {status})')
