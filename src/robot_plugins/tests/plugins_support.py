"""Helpers for the robot_plugins tests (not a test module): a stub Robot API v1 server over HTTP and
a factory for throw-away plugins."""
import json
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from robot_plugins.robot_api import FakeRobotApi, RobotApiError

REPO = Path(__file__).resolve().parents[3]
PLUGINS = REPO / 'plugins'


class StubRobotServer:
    """Speaks the Robot API v1 over HTTP on 127.0.0.1 (backed by FakeRobotApi), so HttpRobotApi and
    plugin subprocesses can be tested without a simulator."""

    def __init__(self, job_duration: float = 0.3):
        self.fake = FakeRobotApi(job_duration=job_duration)
        fake = self.fake

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get('Content-Length') or 0)
                return json.loads(self.rfile.read(n) or b'{}')

            def _run(self, fn):
                try:
                    self._send(*fn())
                except RobotApiError as exc:
                    self._send(exc.status, {'error': exc.message, **exc.body})

            def do_GET(self):          # noqa: N802
                if self.path == '/api/state':
                    return self._run(lambda: (200, {'api_version': 1, 'robot': fake.state()}))
                if self.path.startswith('/api/jobs/'):
                    return self._run(lambda: (200, fake.job(self.path.rsplit('/', 1)[1])))
                self._send(404, {'error': 'yok'})

            def do_POST(self):         # noqa: N802
                body = self._body()
                if self.path == '/api/jobs':
                    return self._run(lambda: (202, fake.submit_job(body['command'],
                                                                   body.get('timeout_s'))))
                if self.path == '/api/stop':
                    return self._run(lambda: (200, fake.stop(body.get('reason', 'http'))))
                if self.path.endswith('/cancel'):
                    return self._run(lambda: (200, fake.cancel(self.path.split('/')[3])))
                self._send(404, {'error': 'yok'})

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_plugin(root: Path, name: str, *, permissions=(), code: str = '', tools=None,
                parameters=None) -> Path:
    """Create ``root/<name>/{package.yaml,main.py}`` and return the directory."""
    d = root / name
    d.mkdir(parents=True)
    tools = tools or [{'name': 'echo', 'description': 'yankı',
                       'input_schema': {'type': 'object',
                                        'properties': {'text': {'type': 'string'}},
                                        'required': ['text'],
                                        'additionalProperties': False}}]
    import yaml
    (d / 'package.yaml').write_text(yaml.safe_dump({
        'name': name, 'version': '0.0.1', 'description': 'test eklentisi', 'entry': 'main:Plugin',
        'permissions': list(permissions), 'parameters': parameters or {'type': 'object',
                                                                       'properties': {}},
        'tools': tools}), encoding='utf-8')
    (d / 'main.py').write_text(textwrap.dedent(code or '''
        from robot_plugins import PluginBase

        class Plugin(PluginBase):
            def call(self, tool, args):
                return {'echo': args['text']}
    '''), encoding='utf-8')
    return d
