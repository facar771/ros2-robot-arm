"""Simülatör = firmware belirtimi: durum makinesi, watchdog, NC acil dur, encoder/açık döngü kuralları.
Sahte saat: gerçek zaman beklenmez."""
import pytest
from helpers import Host

from robot_hw_bridge.protocol import (
    AxisFlag,
    ConfigKey,
    EncoderMode,
    EstopReason,
    FaultCode,
    MsgType,
    Result,
    State,
    StatusFlag,
)
from robot_hw_bridge.sim import SimAxis


def test_boot_state_hello_and_unhomed():
    h = Host()
    hello = h.hello()
    assert len(hello.axes) == 6 and hello.axes[0].encoder == EncoderMode.NONE
    assert hello.axes[4].encoder == EncoderMode.ABSOLUTE
    assert h.status().state == State.UNHOMED and h.status().open_loop_any


def test_state_machine_happy_path():
    h = Host()
    h.send(MsgType.HEARTBEAT)
    assert h.result(MsgType.CMD_ENABLE) == Result.BAD_STATE          # önce home
    assert h.result(MsgType.CMD_MOVE, axis=0, target=10, vmax=100, accel=100) == Result.BAD_STATE
    assert h.result(MsgType.CMD_HOME, mask=0x3F) == Result.OK
    assert h.status().state == State.HOMING
    assert h.result(MsgType.CMD_MOVE, axis=0, target=10, vmax=100, accel=100) == Result.BAD_STATE
    h.adv(3.0, beat=True)
    assert h.status().state == State.HOMED and all(a.homed for a in h.status().axes)
    assert h.result(MsgType.CMD_ENABLE) == Result.OK and h.status().state == State.ENABLED
    assert h.result(MsgType.CMD_MOVE, axis=0, target=3000, vmax=8000, accel=40000) == Result.OK
    h.adv(1.5, beat=True)
    a0 = h.status().axes[0]
    assert a0.pos == 3000 and not a0.moving
    assert h.result(MsgType.CMD_DISABLE) == Result.OK and h.status().state == State.HOMED


def test_move_is_refused_outside_soft_limits_and_bad_args():
    h = Host()
    h.ready()
    assert h.result(MsgType.CMD_MOVE, axis=0, target=-1, vmax=100, accel=100) == Result.LIMIT
    assert h.result(MsgType.CMD_MOVE, axis=0, target=10 ** 9, vmax=100, accel=100) == Result.LIMIT
    assert h.result(MsgType.CMD_MOVE, axis=9, target=0, vmax=100, accel=100) == Result.BAD_ARG
    assert h.result(MsgType.CMD_MOVE, axis=0, target=10, vmax=0, accel=100) == Result.BAD_ARG
    assert h.result(MsgType.CMD_MOVE, axis=0, target=10, vmax=10 ** 6, accel=100) == Result.BAD_ARG
    assert h.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=0, value=100) == Result.BAD_STATE
    assert h.result(MsgType.CMD_DISABLE) == Result.OK
    assert h.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=0, value=100) == Result.OK
    assert h.result(MsgType.CMD_ENABLE) == Result.OK
    assert h.result(MsgType.CMD_MOVE, axis=0, target=101, vmax=100, accel=100) == Result.LIMIT


def test_axis_without_reference_can_never_be_enabled():
    """Encoder'sız + home anahtarsız eksen güvenli referanslanamaz: HOME reddedilir, ENABLE olmaz."""
    axes = [SimAxis(EncoderMode.NONE, has_home_switch=False), SimAxis(EncoderMode.NONE)]
    h = Host(axes)
    h.send(MsgType.HEARTBEAT)
    assert h.result(MsgType.CMD_HOME, mask=0x03) == Result.NO_REFERENCE
    assert h.result(MsgType.CMD_ENABLE) == Result.NO_REFERENCE
    assert h.result(MsgType.CMD_HOME, mask=0x02) == Result.OK            # referanslanabilen eksen tek başına
    h.adv(3.0, beat=True)
    assert h.status().state == State.UNHOMED                              # hepsi homed değil -> HOMED değil
    assert h.result(MsgType.CMD_ENABLE) == Result.NO_REFERENCE


def test_open_loop_is_flagged_and_step_loss_is_invisible():
    h = Host()
    h.ready()
    assert h.result(MsgType.CMD_MOVE, axis=0, target=2000, vmax=8000, accel=40000) == Result.OK
    h.adv(1.0, beat=True)
    h.sim.inject_step_loss(0, 150)                                       # encoder'sız eksen adım kaybetti
    h.adv(1.0, beat=True)
    s = h.status()
    assert s.open_loop_any and s.axes[0].open_loop and s.axes[0].flags & AxisFlag.OPEN_LOOP
    assert s.axes[0].pos == 2000 and s.state == State.ENABLED            # MCU hiçbir şey fark etmedi
    assert h.sim.true_position(0) == pytest.approx(1850, abs=1)          # ama gerçek konum 150 geride


def test_following_error_stops_encoder_axes():
    h = Host()
    h.ready()
    assert h.result(MsgType.CMD_MOVE, axis=2, target=2000, vmax=8000, accel=40000) == Result.OK
    h.adv(1.0, beat=True)
    assert h.status().state == State.ENABLED
    h.sim.inject_step_loss(2, 500)                                       # eşik 200 adım / 100 ms
    h.adv(0.5, beat=True)
    s = h.status()
    assert s.state == State.ESTOPPED and s.reason == EstopReason.FOLLOWING_ERROR
    assert [f.code for f in h.faults()] == [FaultCode.FOLLOWING_ERROR]


def test_hardware_estop_press_cuts_everything_and_needs_clear():
    h = Host()
    h.ready()
    h.result(MsgType.CMD_MOVE, axis=0, target=50000, vmax=2000, accel=20000)
    h.adv(0.3, beat=True)
    assert h.status().axes[0].moving
    h.sim.press_estop()
    s = h.status()
    assert s.state == State.ESTOPPED and s.reason == EstopReason.HW_BUTTON
    assert s.flags & StatusFlag.HW_LOOP_OPEN and not any(a.moving for a in s.axes)
    pos = s.axes[0].pos
    h.adv(1.0, beat=True)
    assert h.status().axes[0].pos == pos                                  # donmuş
    assert h.result(MsgType.CMD_ENABLE) == Result.ESTOP_ACTIVE
    assert h.result(MsgType.CMD_MOVE, axis=0, target=1, vmax=100, accel=100) == Result.ESTOP_ACTIVE
    assert h.result(MsgType.CMD_CLEAR) == Result.HW_LOOP_OPEN             # buton basılıyken açılamaz
    h.sim.release_estop()
    assert h.status().state == State.ESTOPPED                             # bırakmak kilidi AÇMAZ
    assert h.result(MsgType.CMD_CLEAR) == Result.OK
    s = h.status()
    assert s.state == State.UNHOMED                                       # açık döngü: yeniden home
    assert [f.code for f in h.faults()][0] == FaultCode.HW_ESTOP_PRESSED


def test_boot_with_open_loop_starts_estopped():
    h = Host(hw_loop_closed=False)
    s = h.status()
    assert s.state == State.ESTOPPED and s.reason == EstopReason.BOOT
    assert h.result(MsgType.CMD_HOME, mask=0x3F) == Result.ESTOP_ACTIVE


def test_software_estop_always_works_and_clear_is_idempotent():
    h = Host()
    assert h.result(MsgType.CMD_ESTOP) == Result.OK                       # her durumda (UNHOMED'dayken de)
    assert h.status().reason == EstopReason.HOST_CMD
    assert h.result(MsgType.CMD_CLEAR) == Result.OK
    assert h.result(MsgType.CMD_CLEAR) == Result.OK


def test_absolute_encoder_axis_keeps_reference_after_estop():
    h = Host([SimAxis(EncoderMode.ABSOLUTE, has_home_switch=False)])
    h.send(MsgType.HEARTBEAT)
    assert h.result(MsgType.CMD_HOME, mask=1) == Result.OK
    assert h.status().state == State.HOMED
    h.send(MsgType.CMD_ESTOP, cid=500)
    assert h.result(MsgType.CMD_CLEAR) == Result.OK
    assert h.status().state == State.HOMED                                # mutlak encoder referansı korur


# --- iki yönlü watchdog (MCU tarafı) ---------------------------------------------------------------
def test_watchdog_trips_when_host_goes_silent_while_enabled():
    h = Host()
    h.ready()
    h.result(MsgType.CMD_MOVE, axis=0, target=60000, vmax=2000, accel=20000)
    h.adv(0.3, beat=True)
    assert h.status().axes[0].moving
    h.adv(0.25)                                                            # 250 ms sessizlik: henüz değil
    assert h.status().state == State.ENABLED
    h.adv(0.1)                                                             # > 300 ms
    s = h.status()
    assert s.state == State.ESTOPPED and s.reason == EstopReason.WATCHDOG
    assert s.flags & StatusFlag.WATCHDOG_TRIPPED and not any(a.moving for a in s.axes)
    f = h.faults()[-1]
    assert f.code == FaultCode.WATCHDOG and f.detail >= 300
    assert h.sim.counters['watchdog_trips'] == 1


def test_heartbeats_keep_the_watchdog_quiet_and_any_frame_counts():
    h = Host()
    h.ready()
    h.adv(3.0, beat=True)                                                  # 100 ms'de bir değil 50 ms'de bir
    assert h.status().state == State.ENABLED
    for _ in range(8):                                                     # komutlar da canlılık sayılır
        h.adv(0.2)
        h.result(MsgType.CMD_STOP, mask=1)
    assert h.status().state == State.ENABLED


def test_watchdog_does_not_latch_when_drivers_are_off_and_link_recovers():
    h = Host()
    h.send(MsgType.HEARTBEAT)
    h.adv(2.0)                                                             # host sustu ama sürücüler kapalı
    s = h.status()
    assert s.state == State.UNHOMED and not s.flags & StatusFlag.HOST_LINK_OK
    h.send(MsgType.HEARTBEAT)
    h.adv(0.15, beat=True)                                                  # bir sonraki STATUS'u bekle
    assert h.status().flags & StatusFlag.HOST_LINK_OK


def test_watchdog_during_homing_is_also_an_estop():
    h = Host()
    h.send(MsgType.HEARTBEAT)
    h.result(MsgType.CMD_HOME, mask=0x3F)
    h.adv(0.4)
    assert h.status().state == State.ESTOPPED and h.status().reason == EstopReason.WATCHDOG


def test_watchdog_config_range_is_enforced():
    h = Host()
    h.send(MsgType.HEARTBEAT)
    for bad in (199, 501, 0, -5, 10_000):
        assert h.result(MsgType.CMD_CONFIG, key=ConfigKey.WATCHDOG_MS, axis=0, value=bad) == Result.BAD_ARG
    assert h.sim.watchdog_ms == 300
    assert h.result(MsgType.CMD_CONFIG, key=ConfigKey.WATCHDOG_MS, axis=0, value=200) == Result.OK
    h.ready()
    h.adv(0.15)
    assert h.status().state == State.ENABLED
    h.adv(0.1)
    assert h.status().state == State.ESTOPPED                              # 200 ms'de tetiklendi


# --- komut güvenilirliği ---------------------------------------------------------------------------
def test_duplicate_command_is_acked_again_but_not_executed_twice():
    h = Host()
    h.ready()
    h.result(MsgType.CMD_MOVE, axis=0, target=1000, vmax=8000, accel=40000, cid=900)
    h.adv(1.0, beat=True)
    before = h.sim.counters['cmds']
    n_acks = sum(1 for f in h.frames if f.type == MsgType.ACK)
    h.send(MsgType.CMD_MOVE, cid=900, flags=2, axis=0, target=2000, vmax=8000, accel=40000)  # RETRY
    h.adv(1.0, beat=True)
    assert h.sim.counters['cmds'] == before and h.sim.counters['dup_cmds'] == 1
    assert sum(1 for f in h.frames if f.type == MsgType.ACK) == n_acks + 1
    assert h.status().axes[0].pos == 1000                                  # yineleme hareket ettirmedi


def test_controlled_stop_decelerates_without_estop():
    h = Host()
    h.ready()
    h.result(MsgType.CMD_MOVE, axis=0, target=60000, vmax=8000, accel=40000)
    h.adv(0.5, beat=True)
    assert h.status().axes[0].moving
    assert h.result(MsgType.CMD_STOP, mask=1) == Result.OK
    h.adv(1.0, beat=True)
    s = h.status()
    assert s.state == State.ENABLED and not s.axes[0].moving and 0 < s.axes[0].pos < 60000


def test_limit_switch_violation_estops():
    h = Host([SimAxis(EncoderMode.NONE, phys_max=2000.0)])
    h.ready(mask=1)
    h.result(MsgType.CMD_DISABLE)
    h.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=0, value=50_000)   # yanlış yazılım limiti
    h.result(MsgType.CMD_ENABLE)
    h.result(MsgType.CMD_MOVE, axis=0, target=5000, vmax=8000, accel=40000)
    h.adv(1.0, beat=True)
    s = h.status()
    assert s.state == State.ESTOPPED and s.reason == EstopReason.LIMIT_VIOLATION
    assert s.axes[0].flags & AxisFlag.LIMIT_MAX


def test_unknown_and_malformed_commands_get_an_answer_not_a_crash():
    h = Host()
    h.send(MsgType.HEARTBEAT)
    from robot_hw_bridge.codec import Frame, encode_frame
    h.raw(encode_frame(Frame(0x1E, h.seq, 0, b'\x07\x00')))
    h.seq += 1
    assert h.acks[7].result == Result.UNKNOWN_CMD
    h.raw(encode_frame(Frame(MsgType.CMD_MOVE, h.seq, 0, b'\x08\x00\x01')))    # kısa yük
    h.seq += 1
    assert h.acks[8].result == Result.BAD_ARG
    assert h.status().state == State.UNHOMED


def test_lost_host_frames_are_counted_and_stale_ones_ignored():
    h = Host()
    h.send(MsgType.HEARTBEAT)
    h.seq += 3                                                              # 3 çerçeve hatta kayboldu
    h.send(MsgType.HEARTBEAT)
    assert h.sim.seq_stats['lost'] == 3
    from robot_hw_bridge.codec import Frame, encode_frame
    h.raw(encode_frame(Frame(MsgType.CMD_ESTOP, 0, 0, b'\x09\x00')))        # çok eski seq: işlenmez
    assert h.sim.seq_stats['duplicates'] == 1 and h.status().state == State.UNHOMED


def test_corrupted_frames_are_counted_and_never_executed():
    h = Host()
    from robot_hw_bridge.codec import Frame, encode_frame
    from robot_hw_bridge.protocol import pack_cmd
    wire = bytearray(encode_frame(Frame(MsgType.CMD_ESTOP, 0, 0, pack_cmd(MsgType.CMD_ESTOP, 1))))
    wire[5] ^= 0x10
    h.raw(bytes(wire))
    assert h.sim.decoder_stats['crc_errors'] + h.sim.decoder_stats['cobs_errors'] \
        + h.sim.decoder_stats['length_errors'] == 1
    assert h.status().state == State.UNHOMED and 1 not in h.acks
