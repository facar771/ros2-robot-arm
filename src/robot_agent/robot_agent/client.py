"""HTTP access to the Robot API v1 (stdlib only). The robot API lives on 127.0.0.1; this client
refuses any other host, so an agent cannot be pointed at a robot elsewhere by accident."""
from __future__ import annotations

import http.client
import json
from typing import Any, Dict, Optional, Protocol, Tuple
from urllib.parse import urlsplit

LOOPBACK = ('127.0.0.1', 'localhost', '::1')
DEFAULT_URL = 'http://127.0.0.1:8080'


class RobotUnreachable(Exception):
    """The Robot API did not answer (not running, wrong port, timeout)."""


class RobotPort(Protocol):
    def get(self, path: str) -> Tuple[int, Optional[Dict[str, Any]]]: ...

    def post(self, path: str, payload: Optional[Dict[str, Any]] = None
             ) -> Tuple[int, Optional[Dict[str, Any]]]: ...


class HttpRobot:
    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 15.0):
        parts = urlsplit(base_url)
        if parts.scheme != 'http' or parts.hostname not in LOOPBACK or parts.port is None:
            raise ValueError(f'robot adresi http://127.0.0.1:<port> olmalı (verilen: {base_url!r});'
                             ' robot API yalnızca 127.0.0.1 üzerindedir')
        self.host, self.port, self.timeout = parts.hostname, parts.port, timeout

    @property
    def base_url(self) -> str:
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'http://{host}:{self.port}'

    def _request(self, method: str, path: str, body: Optional[bytes] = None):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise RobotUnreachable(f'robot API ({self.host}:{self.port}) yanıt vermedi: '
                                   f'{type(exc).__name__}') from exc
        finally:
            conn.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = None
        return resp.status, parsed if isinstance(parsed, dict) else None

    def get(self, path: str):
        return self._request('GET', path)

    def post(self, path: str, payload: Optional[Dict[str, Any]] = None):
        body = None if payload is None else json.dumps(payload).encode()
        return self._request('POST', path, body)
