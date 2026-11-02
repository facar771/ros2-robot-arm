"""Browser view of the robot (stdlib ``http.server`` only, no ROS). Read-only unless the server is
given a :class:`~robot_core.webcmd.CommandRunner`.

The ROS node (``robot_viz.web_view``) only fills a :class:`Snapshot`; everything else lives here so
it can be tested without ROS:

    GET /                 HTML page (RGB + overlay, depth, object table, joints, tool position)
    GET /app.js, /style.css
    GET /frame/rgb.png    camera image with the detected objects drawn on it
    GET /frame/depth.png  depth colour map
    GET /api/state        JSON: objects (+ pixel boxes), joint angles, tool position, frame ages

    POST /api/command     {"command": "above green 0.25"}: white-listed robot commands (see
                          :mod:`robot_core.webcmd`); only with a CommandRunner, otherwise 403
    POST /api/jobs        {"command": "...", "timeout_s": 60}: start a command, answers 202 at once
    GET  /api/jobs[/<id>] job documents (running|done|failed|cancelled, progress, result)
    POST /api/jobs/<id>/cancel   stop the running job (no latch; the e-stop is /api/stop)
    POST /api/stop        EMERGENCY STOP: cancel the running goal, latch "estopped", verify from the
                          joint velocities that the arm stopped (never blocked by a running command)
    POST /api/reset       clear the latch (only while the arm stands still)

Without a CommandRunner nothing can change anything and every method except GET/HEAD gets ``405``.
Requests whose ``Host`` header is not a loopback name get ``403`` (DNS-rebinding guard); bind to
127.0.0.1 unless a container port mapping needs 0.0.0.0, and then restrict the *published* port to
127.0.0.1 (see compose.yaml).

POST /api/command is the only door to the robot and is guarded against other web pages in the
user's browser (CSRF): ``Content-Type: application/json`` is required (a cross-site page cannot send
that without a pre-flight, which is never answered), a foreign ``Origin`` or
``Sec-Fetch-Site: cross-site`` gets ``403``, the body is limited to 512 bytes, the command is parsed
by a strict white list, and only one command runs at a time (``409`` otherwise). Status codes:
200 done, 400 invalid command, 403 forbidden/off, 405 wrong method, 409 busy, 411/413 body length,
415 content type, 422 valid command the robot could not carry out, 423 emergency stop latched,
500 internal error. The full contract is docs/INTERFACES.md ("Robot API v1").
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlsplit

import numpy as np

from robot_core.jobs import BadTimeout, JobBusy, JobManager, JobNotFound, check_timeout
from robot_core.perception import Intrinsics, Transform
from robot_core.supervisor import API_VERSION, MOVING_SPEED, EStopped, Supervisor, max_speed
from robot_core.viz import OverlayItem, box_to_pixels, depth_to_rgb, draw_overlay, encode_png
from robot_core.webcmd import Busy, CommandError, CommandRunner, parse_command

log = logging.getLogger('robot_core.webview')

Vec = Tuple[float, float, float]
LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '[::1]', '::1'})


@dataclass(frozen=True)
class SceneObject:
    """A detected object in the world frame (one marker of /perception/objects)."""
    label: str                 # colour: red / green / blue
    index: int                 # blob index within that colour (0 = largest)
    position: Vec
    size: Vec

    @property
    def name(self) -> str:
        return f'{self.label}#{self.index}'


@dataclass(frozen=True)
class Snapshot:
    """Everything the page shows, at one instant. Any part may be missing (None / empty)."""
    rgb: Optional[np.ndarray] = None                  # (H, W, 3) uint8
    depth: Optional[np.ndarray] = None                # (H, W) float32, z-depth in metres
    rgb_age_s: Optional[float] = None
    depth_age_s: Optional[float] = None
    objects: Tuple[SceneObject, ...] = ()
    objects_age_s: Optional[float] = None
    intrinsics: Optional[Intrinsics] = None
    world_from_optical: Optional[Transform] = None
    joint_names: Tuple[str, ...] = ()
    joint_positions: Optional[Dict[str, float]] = None
    tool_link: str = ''
    tool_position: Optional[Vec] = None
    joint_velocities: Optional[Dict[str, float]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class ViewSource(Protocol):
    def snapshot(self) -> Snapshot: ...


def host_name(header: Optional[str]) -> str:
    """Host name from a ``Host`` header value, lower case, without the port ('[::1]:80' -> '[::1]');
    '' if absent."""
    raw = (header or '').strip().lower()
    if raw.startswith('['):
        return raw[:raw.find(']') + 1] if ']' in raw else raw
    return raw.split(':', 1)[0]


def _finite_list(values: Sequence[float], digits: int = 4) -> list:
    return [round(float(v), digits) for v in values]


def robot_document(snap: Snapshot, supervisor: Optional[Supervisor], mode: str,
                   runner: Optional[CommandRunner] = None) -> Dict[str, Any]:
    """The ``robot`` object. Without a Supervisor (read-only view) there is no e-stop: the state
    is ``busy`` while a command runs and ``idle`` otherwise."""
    if supervisor is not None:
        return supervisor.document()
    speed = max_speed(snap.joint_velocities, snap.joint_names)
    status = runner.status() if runner is not None else {}
    return {'api_version': API_VERSION, 'mode': mode,
            'state': 'busy' if status.get('running') else 'idle',
            'current_job': status.get('job'), 'last_error': status.get('last_error'),
            'moving': None if speed is None else speed > MOVING_SPEED,
            'estop': {'tripped': False, 'reason': None, 'since': None}, 'safety': None}


def build_state(snap: Snapshot, runner: Optional[CommandRunner] = None,
                supervisor: Optional[Supervisor] = None, mode: str = 'gazebo') -> Dict[str, Any]:
    """The ``/api/state`` document. Always the same keys; missing data is ``null`` / ``[]``."""
    pixel_ok = snap.intrinsics is not None and snap.world_from_optical is not None
    objects = []
    for obj in snap.objects:
        pixel = None
        if pixel_ok:
            box = box_to_pixels(obj.position, obj.size, snap.intrinsics, snap.world_from_optical)
            if box is not None:
                pixel = {'center': _finite_list(box.center, 2), 'bbox': _finite_list(box.bbox, 2),
                         'rect': [int(round(x)) for x in box.bbox]}   # exactly what is drawn
        objects.append({'name': obj.name, 'label': obj.label, 'index': obj.index,
                        'position': _finite_list(obj.position), 'size': _finite_list(obj.size),
                        'pixel': pixel})
    joints = None
    if snap.joint_positions and all(n in snap.joint_positions for n in snap.joint_names):
        joints = [{'name': n, 'rad': round(snap.joint_positions[n], 4),
                   'deg': round(math.degrees(snap.joint_positions[n]), 2)}
                  for n in snap.joint_names]
    tool = None
    if snap.tool_position is not None:
        tool = {'link': snap.tool_link, 'position': _finite_list(snap.tool_position)}
    image = snap.rgb if snap.rgb is not None else snap.depth
    return {
        'api_version': API_VERSION,
        'time': round(time.time(), 3),
        'robot': robot_document(snap, supervisor, mode, runner),
        'camera': {
            'width': int(image.shape[1]) if image is not None else None,
            'height': int(image.shape[0]) if image is not None else None,
            'rgb_age_s': None if snap.rgb_age_s is None else round(snap.rgb_age_s, 3),
            'depth_age_s': None if snap.depth_age_s is None else round(snap.depth_age_s, 3),
            'overlay': bool(pixel_ok and snap.rgb is not None),
        },
        'objects': objects,
        'objects_age_s': None if snap.objects_age_s is None else round(snap.objects_age_s, 3),
        'joints': joints,
        'tool': tool,
        'commands': {'enabled': runner is not None,
                     'allowed': runner.allowed if runner is not None else []},
        'command': runner.status() if runner is not None else {
            'running': False, 'name': None, 'job': None, 'log': [], 'last': None,
            'last_error': None},
    }


def render_rgb(snap: Snapshot) -> Optional[bytes]:
    """RGB PNG with the object overlay (plain image if the projection is not available yet)."""
    if snap.rgb is None:
        return None
    img = snap.rgb
    if snap.objects and snap.intrinsics is not None and snap.world_from_optical is not None:
        items = [OverlayItem(o.name, o.position, o.size) for o in snap.objects]
        img, _ = draw_overlay(img, items, snap.intrinsics, snap.world_from_optical)
    return encode_png(img)


def render_depth(snap: Snapshot, near: float, far: float) -> Optional[bytes]:
    if snap.depth is None:
        return None
    return encode_png(depth_to_rgb(snap.depth, near, far))


# ---- static page (robot_core/web/) ---------------------------------------------------------------
WEB_DIR = Path(__file__).resolve().parent / 'web'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}


def load_assets(directory: Path = WEB_DIR) -> Dict[str, Tuple[str, bytes]]:
    """path -> (content type, bytes) of the page files; read once at start-up."""
    return {path: (ctype, (directory / name).read_bytes())
            for path, (name, ctype) in ASSETS.items()}


_RESPONDED = object()      # sentinel: the handler already answered the request
COMMAND_PATH = '/api/command'
STOP_PATH = '/api/stop'
RESET_PATH = '/api/reset'
JOBS_PATH = '/api/jobs'
POST_PATHS = (COMMAND_PATH, STOP_PATH, RESET_PATH, JOBS_PATH)
_JOB_PATH = re.compile(r'^/api/jobs/(j[0-9]{1,9})$')
_CANCEL_PATH = re.compile(r'^/api/jobs/(j[0-9]{1,9})/cancel$')
MAX_BODY = 512
MAX_REASON = 64
_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class WebView:
    """HTTP server around a :class:`ViewSource`. ``port=0`` picks a free port (see :attr:`port`)."""

    def __init__(self, source: ViewSource, host: str = '127.0.0.1', port: int = 8080,
                 depth_range: Tuple[float, float] = (0.5, 1.7),
                 extra_allowed_hosts: Sequence[str] = (),
                 commands: Optional[CommandRunner] = None,
                 supervisor: Optional[Supervisor] = None, mode: str = 'gazebo',
                 jobs: Optional[JobManager] = None):
        self.source = source
        self.commands = commands
        self.supervisor = supervisor
        self.jobs = jobs or (JobManager(commands, estop=supervisor.estop if supervisor else None)
                             if commands is not None else None)
        self.mode = mode
        self.depth_range = depth_range
        self.allowed_hosts = LOOPBACK_HOSTS | {h.lower() for h in extra_allowed_hosts}
        self._assets = load_assets()
        self._server = _Server((host, port), _Handler)
        self._server.view = self
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.05)

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        """Stop serving and release the port (safe to call from another thread or a signal
        handler thread; not from the thread running :meth:`serve_forever` inside a request)."""
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    # ---- routing ----------------------------------------------------------------------------
    def respond(self, path: str) -> Tuple[int, str, bytes]:
        """(status, content type, body) for a GET of ``path`` (query string already removed)."""
        if path in self._assets:
            ctype, body = self._assets[path]
            return 200, ctype, body
        if path == '/api/state':
            body = json.dumps(build_state(self.source.snapshot(), self.commands, self.supervisor,
                                          self.mode)).encode()
            return 200, 'application/json', body
        if path in ('/frame/rgb.png', '/frame/depth.png'):
            snap = self.source.snapshot()
            png = (render_rgb(snap) if path == '/frame/rgb.png'
                   else render_depth(snap, *self.depth_range))
            if png is None:
                return 503, 'application/json', b'{"error": "henuz kare yok"}'
            return 200, 'image/png', png
        return 404, 'application/json', b'{"error": "bulunamadi"}'


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    view: WebView


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = 'robot-web-view'
    protocol_version = 'HTTP/1.1'
    timeout = 30       # a client that stalls (or an idle keep-alive connection) is dropped

    def _host_allowed(self) -> bool:
        return host_name(self.headers.get('Host')) in self.server.view.allowed_hosts

    def _send(self, status: int, ctype: str, body: bytes, head_only: bool = False,
              extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', _CSP)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _get(self, head_only: bool) -> None:
        if not self._host_allowed():
            self._send(403, 'application/json', b'{"error": "host izinli degil"}', head_only)
            return
        path = urlsplit(self.path).path
        if path in (COMMAND_PATH, STOP_PATH, RESET_PATH):
            self._not_allowed()               # POST only
            return
        if path == JOBS_PATH or _JOB_PATH.match(path):
            self._get_jobs(path, head_only)
            return
        if _CANCEL_PATH.match(path):
            self._not_allowed()
            return
        try:
            status, ctype, body = self.server.view.respond(path)
        except Exception:    # noqa: BLE001 - a broken snapshot must not kill the server thread
            log.exception('istek islenemedi: %s', self.path)
            status, ctype, body = 500, 'application/json', b'{"error": "sunucu hatasi"}'
        self._send(status, ctype, body, head_only)

    def _get_jobs(self, path: str, head_only: bool) -> None:
        jobs = self.server.view.jobs
        if jobs is None:
            self._send(403, 'application/json', b'{"error": "komut ucu kapali"}', head_only)
            return
        if path == JOBS_PATH:
            status, payload = 200, {'jobs': jobs.recent()}
        else:
            doc = jobs.get(_JOB_PATH.match(path).group(1))
            status, payload = (200, doc) if doc is not None else (404, {'error': 'iş bulunamadı'})
        self._send(status, 'application/json', json.dumps(payload).encode(), head_only)

    def do_GET(self) -> None:    # noqa: N802
        self._get(head_only=False)

    def do_HEAD(self) -> None:    # noqa: N802
        self._get(head_only=True)

    # ---- everything that is not GET/HEAD -----------------------------------------------------
    def _json(self, status: int, payload: Dict[str, Any], close: bool = False,
              extra: Optional[Dict[str, str]] = None) -> None:
        headers = dict(extra or {})
        if close:
            self.close_connection = True     # the request body (if any) was not read
            headers['Connection'] = 'close'
        self._send(status, 'application/json', json.dumps(payload).encode(), extra=headers)

    def _not_allowed(self) -> None:
        path = urlsplit(self.path).path
        if path in (COMMAND_PATH, STOP_PATH, RESET_PATH) or _CANCEL_PATH.match(path):
            allow, why = 'POST', 'bu uç noktaya yalnızca POST yapılır'
        elif path == JOBS_PATH:
            allow, why = 'GET, HEAD, POST', 'GET (liste) ya da POST (iş başlat)'
        else:
            allow, why = 'GET, HEAD', 'salt okunur: yalnızca GET/HEAD'
        self._json(405, {'error': why}, close=True, extra={'Allow': allow})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed    # noqa: N815

    def _cross_site_reason(self) -> Optional[str]:
        """Why this POST looks like it comes from another web page (None: it does not)."""
        if (self.headers.get('Sec-Fetch-Site') or 'same-origin').lower() not in (
                'same-origin', 'none'):
            return 'başka bir siteden gelen istek (Sec-Fetch-Site)'
        origin = self.headers.get('Origin')
        if origin is not None:
            parts = urlsplit(origin)
            host = (self.headers.get('Host') or '').strip().lower()
            if parts.scheme != 'http' or not host or parts.netloc.lower() != host:
                return f'yabancı Origin: {origin[:60]}'
        return None

    def _guard_post(self):
        """Checks every POST door shares. Returns the runner, or None after answering."""
        view = self.server.view
        runner = view.commands
        if not self._host_allowed():
            self._json(403, {'error': 'host izinli degil'}, close=True)
            return None
        if runner is None:
            self._json(403, {'error': 'komut ucu kapalı: web_view komutlar açık başlatılmadı '
                                      '(commands:=true)'}, close=True)
            return None
        if self.path.split('?')[0] in (STOP_PATH, RESET_PATH) and view.supervisor is None:
            self._json(403, {'error': 'acil dur bu sunucuda bağlı değil'}, close=True)
            return None
        reason = self._cross_site_reason()
        if reason:
            runner.reject('', reason)
            self._json(403, {'error': reason}, close=True)
            return None
        return runner

    def _read_json_body(self, runner: CommandRunner):
        """The parsed JSON of a strict POST, or ``_RESPONDED`` after answering with an error."""
        if (self.headers.get('Content-Type') or '').split(';')[0].strip().lower() != \
                'application/json':
            self._json(415, {'error': "Content-Type: application/json gerekli"}, close=True)
            return _RESPONDED
        length = self.headers.get('Content-Length')
        if length is None:
            self._json(411, {'error': 'Content-Length gerekli'}, close=True)
            return _RESPONDED
        if not length.isdigit() or int(length) > MAX_BODY:
            self._json(413, {'error': f'gövde en fazla {MAX_BODY} bayt olabilir'}, close=True)
            return _RESPONDED
        raw = self.rfile.read(int(length))
        try:
            body = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            runner.reject('', 'JSON okunamadı')
            self._json(400, {'error': 'gövde geçerli JSON değil', 'allowed': runner.allowed})
            return _RESPONDED
        return body

    def do_POST(self) -> None:    # noqa: N802
        path = urlsplit(self.path).path
        cancel = _CANCEL_PATH.match(path)
        if path not in POST_PATHS and not cancel:
            self._not_allowed()
            return
        runner = self._guard_post()
        if runner is None:
            return
        if path == STOP_PATH:
            self._post_stop()
        elif path == RESET_PATH:
            self._post_reset(runner)
        elif path == JOBS_PATH:
            self._post_job(runner)
        elif cancel:
            self._post_cancel(runner, cancel.group(1))
        else:
            self._post_command(runner)

    def _post_stop(self) -> None:
        """Emergency stop. Deliberately lenient about the body (a hardware button or ``curl -X
        POST`` must work); the host and cross-site checks above still apply."""
        view = self.server.view
        reason = 'api'
        raw, unread = b'', False
        length = self.headers.get('Content-Length')
        if length is None:
            unread = bool(self.headers.get('Transfer-Encoding'))     # chunked: not read
        elif length.isdigit() and int(length) <= MAX_BODY:
            raw = self.rfile.read(int(length))
        else:
            unread = True
        if raw:
            try:
                body = json.loads(raw.decode('utf-8'))
                if isinstance(body, dict) and isinstance(body.get('reason'), str):
                    reason = ''.join(c for c in body['reason'][:MAX_REASON] if c.isprintable())
            except (UnicodeDecodeError, ValueError):
                pass                      # a stop is never refused over its body
        try:
            result = view.supervisor.stop(reason or 'api')
        except Exception:    # noqa: BLE001
            log.exception('acil dur işlenemedi')
            self._json(500, {'ok': False, 'error': 'iç hata; robot durdurulamadı olabilir',
                             'state': view.supervisor.state()}, close=True)
            return
        self._json(200, {'ok': True, 'state': view.supervisor.state(), **result.as_dict()},
                   close=unread)

    def _post_reset(self, runner: CommandRunner) -> None:
        body = self._read_json_body(runner)
        if body is _RESPONDED:
            return
        if body != {}:
            self._json(400, {'error': 'gövde boş bir JSON nesnesi {} olmalı'})
            return
        supervisor = self.server.view.supervisor
        result = supervisor.reset()
        payload = {'ok': result.ok, 'changed': result.changed, 'message': result.message,
                   'state': supervisor.state()}
        if result.ok:
            self._json(200, payload)
        else:
            self._json(409, {**payload, 'error': result.message})

    def _post_command(self, runner: CommandRunner) -> None:
        body = self._read_json_body(runner)
        if body is _RESPONDED:
            return
        if not isinstance(body, dict) or set(body) != {'command'}:
            runner.reject('', "gövde tam olarak {'command': ...} olmalı")
            self._json(400, {'error': 'gövde yalnızca {"command": "..."} olmalı',
                             'allowed': runner.allowed})
            return
        try:
            command = parse_command(body['command'])
        except CommandError as exc:
            runner.reject(body['command'], str(exc))
            self._json(400, {'error': str(exc), 'allowed': runner.allowed})
            return
        jobs = self.server.view.jobs
        try:
            outcome = jobs.run_sync(command) if jobs is not None else runner.run(command)
        except EStopped as exc:
            runner.reject(command.line, 'acil dur etkin')
            self._json(423, {'error': str(exc), 'state': 'estopped'})
            return
        except Busy as exc:
            self._json(409, {'error': str(exc), **({'job_id': exc.job_id}
                                                    if isinstance(exc, JobBusy) else {})})
            return
        status = 500 if outcome.internal_error else (200 if outcome.ok else 422)
        self._json(status, {'ok': outcome.ok, 'command': command.line, 'message': outcome.message,
                            'data': outcome.data, 'duration_s': outcome.duration_s,
                            'deprecated': 'POST /api/jobs kullan (bloklamaz, iptal edilebilir)'},
                   extra={'Deprecation': 'true'})

    def _post_job(self, runner: CommandRunner) -> None:
        body = self._read_json_body(runner)
        if body is _RESPONDED:
            return
        if not isinstance(body, dict) or 'command' not in body or set(body) - {'command',
                                                                               'timeout_s'}:
            runner.reject('', "gövde {'command': ..., 'timeout_s': ...(isteğe bağlı)} olmalı")
            self._json(400, {'error': 'gövde {"command": "...", "timeout_s": 60} olmalı '
                                      '(timeout_s isteğe bağlı)', 'allowed': runner.allowed})
            return
        try:
            command = parse_command(body['command'])
            timeout = check_timeout(body['timeout_s']) if 'timeout_s' in body else None
        except (CommandError, BadTimeout) as exc:
            runner.reject(body['command'], str(exc))
            self._json(400, {'error': str(exc), 'allowed': runner.allowed})
            return
        try:
            job = self.server.view.jobs.submit(command, timeout)
        except EStopped as exc:
            runner.reject(command.line, 'acil dur etkin')
            self._json(423, {'error': str(exc), 'state': 'estopped'})
            return
        except JobBusy as exc:
            self._json(409, {'error': str(exc), 'job_id': exc.job_id})
            return
        doc = self.server.view.jobs.get(job.id)
        self._json(202, doc, extra={'Location': f'{JOBS_PATH}/{job.id}'})

    def _post_cancel(self, runner: CommandRunner, job_id: str) -> None:
        body = self._read_json_body(runner)
        if body is _RESPONDED:
            return
        if body != {}:
            self._json(400, {'error': 'gövde boş bir JSON nesnesi {} olmalı'})
            return
        jobs = self.server.view.jobs
        try:
            _job, was_running = jobs.cancel(job_id)
        except JobNotFound:
            self._json(404, {'error': 'iş bulunamadı'})
            return
        doc = jobs.get(job_id)
        if not was_running:
            self._json(409, {'error': 'iş zaten bitti', 'job': doc})
            return
        self._json(200, {'ok': True, 'job': doc})

    def log_message(self, format: str, *args: Any) -> None:    # noqa: A002
        log.debug('%s - %s', self.address_string(), format % args)
