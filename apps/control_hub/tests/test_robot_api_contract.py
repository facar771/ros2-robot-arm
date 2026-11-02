"""RobotApiBackend + Hub, robotun **gerçek HTTP sunucu koduna** karşı (Robot API v1).

``tools.robot_stub``: ``robot_core.webview.WebView`` / ``CommandRunner`` / ``JobManager`` /
``Supervisor`` gerçek koddur (ROS'suz çalışır), yalnızca kol ve hareket simüle edilir. Bu yüzden
burada sınanan şey, Hub'ın sözleşmeyle (durum kodları, iş yaşam döngüsü, hız doğrulamalı acil dur,
423/409 kuralları) gerçekten uyuşması: kendi yazdığımız bir taklitle değil.

Repo kökündeki ``src/robot_core`` ya da numpy yoksa dosya atlanır.
"""
import http.server
import json
import threading
import time

import pytest

pytest.importorskip('numpy')
robot_stub = pytest.importorskip('tools.robot_stub')

from control_hub.hub_core import (  # noqa: E402
    AccessControl,
    EstopLatchedError,
    Hub,
    ResetRefusedError,
    RobotApiBackend,
)
from tests.conftest import make_hub  # noqa: E402

FAST = dict(poll_s=0.05, job_poll_s=0.05, request_timeout_s=3.0)


def wait_for(pred, timeout=5.0, what='koşul'):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f'zaman aşımı: {what}')


@pytest.fixture
def stub():
    s = robot_stub.RobotStub(time_scale=0.05)
    yield s
    s.close()


def connect(stub, scenarios, ui, **kw):
    backend = RobotApiBackend(stub.url, **{**FAST, **kw})
    assert backend.wait_first_poll(3)
    wait_for(lambda: backend.snapshot()['connected'], what='bağlantı')
    hub = Hub(backend, scenarios, ui, AccessControl('open'))
    return hub, backend


@pytest.fixture
def rig(stub, scenarios, ui):
    hub, backend = connect(stub, scenarios, ui)
    yield stub, hub, backend
    hub.close()


def test_snapshot_mirrors_the_v1_state(rig):
    stub, hub, backend = rig
    snap = backend.snapshot()
    assert snap['connected'] and snap['compatible'] and snap['problem'] is None
    assert snap['api_version'] == 1 and snap['mode'] == 'gazebo' and snap['state'] == 'idle'
    assert snap['estop']['tripped'] is False and snap['can_interrupt'] is True
    assert len(snap['joints']) == 6 and snap['tool'] == list(robot_stub.HOME)
    assert {o['label'] for o in snap['objects']} == {'red', 'green', 'blue'}
    assert snap['safety']['zone']['box']['z'] == [0.02, 0.70]


def test_demo_tour_runs_through_the_real_jobs_api(rig):
    stub, hub, backend = rig
    hub.run_scenario('demo_tour')
    assert hub.runner.wait_idle(20)
    last = hub.runner.status()['last']
    assert last['ok'], last
    jobs = [e for e in hub.events.since(0) if e.kind == 'step_finished']
    assert [e.data['command'] for e in jobs] == ['home', 'above green 0.25', 'above red 0.35', 'home']
    assert stub.arm.tool == pytest.approx(robot_stub.HOME, abs=1e-6)         # sonunda eve döndü
    # her adım robotta gerçek bir iş olarak görüldü
    status, listing = backend._request('GET', '/api/jobs')
    assert status == 200
    docs = listing.get('jobs') or listing.get('value') or []
    assert len(docs) >= 4 and all(d['state'] == 'done' for d in docs[:4])


def test_demo_tour_matches_the_robots_own_default_steps(scenarios):
    from robot_core.scenario import DEFAULT_STEPS
    theirs = []
    for st in DEFAULT_STEPS:
        theirs.append('home' if st.action[0] == 'home' else f'above {st.action[1]} {st.action[2]:g}')
    assert [s.command for s in scenarios['demo_tour'].steps] == theirs


def test_robot_failure_is_reported_and_stops_the_scenario(rig):
    stub, hub, _ = rig
    stub.skills.objects = []                                  # robot "nesne yok" der: 422/failed
    hub.run_scenario('demo_tour')
    assert hub.runner.wait_idle(10)
    last = hub.runner.status()['last']
    assert not last['ok'] and 'adım 2/4' in last['message'] and 'bilinmeyen nesne' in last['message']


def test_cancel_cancels_the_robot_job_without_latching(stub, scenarios, ui):
    stub.arm.scale = 1.0                                      # gerçek hızda: hareket ~1 sn
    hub, _ = connect(stub, scenarios, ui)
    try:
        hub.run_scenario('demo_tour')
        wait_for(lambda: stub.runner.status()['running'], what='robot işi başladı')
        t0 = time.monotonic()
        hub.cancel_scenario()
        assert hub.runner.wait_idle(5) and time.monotonic() - t0 < 3
        last = hub.runner.status()['last']
        assert last['cancelled'] and not last['ok']
        wait_for(lambda: stub.supervisor.state() == 'idle', what='robot hazır')
        assert not stub.estop.is_set                          # iptal kilitlemez
        hub.run_scenario('home')                              # hemen yeni iş kabul edilir
        assert hub.runner.wait_idle(10) and hub.runner.status()['last']['ok']
    finally:
        hub.close()


def test_estop_calls_api_stop_and_reports_the_verification(stub, scenarios, ui):
    stub.arm.scale = 1.0
    hub, backend = connect(stub, scenarios, ui)
    try:
        hub.run_scenario('demo_tour')
        wait_for(lambda: stub.runner.status()['running'], what='robot işi başladı')
        time.sleep(0.2)
        hub.estop('test')
        report = hub.wait_estop_report(10)
        assert report is not None and report.stopped is True, report
        assert report.details['max_joint_speed'] is not None
        assert report.details['stop_time_s'] < 5 and report.details['was_running'] is True
        assert stub.estop.is_set and stub.arm.stop_calls >= 1       # /api/stop gerçekten çağrıldı
        assert hub.runner.wait_idle(5)
        assert hub.runner.status()['last']['cancelled']
        ev = [e.kind for e in hub.events.since(0)]
        assert 'estop_confirmed' in ev and 'estop_unverified' not in ev
        with pytest.raises(EstopLatchedError):
            hub.run_scenario('home')
        wait_for(lambda: backend.snapshot()['estop']['tripped'], what='robot kilidi görüldü')
        hub.reset_estop('test')                                      # /api/reset
        assert not stub.estop.is_set and not hub.estopped
        hub.run_scenario('home')
        assert hub.runner.wait_idle(10) and hub.runner.status()['last']['ok']
    finally:
        hub.close()


def test_estop_not_verified_is_reported_loudly(rig):
    stub, hub, _ = rig
    stub.supervisor._stop_timeout_s = 0.6                     # 5 sn beklemeden sınayalım
    stub.arm._vel = 1.0                                       # kol "durmuyor"
    stub.arm.joint_velocities = lambda: {'joint_1': 1.0}      # type: ignore[method-assign]
    hub.estop('test')
    report = hub.wait_estop_report(10)
    assert report is not None and report.stopped is False
    snap = hub.snapshot()['estop']
    assert snap['latched'] and snap['report']['stopped'] is False
    crit = [e for e in hub.events.since(0) if e.kind == 'estop_unverified']
    assert crit and crit[0].level == 'critical' and 'fiziksel' in crit[0].message


def test_robot_side_estop_locks_the_hub_too(rig):
    stub, hub, backend = rig
    status, doc = backend._request('POST', '/api/stop', {'reason': 'başka istemci'})
    assert status == 200 and doc['stopped']
    wait_for(lambda: hub.snapshot()['estop']['latched'], what='robot kilidi Hub\'a yansıdı')
    snap = hub.snapshot()['estop']
    assert snap['by_robot_only'] is True and hub.estopped
    with pytest.raises(EstopLatchedError):
        hub.run_scenario('home')
    hub.reset_estop('test')
    wait_for(lambda: not hub.snapshot()['estop']['latched'], what='kilit kalktı')
    hub.run_scenario('home')
    assert hub.runner.wait_idle(10) and hub.runner.status()['last']['ok']


def test_reset_refused_while_arm_is_moving_keeps_everything_latched(rig):
    stub, hub, _ = rig
    hub.estop('test')
    assert hub.wait_estop_report(5).stopped is True
    stub.arm.joint_velocities = lambda: {'joint_1': 1.0}      # type: ignore[method-assign]
    with pytest.raises(ResetRefusedError):
        hub.reset_estop('test')
    assert stub.estop.is_set and hub.snapshot()['estop']['latched']
    assert 'estop_reset_refused' in [e.kind for e in hub.events.since(0)]
    stub.arm.joint_velocities = lambda: {n: 0.0 for n in robot_stub.JOINTS}   # type: ignore
    hub.reset_estop('test')
    assert not stub.estop.is_set


def test_robot_unreachable(monkeypatch, scenarios, ui):
    monkeypatch.setattr(time, 'sleep', lambda s: None)        # ACİL DUR yeniden denemeleri beklemesin
    backend = RobotApiBackend('http://127.0.0.1:9', **FAST)   # 9 = discard: kimse dinlemiyor
    hub = make_hub(scenarios, ui)
    hub.backend = backend
    hub.runner._backend = backend
    try:
        assert backend.wait_first_poll(3)
        snap = backend.snapshot()
        assert snap['connected'] is False and 'ulaşılamıyor' in snap['problem']
        res = backend.execute('home', threading.Event())
        assert not res.ok and res.data.get('unavailable')
        hub.run_scenario('home')
        assert hub.runner.wait_idle(5) and not hub.runner.status()['last']['ok']
        hub.estop('test')                                      # robot yok: Hub yine kilitler
        report = hub.wait_estop_report(10)
        assert hub.estopped and report.stopped is False and 'ulaşılamadı' in report.message
        assert not backend.reset_emergency_stop().ok
        with pytest.raises(ResetRefusedError):
            hub.reset_estop('test')
    finally:
        backend.close()
        hub.close()


class _OldRobot(http.server.BaseHTTPRequestHandler):
    version_doc = {'api_version': 2, 'robot': {'api_version': 2}, 'commands': {'enabled': True}}

    def do_GET(self):                                          # noqa: N802
        body = json.dumps(self.version_doc).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_incompatible_api_version_is_refused(scenarios, ui):
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _OldRobot)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    backend = RobotApiBackend(f'http://127.0.0.1:{srv.server_port}', **FAST)
    try:
        assert backend.wait_first_poll(3)
        snap = backend.snapshot()
        assert snap['connected'] and snap['compatible'] is False and 'sürümü 2' in snap['problem']
        res = backend.execute('home', threading.Event())
        assert not res.ok and 'sürümü 2' in res.message
    finally:
        backend.close()
        srv.shutdown()


def test_commands_disabled_on_robot_is_reported(stub, scenarios, ui):
    stub.view.commands = None                                  # salt okunur web_view gibi
    stub.view.supervisor = None
    backend = RobotApiBackend(stub.url, **FAST)
    try:
        assert backend.wait_first_poll(3)
        assert backend.snapshot()['compatible'] is False
    finally:
        backend.close()


def test_connection_loss_mid_job_is_reported_as_unknown(stub, scenarios, ui):
    stub.arm.scale = 1.0
    backend = RobotApiBackend(stub.url, **FAST, max_poll_failures=3)
    try:
        assert backend.wait_first_poll(3)
        wait_for(lambda: backend.snapshot()['connected'])
        out = {}
        t = threading.Thread(target=lambda: out.update(r=backend.execute('home', threading.Event())))
        t.start()
        wait_for(lambda: stub.runner.status()['running'])
        stub.close()                                           # robot API'si düştü
        t.join(10)
        res = out['r']
        assert not res.ok and res.data.get('unknown') and res.data['job_id']
    finally:
        backend.close()


def test_http_api_end_to_end_with_the_real_robot(stub, scenarios, ui):
    """Telefondaki akış: Hub HTTP API'si → RobotApiBackend → robotun gerçek sunucusu."""
    from fastapi.testclient import TestClient

    from control_hub.server import create_app
    hub, backend = connect(stub, scenarios, ui)
    try:
        c = TestClient(create_app(hub), base_url='http://localhost')
        r = c.post('/api/scenarios/above_green/run', json={})
        assert r.status_code == 202
        assert hub.runner.wait_idle(10)
        st = c.get('/api/state').json()
        assert st['runner']['last']['ok'] and st['robot']['name'] == 'robot-api'
        assert st['robot']['mode'] == 'gazebo' and st['robot']['moving'] in (True, False, None)
        r = c.post('/api/estop')
        assert r.status_code == 200 and r.json()['interrupts_motion'] is True
        hub.wait_estop_report(10)
        st = c.get('/api/state').json()['estop']
        assert st['latched'] and st['report']['stopped'] is True and not st['pending']
        assert c.post('/api/scenarios/home/run', json={}).status_code == 423
        assert c.post('/api/estop/reset', json={}).status_code == 200
        assert c.post('/api/scenarios/home/run', json={}).status_code == 202
        hub.runner.wait_idle(10)
        # robot kolu hareket ederken sıfırlama reddedilir -> 409 reset_refused
        c.post('/api/estop')
        hub.wait_estop_report(10)
        stub.arm.joint_velocities = lambda: {'joint_1': 1.0}   # type: ignore[method-assign]
        r = c.post('/api/estop/reset', json={})
        assert r.status_code == 409 and r.json()['error'] == 'reset_refused'
    finally:
        hub.close()
