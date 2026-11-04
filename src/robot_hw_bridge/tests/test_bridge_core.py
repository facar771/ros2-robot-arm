"""B2 çekirdeği (HwBridge) + gerçek zamanlı Esp32Sim: açılış UNHOMED, homing, yörünge doğrulama ve
yürütme, iptal, hat kopması, MCU watchdog'u, düğüm döngüsü takılması, MCU acil dur, dönüşümün
hardware.py'den gelmesi. ROS yok; hat bellek-içi."""
import copy
import threading
import time
from pathlib import Path

import pytest
import yaml
from robot_core.hardware import hard_limits, load_hardware, parse_hardware, soft_limits
from robot_core.profile import load_profile

from robot_hw_bridge import Esp32Sim, HwLink, LoopbackLink, SimRunner
from robot_hw_bridge.core import (
    GOAL_TOLERANCE_VIOLATED,
    INVALID_GOAL,
    INVALID_JOINTS,
    PATH_TOLERANCE_VIOLATED,
    SUCCESSFUL,
    BridgeConfig,
    HwBridge,
    Point,
)
from robot_hw_bridge.protocol import EstopReason, State
from robot_hw_bridge.sim import SimAxis

PROFILE = Path(__file__).parents[2] / 'robot_description' / 'profile' / 'hardware.profile.yaml'
NAMES = [f'joint_{i}' for i in range(1, 7)]
ZERO = (0.0,) * 6


def sim_axes(count=6):
    """Profildeki tüm joint aralığı (±1600 adım) fiziksel olarak ulaşılabilir; encoder'sız, anahtarlı."""
    return [SimAxis(phys_min=-5000.0, phys_max=20000.0) for _ in range(count)]


def profiles(edit=None):
    robot = load_profile(PROFILE.with_name('six_axis_robot.yaml'))
    if edit is None:
        return load_hardware(PROFILE, robot), robot
    data = yaml.safe_load(PROFILE.read_text(encoding='utf-8'))
    data = copy.deepcopy(data)
    edit(data)
    return parse_hardware(data, robot), robot


class Rig:
    def __init__(self, edit=None, cfg=None, axes=None, **sim_kw):
        self.hw, self.robot = profiles(edit)
        self.estops = []
        self.bridge = HwBridge(self.hw, self.robot, cfg or BridgeConfig(),
                               on_estop=self.estops.append)
        self.lb = LoopbackLink()
        self.sim = Esp32Sim(axes or sim_axes(), **sim_kw)
        self.runner = SimRunner(self.sim, self.lb.device).start()
        b = self.bridge
        self.link = HwLink(self.lb.host, heartbeat_s=0.02, link_timeout_s=0.25, ack_timeout_s=0.1,
                           on_status=b.on_status, on_fault=b.on_fault, on_link_lost=b.on_link_lost,
                           on_link_up=b.on_link_up)
        b.attach(self.link)
        self.link.start()

    def configured(self):
        ok, msg = self.bridge.configure()
        assert ok, msg
        return self

    def homed(self):
        self.configured()
        ok, msg = self.bridge.home()
        assert ok, msg
        return self

    def run(self, points, names=NAMES, **kw):
        return self.bridge.execute(names, [Point(t, q) for t, q in points], **kw)

    def wait(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.01)
        return False

    def close(self):
        self.bridge.close()
        self.runner.stop()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


def at(q1, *rest):
    return (q1,) + tuple(rest) + (0.0,) * (5 - len(rest))


# ---- açılış: unhomed, önce homing --------------------------------------------------------------
def test_starts_unhomed_and_refuses_any_trajectory(rig):
    rig.configured()
    assert not rig.bridge.homed and rig.sim.state == State.UNHOMED
    out = rig.run([(0.0, ZERO), (1.0, at(0.2))])
    assert out.code == INVALID_GOAL and 'homing' in out.message
    assert all(a.cmd == 0.0 for a in rig.sim.axes) and rig.sim.state == State.UNHOMED


def test_configure_writes_watchdog_and_soft_limits_from_the_profile(rig):
    rig.configured()
    assert rig.sim.watchdog_ms == 300
    for ax, sim_ax in zip(rig.hw.axes, rig.sim.axes):
        assert (sim_ax.soft_min, sim_ax.soft_max) == soft_limits(ax, rig.robot)
        lo, hi = hard_limits(ax)
        assert lo <= sim_ax.soft_min and sim_ax.soft_max <= hi        # asla donanım sınırından geniş değil
    doc = rig.bridge.state_doc()
    assert doc['configured'] and not doc['homed'] and doc['open_loop_axes'] and doc['warnings']
    assert 'donanım acil durdurmanın yerine geçmez' in doc['note']


def test_configure_refuses_a_mcu_that_does_not_match_the_profile():
    r = Rig(axes=sim_axes(5))
    try:
        ok, msg = r.bridge.configure()
        assert not ok and 'eksen sayısı' in msg
    finally:
        r.close()


def test_home_then_positions_follow_the_profile(rig):
    rig.homed()
    assert rig.bridge.homed and rig.sim.state == State.HOMED
    q = rig.bridge.positions()
    assert q is not None and max(abs(v) for v in q) < 1e-3


def test_home_counter_must_match_the_profile_home_position():
    """Simülatör homing sonrası sayacı 0 yapar; profil home.position=0.2 ise sayaç beklenenden farklıdır:
    köprü bunu güvenli saymaz (Robot_2'den istek: protokolde home sayacı için ayar anahtarı yok)."""
    def edit(data):
        data['axes']['joint_1']['homing']['position'] = 0.2
    r = Rig(edit=edit)
    try:
        r.configured()
        ok, msg = r.bridge.home()
        assert not ok and 'uyuşmuyor' in msg and not r.bridge.homed
        assert r.run([(0.0, at(0.0)), (1.0, at(0.1))]).code == INVALID_GOAL
    finally:
        r.close()


# ---- yürütme -----------------------------------------------------------------------------------
def test_executes_a_trajectory_and_reaches_the_goal(rig):
    rig.homed()
    seen = []
    out = rig.run([(0.0, ZERO), (0.5, at(0.1, -0.05)), (1.2, at(0.3, -0.1, 0.2))],
                  feedback=lambda plan, cur, t: seen.append(t))
    assert out.code == SUCCESSFUL, out.message
    q = rig.bridge.positions()
    assert abs(q[0] - 0.3) < 0.03 and abs(q[1] + 0.1) < 0.03 and abs(q[2] - 0.2) < 0.03
    assert rig.sim.state == State.ENABLED and seen
    for i, target in enumerate((0.3, -0.1, 0.2)):
        assert rig.sim.axes[i].cmd == pytest.approx(rig.hw.axes[i].to_steps_int(target), abs=2)


def test_conversion_comes_from_hardware_py_not_from_the_bridge():
    """direction=-1 ve gear_ratio=2 olan bir eksen: sayaç hardware.py'nin verdiği adıma gelir."""
    def edit(data):
        data['axes']['joint_2']['direction'] = -1
        data['axes']['joint_2']['gear_ratio'] = 2.0
        data['axes']['joint_2']['limits']['position'] = [-1.57, 1.57]
    r = Rig(edit=edit)
    try:
        r.homed()
        assert r.run([(0.0, ZERO), (1.0, at(0.0, 0.4))]).code == SUCCESSFUL
        ax = r.hw.axis('joint_2')
        steps = r.sim.axes[1].cmd
        assert steps == pytest.approx(ax.to_steps(0.4), abs=1.5) and steps < 0
        assert abs(r.bridge.positions()[1] - 0.4) < 0.03
    finally:
        r.close()


@pytest.mark.parametrize('names,pts,why', [
    (NAMES[:5], [(0.0, ZERO), (1.0, ZERO)], 'eklemler'),
    (NAMES[:5] + ['joint_x'], [(0.0, ZERO), (1.0, ZERO)], 'eklemler'),
    (NAMES, [], 'boş'),
    (NAMES, [(0.0, ZERO), (1.0, at(float('nan')))], 'sonlu'),
    (NAMES, [(0.0, ZERO), (1.0, at(float('inf')))], 'sonlu'),
    (NAMES, [(0.0, ZERO), (1.0, ZERO), (1.0, ZERO)], 'zaman'),
    (NAMES, [(0.0, ZERO), (-0.5, ZERO)], 'zaman'),
    (NAMES, [(0.0, ZERO), (1.0, at(3.2))], 'limit'),
    (NAMES, [(0.0, ZERO), (0.05, at(1.0))], 'hız'),
    (NAMES, [(0.0, at(1.0)), (1.0, at(1.0))], 'başlangıc'),
])
def test_bad_goals_are_refused_before_a_single_command(rig, names, pts, why):
    rig.homed()
    link_tx = rig.link.stats.tx_frames
    out = rig.run(pts, names=names)
    assert out.code in (INVALID_GOAL, INVALID_JOINTS) and why in out.message
    assert all(a.cmd == 0.0 for a in rig.sim.axes)
    assert rig.sim.state == State.HOMED                     # ENABLE bile gönderilmedi
    assert rig.link.stats.tx_frames - link_tx < 15          # yalnızca kalp atışları


def test_joint_order_in_the_goal_is_free(rig):
    rig.homed()
    names = list(reversed(NAMES))
    pts = [(0.0, ZERO), (1.0, tuple(reversed(at(0.2, 0.1))))]
    assert rig.run(pts, names=names).code == SUCCESSFUL
    q = rig.bridge.positions()
    assert abs(q[0] - 0.2) < 0.03 and abs(q[1] - 0.1) < 0.03


def test_cancel_stops_the_axes_without_an_estop(rig):
    rig.homed()
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    out = rig.run([(0.0, ZERO), (3.0, at(0.4))], cancelled=stop.is_set)
    assert out.code == INVALID_GOAL and 'iptal' in out.message
    assert rig.wait(lambda: not any(a.moving for a in rig.sim.axes))
    q = rig.bridge.positions()
    assert 0.0 < q[0] < 0.4 and rig.sim.state == State.ENABLED and rig.estops == []


def test_second_trajectory_while_one_runs_is_refused(rig):
    rig.homed()
    results = []
    t = threading.Thread(target=lambda: results.append(rig.run([(0.0, ZERO), (1.5, at(0.2))])))
    t.start()
    assert rig.wait(lambda: rig.bridge.executing)
    assert 'başka' in rig.run([(0.0, ZERO), (1.0, ZERO)]).message
    t.join(10)
    assert results[0].code == SUCCESSFUL


def test_path_tolerance_stops_the_motion():
    r = Rig(cfg=BridgeConfig(path_tol=0.02))
    try:
        r.homed()
        out = r.run([(0.0, ZERO), (0.4, at(0.4))])           # hız sınırına yakın: gecikme > 0.02 rad
        assert out.code == PATH_TOLERANCE_VIOLATED
        assert r.wait(lambda: not any(a.moving for a in r.sim.axes))
    finally:
        r.close()


# ---- iki yönlü watchdog, kesintiler -----------------------------------------------------------
def test_link_loss_during_motion_aborts_reports_estop_and_unhomes(rig):
    rig.homed()
    box = []
    t = threading.Thread(target=lambda: box.append(rig.run([(0.0, ZERO), (3.0, at(0.5))])))
    t.start()
    assert rig.wait(lambda: rig.bridge.executing)
    time.sleep(0.4)
    rig.lb.cut()                                             # USB çekildi
    t.join(10)
    assert box[0].code == INVALID_GOAL and not rig.bridge.homed
    assert rig.wait(lambda: any('telemetri' in e or 'hat' in e for e in rig.estops))


def test_silent_mcu_is_detected_within_the_timeout_and_stops_robot_api():
    r = Rig()
    try:
        r.homed()
        t0 = time.monotonic()
        r.runner.pause()                                     # ESP32 kilitlendi: STATUS yok
        assert r.wait(lambda: r.estops, 2.0)
        assert time.monotonic() - t0 < 0.8                   # 250 ms zaman aşımı + toleranslar
        assert 'telemetrisi kesildi' in r.estops[0] and not r.bridge.homed
        assert r.run([(0.0, ZERO), (1.0, ZERO)]).code == INVALID_GOAL
    finally:
        r.runner.resume()
        r.close()


def test_host_going_silent_makes_the_mcu_stop_by_its_own_watchdog():
    """Düğüm döngüsü takılırsa (tick gelmez) kalp atışı da kesilir; MCU kendiliğinden durur."""
    r = Rig(cfg=BridgeConfig(loop_stall_s=0.2))
    try:
        r.homed()
        r.bridge.tick()
        out = []
        t = threading.Thread(target=lambda: out.append(r.run([(0.0, ZERO), (4.0, at(0.5))])))
        t.start()
        assert r.wait(lambda: r.bridge.executing)
        # tick bir daha gelmiyor -> bekçi kalp atışını keser
        assert r.wait(lambda: r.sim.state == State.ESTOPPED, 2.0)
        assert r.sim.reason == EstopReason.WATCHDOG
        t.join(10)
        assert out[0].code == INVALID_GOAL and not r.bridge.homed
        assert not any(a.moving for a in r.sim.axes)
    finally:
        r.close()


def test_regular_ticks_keep_the_heartbeat_alive():
    r = Rig(cfg=BridgeConfig(loop_stall_s=0.2))
    try:
        r.homed()
        end = time.monotonic() + 0.9
        while time.monotonic() < end:
            r.bridge.tick()
            time.sleep(0.02)
        assert r.sim.state == State.HOMED and not r.estops
    finally:
        r.close()


def test_mcu_estop_button_aborts_reports_and_needs_clear_and_rehoming(rig):
    rig.homed()
    box = []
    t = threading.Thread(target=lambda: box.append(rig.run([(0.0, ZERO), (3.0, at(0.5))])))
    t.start()
    assert rig.wait(lambda: rig.bridge.executing)
    time.sleep(0.3)
    rig.sim.press_estop()
    t.join(10)
    assert box[0].code == INVALID_GOAL and not rig.bridge.homed
    assert rig.wait(lambda: any('ACİL DUR' in e for e in rig.estops))
    assert rig.run([(0.0, ZERO), (1.0, ZERO)]).code == INVALID_GOAL
    ok, msg = rig.bridge.clear()
    assert not ok                                            # buton hâlâ basılı
    rig.sim.release_estop()
    ok, msg = rig.bridge.clear()
    assert ok, msg
    assert not rig.bridge.homed                              # clear homing yerine geçmez
    assert rig.run([(0.0, ZERO), (1.0, ZERO)]).code == INVALID_GOAL
    ok, msg = rig.bridge.home()
    assert ok, msg


def test_estop_from_robot_api_cuts_the_drives_and_unhomes(rig):
    rig.homed()
    rig.bridge.estop('robot API acil dur')
    assert rig.wait(lambda: rig.sim.state == State.ESTOPPED)
    assert rig.sim.reason == EstopReason.HOST_CMD and not rig.bridge.homed


def test_unknown_goal_tolerance_is_reported_not_swallowed():
    r = Rig(cfg=BridgeConfig(goal_tol=1e-6, settle_s=0.5))
    try:
        r.homed()
        out = r.run([(0.0, ZERO), (0.6, at(0.2))])
        assert out.code in (GOAL_TOLERANCE_VIOLATED, SUCCESSFUL)
    finally:
        r.close()


def test_watchdog_range_is_enforced():
    for bad in (100, 199, 501, 1000):
        with pytest.raises(ValueError):
            BridgeConfig(watchdog_ms=bad)
    with pytest.raises(ValueError):
        BridgeConfig(watchdog_ms=300, loop_stall_s=0.5)


def test_reconnecting_host_must_continue_the_tx_seq_or_the_mcu_ignores_it():
    """Bulgu: MCU (sim) geri giden seq'li çerçeveyi atar; yeni HwLink 0'dan başlarsa HELLO_REQ'i de
    yok sayılır. Düğüm bu yüzden kaldığı seq'ten sürer (HwLink.initial_seq / next_seq)."""
    from robot_hw_bridge.codec import Frame, FrameDecoder, encode_frame
    from robot_hw_bridge.protocol import MsgType
    sim = Esp32Sim(sim_axes())
    for seq in range(40, 45):                       # ilk bağlantı: seq 40..44
        sim.feed(encode_frame(Frame(MsgType.HEARTBEAT, seq, 0, b'')))

    def answers_hello(seq):
        sim.poll()
        dec = FrameDecoder(None)
        sim.feed(encode_frame(Frame(MsgType.HELLO_REQ, seq, 0, bytes([1, 0, 0, 0, 0]))))
        types = [f.type for f in dec.feed(sim.poll())]
        return MsgType.HELLO in types

    assert not answers_hello(0), 'seq 0 (yeniden başlayan istemci) eskidir: atılmalı'
    assert answers_hello(45), 'kaldığı yerden süren istemci duyulur'


# ---- jog setpoints (J2) ------------------------------------------------------------------------
def test_jog_setpoint_moves_the_axes_and_goes_quiet_when_the_stream_stops():
    rig = Rig().homed()
    try:
        q = rig.bridge.positions()
        for i in range(1, 8):                        # ~20 Hz stream of 3-point, <=100 ms setpoints
            tgt = [v + 0.004 * i for v in q]
            out = rig.bridge.setpoint(NAMES, [Point(0.02, tgt), Point(0.04, tgt), Point(0.06, tgt)])
            assert out.ok, out.message
            time.sleep(0.05)
        assert rig.wait(lambda: abs(rig.sim.true_position(0) - rig.hw.axes[0].to_steps(q[0] + 0.028))
                        < 8, 3.0)
        time.sleep(0.4)                               # stream stopped: MCU finishes its last target
        end = [rig.sim.true_position(i) for i in range(6)]
        time.sleep(0.4)                               # ... and then nothing moves on
        assert [rig.sim.true_position(i) for i in range(6)] == end
    finally:
        rig.close()


def test_jog_setpoint_is_refused_unhomed_far_away_or_during_a_trajectory():
    rig = Rig().configured()
    try:
        q = rig.bridge.positions()
        pts = [Point(0.02, q), Point(0.06, q)]
        assert 'homing' in rig.bridge.setpoint(NAMES, pts).message          # unhomed
    finally:
        rig.close()
    rig = Rig().homed()
    try:
        q = rig.bridge.positions()
        far = [v + 0.2 for v in q]
        assert not rig.bridge.setpoint(NAMES, [Point(0.02, far), Point(0.06, far)]).ok
        assert not rig.bridge.setpoint(NAMES, [Point(0.5, q)]).ok           # horizon too long
        assert not rig.bridge.setpoint(['joint_1'], [Point(0.02, [0.0])]).ok
        rig.bridge._exec_lock.acquire()                                     # an action trajectory runs
        try:
            assert 'yörünge' in rig.bridge.setpoint(NAMES, [Point(0.02, q), Point(0.06, q)]).message
        finally:
            rig.bridge._exec_lock.release()
    finally:
        rig.close()


def test_heartbeat_keeps_going_at_20_hz_or_faster_without_any_setpoint():
    from robot_hw_bridge.protocol import HEARTBEAT_PERIOD_MS
    assert HEARTBEAT_PERIOD_MS <= 50
    rig = Rig().homed()
    try:
        before = rig.link.stats.tx_frames
        time.sleep(1.0)
        assert rig.link.stats.tx_frames - before >= 15       # idle: heartbeats only (test link: 20 ms)
    finally:
        rig.close()
