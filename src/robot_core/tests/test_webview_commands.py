"""POST /api/command over real HTTP (fake skills): the white list, the browser-attack guards and the
status codes."""
import http.client
import json
import socket
import threading

import pytest
from robot_core.webcmd import USAGE, CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.fakes import FakeSkills, FakeTool, RecordingSink

JSON = {'Content-Type': 'application/json'}


class Source:
    def snapshot(self):
        return Snapshot()


def start(skills=None, enabled=True):
    sink = RecordingSink()
    skills = skills or FakeSkills()
    runner = CommandRunner(skills, FakeTool((0.0, 0.0, 1.0)), sink) if enabled else None
    view = WebView(Source(), port=0, commands=runner)
    view.start_background()
    return view, skills, sink


@pytest.fixture
def server():
    view, skills, sink = start()
    yield view, skills, sink
    view.shutdown()


def post(view, command=None, *, raw=None, headers=None, path='/api/command'):
    body = raw if raw is not None else json.dumps({'command': command}).encode()
    conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=15)
    try:
        conn.request('POST', path, body=body, headers=JSON if headers is None else headers)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, dict(resp.getheaders()), json.loads(data) if data else None
    finally:
        conn.close()


def state(view):
    conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=5)
    try:
        conn.request('GET', '/api/state')
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


# ---- what works --------------------------------------------------------------------------------
def test_home(server):
    view, skills, _ = server
    status, headers, body = post(view, 'home')
    assert status == 200 and body['ok'] is True and body['command'] == 'home'
    assert body['duration_s'] >= 0 and 'no-store' in headers['Cache-Control']
    assert skills.calls == [('go_to_named_pose', 'home')]


def test_objects(server):
    view, _, _ = server
    status, _, body = post(view, 'objects')
    assert status == 200 and [o['name'] for o in body['data']['objects']] == [
        'obj_red_1', 'obj_green_1']


def test_above_with_colour_and_height(server):
    view, skills, _ = server
    status, _, body = post(view, 'Above  Yeşil 0,25')
    assert status == 200 and body['command'] == 'above green 0.25'
    assert body['data']['target'][2] == pytest.approx(0.05 + 0.05 + 0.25)
    assert skills.calls == [('go_above', 'green', 0.25)]


def test_same_origin_header_is_accepted(server):
    view, _, _ = server
    origin = f'http://127.0.0.1:{view.port}'
    headers = dict(JSON, Origin=origin, **{'Sec-Fetch-Site': 'same-origin'})
    assert post(view, 'home', headers=headers)[0] == 200
    headers = dict(JSON, Origin=f'http://localhost:{view.port}', Host=f'localhost:{view.port}')
    assert post(view, 'home', headers=headers)[0] == 200


def test_json_content_type_with_charset_is_fine(server):
    view, _, _ = server
    assert post(view, 'home', headers={'Content-Type': 'application/json; charset=utf-8'})[0] == 200


def test_state_shows_the_finished_command_and_its_log(server):
    view, _, _ = server
    post(view, 'above red 0.3')
    s = state(view)
    assert s['commands']['enabled'] is True and s['commands']['allowed'] == list(USAGE)
    assert s['command']['running'] is False
    assert s['command']['log'][0] == '> above red 0.3'
    assert s['command']['last']['command'] == 'above red 0.3' and s['command']['last']['ok']


# ---- refused commands: 400, the robot is never asked -------------------------------------------
@pytest.mark.parametrize('command', [
    'dance', 'goto 1 2 3', 'move home', 'home now', 'above', 'above purple', 'above green 9',
    'above green abc', 'above green; reboot', 'above green -1', 'above green nan', '', 'x' * 100,
    'home\nobjects', '$(id)', 'demo_scenario now'])
def test_invalid_commands_are_400_with_the_allowed_list(server, command):
    view, skills, sink = server
    status, _, body = post(view, command)
    assert status == 400 and body['error'] and body['allowed'] == list(USAGE)
    assert skills.calls == []
    assert sink.events and sink.events[-1][0] == 'command_rejected'   # recorded for the run history


@pytest.mark.parametrize('raw', [b'not json', b'', b'\xff\xfe', b'[]', b'"home"', b'null', b'5',
                                 b'{}', b'{"command": 5}', b'{"command": null}',
                                 b'{"command": "home", "extra": 1}', b'{"cmd": "home"}',
                                 b'{"command": ["home"]}', b'{"command": {"a": 1}}'])
def test_bad_bodies_are_400(server, raw):
    view, skills, _ = server
    status, _, body = post(view, raw=raw)
    assert status == 400 and body['error']
    assert skills.calls == []


def test_duplicate_keys_cannot_smuggle_a_command(server):
    """A JSON object with two 'command' keys: Python keeps the last; either way only one parsed
    string is ever executed and it is checked against the white list."""
    view, skills, _ = server
    status, _, body = post(view, raw=b'{"command": "home", "command": "dance"}')
    assert status == 400 and skills.calls == []


# ---- guards against other web pages and odd clients --------------------------------------------
@pytest.mark.parametrize('headers', [
    dict(JSON, Origin='http://evil.example.com'),
    dict(JSON, Origin='https://127.0.0.1:1'),
    dict(JSON, Origin='null'),
    dict(JSON, Origin='http://127.0.0.1:1'),                 # right host, wrong port
    dict(JSON, **{'Sec-Fetch-Site': 'cross-site'}),
    dict(JSON, **{'Sec-Fetch-Site': 'same-site'}),
])
def test_cross_site_posts_are_403(server, headers):
    view, skills, sink = server
    status, _, body = post(view, 'home', headers=headers)
    assert status == 403 and skills.calls == []
    assert sink.events[-1][0] == 'command_rejected'


@pytest.mark.parametrize('host', ['evil.example.com', 'evil.example.com:8080', '192.168.1.5', ''])
def test_foreign_host_header_is_403(server, host):
    view, skills, _ = server
    assert post(view, 'home', headers=dict(JSON, Host=host))[0] == 403
    assert skills.calls == []


@pytest.mark.parametrize('content_type', ['text/plain', 'application/x-www-form-urlencoded',
                                          'multipart/form-data; boundary=x', 'application/jsonx',
                                          'text/json', ''])
def test_other_content_types_are_415(server, content_type):
    """text/plain, form and multipart are the 'simple' types a hostile page can send without a
    pre-flight; they must never reach the robot."""
    view, skills, _ = server
    headers = {'Content-Type': content_type} if content_type else {}
    status, _, _ = post(view, 'home', headers=headers)
    assert status == 415 and skills.calls == []


def test_oversized_body_is_413_and_not_read(server):
    view, skills, _ = server
    status, _, body = post(view, raw=b'{"command": "home", "pad": "' + b'x' * 600 + b'"}')
    assert status == 413 and skills.calls == []


def test_missing_and_bogus_content_length():
    view, skills, _ = start()
    try:
        for header, expected in (('', 411), ('Content-Length: abc\r\n', 413),
                                 ('Content-Length: -5\r\n', 413)):
            with socket.create_connection(('127.0.0.1', view.port), timeout=5) as s:
                s.sendall(('POST /api/command HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                           'Content-Type: application/json\r\n' + header + '\r\n').encode())
                assert s.recv(4096).split(b' ')[1] == str(expected).encode()
        assert skills.calls == []
    finally:
        view.shutdown()


def test_chunked_body_is_not_accepted():
    view, skills, _ = start()
    try:
        with socket.create_connection(('127.0.0.1', view.port), timeout=5) as s:
            s.sendall(b'POST /api/command HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                      b'Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n'
                      b'18\r\n{"command": "home"}\r\n0\r\n\r\n')
            assert s.recv(4096).split(b' ')[1] == b'411'
        assert skills.calls == []
    finally:
        view.shutdown()


def test_server_survives_all_of_the_above(server):
    view, skills, _ = server
    post(view, 'dance')
    post(view, raw=b'garbage')
    post(view, 'home', headers={'Content-Type': 'text/plain'})
    assert post(view, 'home')[0] == 200
    assert skills.calls == [('go_to_named_pose', 'home')]


# ---- valid command the robot cannot do, internal errors, busy ----------------------------------
def test_command_the_robot_refuses_is_422_with_the_reason():
    view, skills, _ = start(FakeSkills(fail='erişim alanı dışında, robot hareket etmedi'))
    try:
        status, _, body = post(view, 'above red 0')
        assert status == 422 and body['ok'] is False
        assert 'robot hareket etmedi' in body['message']
        assert state(view)['command']['last']['ok'] is False
    finally:
        view.shutdown()


def test_unknown_object_is_422():
    view, _, _ = start(FakeSkills(objects=[]))
    try:
        status, _, body = post(view, 'above blue')
        assert status == 422 and 'blue' in body['message']
    finally:
        view.shutdown()


def test_internal_error_is_500_without_leaking_details():
    view, _, _ = start(FakeSkills(raises=RuntimeError('parola=hunter2 /home/x')))
    try:
        status, _, body = post(view, 'home')
        assert status == 500 and 'hunter2' not in json.dumps(body)
        assert 'RuntimeError' in body['message']
        assert post(view, 'objects')[0] == 200                    # and the server is fine
    finally:
        view.shutdown()


def test_second_command_while_one_runs_is_409_and_state_shows_it_live():
    block, entered = threading.Event(), threading.Event()
    skills = FakeSkills(block=block)
    skills.entered = entered
    view, _, _ = start(skills)
    try:
        first = []
        worker = threading.Thread(target=lambda: first.append(post(view, 'above green 0.25')))
        worker.start()
        assert entered.wait(5)
        s = state(view)
        assert s['command']['running'] is True and s['command']['name'] == 'above green 0.25'
        assert s['command']['log'] == ['> above green 0.25']
        status, _, body = post(view, 'home')
        assert status == 409 and 'above green' in body['error']
        assert ('go_to_named_pose', 'home') not in skills.calls
        block.set()
        worker.join(5)
        assert first[0][0] == 200
        assert post(view, 'home')[0] == 200                       # free again
    finally:
        block.set()
        view.shutdown()


def test_reads_stay_available_while_a_command_runs():
    block, entered = threading.Event(), threading.Event()
    skills = FakeSkills(block=block)
    skills.entered = entered
    view, _, _ = start(skills)
    try:
        worker = threading.Thread(target=lambda: post(view, 'above red'))
        worker.start()
        assert entered.wait(5)
        assert state(view)['command']['running'] is True          # /api/state is not blocked
    finally:
        block.set()
        view.shutdown()


def test_demo_scenario_over_http():
    class Tool:
        """Reports exactly where the fake skills were told to go."""
        def __init__(self, skills):
            self.skills = skills

        def tool_position(self):
            for call in reversed(self.skills.calls):
                if call[0] == 'go_to_named_pose':
                    return (0.0, 0.0, 1.24)
                obj = next(o for o in self.skills.objects if o.label == call[1])
                return (obj.position[0], obj.position[1], obj.top_z + call[2])
    from functools import partial

    from robot_core.models import ObjectInfo
    from robot_core.scenario import run_demo_scenario
    skills = FakeSkills()
    skills.objects.append(ObjectInfo('obj_blue_1', 'blue', (0.3, 0.0, 0.04), (0.08,) * 3))
    runner = CommandRunner(skills, Tool(skills), RecordingSink(),
                           scenario=partial(run_demo_scenario, settle_s=0.0,
                                            sleep=lambda _: None))
    view = WebView(Source(), port=0, commands=runner)
    view.start_background()
    try:
        status, _, body = post(view, 'demo_scenario')
        assert status == 200, body
        assert body['ok'] and [s['step'] for s in body['data']['steps']] == [1, 2, 3, 4]
        assert all(s['ok'] for s in body['data']['steps'])
        log = state(view)['command']['log']
        assert log[0] == '> demo_scenario' and any(line.startswith('[1/4]') for line in log)
    finally:
        view.shutdown()
