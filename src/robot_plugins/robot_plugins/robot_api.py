"""Client for the Robot API v1 (``docs/INTERFACES.md``, "Robot API v1") and an in-memory fake.

Plugins never touch ROS or the arm: every motion goes through ``POST /api/jobs`` (white-listed
commands, safe work box, speed cap) and stopping goes through ``POST /api/stop``. That keeps the
safety layer of the robot in force for plugin code too.

Status codes become exceptions: ``423`` -> :class:`RobotLocked` (emergency stop latched),
``409`` -> :class:`RobotBusy` (another job runs), ``400/422`` -> :class:`RobotRefused`.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

ALLOWED_COMMANDS = ('home', 'objects', 'above', 'demo_scenario')


class RobotApiError(Exception):
    def __init__(self, status: int, message: str, body: Optional[dict] = None):
        super().__init__(f'{status}: {message}')
        self.status = status
        self.message = message
        self.body = body or {}


class RobotLocked(RobotApiError):
    """423: the emergency stop is latched; nothing moves until a human resets it."""


class RobotBusy(RobotApiError):
    """409: another job is running (one job at a time)."""


class RobotRefused(RobotApiError):
    """400 / 422: the command is invalid, or the robot could not do it (nothing moved)."""


def _raise_for(status: int, body: dict) -> None:
    message = str(body.get('error') or body.get('message') or f'HTTP {status}')
    if status == 423:
        raise RobotLocked(status, message, body)
    if status == 409:
        raise RobotBusy(status, message, body)
    if status in (400, 422):
        raise RobotRefused(status, message, body)
    raise RobotApiError(status, message, body)


class RobotApi(ABC):
    @abstractmethod
    def state(self) -> Dict[str, Any]:
        """``GET /api/state`` -> the ``robot`` block (``state``, ``current_job``, ``moving``...)."""

    @abstractmethod
    def submit_job(self, command: str, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """``POST /api/jobs``; returns the job document (``id``, ``state``...)."""

    @abstractmethod
    def job(self, job_id: str) -> Dict[str, Any]:
        """``GET /api/jobs/<id>``."""

    @abstractmethod
    def cancel(self, job_id: str) -> Dict[str, Any]:
        """``POST /api/jobs/<id>/cancel`` (does not latch)."""

    @abstractmethod
    def stop(self, reason: str = 'plugin') -> Dict[str, Any]:
        """``POST /api/stop``: the emergency stop. Never refused by the robot."""


class HttpRobotApi(RobotApi):
    """``urllib`` client; default is the loopback ``web_view`` port."""

    def __init__(self, base_url: str = 'http://127.0.0.1:8080', timeout: float = 10.0):
        self.base = base_url.rstrip('/')
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        data = None if body is None else json.dumps(body).encode('utf-8')
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if method == 'POST':
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode('utf-8') or '{}')
            except ValueError:
                payload = {}
            _raise_for(exc.code, payload if isinstance(payload, dict) else {})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RobotApiError(0, f'robot API erişilemedi: {exc}') from exc
        raise AssertionError('unreachable')

    def state(self) -> Dict[str, Any]:
        doc = self._request('GET', '/api/state')
        return doc.get('robot', doc)

    def submit_job(self, command: str, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {'command': command}
        if timeout_s is not None:
            body['timeout_s'] = timeout_s
        return self._request('POST', '/api/jobs', body)

    def job(self, job_id: str) -> Dict[str, Any]:
        return self._request('GET', f'/api/jobs/{job_id}')

    def cancel(self, job_id: str) -> Dict[str, Any]:
        return self._request('POST', f'/api/jobs/{job_id}/cancel', {}, timeout=10.0)

    def stop(self, reason: str = 'plugin') -> Dict[str, Any]:
        return self._request('POST', '/api/stop', {'reason': reason[:64]}, timeout=15.0)


class FakeRobotApi(RobotApi):
    """The v1 contract in memory, for tests and for developing plugins without a simulator.

    One job at a time; jobs finish after ``job_duration`` seconds; ``stop`` latches (jobs started
    afterwards get 423) and cancels the running job with ``cancelled_by: estop``; ``reset`` is
    deliberately NOT part of :class:`RobotApi` (only a human unlatches).
    """

    def __init__(self, job_duration: float = 0.2, now=time.monotonic):
        self.job_duration = job_duration
        self._now = now
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._next = 1
        self.latched = False
        self.history: List[str] = []         # every command accepted, in order
        self.stops: List[str] = []           # reasons of every stop() call

    def _settle(self) -> None:
        for job in self._jobs.values():
            if job['state'] == 'running' and self._now() - job['_t0'] >= self.job_duration:
                job.update(state='done', result={'ok': True, 'message': 'tamam', 'data': {}},
                           finished_at=time.time())

    @staticmethod
    def _valid(command: str) -> bool:
        parts = command.strip().lower().split()
        if not parts or parts[0] not in ALLOWED_COMMANDS:
            return False
        if parts[0] == 'above':
            if len(parts) not in (2, 3):
                return False
            if len(parts) == 3:
                try:
                    if not 0.0 <= float(parts[2].replace(',', '.')) <= 0.5:
                        return False
                except ValueError:
                    return False
        return True

    def state(self) -> Dict[str, Any]:
        with self._lock:
            self._settle()
            running = next((j for j in self._jobs.values() if j['state'] == 'running'), None)
            state = 'estopped' if self.latched else ('busy' if running else 'idle')
            return {'api_version': 1, 'mode': 'mock', 'state': state,
                    'current_job': None if running is None else {'id': running['id']},
                    'estop': {'tripped': self.latched}}

    def submit_job(self, command: str, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            self._settle()
            if self.latched:
                raise RobotLocked(423, 'acil dur kilitli', {'state': 'estopped'})
            if not self._valid(command):
                raise RobotRefused(400, f'geçersiz komut: {command!r}')
            running = next((j for j in self._jobs.values() if j['state'] == 'running'), None)
            if running is not None:
                raise RobotBusy(409, 'başka iş çalışıyor', {'job_id': running['id']})
            job_id = f'j{self._next}'
            self._next += 1
            self.history.append(command.strip().lower())
            job = {'id': job_id, 'command': command.strip().lower(), 'state': 'running',
                   'created_at': time.time(), 'cancelled_by': None, 'result': None,
                   'error': None, '_t0': self._now()}
            self._jobs[job_id] = job
            return self._public(job)

    @staticmethod
    def _public(job: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in job.items() if not k.startswith('_')}

    def job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            self._settle()
            if job_id not in self._jobs:
                raise RobotApiError(404, 'iş yok')
            return self._public(self._jobs[job_id])

    def cancel(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            self._settle()
            job = self._jobs.get(job_id)
            if job is None:
                raise RobotApiError(404, 'iş yok')
            if job['state'] != 'running':
                raise RobotBusy(409, 'iş zaten bitti', {'job_id': job_id})
            job.update(state='cancelled', cancelled_by='user', error='iş iptal edildi')
            return {'ok': True, 'job': self._public(job)}

    def stop(self, reason: str = 'plugin') -> Dict[str, Any]:
        with self._lock:
            self._settle()
            self.latched = True
            self.stops.append(reason)
            for job in self._jobs.values():
                if job['state'] == 'running':
                    job.update(state='cancelled', cancelled_by='estop', error='acil dur etkin')
            return {'ok': True, 'state': 'estopped', 'latched': True, 'stopped': True}

    def reset(self) -> Dict[str, Any]:
        """Human action (test helper): clears the latch."""
        with self._lock:
            changed = self.latched
            self.latched = False
            return {'ok': True, 'changed': changed, 'state': 'idle'}


def wait_for_job(api: RobotApi, job_id: str, *, stop_token=None, timeout: float = 120.0,
                 poll: float = 0.1) -> Dict[str, Any]:
    """Poll ``job_id`` until it leaves ``running``. If ``stop_token`` is set meanwhile, the job is
    cancelled (best effort) and the last document returned; if ``timeout`` passes it is cancelled
    and the document returned too."""
    deadline = time.monotonic() + timeout
    while True:
        doc = api.job(job_id)
        if doc.get('state') != 'running':
            return doc
        stopping = stop_token is not None and stop_token.is_set()
        if stopping or time.monotonic() > deadline:
            try:
                api.cancel(job_id)
            except RobotApiError:
                pass
            return api.job(job_id)
        time.sleep(poll)
