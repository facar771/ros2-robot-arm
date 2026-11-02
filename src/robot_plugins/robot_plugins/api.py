"""Management API of the plugin manager: small, JSON, loopback only, on its own port (default 8770).

    GET  /api/plugins                       list (status of every plugin, valid or not)
    GET  /api/plugins/<name>                one plugin (+ manifest summary and the tool catalogue)
    GET  /api/plugins/<name>/logs           last log lines
    POST /api/plugins/<name>/enable         body {"params": {...}} (optional) -> status
    POST /api/plugins/<name>/disable        -> status
    POST /api/plugins/estop                 e-stop: every plugin AND the robot (POST /api/stop)

Same protection as the robot's web view: binds to loopback only (anything else is refused at
construction), the ``Host`` header must be loopback (DNS rebinding), a foreign ``Origin`` or
``Sec-Fetch-Site: cross-site`` is refused (a web page cannot drive it), POST bodies must be
``application/json`` and at most 4 KB. There is no authentication: access = being on this machine.
Calling a tool is deliberately NOT exposed here; that is for the LLM layer (``PluginManager.call``).
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from robot_plugins.manager import ManagerError, PluginManager

API_VERSION = 1
MAX_BODY = 4096
LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1', '[::1]'}
NAME = r'[a-z][a-z0-9_]{1,39}'


def _host_ok(header: Optional[str]) -> bool:
    if not header:
        return False
    host = header.rsplit(':', 1)[0] if not header.startswith('[') else header.split(']')[0] + ']'
    return host in LOOPBACK_HOSTS


class _Handler(BaseHTTPRequestHandler):
    server: 'PluginApiServer'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):       # keep test and service output quiet
        pass

    # ---- plumbing ------------------------------------------------------------------------------
    def _send(self, status: int, body: Dict[str, Any],
              extra: Optional[Dict[str, str]] = None) -> None:
        payload = json.dumps(body).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _guard(self) -> bool:
        if not _host_ok(self.headers.get('Host')):
            self._send(403, {'error': 'Host başlığı loopback değil'})
            return False
        origin = self.headers.get('Origin')
        if origin is not None:
            host = urlsplit(origin).hostname or ''
            if host not in LOOPBACK_HOSTS:
                self._send(403, {'error': 'yabancı Origin'})
                return False
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            self._send(403, {'error': 'siteler arası istek reddedildi'})
            return False
        return True

    def _json_body(self) -> Tuple[Optional[Dict[str, Any]], bool]:
        """(body, ok); on failure the error response is already sent."""
        ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip()
        if ctype != 'application/json':
            self._send(415, {'error': 'Content-Type application/json olmalı'})
            return None, False
        length = self.headers.get('Content-Length')
        if length is None:
            self._send(411, {'error': 'Content-Length gerekli'})
            return None, False
        if not length.isdigit() or int(length) > MAX_BODY:
            self._send(413, {'error': f'gövde en çok {MAX_BODY} bayt olabilir'})
            return None, False
        raw = self.rfile.read(int(length))
        try:
            body = json.loads(raw.decode('utf-8') or '{}')
        except ValueError:
            self._send(400, {'error': 'geçersiz JSON'})
            return None, False
        if not isinstance(body, dict):
            self._send(400, {'error': 'gövde bir JSON nesnesi olmalı'})
            return None, False
        return body, True

    # ---- routes --------------------------------------------------------------------------------
    def do_GET(self) -> None:                      # noqa: N802
        if not self._guard():
            return
        mgr = self.server.manager
        path = urlsplit(self.path).path
        if path == '/api/plugins':
            return self._send(200, {'api_version': API_VERSION, 'plugins': mgr.list()})
        m = re.fullmatch(rf'/api/plugins/({NAME})', path)
        if m:
            try:
                doc = mgr.status(m.group(1))
                if doc['status'] != 'invalid':
                    doc['manifest'] = mgr.manifest(m.group(1)).describe()
                return self._send(200, doc)
            except ManagerError as exc:
                return self._send(404, {'error': str(exc)})
        m = re.fullmatch(rf'/api/plugins/({NAME})/logs', path)
        if m:
            try:
                return self._send(200, {'name': m.group(1), 'logs': mgr.logs(m.group(1))})
            except ManagerError as exc:
                return self._send(404, {'error': str(exc)})
        self._send(404, {'error': 'yok'})

    def do_POST(self) -> None:                     # noqa: N802
        if not self._guard():
            return
        mgr = self.server.manager
        path = urlsplit(self.path).path
        if path == '/api/plugins/estop':
            body, ok = self._json_body()
            if not ok:
                return
            reason = str(body.get('reason') or 'yönetim API')[:64]
            return self._send(200, mgr.emergency_stop(reason))
        m = re.fullmatch(rf'/api/plugins/({NAME})/(enable|disable)', path)
        if not m:
            return self._send(404, {'error': 'yok'})
        body, ok = self._json_body()
        if not ok:
            return
        name, action = m.groups()
        try:
            if action == 'enable':
                params = body.get('params')
                if params is not None and not isinstance(params, dict):
                    return self._send(400, {'error': 'params bir nesne olmalı'})
                return self._send(200, mgr.enable(name, params))
            return self._send(200, mgr.disable(name))
        except ManagerError as exc:
            status = {'unknown_plugin': 404, 'bad_parameters': 400, 'estop': 423}.get(exc.code, 500)
            self._send(status, {'error': str(exc), 'code': exc.code})

    def _method_not_allowed(self) -> None:
        self._send(405, {'error': 'yöntem desteklenmiyor'}, {'Allow': 'GET, POST'})

    do_PUT = do_DELETE = do_PATCH = _method_not_allowed        # noqa: N815


class PluginApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, manager: PluginManager, host: str = '127.0.0.1', port: int = 8770):
        if host not in ('127.0.0.1', '::1', 'localhost'):
            raise ValueError(f'yönetim API yalnızca loopback adresine bağlanır (verilen: {host!r})')
        super().__init__((host, port), _Handler)
        self.manager = manager

    @property
    def port(self) -> int:
        return self.server_address[1]
