"""Read-only browser view of the robot (stdlib ``http.server`` only, no ROS).

The ROS node (``robot_viz.web_view``) only fills a :class:`Snapshot`; everything else lives here so
it can be tested without ROS:

    GET /                 HTML page (RGB + overlay, depth, object table, joints, tool position)
    GET /app.js, /style.css
    GET /frame/rgb.png    camera image with the detected objects drawn on it
    GET /frame/depth.png  depth colour map
    GET /api/state        JSON: objects (+ pixel boxes), joint angles, tool position, frame ages

There is deliberately no endpoint that changes anything. Every other method gets ``405``.
Requests whose ``Host`` header is not a loopback name get ``403`` (DNS-rebinding guard); bind to
127.0.0.1 unless a container port mapping needs 0.0.0.0, and then restrict the *published* port to
127.0.0.1 (see compose.yaml).
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlsplit

import numpy as np

from robot_core.perception import Intrinsics, Transform
from robot_core.viz import OverlayItem, box_to_pixels, depth_to_rgb, draw_overlay, encode_png

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


def build_state(snap: Snapshot) -> Dict[str, Any]:
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
        'time': round(time.time(), 3),
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


_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class WebView:
    """HTTP server around a :class:`ViewSource`. ``port=0`` picks a free port (see :attr:`port`)."""

    def __init__(self, source: ViewSource, host: str = '127.0.0.1', port: int = 8080,
                 depth_range: Tuple[float, float] = (0.5, 1.7),
                 extra_allowed_hosts: Sequence[str] = ()):
        self.source = source
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
        self._server.serve_forever(poll_interval=0.2)

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
            body = json.dumps(build_state(self.source.snapshot())).encode()
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
        try:
            status, ctype, body = self.server.view.respond(urlsplit(self.path).path)
        except Exception:    # noqa: BLE001 - a broken snapshot must not kill the server thread
            log.exception('istek islenemedi: %s', self.path)
            status, ctype, body = 500, 'application/json', b'{"error": "sunucu hatasi"}'
        self._send(status, ctype, body, head_only)

    def do_GET(self) -> None:    # noqa: N802
        self._get(head_only=False)

    def do_HEAD(self) -> None:    # noqa: N802
        self._get(head_only=True)

    def _not_allowed(self) -> None:
        self.close_connection = True      # the request body, if any, is never read
        self._send(405, 'application/json', b'{"error": "salt okunur: yalnizca GET/HEAD"}',
                   extra={'Allow': 'GET, HEAD', 'Connection': 'close'})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed    # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:    # noqa: A002
        log.debug('%s - %s', self.address_string(), format % args)
