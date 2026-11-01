"""/api/jobs over real HTTP (fake skills): 202, polling, cancel, timeout, busy/locked, guards."""
import http.client
import json
import threading
import time

import pytest
from robot_core.jobs import JobManager
from robot_core.supervisor import CancelFlag, EStop, Supervisor
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.fakes import CancelAwareSkills, FakeArm, FakeTool, RecordingSink

JSON = {'Content-Type': 'application/json'}
NAMES = ('joint_1', 'joint_2')


class Source:
    def snapshot(self):
        return Snapshot()


class Rig:
    def __init__(self):
        self.estop, self.cancel, self.sink = EStop(), CancelFlag(), RecordingSink()
        self.skills = CancelAwareSkills(self.cancel, self.estop)
        self.skills.entered = threading.Event()
        self.skills.release = threading.Event()
        self.arm = FakeArm(NAMES)
        self.runner = CommandRunner(self.skills, FakeTool((0, 0, 1)), self.sink, estop=self.estop)
        self.jobs = JobManager(self.runner, self.cancel, self.estop)
        self.supervisor = Supervisor(self.estop, self.arm, NAMES, stop_port=self.arm,
                                     runner=self.runner, events=self.sink)
        self.view = WebView(Source(), port=0, commands=self.runner, supervisor=self.supervisor,
                            jobs=self.jobs)
        self.view.start_background()

    def request(self, method, path, payload=None, headers=None, raw=None):
        body = raw
        if body is None and payload is not None:
            body = json.dumps(payload).encode()
        sent = JSON if headers is None else headers
        conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=20)
        try:
            conn.request(method, path, body=body, headers=sent)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), json.loads(data) if data else None
        finally:
            conn.close()

    def submit(self, command='above green 0.25', **extra):
        return self.request('POST', '/api/jobs', {'command': command, **extra})

    def job(self, job_id):
        return self.request('GET', f'/api/jobs/{job_id}')[2]

    def wait(self, job_id, state, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            doc = self.job(job_id)
            if doc['state'] == state:
                return doc
            time.sleep(0.02)
        raise AssertionError(f'{job_id}: {self.job(job_id)["state"]}, beklenen {state}')

    def close(self):
        self.skills.release.set()
        self.view.shutdown()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def test_post_answers_202_at_once_with_the_job_document(rig):
    started = time.monotonic()
    status, headers, doc = rig.submit('above green 0.25')
    assert time.monotonic() - started < 2.0 and status == 202
    assert doc['state'] == 'running' and doc['command'] == 'above green 0.25'
    assert headers['Location'] == f"/api/jobs/{doc['id']}" and doc['timeout_s'] == 120.0
    assert rig.skills.entered.wait(5)
    rig.skills.release.set()
    done = rig.wait(doc['id'], 'done')
    assert done['result']['ok'] and done['result']['data']['target'] == [0.4, -0.1, 0.35]
    assert done['progress']['log'][0] == '> above green 0.25' and done['elapsed_s'] >= 0


def test_state_shows_the_running_job_with_the_same_id(rig):
    _, _, doc = rig.submit()
    assert rig.skills.entered.wait(5)
    robot = rig.request('GET', '/api/state')[2]['robot']
    assert robot['state'] == 'busy' and robot['current_job']['id'] == doc['id']


def test_a_second_job_while_one_runs_is_409_with_its_id(rig):
    _, _, doc = rig.submit()
    assert rig.skills.entered.wait(5)
    status, _, body = rig.submit('home')
    assert status == 409 and body['job_id'] == doc['id'] and doc['id'] in body['error']
    status, _, body = rig.request('POST', '/api/command', {'command': 'home'})
    assert status == 409 and body['job_id'] == doc['id']


def test_cancel_ends_the_job_and_nothing_stays_locked(rig):
    _, _, doc = rig.submit()
    assert rig.skills.entered.wait(5)
    status, _, body = rig.request('POST', f"/api/jobs/{doc['id']}/cancel", {})
    assert status == 200 and body['ok'] and body['job']['state'] == 'cancelled'
    assert body['job']['cancelled_by'] == 'user'
    assert rig.request('GET', '/api/state')[2]['robot']['state'] == 'idle'
    status, _, again = rig.request('POST', f"/api/jobs/{doc['id']}/cancel", {})
    assert status == 409 and again['job']['state'] == 'cancelled'
    rig.skills.release.set()
    assert rig.submit('above red 0.35')[0] == 202             # no reset needed


def test_cancel_errors(rig):
    assert rig.request('POST', '/api/jobs/j99/cancel', {})[0] == 404
    _, _, doc = rig.submit()
    path = f"/api/jobs/{doc['id']}/cancel"
    assert rig.request('POST', path, {'x': 1})[0] == 400
    assert rig.request('POST', path, raw=b'{}', headers={'Content-Type': 'text/plain'})[0] == 415
    status, headers, _ = rig.request('GET', path)
    assert status == 405 and headers['Allow'] == 'POST'
    assert rig.job(doc['id'])['state'] == 'running'            # none of those cancelled it


def test_timeout_fails_the_job(rig):
    _, _, doc = rig.submit(timeout_s=1)
    failed = rig.wait(doc['id'], 'failed', timeout=8)
    assert failed['timed_out'] and 'zaman aşımı' in failed['error'] and failed['timeout_s'] == 1.0


@pytest.mark.parametrize('extra', [{'timeout_s': 0}, {'timeout_s': 601}, {'timeout_s': 'x'},
                                   {'timeout_s': True}, {'timeout_s': None}])
def test_bad_timeout_is_400(rig, extra):
    status, _, body = rig.submit(**extra)
    assert status == 400 and 'timeout_s' in body['error'] and rig.skills.calls == []


@pytest.mark.parametrize('body', [{}, {'command': 'home', 'x': 1}, {'cmd': 'home'}, [], 'home'])
def test_bad_bodies_are_400(rig, body):
    assert rig.request('POST', '/api/jobs', body)[0] == 400


def test_invalid_commands_are_400_and_never_start_a_job(rig):
    for command in ('goto 1 2 3', 'above purple', 'home; reboot', 5):
        assert rig.submit(command)[0] == 400
    assert rig.skills.calls == [] and rig.request('GET', '/api/jobs')[2]['jobs'] == []


def test_locked_by_the_estop_is_423(rig):
    rig.estop.trip('x')
    status, _, body = rig.submit('home')
    assert status == 423 and body['state'] == 'estopped' and rig.skills.calls == []


def test_list_and_lookup(rig):
    rig.skills.release.set()
    ids = []
    for command in ('objects', 'home'):
        _, _, doc = rig.submit(command)
        rig.wait(doc['id'], 'done')
        ids.append(doc['id'])
    listing = rig.request('GET', '/api/jobs')[2]['jobs']
    assert [j['id'] for j in listing] == ids[::-1]
    assert rig.request('GET', '/api/jobs/j999')[0] == 404
    for path in ('/api/jobs/abc', '/api/jobs/j1/x', '/api/jobs/../state', '/api/jobs/j1/'):
        assert rig.request('GET', path)[0] in (404, 405), path


def test_methods(rig):
    status, headers, _ = rig.request('PUT', '/api/jobs', {})
    assert status == 405 and headers['Allow'] == 'GET, HEAD, POST'
    status, headers, _ = rig.request('DELETE', '/api/jobs/j1')
    assert status == 405 and headers['Allow'] == 'GET, HEAD'


def test_old_command_endpoint_still_works_and_is_marked_deprecated(rig):
    rig.skills.release.set()
    status, headers, body = rig.request('POST', '/api/command', {'command': 'above green 0.25'})
    assert status == 200 and body['ok'] and body['command'] == 'above green 0.25'
    assert headers['Deprecation'] == 'true' and '/api/jobs' in body['deprecated']
    recent = rig.request('GET', '/api/jobs')[2]['jobs']
    assert recent[0]['command'] == 'above green 0.25' and recent[0]['state'] == 'done'


def test_old_command_endpoint_reports_a_cancelled_job_as_422(rig):
    answers = []
    worker = threading.Thread(target=lambda: answers.append(
        rig.request('POST', '/api/command', {'command': 'above green 0.25'})))
    worker.start()
    assert rig.skills.entered.wait(5)
    job_id = rig.request('GET', '/api/state')[2]['robot']['current_job']['id']
    rig.request('POST', f'/api/jobs/{job_id}/cancel', {})
    worker.join(10)
    assert answers[0][0] == 422 and answers[0][2]['ok'] is False


@pytest.mark.parametrize('headers', [{'Origin': 'http://evil.example', **JSON},
                                     {'Sec-Fetch-Site': 'cross-site', **JSON},
                                     {'Host': 'evil.example', **JSON}])
def test_cross_site_and_foreign_host_are_403(rig, headers):
    assert rig.request('POST', '/api/jobs', {'command': 'home'}, headers)[0] == 403
    assert rig.request('POST', '/api/jobs/j1/cancel', {}, headers)[0] == 403
    assert rig.skills.calls == []


def test_foreign_host_cannot_read_jobs_either(rig):
    assert rig.request('GET', '/api/jobs', headers={'Host': 'evil.example'})[0] == 403


def test_jobs_are_403_without_commands():
    view = WebView(Source(), port=0)
    view.start_background()
    try:
        for method, path in (('GET', '/api/jobs'), ('GET', '/api/jobs/j1'),
                             ('POST', '/api/jobs'), ('POST', '/api/jobs/j1/cancel')):
            conn = http.client.HTTPConnection('127.0.0.1', view.port, timeout=5)
            conn.request(method, path, body=b'{}' if method == 'POST' else None, headers=JSON)
            resp = conn.getresponse()
            resp.read()
            conn.close()
            assert resp.status == 403, (method, path)
    finally:
        view.shutdown()
