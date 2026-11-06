"""L9b: GET /api/model - view model over the Robot API (read-only, no paths, 503 if bad)."""

import copy
import http.client
import json

import pytest
from robot_core.modelapi import ModelApi, ModelUnavailable, load_model
from robot_core.supervisor import EStop, Supervisor
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.conftest import SRC
from tests.fakes import CancelAwareSkills, FakeArm, FakeTool, RecordingSink

FILE = SRC / 'robot_description' / 'profile' / 'generated' / 'view_model.json'


class Source:
    def snapshot(self):
        return Snapshot()


class Rig:
    def __init__(self, model, with_commands=False):
        self.estop = EStop()
        self.arm = FakeArm(['joint_1'])
        kwargs = {}
        if with_commands:
            kwargs['commands'] = CommandRunner(CancelAwareSkills(), FakeTool((0, 0, 1)),
                                               RecordingSink(), estop=self.estop)
            kwargs['supervisor'] = Supervisor(self.estop, self.arm, ['joint_1'],
                                              stop_port=self.arm, events=RecordingSink())
        self.view = WebView(Source(), port=0, model=model, **kwargs)
        self.view.start_background()

    def req(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=10)
        body = None if payload is None else json.dumps(payload).encode()
        h = {'Content-Type': 'application/json'} if body is not None else {}
        h.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def close(self):
        self.view.shutdown()


@pytest.fixture
def rig(config):
    r = Rig(ModelApi.from_file(FILE, config.joint_names), with_commands=True)
    yield r
    r.close()


def write(tmp_path, text):
    path = tmp_path / 'view_model.json'
    path.write_text(text, encoding='utf-8')
    return path


def test_serves_the_file_minus_underscore_notes(rig):
    status, headers, body = rig.req('GET', '/api/model')
    served = json.loads(body)
    original = json.loads(FILE.read_text(encoding='utf-8'))
    assert status == 200 and headers['Content-Type'] == 'application/json'
    assert headers['Cache-Control'] == 'no-store'
    assert served == {k: v for k, v in original.items() if not k.startswith('_')}
    assert '_generated' not in served and served['view_model_version'] == 1
    assert rig.req('HEAD', '/api/model')[0] == 200


def test_model_joint_names_equal_the_robots_joint_names(config):
    """The names in the Robot API state 'joints' (URDF/SRDF = RobotConfig) must be the model's."""
    doc = load_model(FILE)
    assert [j['name'] for j in doc['joints']] == list(config.joint_names)
    assert all(len(v) == len(config.joint_names) for v in doc['named_poses'].values())


def test_mismatching_joint_names_make_the_door_503(tmp_path, config):
    doc = json.loads(FILE.read_text(encoding='utf-8'))
    doc['joints'][2]['name'] = 'joint_three'
    api = ModelApi.from_file(write(tmp_path, json.dumps(doc)), config.joint_names)
    assert 'eşleşmiyor' in api.error and 'joint_three' in api.error
    r = Rig(api)
    try:
        status, _, body = r.req('GET', '/api/model')
        assert status == 503 and 'eşleşmiyor' in json.loads(body)['error']
        assert r.req('GET', '/api/state')[0] == 200            # the Robot API itself is up
    finally:
        r.close()
    short = copy.deepcopy(doc)
    short['joints'] = short['joints'][:5]
    with pytest.raises(ModelUnavailable):
        load_model(write(tmp_path, json.dumps(short)), config.joint_names)


@pytest.mark.parametrize('text, reason', [
    ('{kırık', 'geçerli JSON değil'),
    ('[]', 'JSON nesnesi değil'),
    ('{"view_model_version": 1}', 'eksik alan'),
    ('{"view_model_version": NaN}', 'geçerli JSON değil'),
])
def test_corrupt_file_is_a_clear_503_and_the_api_still_runs(tmp_path, text, reason):
    path = write(tmp_path, text)
    api = ModelApi.from_file(path)
    r = Rig(api)
    try:
        status, _, body = r.req('GET', '/api/model')
        message = json.loads(body)['error']
        assert status == 503 and reason in message and 'robot modeli kullanılamıyor' in message
        assert str(path) not in message and str(tmp_path) not in message     # no path leaks
        assert r.req('GET', '/api/state')[0] == 200
    finally:
        r.close()


def test_wrong_version_and_bool_version(tmp_path):
    doc = json.loads(FILE.read_text(encoding='utf-8'))
    for bad in (2, True, '1', None):
        doc['view_model_version'] = bad
        with pytest.raises(ModelUnavailable, match='view_model_version'):
            load_model(write(tmp_path, json.dumps(doc)))


def test_missing_and_unreadable_file_do_not_leak_the_path(tmp_path):
    missing = tmp_path / 'gizli_dizin' / 'view_model.json'
    api = ModelApi.from_file(missing)
    assert api.error == 'model dosyası bulunamadı'
    directory = tmp_path / 'klasor'
    directory.mkdir()
    api = ModelApi.from_file(directory)                       # IsADirectoryError
    assert api.error.startswith('model dosyası okunamadı') and 'klasor' not in api.error
    (tmp_path / 'ikili').write_bytes(b'\xff\xfe\x00')
    api = ModelApi.from_file(tmp_path / 'ikili')
    assert 'ikili' not in api.error and api.error


def test_no_path_or_secret_in_the_served_body(rig):
    body = rig.req('GET', '/api/model')[2].decode()
    for needle in ('/home', '/workspace', '/opt', '/tmp', 'src/', '.yaml', 'ÜRETİLDİ',
                   'password', 'token', 'pin', 'secret'):
        assert needle.lower() not in body.lower(), needle


def test_read_only_methods_and_query_ignored(rig):
    for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        status, headers, _ = rig.req(method, '/api/model', {} if method != 'DELETE' else None)
        assert status == 405 and 'GET' in headers.get('Allow', 'GET'), method
    a = rig.req('GET', '/api/model')[2]
    assert rig.req('GET', '/api/model?pin=1234&x=/etc/passwd')[2] == a
    assert rig.req('GET', '/api/model', headers={'Host': 'evil.example'})[0] == 403


def test_estop_and_423_are_not_touched(rig):
    rig.req('POST', '/api/stop', {})
    assert rig.estop.is_set
    assert rig.req('GET', '/api/model')[0] == 200             # a latched e-stop must not hide it
    status, _, _ = rig.req('POST', '/api/command', {'command': 'home'})
    assert status == 423                                      # movement is still refused


def test_door_closed_when_not_wired():
    r = Rig(None)
    try:
        assert r.req('GET', '/api/model')[0] == 403
    finally:
        r.close()
