"""R6 ikinci yarı: rotalar arka uç yöntemlerini DOĞRUDAN çağırır; "bu aileyi bilmiyorum" yolu sözleşmenin
varsayılanıdır (``NoJog``/``NoMove``/``NoSim``) ve eski ``getattr`` yolunun yanıtıyla BAYT BAYT aynıdır.

Altın baytlar eski rota koduyla (``fail(501, ...)`` / ``not_sim()``) üretilen yanıtların aynısıdır:
bu dosya onları elle sabitler; varsayılan metin ya da anahtar sırası kayarsa kırmızı olur.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, FakeBackend, Hub, load_scenarios, load_ui_config
from control_hub.hub_core import backend as contract
from control_hub.hub_core.program_move import BackendMoveClient, SimMoveClient, make_move_client
from control_hub.hub_core.robot_api_backend import RobotApiBackend, RobotApiError
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG

MOVE_BYTES = ('{"ok":false,"error":"move_unsupported",'
              '"message":"Bu robot arka ucu hareket uçlarını desteklemiyor"}')
SIM_BYTES = '{"ok":false,"error":"not_sim","message":"Bu uç yalnız SİM modunda çalışır"}'
JOG_BYTES = ('{"ok":false,"error":"jog_unsupported",'
             '"message":"Bu robot arka ucu jog/çerçeve ucunu desteklemiyor"}')
JOINT_BYTES = ('{"ok":false,"error":"jog_unsupported",'
               '"message":"Bu robot arka ucu eklem jog\'unu desteklemiyor"}')

MOVE_CALLS = [('post', '/api/move', {}), ('post', '/api/move/check', {}), ('post', '/api/move/stop', {}),
              ('post', '/api/move/pause', {}), ('post', '/api/move/resume', {}), ('get', '/api/move/x', None)]
SIM_CALLS = [('post', '/api/sim/reset', {}), ('post', '/api/sim/speed', {'speed_pct': 50})]
JOG_CALLS = [('get', '/api/jog', None), ('post', '/api/jog', {}), ('get', '/api/model', None),
             ('get', '/api/frames', None), ('put', '/api/frames', {}), ('post', '/api/frames/reach', {})]


def build(backend, *, phone=False):
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    url, peer = (('http://192.168.1.50', ('192.168.1.50', 40000)) if phone
                 else ('http://localhost', ('127.0.0.1', 50000)))
    return TestClient(create_app(hub), base_url=url, client=peer), hub


def send(client, method, path, body):
    return getattr(client, method)(path, **({} if body is None else {'json': body}))


@pytest.fixture(params=['fake', 'robot_api'])
def plain(request):
    """``--fake`` ve gerçek Robot API: move/sim ailesini bilmeyen iki arka uç."""
    backend = (FakeBackend(time_scale=0.02) if request.param == 'fake'
               else RobotApiBackend('http://127.0.0.1:9', poll_s=5, autostart=False))
    client, hub = build(backend)
    yield client
    hub.close()


# --- varsayılanların kendisi: eski yanıtın baytları ---------------------------------------------------
def test_default_replies_are_the_old_route_bodies_with_the_same_key_order():
    def dumped(doc):
        return json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
    assert dumped(contract.NoMove().move_get('x')[1]) == MOVE_BYTES
    assert dumped(contract.NoSim().sim_reset()[1]) == SIM_BYTES
    assert dumped(contract.NoJog().model()[1]) == JOG_BYTES
    codes = [contract.NoMove().move_stop()[0], contract.NoSim().set_speed(3)[0],
             contract.NoJog().frames_put({})[0]]
    assert codes == [501, 409, 501]


def test_every_default_method_answers_its_family_and_ignores_its_arguments():
    nomove, nosim, nojog = contract.NoMove(), contract.NoSim(), contract.NoJog()
    replies = [nomove.move_submit({}), nomove.move_check(1), nomove.move_get('a'), nomove.move_stop(),
               nomove.move_pause(), nomove.move_resume()]
    assert all(r == replies[0] for r in replies) and replies[0][0] == 501
    assert nosim.sim_reset() == nosim.set_speed(77) and nosim.sim_reset()[0] == 409
    jogs = [nojog.jog_command({}, remote=True), nojog.jog_status(), nojog.model(), nojog.frames_get(),
            nojog.frames_put({}), nojog.frames_reach({})]
    assert all(r == jogs[0] for r in jogs) and jogs[0][0] == 501


def test_defaults_return_a_fresh_body_each_time():
    a, b = contract.NoMove().move_stop()[1], contract.NoMove().move_stop()[1]
    a['mutated'] = True
    assert 'mutated' not in b                        # paylaşılan sabit sözlük değil


def test_supports_move_means_a_real_move_family_not_the_default():
    class Real(FakeBackend):                         # NoMove'dan türeyen sınıf TANIMI GEREĞİ "move yok"
        def move_submit(self, body):
            return 202, {}
    assert contract.supports_move(Real()) is False
    assert contract.supports_move(object()) is False and contract.supports_move(None) is False
    sim = SimBackend(clock=SimClock(), autostart=False)
    try:
        assert contract.supports_move(sim) is True
    finally:
        sim.close()


# --- rota düzeyi: --fake ve robot_api eski 501 / 409 gövdelerini AYNEN verir ----------------------------
@pytest.mark.parametrize('method,path,body', MOVE_CALLS)
def test_move_family_on_plain_backends_is_501_move_unsupported_byte_for_byte(plain, method, path, body):
    r = send(plain, method, path, body)
    assert (r.status_code, r.text) == (501, MOVE_BYTES), path


@pytest.mark.parametrize('method,path,body', SIM_CALLS)
def test_sim_family_on_plain_backends_is_409_not_sim_byte_for_byte(plain, method, path, body):
    r = send(plain, method, path, body)
    assert (r.status_code, r.text) == (409, SIM_BYTES), path


def test_estop_latch_still_answers_423_before_not_sim_and_before_move_unsupported():
    client, hub = build(FakeBackend(time_scale=0.02))
    try:
        assert client.post('/api/estop').status_code == 200
        for path in ('/api/sim/reset', '/api/move', '/api/move/resume'):
            assert client.post(path, json={}).status_code == 423, path      # guard_motion önce
        assert client.post('/api/move/stop', json={}).status_code == 501    # durdurmak serbest: varsayılan
    finally:
        hub.close()


def test_fake_joint_hold_route_and_flag_stay_unsupported():
    client, hub = build(FakeBackend(time_scale=0.02))
    try:
        r = client.post('/api/jog/joint', json={'lease': 'x', 'joint': 0, 'dir': 1, 'ttl_ms': 200})
        assert (r.status_code, r.text) == (501, JOINT_BYTES)
        st = client.get('/api/jog')
        assert st.status_code == 200 and st.json()['joint_hold'] is False
        assert hub.joint_hold.clock is time.monotonic                    # saatsiz arka uç: gerçek zaman
    finally:
        hub.close()


def test_a_backend_that_inherits_nojog_answers_501_on_every_jog_and_frames_route():
    class NoJogFake(contract.NoJog, FakeBackend):
        pass
    client, hub = build(NoJogFake(time_scale=0.02))
    try:
        for method, path, body in JOG_CALLS:
            r = send(client, method, path, body)
            assert (r.status_code, r.text) == (501, JOG_BYTES), path
    finally:
        hub.close()


# --- rota düzeyi: yöntem DOĞRUDAN, doğru argümanla çağrılır ------------------------------------------------
NAMES = ('jog_command', 'jog_status', 'model', 'frames_get', 'frames_put', 'frames_reach',
         'move_submit', 'move_check', 'move_get', 'move_stop', 'move_pause', 'move_resume',
         'sim_reset', 'set_speed')


def spy(calls):
    def recorder(name):
        def fn(self, *args, **kw):
            calls.append((name, args, kw))
            return 200, {'spy': name}
        return fn
    return type('Spy', (FakeBackend,), {n: recorder(n) for n in NAMES})(time_scale=0.02)


EXPECT = [
    ('post', '/api/move', {'a': 1}, False, ('move_submit', ({'a': 1},), {})),
    ('post', '/api/move/check', {'b': 2}, False, ('move_check', ({'b': 2},), {})),
    ('post', '/api/move/stop', {}, False, ('move_stop', (), {})),
    ('post', '/api/move/pause', {}, False, ('move_pause', (), {})),
    ('post', '/api/move/resume', {}, False, ('move_resume', (), {})),
    ('get', '/api/move/job7', None, False, ('move_get', ('job7',), {})),
    ('post', '/api/sim/reset', {}, False, ('sim_reset', (), {})),
    ('post', '/api/sim/speed', {'speed_pct': 40}, False, ('set_speed', (40,), {})),
    ('post', '/api/sim/speed', {}, False, ('set_speed', (None,), {})),
    ('post', '/api/jog', {'x': 1}, False, ('jog_command', ({'x': 1},), {'remote': False})),
    ('post', '/api/jog', {'x': 1}, True, ('jog_command', ({'x': 1},), {'remote': True})),
    ('get', '/api/jog', None, False, ('jog_status', (), {})),
    ('get', '/api/model', None, False, ('model', (), {})),
    ('get', '/api/frames', None, False, ('frames_get', (), {})),
    ('put', '/api/frames', {'f': 1}, False, ('frames_put', ({'f': 1},), {})),
    ('post', '/api/frames/reach', {'c': 3}, False, ('frames_reach', ({'c': 3},), {})),
]


@pytest.mark.parametrize('method,path,body,phone,want', EXPECT, ids=[f'{e[0]} {e[1]} {e[3]}' for e in EXPECT])
def test_routes_call_the_backend_method_directly_with_the_exact_arguments(method, path, body, phone, want):
    calls = []
    client, hub = build(spy(calls), phone=phone)
    try:
        r = send(client, method, path, body)
        assert r.status_code == 200 and r.json()['spy'] == want[0]
        assert calls == [want]
    finally:
        hub.close()


def test_network_error_from_the_backend_is_still_502_robot_unreachable():
    def boom(self):
        raise RobotApiError('kopuk')
    client, hub = build(type('Down', (FakeBackend,), {'jog_status': boom})(time_scale=0.02))
    try:
        r = client.get('/api/jog')
        assert (r.status_code, r.json()) == (502, {'ok': False, 'error': 'robot_unreachable',
                                                   'message': 'Robota ulaşılamadı: kopuk'})
    finally:
        hub.close()


def test_backend_error_status_still_gets_a_message_added():
    def busy(self, body):
        return 409, {'error': 'busy'}
    client, hub = build(type('Busy', (FakeBackend,), {'frames_put': busy})(time_scale=0.02))
    try:
        r = client.put('/api/frames', json={})
        assert (r.status_code, r.json()) == (409, {'error': 'busy', 'message': 'busy'})
    finally:
        hub.close()


# --- program_move: arka ucun saati ve hız ölçeği doğrudan okunur --------------------------------------------
class Holder:
    def __init__(self, backend):
        self.backend = backend


def test_make_move_client_uses_the_contract_for_move_and_the_time_scale_for_the_fake():
    sim = SimBackend(clock=SimClock(), autostart=False)
    try:
        assert isinstance(make_move_client(Holder(sim)), BackendMoveClient)
    finally:
        sim.close()
    fake = make_move_client(Holder(FakeBackend(time_scale=0.5)))
    plain = make_move_client(Holder(RobotApiBackend('http://127.0.0.1:9', poll_s=5, autostart=False)))
    assert isinstance(fake, SimMoveClient) and fake._k == pytest.approx(2.0)
    assert isinstance(plain, SimMoveClient) and plain._k == pytest.approx(1.0)       # NoMove.time_scale = 1.0


def test_sim_backend_clock_drives_the_joint_hold_holder():
    sim = SimBackend(clock=SimClock(), autostart=False)
    _, hub = build(sim)
    try:
        assert hub.joint_hold.clock is sim.clock
    finally:
        hub.close()
