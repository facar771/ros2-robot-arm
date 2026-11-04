"""J2: lease / dead-man / e-stop / exclusion around the J1 engine; /api/jog + /api/frames (HTTP)."""

import http.client
import json
import time

import numpy as np
import pytest
from robot_core.frames import FrameStore
from robot_core.jobs import JobManager
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.jogapi import (
    READY_POSE,
    JogApiConfig,
    JogController,
    JogDenied,
    clamp_ttl_ms,
)
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.safezone import SafetyConfig, SafeZone
from robot_core.supervisor import CancelFlag, EStop, Supervisor
from robot_core.webcmd import CommandRunner
from robot_core.webview import Snapshot, WebView

from tests.fakes import CancelAwareSkills, FakeArm, FakeTool, RecordingSink

LEASE = 'client-aaaaaaaa'
OTHER = 'client-bbbbbbbb'
TICK = 0.02
P = load_profile(PROFILE_PATH)
CHAIN = Chain(P)
NAMES = CHAIN.joint_names
Q0 = np.array(
    [0.15, -0.231, 1.8, -0.471, 1.404, 1.131]
)  # TCP ~ (0.45, 0, 0.45), well inside the box


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class Plant:
    """Perfect follower: the measured joints jump to the first point of the last setpoint."""

    def __init__(self, q):
        self.q = np.array(q, float)
        self.sent = []  # (time, q_first, q_last, horizon)
        self.holds = 0
        self.clock = None

    def joint_positions(self):
        return dict(zip(NAMES, self.q.tolist()))

    def send(self, names, positions, times_s):
        self.sent.append(
            (self.clock(), np.array(positions[0]), np.array(positions[-1]), times_s[-1])
        )
        self.q = np.array(positions[0])

    def hold(self, names, position):
        self.holds += 1


class Rig:
    def __init__(self, tmp_path, q=Q0, busy=False, safety=None, **api):
        self.clock, self.plant, self.estop = Clock(), Plant(q), EStop()
        self.plant.clock = self.clock
        self.busy = busy
        self.frames = FrameStore(tmp_path / 'frames.yaml')
        engine = DlsJogEngine(CHAIN, safety or P.safety, JogConfig())
        self.jog = JogController(
            engine,
            CHAIN,
            self.plant,
            self.plant,
            self.estop,
            self.frames,
            config=JogApiConfig(**api),
            busy=lambda: self.busy,
            clock=self.clock,
        )

    def tick(self, n=1):
        for _ in range(n):
            self.clock.t += TICK
            self.jog.tick()

    def hold(self, lease=LEASE, twist=(0.05, 0, 0, 0, 0, 0), **extra):
        return self.jog.command({'lease': lease, 'frame': 'World', 'twist': list(twist), **extra})


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def denied(fn, *a, **k):
    with pytest.raises(JogDenied) as exc:
        fn(*a, **k)
    return exc.value


# ---- TTL: the server decides ----------------------------------------------------------------
def test_ttl_default_and_hard_ceiling():
    assert clamp_ttl_ms(None) == 250 and clamp_ttl_ms(100) == 100
    assert clamp_ttl_ms(5000) == 500 and clamp_ttl_ms(1) == 50
    assert (
        denied(clamp_ttl_ms, 'x').status == 400 and denied(clamp_ttl_ms, float('nan')).status == 400
    )


def test_reply_reports_a_clamped_ttl(rig):
    doc = rig.hold(ttl_ms=60000)
    assert doc['ttl_ms'] == 500 and doc['ttl_clamped'] is True
    assert rig.hold()['ttl_ms'] == 250


def test_velocity_reaches_zero_within_150_ms_of_the_ttl_and_nothing_is_sent_after(rig):
    rig.hold(ttl_ms=250)
    rig.tick(10)  # moving
    moved = rig.plant.sent[-1]
    assert np.linalg.norm(moved[2] - moved[1]) > 0
    expiry = rig.jog._expires
    rig.tick(40)
    last_motion = [s for s in rig.plant.sent if np.linalg.norm(s[2] - s[1]) > 1e-9][-1][0]
    assert last_motion - expiry <= 0.15 + 1e-9, (
        'komut hızı TTL bitişinden sonra 150 ms içinde sıfırlanmalı'
    )
    n = len(rig.plant.sent)
    rig.tick(20)
    assert len(rig.plant.sent) == n, 'sıfır hızda setpoint akıtılmaz'


def test_setpoint_horizon_is_at_most_100_ms(rig):
    rig.hold()
    rig.tick(5)
    assert all(s[3] <= 0.1 + 1e-9 for s in rig.plant.sent)


def test_commands_keep_it_alive_and_it_follows_the_asked_direction(rig):
    p0 = CHAIN.fk(rig.plant.q)[:3, 3]
    for _ in range(25):
        rig.hold(ttl_ms=100)
        rig.tick()
    p1 = CHAIN.fk(rig.plant.q)[:3, 3]
    assert p1[0] - p0[0] > 0.005 and abs(p1[1] - p0[1]) < 1e-3


# ---- lease ----------------------------------------------------------------------------------
def test_second_client_gets_409_until_the_lease_is_free(rig):
    rig.hold()
    e = denied(rig.hold, OTHER)
    assert e.status == 409
    rig.tick(15)  # TTL over, still reserved for lease_hold_s
    assert denied(rig.hold, OTHER).status == 409
    rig.tick(110)  # > 2 s later
    assert rig.hold(OTHER)['ok']


def test_release_frees_the_lease_at_once(rig):
    rig.hold()
    rig.jog.command({'lease': LEASE, 'ttl_ms': 0})
    assert rig.hold(OTHER)['ok']


@pytest.mark.parametrize(
    'body',
    [
        {},
        {'lease': 'kisa'},
        {'lease': LEASE},
        {'lease': LEASE, 'twist': [0] * 6, 'increment': {}},
        {'lease': LEASE, 'twist': [0] * 5},
        {'lease': LEASE, 'twist': ['a'] * 6},
        {'lease': LEASE, 'twist': [5, 0, 0, 0, 0, 0]},
        {'lease': LEASE, 'twist': [0] * 6, 'x': 1},
        {'lease': LEASE, 'twist': [0] * 6, 'frame': 'yok'},
    ],
)
def test_bad_bodies_are_400(rig, body):
    assert denied(rig.jog.command, body).status == 400


# ---- e-stop, jobs ------------------------------------------------------------------------------
def test_estop_cancels_the_jog_and_every_post_is_423(rig):
    rig.hold()
    rig.tick(5)
    n = len(rig.plant.sent)
    rig.estop.trip('test')
    rig.tick()
    assert rig.plant.holds == 1 and len(rig.plant.sent) == n and not rig.jog.active
    assert denied(rig.hold).status == 423
    assert denied(rig.jog.command, {'lease': LEASE, 'ttl_ms': 0}).status == 423


def test_a_job_and_a_jog_exclude_each_other(rig):
    rig.busy = True
    assert denied(rig.hold).status == 409
    rig.busy = False
    rig.hold()
    rig.tick(3)
    rig.busy = True  # a job started: jog is dropped on the next tick
    rig.tick()
    assert not rig.jog.active and rig.plant.holds == 1


def test_lost_joint_states_end_the_session(rig):
    rig.hold()
    rig.plant.joint_positions = lambda: None
    rig.tick()
    assert (
        not rig.jog.active and rig.plant.holds == 0
    )  # blind: no hold possible, the <=100 ms horizon ends
    assert denied(rig.hold).status == 503


# ---- modes ----------------------------------------------------------------------------------
def test_hold_mode_can_be_switched_off_and_is_off_for_remote_clients_by_default(tmp_path):
    r = Rig(tmp_path, hold_enabled=False)
    assert denied(r.hold).status == 403
    r2 = Rig(tmp_path)
    assert (
        denied(r2.jog.command, {'lease': LEASE, 'twist': [0.01, 0, 0, 0, 0, 0]}, remote=True).status
        == 403
    )
    r3 = Rig(tmp_path, hold_remote_enabled=True)
    assert r3.jog.command({'lease': LEASE, 'twist': [0.01, 0, 0, 0, 0, 0]}, remote=True)['ok']


def test_increment_moves_exactly_the_asked_distance_and_only_allowed_amounts(rig):
    p0 = CHAIN.fk(rig.plant.q)[:3, 3]
    rig.jog.command({'lease': LEASE, 'increment': {'axis': 0, 'amount': 10}})
    rig.tick(80)
    p1 = CHAIN.fk(rig.plant.q)[:3, 3]
    assert abs((p1 - p0)[0] - 0.010) < 1e-4 and abs((p1 - p0)[1:]).max() < 1e-4
    assert rig.jog.status()['status'] == 'done'
    for amount in (0.5, 100, 0, float('inf')):
        assert (
            denied(
                rig.jog.command, {'lease': LEASE, 'increment': {'axis': 0, 'amount': amount}}
            ).status
            == 400
        )
    assert (
        denied(rig.jog.command, {'lease': LEASE, 'increment': {'axis': 6, 'amount': 1}}).status
        == 400
    )


def test_increment_cannot_be_stacked(rig):
    rig.jog.command({'lease': LEASE, 'increment': {'axis': 0, 'amount': 10}})
    assert (
        denied(rig.jog.command, {'lease': LEASE, 'increment': {'axis': 1, 'amount': 1}}).status
        == 409
    )


def test_speed_is_the_small_default_fraction(rig):
    st = rig.jog.status()['speed']
    assert st['max_linear_m_s'] == pytest.approx(0.02) and st['max_angular_rad_s'] == pytest.approx(
        0.1
    )
    rig.hold(twist=(1.0, 0, 0, 0, 0, 0))
    rig.tick(40)
    step = np.linalg.norm(
        CHAIN.fk(rig.plant.sent[-1][1])[:3, 3] - CHAIN.fk(rig.plant.sent[-2][1])[:3, 3]
    )
    assert step / TICK <= 0.0205  # the server clipped 1.0 m/s to the cap


# ---- limits: wall slide, singularity, ready pose --------------------------------------------
def test_slides_along_the_wall_and_never_leaves_the_box(tmp_path):
    x0 = CHAIN.fk(Q0)[0, 3]
    zone = SafeZone((0.05, -0.5, 0.02), (x0 + 0.03, 0.5, 0.7))  # a wall the arm can reach
    r = Rig(tmp_path, safety=SafetyConfig(zone, 0.5))
    for _ in range(600):  # push +X for 12 s at the wall with a bit of +Y
        r.hold(twist=(0.1, 0.05, 0, 0, 0, 0), ttl_ms=100)
        r.tick()
        t = CHAIN.fk(r.plant.q)[:3, 3]
        assert t[0] <= zone.upper[0] + 1e-6, 'kutudan çıkılmamalı'
    t = CHAIN.fk(r.plant.q)[:3, 3]
    assert t[0] > zone.upper[0] - 0.012 and t[1] > 0.05, 'duvar boyunca kayma beklenir'


def test_singularity_is_reported_with_a_message(tmp_path):
    r = Rig(tmp_path, q=np.zeros(6))  # upright home: wrist singularity (and outside the box)
    r.hold(twist=(0, 0, -0.05, 0, 0, 0))
    r.tick(5)
    doc = r.jog.status()
    assert doc['status'] in ('singularity', 'box_stop', 'joint_limit') and doc['messages']
    assert not r.plant.sent or all(np.linalg.norm(s[2] - s[1]) == 0 for s in r.plant.sent[-3:])


def test_ready_pose_is_inside_the_box_away_from_singularity_and_limits():
    q = np.array(READY_POSE)
    t = CHAIN.fk(q)[:3, 3]
    zone = P.safety.zone
    margin = min(min(t - np.array(zone.lower)), min(np.array(zone.upper) - t))
    sigma = np.linalg.svd(CHAIN.jacobian(q), compute_uv=False)[-1]
    limit = min(np.min(q - CHAIN.lower), np.min(CHAIN.upper - q))
    assert zone.check(tuple(t)) is None and margin > 0.08
    assert sigma > JogConfig().sigma_slow * 2 and limit > 0.3


# ---- over HTTP ------------------------------------------------------------------------------
class Source:
    def snapshot(self):
        return Snapshot()


class Http:
    def __init__(self, tmp_path):
        self.estop, self.cancel, self.sink = EStop(), CancelFlag(), RecordingSink()
        self.skills = CancelAwareSkills(self.cancel, self.estop)
        self.arm = FakeArm(NAMES)
        self.runner = CommandRunner(self.skills, FakeTool((0, 0, 1)), self.sink, estop=self.estop)
        self.jobs = JobManager(self.runner, self.cancel, self.estop)
        self.plant = Plant(Q0)
        self.plant.clock = time.monotonic
        self.frames = FrameStore(tmp_path / 'data' / 'frames.yaml')
        self.jog = JogController(
            DlsJogEngine(CHAIN, P.safety, JogConfig()),
            CHAIN,
            self.plant,
            self.plant,
            self.estop,
            self.frames,
            busy=lambda: self.jobs.is_running,
        )
        self.jobs.jog_gate = lambda: self.jog.active
        self.supervisor = Supervisor(
            self.estop,
            self.arm,
            NAMES,
            stop_port=self.arm,
            runner=self.runner,
            events=self.sink,
            jog=self.jog,
        )
        self.view = WebView(
            Source(),
            port=0,
            commands=self.runner,
            supervisor=self.supervisor,
            jobs=self.jobs,
            jog=self.jog,
            frames=self.frames,
        )
        self.view.start_background()
        self.jog.start()
        self.conn = http.client.HTTPConnection('127.0.0.1', self.view.port, timeout=10)

    def req(self, method, path, payload=None, conn=None):
        c = conn or self.conn
        body = None if payload is None else json.dumps(payload).encode()
        c.request(method, path, body=body, headers={'Content-Type': 'application/json'})
        r = c.getresponse()
        data = r.read()
        return r.status, json.loads(data) if data else None

    def close(self):
        self.jog.close()
        self.view.shutdown()


@pytest.fixture
def web(tmp_path):
    w = Http(tmp_path)
    yield w
    w.close()


def test_http_keep_alive_409_423_and_state(web):
    body = {'lease': LEASE, 'frame': 'World', 'twist': [0.05, 0, 0, 0, 0, 0]}
    first = web.conn
    for _ in range(5):
        status, doc = web.req('POST', '/api/jog', body)  # the same TCP connection every time
        assert status == 200 and doc['ok']
    assert web.conn.sock is first.sock and web.conn.sock is not None
    status, state = web.req('GET', '/api/state')
    assert (
        state['robot']['state'] == 'jogging'
        and state['robot']['jog']['active']
        and state['tcp']['position']
    )
    other = http.client.HTTPConnection('127.0.0.1', web.view.port, timeout=10)
    assert web.req('POST', '/api/jog', {**body, 'lease': OTHER}, conn=other)[0] == 409
    assert web.req('POST', '/api/jobs', {'command': 'home'})[0] == 409  # jog excludes jobs
    status, stop = web.req('POST', '/api/stop', {'reason': 'test'}, conn=other)
    assert status == 200 and stop['latched']
    assert web.req('POST', '/api/jog', body)[0] == 423
    assert web.req('GET', '/api/jog')[0] == 200
    other.close()


def test_http_jog_goes_quiet_by_itself_when_the_client_vanishes(web):
    web.req('POST', '/api/jog', {'lease': LEASE, 'twist': [0.05, 0, 0, 0, 0, 0], 'ttl_ms': 200})
    time.sleep(0.35)
    n = len(web.plant.sent)
    time.sleep(0.3)
    assert len(web.plant.sent) == n and n > 0
    assert web.req('GET', '/api/jog')[1]['moving'] is False


def test_frames_get_put_roundtrip_is_atomic_and_validated(web, tmp_path):
    path = tmp_path / 'data' / 'frames.yaml'
    pts = {'origin': [0.3, 0, 0.2], 'x': [0.4, 0, 0.2], 'xy': [0.3, 0.1, 0.2]}
    status, doc = web.req('PUT', '/api/frames', {'frames': [{'name': 'Tezgah', 'points': pts}]})
    assert status == 200 and doc['frames'][0]['name'] == 'Tezgah' and path.exists()
    assert web.req('GET', '/api/frames')[1]['frames'][0]['wpr_deg'] == [0.0, 0.0, 0.0]
    assert FrameStore(path).names() == ['Tezgah']
    before = path.read_text()
    bad = [
        {
            'frames': [{'name': 'Tezgah', 'points': pts}, {'name': 'tezgah', 'points': pts}]
        },  # duplicate name
        {'frames': [{'name': 'Kotu', 'points': {**pts, 'x': pts['origin']}}]},  # degenerate
        {'frames': [{'name': 'World', 'points': pts}]},
        {'frames': 'x'},
        {},
    ]
    for body in bad:
        assert web.req('PUT', '/api/frames', body)[0] == 400
    assert path.read_text() == before and not list(path.parent.glob('*.tmp'))
    status, _ = web.req(
        'POST', '/api/jog', {'lease': LEASE, 'frame': 'Tezgah', 'twist': [0.02, 0, 0, 0, 0, 0]}
    )
    assert status == 200
    assert web.req('PUT', '/api/frames', {'frames': []})[0] == 409  # not while jogging
    assert web.req('POST', '/api/frames', {})[0] == 405


def test_jog_endpoints_respect_the_host_and_cross_site_guards(web):
    for headers in ({'Host': 'evil.example'}, {'Origin': 'http://evil.example'}):
        c = http.client.HTTPConnection('127.0.0.1', web.view.port, timeout=5)
        c.request(
            'POST',
            '/api/jog',
            body=json.dumps({'lease': LEASE, 'twist': [0] * 6}),
            headers={'Content-Type': 'application/json', **headers},
        )
        assert c.getresponse().status == 403
        c.close()
