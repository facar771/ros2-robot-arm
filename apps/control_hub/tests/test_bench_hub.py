"""KM3: Hub "Tezgâh" (tools/bench mantığı Hub'dan). BenchSim (sim'de BENCH_MODE) + gerçek
tools/bench/bench_core.py + HwLink. GERÇEK Uno/motor/COM yok: doğrulanan, kapıların ve çıkış davranışının Hub
tarafı ile simülatör anlamlarıdır."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from robot_hw_bridge import HwLink, LoopbackLink, SimRunner
from robot_hw_bridge.protocol import State
from robot_hw_bridge.sim_bench import BenchSim

from control_hub.cli import build_hub, build_parser
from control_hub.desktop.settings import Settings
from control_hub.hw import BenchError, BenchService
from control_hub.server import create_app

from .conftest import make_hub

J = {'content-type': 'application/json'}


class Rig:
    def __init__(self, bench_flag=True, bridge=False, clock=None, **kw):
        self.sim = BenchSim(bench_flag=bench_flag)
        self.lb = LoopbackLink()
        self.runner = SimRunner(self.sim, self.lb.device).start()
        self.bridge = [bridge]
        self.now = [0.0]
        self.svc = BenchService(enabled=True, serial_url='COM-TEST', bridge_active=lambda: self.bridge[0],
                                link_factory=lambda: HwLink(self.lb.host, heartbeat_s=0.02,
                                                            link_timeout_s=0.3, ack_timeout_s=0.1),
                                clock=lambda: self.now[0], idle_timeout_s=5.0, **kw)

    def cmds(self):
        return self.sim.counters['cmds']

    def ready(self, axis='j1'):
        self.svc.open()
        assert self.svc.command('zero', axis, confirm=True)['ok']
        assert self.svc.command('enable')['ok']
        return self

    def close(self):
        self.svc.close()
        self.runner.stop()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def text(res):
    return '\n'.join(res['messages'])


# --- varsayılan KAPALI, köprü, port ---------------------------------------------------------------
def test_default_is_off_and_cannot_open():
    with pytest.raises(BenchError) as e:
        BenchService().open()
    assert e.value.code == 'bench_disabled' and 'KAPALI' in e.value.message
    assert Settings().bench_enabled is False


def test_esp_bridge_open_refuses_with_a_clear_message(rig):
    rig.bridge[0] = True
    with pytest.raises(BenchError) as e:
        rig.svc.open()
    assert e.value.code == 'bridge_active' and 'tek sahip' in e.value.message and not rig.svc.is_open
    assert rig.cmds() == 0


def test_second_open_and_busy_port_are_refused(rig):
    rig.svc.open()
    with pytest.raises(BenchError) as e:
        rig.svc.open()
    assert e.value.code == 'bench_open'

    def boom():
        raise OSError('PermissionError(13)')
    s2 = BenchService(enabled=True, serial_url='COM9', link_factory=boom)
    with pytest.raises(BenchError) as e2:
        s2.open()
    assert e2.value.code == 'port_busy' and 'bench CLI' in e2.value.message and not s2.is_open


# --- BENCH_MODE görülmeden hareket yok ---------------------------------------------------------------
def test_without_bench_mode_flag_nothing_but_status_is_sent():
    r = Rig(bench_flag=False)
    try:
        info = r.svc.open()
        assert info['open'] and info['bench_mode'] is False
        for cmd, axis, amount in (('zero', 'j1', None), ('enable', None, None), ('move', 'j1', '10')):
            res = r.svc.command(cmd, axis, amount, confirm=True)
            assert res['ok'] is False and 'BENCH_MODE' in text(res)
        assert r.cmds() == 0 and r.sim.state == State.UNHOMED
    finally:
        r.close()


def test_open_loop_circuit_blocks_motion_commands(rig):
    rig.sim.press_estop()
    rig.svc.open()
    res = rig.svc.command('zero', 'j1', confirm=True)
    assert not res['ok'] and 'döngüsü açık' in text(res)
    assert rig.cmds() == 0


# --- akış, onay, limitler ----------------------------------------------------------------------------
def test_zero_needs_the_confirmation_box_and_sends_nothing_otherwise(rig):
    rig.svc.open()
    res = rig.svc.command('zero', 'j1', confirm=False)
    assert not res['ok'] and 'Onay verilmedi' in text(res) and rig.cmds() == 0
    assert rig.sim.state == State.UNHOMED


def test_full_flow_reports_estimate_and_limits_from_bench(rig):
    rig.ready()
    res = rig.svc.command('move', 'j1', '20')
    assert res['ok'] and 'vardı' in text(res)
    j1 = next(a for a in res['axes'] if a['name'] == 'j1')
    assert j1['pos'] == 20 and j1['estimate'] is True and (j1['lo'], j1['hi']) == (-256, 256)
    assert res['bench_mode'] and 'BENCH_MODE' in res['flags'] and res['state'] == 'ENABLED'
    assert rig.svc.command('disable')['ok'] and rig.sim.state == State.HOMED


def test_limit_overrun_and_first_move_cap_are_refused_before_sending(rig):
    rig.ready()
    before = rig.cmds()
    far = rig.svc.command('move', 'j1', '400')
    assert not far['ok'] and 'sınır' in text(far)
    first = rig.svc.command('move', 'j1', '120')           # ilk hareket sınırı (<=5°=56 adım) bench'ten
    assert not first['ok'] and 'ilk hareketi' in text(first)
    assert rig.cmds() == before


@pytest.mark.parametrize('amount', ['abc', '', '1e9', '30; stop', '99999999', None])
def test_hub_input_filter_rejects_garbage_amounts(rig, amount):
    rig.ready()
    with pytest.raises(BenchError) as e:
        rig.svc.command('move', 'j1', amount)
    assert e.value.code == 'bad_amount'


def test_unknown_command_or_axis_is_a_400(rig):
    rig.svc.open()
    for args in (('format', None), ('zero', 'j9'), ('zero', None)):
        with pytest.raises(BenchError) as e:
            rig.svc.command(*args)
        assert e.value.status == 400


def test_j7_has_no_degrees(rig):
    rig.ready('j7')
    res = rig.svc.command('move', 'j7', '5deg')
    assert not res['ok'] and 'derece kullanılamaz' in text(res)


# --- ACİL DUR ----------------------------------------------------------------------------------------
def test_estop_works_while_a_move_is_running_and_drops_the_zero(rig):
    rig.ready()
    rig.svc.command('move', 'j1', '10')                      # ilk hareket tamam; sonraki uzun olsun
    t = threading.Thread(target=lambda: rig.svc.command('move', 'j1', '-50'), daemon=True)
    t.start()
    end = time.monotonic() + 3
    while time.monotonic() < end and not rig.sim.axes[0].moving:
        time.sleep(0.01)
    assert rig.sim.axes[0].moving
    assert rig.svc.estop() is True                            # komut kilidini beklemez
    end = time.monotonic() + 3
    while time.monotonic() < end and rig.sim.state != State.ESTOPPED:
        time.sleep(0.01)
    assert rig.sim.state == State.ESTOPPED and not rig.sim.enabled_axes
    t.join(5)
    assert not rig.sim.axes[0].homed                         # yeniden zero şart
    res = rig.svc.command('enable')
    assert not res['ok']                                     # kilitli: clear + zero gerekir


def test_estop_without_connection_is_a_noop():
    assert BenchService(enabled=True).estop() is False


# --- çıkışta DISABLE, canlılık, hat kaybı --------------------------------------------------------------
def test_close_sends_stop_and_disable_before_dropping_the_port(rig):
    rig.ready()
    assert rig.sim.state == State.ENABLED
    rig.svc.close()
    assert rig.sim.state == State.HOMED and not rig.sim.enabled_axes and not rig.svc.is_open


def test_ui_silence_triggers_disable_but_polling_keeps_it_alive(rig):
    rig.ready()
    rig.now[0] += 4.0
    rig.svc.tick()
    assert rig.svc.is_open                                    # henüz süre dolmadı
    rig.svc.touch()
    rig.now[0] += 4.0
    rig.svc.tick()
    assert rig.svc.is_open                                    # sorgu canlılık işareti
    rig.now[0] += 6.0
    rig.svc.tick()
    assert not rig.svc.is_open and rig.sim.state == State.HOMED


def test_link_loss_releases_the_port(rig):
    rig.ready()
    rig.runner.stop()                                         # MCU sustu
    time.sleep(0.6)
    rig.svc.tick()
    assert not rig.svc.is_open


# --- HTTP uçları: PIN kipi, oturum, ACİL DUR ------------------------------------------------------------
@pytest.fixture
def web(scenarios, ui):
    r = Rig()
    hub = make_hub(scenarios, ui, mode='pin')
    hub.attach_bench(r.svc)
    c = TestClient(create_app(hub), base_url='http://localhost')
    yield c, hub, r
    hub.close()
    r.runner.stop()


def login(c):
    assert c.post('/api/login', json={'pin': '123456'}, headers=J).status_code == 200


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers=J)


def test_routes_need_login_and_pin_mode(web, scenarios, ui):
    c, hub, r = web
    assert post(c, '/api/bench/open').status_code == 401
    assert c.get('/api/bench').status_code == 401
    login(c)
    assert post(c, '/api/bench/open').status_code == 200
    # open kipinde PIN olmaz -> Tezgâh reddeder
    hub2 = make_hub(scenarios, ui, mode='open')
    hub2.attach_bench(BenchService(enabled=True, serial_url='x', link_factory=lambda: None))
    c2 = TestClient(create_app(hub2), base_url='http://localhost')
    res = post(c2, '/api/bench/open')
    assert res.status_code == 403 and res.json()['error'] == 'bench_needs_pin'
    assert post(c2, '/api/bench/command', {'cmd': 'estop'}).status_code == 403
    hub2.close()
    hub3 = make_hub(scenarios, ui, mode='locked')
    hub3.attach_bench(BenchService(enabled=True, serial_url='x', link_factory=lambda: None))
    c3 = TestClient(create_app(hub3), base_url='http://localhost')
    assert post(c3, '/api/bench/open').status_code == 403
    hub3.close()


def test_routes_flow_and_bridge_refusal(web):
    c, hub, r = web
    login(c)
    r.bridge[0] = True
    res = post(c, '/api/bench/open')
    assert res.status_code == 409 and res.json()['error'] == 'bridge_active'
    r.bridge[0] = False
    assert post(c, '/api/bench/open').json()['bench_mode'] is True
    ok = post(c, '/api/bench/command', {'cmd': 'zero', 'axis': 'j1', 'confirm': True}).json()
    assert ok['ok'] and post(c, '/api/bench/command', {'cmd': 'enable'}).json()['ok']
    mv = post(c, '/api/bench/command', {'cmd': 'move', 'axis': 'j1', 'amount': '15'}).json()
    assert mv['ok'] and any(a['pos'] == 15 for a in c.get('/api/bench').json()['axes'])
    assert post(c, '/api/bench/command', {'cmd': 'zero', 'axis': 'j9'}).status_code == 400
    assert post(c, '/api/bench/close').json()['open'] is False
    assert r.sim.state == State.HOMED
    assert c.get('/api/state').json()['bench'] == {'enabled': True, 'open': False}


def test_bench_estop_route_needs_no_session_and_global_estop_also_stops_bench(web):
    c, hub, r = web
    login(c)
    post(c, '/api/bench/open')
    post(c, '/api/bench/command', {'cmd': 'zero', 'axis': 'j1', 'confirm': True})
    post(c, '/api/bench/command', {'cmd': 'enable'})
    anon = TestClient(create_app(hub), base_url='http://localhost')     # oturumsuz
    res = anon.post('/api/bench/estop', headers=J)
    assert res.status_code == 200 and res.json()['sent'] is True
    end = time.monotonic() + 3
    while time.monotonic() < end and r.sim.state != State.ESTOPPED:
        time.sleep(0.01)
    assert r.sim.state == State.ESTOPPED
    post(c, '/api/bench/command', {'cmd': 'clear'})
    assert r.sim.state == State.UNHOMED
    post(c, '/api/bench/command', {'cmd': 'zero', 'axis': 'j1', 'confirm': True})
    post(c, '/api/bench/command', {'cmd': 'enable'})
    assert r.sim.state == State.ENABLED
    assert anon.post('/api/estop', headers=J).status_code == 200      # genel ACİL DUR
    end = time.monotonic() + 3
    while time.monotonic() < end and r.sim.state != State.ESTOPPED:
        time.sleep(0.01)
    assert r.sim.state == State.ESTOPPED


def test_hub_without_bench_answers_404(scenarios, ui):
    hub = make_hub(scenarios, ui, mode='pin')
    c = TestClient(create_app(hub), base_url='http://localhost')
    login(c)
    assert c.get('/api/bench').status_code == 404
    assert c.get('/api/state').json()['bench'] is None
    hub.close()


# --- ayar ve CLI --------------------------------------------------------------------------------------
def test_settings_validation():
    assert Settings(bench_enabled=True).problems()                                    # port yok
    assert any('PIN' in p for p in Settings(bench_enabled=True, bench_serial='COM3', access_mode='open',
                                            fake=True).problems())
    assert any('aynı' in p for p in Settings(bench_enabled=True, bench_serial='COM5', serial_port='COM5',
                                             fake=True).problems())
    assert not Settings(bench_enabled=True, bench_serial='COM3', fake=True).problems()


def test_cli_bench_is_off_by_default_and_needs_pin_mode_and_port():
    base = ['--fake', '--no-agent', '--pin', '123456']
    hub = build_hub(build_parser().parse_args(base))
    assert hub.bench is None
    hub.close()
    with pytest.raises(ValueError, match='pin'):
        build_hub(build_parser().parse_args(
            base + ['--access-mode', 'open', '--bench', '--bench-port', 'COM3']))
    with pytest.raises(ValueError, match='bench-port'):
        build_hub(build_parser().parse_args(base + ['--bench']))
    hub = build_hub(build_parser().parse_args(base + ['--bench', '--bench-port', 'COM3']))
    assert hub.bench is not None and hub.bench.enabled and hub.bench.serial_url == 'COM3'
    hub.close()


def test_panel_logic_and_dom_flow_in_node():
    """Gerçek bench.js sahte DOM üzerinde: sarı bant, onaysız sıfırla pasif, firmware sınırı dışı hedef
    gönderilmez, köprü açıkken mesaj, ACİL DUR oturumsuz uca gider."""
    import shutil
    import subprocess
    from pathlib import Path
    if shutil.which('node') is None:
        pytest.skip('node yok')
    web = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
    script = Path(__file__).parent / 'js' / 'bench_panel_check.js'
    r = subprocess.run(['node', str(script), str(web)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'BENCH_PANEL_JS_OK' in r.stdout, r.stdout + r.stderr


def test_shallow_install_path_does_not_break_import():
    """Docker imajında Hub /app/control_hub/... altındadır (depo kökü yok): içe aktarma çökmemeli,
    tezgâh aracı yoksa 503 ile net hata verilmeli. (CI Docker duman testini bu yakaladı.)"""
    from pathlib import PurePosixPath

    from control_hub.hw.bench_service import bench_candidates
    assert bench_candidates(PurePosixPath('/app/control_hub/hw/bench_service.py')) == []   # type: ignore[arg-type]
    deep = bench_candidates(PurePosixPath('/r/apps/control_hub/control_hub/hw/bench_service.py'))  # type: ignore[arg-type]
    assert [str(p) for p in deep] == ['/r/tools/bench/bench_core.py']
    svc = BenchService(enabled=True, serial_url='COM3', loader=lambda: (_ for _ in ()).throw(
        BenchError(503, 'bench_unavailable', 'yok')))
    with pytest.raises(BenchError) as e:
        svc.open()
    assert e.value.status == 503


def test_only_a_controlling_session_refreshes_liveness(web, scenarios, ui):
    """GET /api/bench canlılık tazeler YALNIZ denetim yetkili oturumdan; salt izleyici (locked kipi)
    sürücü sekmesi kapandığında Tezgâh'ı ayakta tutamaz."""
    c, hub, r = web
    login(c)
    r.now[0] = 100.0
    assert c.get('/api/bench').status_code == 200
    assert r.svc._seen == 100.0                                        # oturumlu: tazelendi
    hub2 = make_hub(scenarios, ui, mode='locked')
    r2 = Rig()
    hub2.attach_bench(r2.svc)
    v = TestClient(create_app(hub2), base_url='http://localhost')
    r2.now[0] = 100.0
    assert v.get('/api/bench').status_code == 200                      # izleyici görebilir
    assert r2.svc._seen == 0.0                                         # ama tazeleyemez
    hub2.close()
    r2.runner.stop()
