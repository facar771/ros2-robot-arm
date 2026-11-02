import http.client
import json
import threading

import pytest
from plugins_support import StubRobotServer, make_plugin
from robot_plugins.api import PluginApiServer

from robot_plugins import PluginManager


@pytest.fixture()
def api(tmp_path):
    robot = StubRobotServer(job_duration=0.2)
    make_plugin(tmp_path, 'lab', permissions=['camera:read'], parameters={
        'type': 'object', 'properties': {'rate': {'type': 'number', 'minimum': 1, 'default': 5}}})
    mgr = PluginManager(tmp_path, robot_api=robot.fake, robot_url=robot.url, grace_s=1.0)
    server = PluginApiServer(mgr, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, mgr, robot
    server.shutdown()
    server.server_close()
    mgr.shutdown()
    robot.close()


def request(server, method, path, body=None, headers=None, raw=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=15)
    hdr = dict(headers or {})
    payload = raw
    if body is not None:
        payload = json.dumps(body)
        hdr.setdefault('Content-Type', 'application/json')
    conn.request(method, path, body=payload, headers=hdr)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, (json.loads(data) if data else None), resp


def test_it_only_binds_to_loopback(tmp_path):
    mgr = PluginManager(tmp_path)
    with pytest.raises(ValueError, match='loopback'):
        PluginApiServer(mgr, host='0.0.0.0')
    with pytest.raises(ValueError):
        PluginApiServer(mgr, host='192.168.1.5')


def test_list_and_detail(api):
    server, _, _ = api
    status, doc, resp = request(server, 'GET', '/api/plugins')
    assert status == 200 and doc['api_version'] == 1
    assert resp.getheader('Cache-Control') == 'no-store'
    assert [p['name'] for p in doc['plugins']] == ['lab']
    assert doc['plugins'][0]['status'] == 'stopped'
    status, doc, _ = request(server, 'GET', '/api/plugins/lab')
    assert status == 200 and doc['manifest']['tools'][0]['name'] == 'echo'
    assert request(server, 'GET', '/api/plugins/ghost')[0] == 404
    assert request(server, 'GET', '/api/nothing')[0] == 404


def test_enable_disable_with_parameters(api):
    server, mgr, _ = api
    status, doc, _ = request(server, 'POST', '/api/plugins/lab/enable', {'params': {'rate': 3}})
    assert status == 200 and doc['status'] == 'running' and doc['params'] == {'rate': 3}
    assert mgr.call('lab', 'echo', {'text': 'x'})['ok']
    status, doc, _ = request(server, 'GET', '/api/plugins/lab/logs')
    assert status == 200 and isinstance(doc['logs'], list)
    status, doc, _ = request(server, 'POST', '/api/plugins/lab/disable', {})
    assert status == 200 and doc['status'] == 'stopped'


def test_bad_parameters_and_unknown_plugin_are_client_errors(api):
    server, _, _ = api
    assert request(server, 'POST', '/api/plugins/lab/enable', {'params': {'rate': 0}})[0] == 400
    assert request(server, 'POST', '/api/plugins/lab/enable', {'params': [1]})[0] == 400
    assert request(server, 'POST', '/api/plugins/ghost/enable', {})[0] == 404


def test_emergency_stop_endpoint_stops_plugins_and_latches_the_robot(api):
    server, _, robot = api
    request(server, 'POST', '/api/plugins/lab/enable', {})
    status, doc, _ = request(server, 'POST', '/api/plugins/estop', {'reason': 'Hub düğmesi'})
    assert status == 200 and doc['plugins_stopped'] == ['lab'] and doc['robot']['latched']
    assert robot.fake.latched and robot.fake.stops == ['Hub düğmesi']
    status, doc, _ = request(server, 'POST', '/api/plugins/lab/enable', {})
    assert status == 423 and doc['code'] == 'estop'


@pytest.mark.parametrize('headers, expect', [
    ({'Host': 'evil.example'}, 403), ({'Host': 'evil.example:8770'}, 403),
    ({'Origin': 'https://evil.example'}, 403), ({'Sec-Fetch-Site': 'cross-site'}, 403),
    ({'Sec-Fetch-Site': 'same-origin'}, 200), ({'Origin': 'http://localhost:3000'}, 200),
    ({'Host': 'localhost:8770'}, 200), ({'Host': '[::1]:8770'}, 200)])
def test_get_is_guarded_against_foreign_hosts_and_origins(api, headers, expect):
    server, _, _ = api
    assert request(server, 'GET', '/api/plugins', headers=headers)[0] == expect


def test_post_guards_content_type_size_and_origin(api):
    server, _, _ = api
    assert request(server, 'POST', '/api/plugins/lab/enable', raw='{}',
                   headers={'Content-Type': 'text/plain'})[0] == 415
    assert request(server, 'POST', '/api/plugins/lab/enable', raw='{',
                   headers={'Content-Type': 'application/json'})[0] == 400
    assert request(server, 'POST', '/api/plugins/lab/enable', raw='[1]',
                   headers={'Content-Type': 'application/json'})[0] == 400
    assert request(server, 'POST', '/api/plugins/lab/enable', raw='x' * 5000,
                   headers={'Content-Type': 'application/json'})[0] == 413
    assert request(server, 'POST', '/api/plugins/lab/enable', {},
                   headers={'Origin': 'https://evil.example'})[0] == 403
    assert request(server, 'POST', '/api/plugins/lab/enable', {},
                   headers={'Sec-Fetch-Site': 'cross-site'})[0] == 403
    status, _, resp = request(server, 'PUT', '/api/plugins/lab')
    assert status == 405 and 'GET' in resp.getheader('Allow')


def test_tools_cannot_be_called_over_http(api):
    server, mgr, _ = api
    mgr.enable('lab')
    for path in ('/api/plugins/lab/call', '/api/plugins/lab/tools/echo'):
        assert request(server, 'POST', path, {'text': 'x'})[0] == 404
