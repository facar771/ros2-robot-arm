import http.client
import json
import socket

import numpy as np
import pytest
from robot_core.perception import Intrinsics, optical_frame_in_world
from robot_core.viz import OVERLAY_COLOR, decode_png
from robot_core.webview import SceneObject, Snapshot, WebView, build_state, host_name
from robot_core.workspace import load_workspace, project

from tests.conftest import SRC
from tests.test_viz import box_size

WORLD = SRC / 'robot_sim' / 'worlds' / 'workspace.sdf'
JOINTS = tuple(f'joint_{i}' for i in range(1, 7))
STATE_KEYS = {'api_version', 'time', 'robot', 'camera', 'objects', 'objects_age_s', 'joints',
              'tool', 'commands', 'command'}


@pytest.fixture(scope='module')
def ws():
    return load_workspace(WORLD)


@pytest.fixture(scope='module')
def full_snapshot(ws):
    cam = ws.camera
    rgb = np.full((cam.height, cam.width, 3), (178, 204, 229), dtype=np.uint8)
    depth = np.full((cam.height, cam.width), 1.5, dtype=np.float32)
    depth[:20] = np.nan
    objs = tuple(SceneObject(s.name.split('_')[0], 0, s.center, box_size(s)) for s in ws.shapes)
    return Snapshot(
        rgb=rgb, depth=depth, rgb_age_s=0.05, depth_age_s=0.06, objects=objs, objects_age_s=0.2,
        intrinsics=Intrinsics.from_camera_spec(cam), world_from_optical=optical_frame_in_world(cam),
        joint_names=JOINTS, joint_positions={n: 0.1 * i for i, n in enumerate(JOINTS)},
        tool_link='link_6', tool_position=(0.1, -0.2, 0.3))


class Source:
    def __init__(self, snap):
        self.snap = snap
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        if isinstance(self.snap, Exception):
            raise self.snap
        return self.snap


def serve(snap, **kwargs):
    view = WebView(Source(snap), host='127.0.0.1', port=0, **kwargs)
    view.start_background()
    return view


@pytest.fixture
def view(full_snapshot):
    v = serve(full_snapshot)
    yield v
    v.shutdown()


def request(view, method='GET', path='/', headers=None, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


# ---- pages -------------------------------------------------------------------------------------
def test_index_and_static_assets(view):
    status, headers, body = request(view, path='/')
    assert status == 200 and headers['Content-Type'].startswith('text/html')
    html = body.decode()
    for needle in ('id="rgb"', 'id="depth"', 'id="objects"', 'id="joints"', 'id="tool"',
                   '/app.js', '/style.css'):
        assert needle in html
    js = request(view, path='/app.js')[2].decode()
    for needle in ('/frame/rgb.png', '/frame/depth.png', '/api/state'):
        assert needle in js
    assert request(view, path='/app.js')[1]['Content-Type'].startswith('text/javascript')
    assert request(view, path='/style.css')[1]['Content-Type'].startswith('text/css')
    assert 'no-store' in headers['Cache-Control']
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert "default-src 'none'" in headers['Content-Security-Policy']


def test_page_posts_only_to_the_robot_api_endpoints(view):
    js = request(view, path='/app.js')[2].decode()
    assert js.count("method: 'POST'") == 2           # sendCommand + the stop/reset helper
    assert js.count("'/api/command'") == 1 and js.count("'/api/stop'") == 1
    assert js.count("'/api/reset'") == 1
    assert 'PUT' not in js and 'DELETE' not in js


def test_query_string_is_ignored(view):
    assert request(view, path='/api/state?t=123')[0] == 200
    assert request(view, path='/frame/rgb.png?t=1')[0] == 200


def test_unknown_path_is_404(view):
    status, _, body = request(view, path='/nope')
    assert status == 404 and json.loads(body)['error']
    assert request(view, path='/api/nothing')[0] == 404


def test_head_has_headers_but_no_body(view):
    status, headers, body = request(view, 'HEAD', '/api/state')
    assert status == 200 and body == b'' and int(headers['Content-Length']) > 0


# ---- state -------------------------------------------------------------------------------------
def test_state_document(view, ws):
    status, headers, body = request(view, path='/api/state')
    assert status == 200 and headers['Content-Type'] == 'application/json'
    s = json.loads(body)
    assert set(s) == STATE_KEYS
    assert s['commands'] == {'enabled': False, 'allowed': []}      # read-only server
    assert s['command'] == {'running': False, 'name': None, 'job': None, 'log': [], 'last': None,
                            'last_error': None}
    assert s['camera'] == {'width': 320, 'height': 240, 'rgb_age_s': 0.05, 'depth_age_s': 0.06,
                           'overlay': True}
    assert [o['name'] for o in s['objects']] == ['red#0', 'green#0', 'blue#0']
    for obj, shape in zip(s['objects'], ws.shapes):
        assert obj['position'] == pytest.approx(shape.center)
        u, v, _ = project(ws, shape.center)
        assert obj['pixel']['center'] == pytest.approx([u, v], abs=0.01)
        u0, v0, u1, v1 = obj['pixel']['bbox']
        assert u0 < u < u1 and v0 < v < v1
        assert obj['pixel']['rect'] == [round(x) for x in obj['pixel']['bbox']]
    assert [j['name'] for j in s['joints']] == list(JOINTS)
    assert s['joints'][1]['rad'] == pytest.approx(0.1) and s['joints'][1]['deg'] == 5.73
    assert s['tool'] == {'link': 'link_6', 'position': [0.1, -0.2, 0.3]}


def test_state_without_any_data_has_the_same_keys_and_nulls():
    s = build_state(Snapshot())
    assert s['camera'] == {'width': None, 'height': None, 'rgb_age_s': None, 'depth_age_s': None,
                           'overlay': False}
    assert s['objects'] == [] and s['joints'] is None and s['tool'] is None
    assert set(s) == STATE_KEYS


def test_state_objects_without_projection_have_null_pixel():
    s = build_state(Snapshot(objects=(SceneObject('red', 0, (0.3, 0, 0.03), (0.06,) * 3),)))
    assert s['objects'][0]['pixel'] is None and s['camera']['overlay'] is False


def test_state_joints_incomplete_is_null():
    snap = Snapshot(joint_names=JOINTS, joint_positions={'joint_1': 0.0})
    assert build_state(snap)['joints'] is None


# ---- images ------------------------------------------------------------------------------------
def test_rgb_png_has_overlay(view, full_snapshot):
    status, headers, body = request(view, path='/frame/rgb.png')
    assert status == 200 and headers['Content-Type'] == 'image/png'
    img = decode_png(body)
    assert img.shape == (240, 320, 3)
    white = np.all(img == OVERLAY_COLOR, axis=2)
    assert white.sum() > 100
    # drawing happens on a copy: the source frame is not modified
    assert (full_snapshot.rgb == (178, 204, 229)).all()


def test_rgb_png_without_objects_is_the_plain_frame(full_snapshot):
    snap = Snapshot(rgb=full_snapshot.rgb)
    v = serve(snap)
    try:
        img = decode_png(request(v, path='/frame/rgb.png')[2])
    finally:
        v.shutdown()
    assert np.array_equal(img, full_snapshot.rgb)


def test_depth_png(view):
    status, headers, body = request(view, path='/frame/depth.png')
    assert status == 200 and headers['Content-Type'] == 'image/png'
    img = decode_png(body)
    assert img.shape == (240, 320, 3)
    assert tuple(img[0, 0]) == (48, 48, 48)               # NaN rows: no data
    assert tuple(img[100, 100]) != (48, 48, 48)


def test_frames_are_503_before_the_first_frame():
    v = serve(Snapshot())
    try:
        for path in ('/frame/rgb.png', '/frame/depth.png'):
            status, headers, body = request(v, path=path)
            assert status == 503 and headers['Content-Type'] == 'application/json'
        assert request(v, path='/api/state')[0] == 200
        assert request(v, path='/')[0] == 200
    finally:
        v.shutdown()


# ---- safety ------------------------------------------------------------------------------------
@pytest.mark.parametrize('method', ['POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def test_write_methods_are_405_on_every_read_only_path(view, method):
    for path in ('/', '/api/state', '/frame/rgb.png', '/api/nothing'):
        status, headers, _ = request(view, method, path, body=b'{"x": 1}',
                                     headers={'Content-Type': 'application/json'})
        assert status == 405 and headers['Allow'] == 'GET, HEAD'


@pytest.mark.parametrize('method', ['PUT', 'DELETE', 'PATCH', 'OPTIONS', 'GET', 'HEAD'])
def test_command_path_accepts_only_post(view, method):
    status, headers, _ = request(view, method, '/api/command', body=b'{}')
    assert status == 405 and headers['Allow'] == 'POST'


def test_post_to_command_path_is_403_when_the_server_has_no_commands(view):
    status, _, body = request(view, 'POST', '/api/command', body=b'{"command": "home"}',
                              headers={'Content-Type': 'application/json'})
    assert status == 403 and 'kapalı' in json.loads(body)['error']


def test_server_stays_usable_after_a_rejected_post_with_body(view):
    request(view, 'POST', '/api/state', body=b'x' * 5000)
    request(view, 'POST', '/api/command', body=b'x' * 5000)
    assert request(view, path='/api/state')[0] == 200


@pytest.mark.parametrize('host', ['evil.example.com', 'evil.example.com:8080', '127.0.0.1.evil.com',
                                  '192.168.1.5:8080', ''])
def test_foreign_host_header_is_403(view, host):
    status, _, _ = request(view, path='/api/state', headers={'Host': host})
    assert status == 403


@pytest.mark.parametrize('host', ['localhost', 'localhost:8080', '127.0.0.1:8080', '[::1]:8080',
                                  'LOCALHOST:8080'])
def test_loopback_host_header_is_accepted(view, host):
    assert request(view, path='/api/state', headers={'Host': host})[0] == 200


def test_extra_allowed_host(full_snapshot):
    v = serve(full_snapshot, extra_allowed_hosts=['robot.lan'])
    try:
        assert request(v, path='/api/state', headers={'Host': 'robot.lan:8080'})[0] == 200
        assert request(v, path='/api/state', headers={'Host': 'other.lan'})[0] == 403
    finally:
        v.shutdown()


def test_host_name_parsing():
    assert host_name('localhost:8080') == 'localhost'
    assert host_name('[::1]:8080') == '[::1]'
    assert host_name('[::1]') == '[::1]'
    assert host_name('Example.COM') == 'example.com'
    assert host_name(None) == ''


def test_a_failing_source_gives_500_and_the_server_survives():
    src = Source(RuntimeError('kaynak bozuk'))
    v = WebView(src, port=0)
    v.start_background()
    try:
        assert request(v, path='/api/state')[0] == 500
        src.snap = Snapshot()
        assert request(v, path='/api/state')[0] == 200
    finally:
        v.shutdown()


def test_shutdown_releases_the_port(full_snapshot):
    v = serve(full_snapshot)
    port = v.port
    v.shutdown()
    with socket.socket() as s:
        s.bind(('127.0.0.1', port))      # would raise if the listener were still open


def test_binds_only_to_the_requested_host(full_snapshot):
    v = serve(full_snapshot)
    try:
        assert v._server.server_address[0] == '127.0.0.1'
    finally:
        v.shutdown()


def test_page_files_are_packaged():
    from robot_core.webview import ASSETS, WEB_DIR
    for name, _ in ASSETS.values():
        assert (WEB_DIR / name).is_file()
    setup_py = (SRC / 'robot_core' / 'setup.py').read_text()
    assert "package_data={'robot_core': ['web/*']}" in setup_py   # colcon install must ship them
