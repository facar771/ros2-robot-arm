"""BenchSim: tezgâh (K9_BENCH) firmware anlamlarının simülatör kopyası (k9_mcu.cpp benchCommand).
GERÇEK firmware değil; Hub "Tezgâh" testleri bunun üstüne kurulur, anlamlar burada ayrıca sabitlenir."""
from helpers import Host

from robot_hw_bridge.protocol import MsgType, Result, State, StatusFlag
from robot_hw_bridge.sim_bench import BenchSim


def host(**kw):
    h = Host()
    h.sim = BenchSim(clock=h.clock, **kw)
    h.send(MsgType.HEARTBEAT)
    return h


def ack(h, t, **kw):
    return h.send(t, **kw).result


def enabled(h, mask=0b1):
    assert ack(h, MsgType.CMD_HOME, mask=mask) == Result.OK
    assert ack(h, MsgType.CMD_ENABLE) == Result.OK
    return h


def test_bench_flag_in_status_and_hello_has_seven_step_axes():
    h = host()
    assert h.status().flags & StatusFlag.BENCH_MODE and len(h.status().axes) == 7
    assert not host(bench_flag=False).status().flags & StatusFlag.BENCH_MODE


def test_home_is_an_instant_zero_only_for_j1_j2_j7():
    h = host()
    assert ack(h, MsgType.CMD_HOME, mask=0b0000100) == Result.BAD_ARG    # servo yok
    assert ack(h, MsgType.CMD_HOME, mask=0) == Result.BAD_ARG
    assert ack(h, MsgType.CMD_ENABLE) == Result.NO_REFERENCE                    # sıfırlanmadan ENABLE yok
    assert ack(h, MsgType.CMD_HOME, mask=0b1000011) == Result.OK
    st = h.status()
    assert st.state == State.HOMED and st.axes[0].homed and st.axes[6].homed and not st.axes[2].homed


def test_enable_powers_only_zeroed_axes_and_move_to_others_is_refused():
    h = enabled(host())
    st = h.status()
    assert st.axes[0].enabled and not st.axes[1].enabled
    ok = dict(target=10, vmax=100, accel=100)
    assert ack(h, MsgType.CMD_MOVE, axis=1, **ok) == Result.BAD_STATE           # J2 sıfırlanmadı
    assert ack(h, MsgType.CMD_MOVE, axis=3, **ok) == Result.BAD_ARG             # bench ekseni değil
    assert ack(h, MsgType.CMD_MOVE, axis=0, **ok) == Result.OK


def test_embedded_limits_cannot_be_widened_by_the_host():
    h = enabled(host(), 0b1000001)
    assert ack(h, MsgType.CMD_MOVE, axis=0, target=257, vmax=100, accel=100) == Result.LIMIT
    assert ack(h, MsgType.CMD_MOVE, axis=0, target=-257, vmax=100, accel=100) == Result.LIMIT
    assert ack(h, MsgType.CMD_MOVE, axis=0, target=10, vmax=301, accel=100) == Result.LIMIT     # hız tavanı
    assert ack(h, MsgType.CMD_MOVE, axis=0, target=10, vmax=100, accel=301) == Result.LIMIT     # ivme tavanı
    assert ack(h, MsgType.CMD_MOVE, axis=6, target=51, vmax=50, accel=50) == Result.LIMIT       # J7 ±50
    assert ack(h, MsgType.CMD_MOVE, axis=6, target=10, vmax=101, accel=50) == Result.LIMIT
    assert ack(h, MsgType.CMD_MOVE, axis=0, target=10, vmax=0, accel=100) == Result.BAD_ARG


def test_move_runs_at_the_commanded_speed_and_stops_exactly_on_target():
    h = enabled(host())
    assert ack(h, MsgType.CMD_MOVE, axis=0, target=20, vmax=100, accel=100) == Result.OK
    h.adv(0.05, beat=True)
    assert h.status().axes[0].moving
    h.adv(2.0, beat=True)
    st = h.status().axes[0]
    assert st.pos == 20 and not st.moving
    assert ack(h, MsgType.CMD_DISABLE) == Result.OK and h.status().state == State.HOMED


def test_estop_and_watchdog_drop_the_zero_and_power():
    h = enabled(host())
    assert ack(h, MsgType.CMD_ESTOP) == Result.OK
    st = h.status()
    assert st.state == State.ESTOPPED and not st.axes[0].homed and not st.axes[0].enabled
    assert ack(h, MsgType.CMD_CLEAR) == Result.OK
    assert h.status().state == State.UNHOMED
    assert ack(h, MsgType.CMD_ENABLE) == Result.NO_REFERENCE                    # yeniden sıfır şart
    enabled(h)
    h.adv(0.6)                                                                  # kalp atışı yok
    assert h.status().state == State.ESTOPPED and not h.status().axes[0].homed


def test_flagless_build_behaves_like_the_motionless_firmware():
    h = host(bench_flag=False)
    assert ack(h, MsgType.CMD_HOME, mask=0b1) == Result.NO_REFERENCE    # asla HOMED olmaz
