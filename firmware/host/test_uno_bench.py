"""K9 Faz 2a: tezgâh derlemesi (K9_BENCH=1) mantık testi. Sahte HAL ile; AVR'de ya da motorda ÇALIŞMAZ.
Ayrıca bayraksız derlemenin hareketsiz kaldığı kanıtlanır (HOME/ENABLE -> NO_REFERENCE).
Çalıştır:  cd <repo> && PYTHONPATH=src/robot_hw_bridge python3 -m pytest firmware/host -q
"""
import shutil

import pytest

from k9_hostlib import Dev, build
from robot_hw_bridge.protocol import (
    ConfigKey, EstopReason, Fault, FaultCode, MsgType, Result, State, StatusFlag,
)

pytestmark = pytest.mark.skipif(shutil.which('g++') is None, reason='g++ yok')
J1, J2, J7 = 0, 1, 6


@pytest.fixture(scope='module')
def bench_bin(tmp_path_factory):
    return build(tmp_path_factory.mktemp('k9b') / 'k9_bench', '-DK9_BENCH=1')


@pytest.fixture(scope='module')
def plain_bin(tmp_path_factory):
    return build(tmp_path_factory.mktemp('k9p') / 'k9_plain')


@pytest.fixture
def b(bench_bin):
    d = Dev(bench_bin)
    d.at(60)
    yield d
    d.close()


def st(d, ms):
    return d.status(d.at(ms))


def zero_enable(d, axes=(J1, J2, J7)):
    mask = sum(1 << a for a in axes)
    assert d.result(MsgType.CMD_HOME, mask=mask).result == Result.OK
    assert d.result(MsgType.CMD_ENABLE).result == Result.OK


def move(d, axis, target, vmax=100, accel=100):
    return d.result(MsgType.CMD_MOVE, axis=axis, target=target, vmax=vmax, accel=accel)


# ------------------------------------------------------------------ bayraksız derleme hareketsiz kalır
def test_plain_build_stays_motionless(plain_bin):
    d = Dev(plain_bin)
    try:
        assert d.result(MsgType.CMD_HOME, mask=0x01).result == Result.NO_REFERENCE
        assert d.result(MsgType.CMD_ENABLE).result == Result.NO_REFERENCE
        assert d.result(MsgType.CMD_MOVE, axis=0, target=1, vmax=1, accel=1).result == Result.BAD_STATE
        s = st(d, 60)
        assert not s.flags & StatusFlag.BENCH_MODE and s.state == State.UNHOMED
    finally:
        d.close()


# ------------------------------------------------------------------ açılış / durum
def test_boot_status_has_bench_mode_and_no_motion(b):
    s = st(b, 120)
    assert s.state == State.UNHOMED
    assert s.flags & StatusFlag.BENCH_MODE and s.flags & StatusFlag.OPEN_LOOP_ANY
    assert not s.flags & StatusFlag.HW_LOOP_OPEN
    assert b.result(MsgType.CMD_ENABLE).result == Result.NO_REFERENCE      # sıfırlanmadan ENABLE yok
    assert move(b, J1, 10).result == Result.BAD_STATE
    pw = [h[2] for h in b.hal()[0]]
    assert pw == [0, 0, 0]


def test_boot_with_loop_open_is_locked(bench_bin):
    d = Dev(bench_bin, 'open')
    try:
        s = st(d, 60)
        assert s.state == State.ESTOPPED and s.reason == EstopReason.BOOT
        assert s.flags & StatusFlag.HW_LOOP_OPEN
        assert d.result(MsgType.CMD_HOME, mask=1).result == Result.ESTOP_ACTIVE
        assert d.result(MsgType.CMD_CLEAR).result == Result.HW_LOOP_OPEN   # döngü açıkken kilit açılmaz
        d.loop(True)
        d.at(120)
        assert d.result(MsgType.CMD_CLEAR).result == Result.OK
        assert st(d, 180).state == State.UNHOMED
    finally:
        d.close()


# ------------------------------------------------------------------ tezgâh sıfırı
def test_home_is_bench_zero_and_limited_to_step_axes(b):
    assert b.result(MsgType.CMD_HOME, mask=1 << 2).result == Result.BAD_ARG       # servo yok
    assert b.result(MsgType.CMD_HOME, mask=0x41 | 0x08).result == Result.BAD_ARG
    assert b.result(MsgType.CMD_HOME, mask=0).result == Result.BAD_ARG
    assert b.result(MsgType.CMD_HOME, mask=1 << J1).result == Result.OK
    s = st(b, 120)
    assert s.state == State.HOMED and s.flags & StatusFlag.BENCH_MODE
    assert s.axes[J1].homed and s.axes[J1].open_loop and not s.axes[J1].enabled
    assert not s.axes[J2].homed


def test_enable_powers_only_zeroed_axes(b):
    assert b.result(MsgType.CMD_HOME, mask=1 << J1).result == Result.OK
    assert b.result(MsgType.CMD_ENABLE).result == Result.OK
    assert [h[2] for h in b.hal()[0]] == [1, 0, 0]
    s = st(b, 120)
    assert s.state == State.ENABLED and s.axes[J1].enabled and not s.axes[J2].enabled
    assert move(b, J2, 5).result == Result.BAD_STATE                              # J2 sıfırlanmadı
    assert b.result(MsgType.CMD_HOME, mask=1 << J1).result == Result.BAD_STATE    # ENABLED iken yeniden sıfır yok


# ------------------------------------------------------------------ gömülü limitler
def test_embedded_limits_reject_and_pc_can_only_narrow(b):
    zero_enable(b)
    assert move(b, J1, 257).result == Result.LIMIT
    assert move(b, J1, -257).result == Result.LIMIT
    assert move(b, J7, 51).result == Result.LIMIT
    r = move(b, J1, 10, vmax=301)
    assert r.result == Result.LIMIT and r.detail == 2
    r = move(b, J1, 10, accel=301)
    assert r.result == Result.LIMIT and r.detail == 3
    assert move(b, J7, 10, vmax=101).result == Result.LIMIT
    assert move(b, J1, 10, vmax=0).result == Result.BAD_ARG
    assert move(b, J1, 10, accel=0).result == Result.BAD_ARG
    assert move(b, 3, 10).result == Result.BAD_ARG                                # servo ekseni
    assert move(b, 9, 10).result == Result.BAD_ARG
    # PC genişletemez
    assert b.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=J1, value=257).result == Result.LIMIT
    assert b.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MIN, axis=J7, value=-51).result == Result.LIMIT
    # PC daraltabilir
    assert b.result(MsgType.CMD_CONFIG, key=ConfigKey.SOFT_MAX, axis=J1, value=40).result == Result.OK
    assert move(b, J1, 41).result == Result.LIMIT
    assert move(b, J1, 40, vmax=300, accel=300).result == Result.OK
    assert b.hal()[0][0][1] == 40
    # hiçbir reddedilen komut HAL'e hedef yazmadı
    assert b.hal()[0][1][1] == 0 and b.hal()[0][2][1] == 0


def test_move_runs_to_target_reports_position_and_flags(b):
    zero_enable(b)
    assert move(b, J1, 100, vmax=300, accel=300).result == Result.OK
    s = st(b, 120)
    assert s.axes[J1].moving
    assert move(b, J1, 5).result == Result.BUSY                                   # hedef değiştirilmez
    assert b.result(MsgType.CMD_DISABLE).result == Result.BUSY                    # hareket ortasında kesme
    b.step(60)
    assert st(b, 180).axes[J1].pos == 60
    b.step(100)
    s = st(b, 240)
    assert s.axes[J1].pos == 100 and not s.axes[J1].moving
    assert b.result(MsgType.CMD_DISABLE).result == Result.OK
    assert [h[2] for h in b.hal()[0]] == [0, 0, 0]
    s = st(b, 300)
    assert s.state == State.HOMED and s.axes[J1].pos == 100 and s.axes[J1].homed   # konum korunur


def test_stop_halts_moving_axis_and_stays_enabled(b):
    zero_enable(b)
    assert move(b, J2, -200, vmax=300, accel=300).result == Result.OK
    b.step(30)
    assert b.result(MsgType.CMD_STOP, mask=1 << J2).result == Result.OK
    b.step(500)
    s = st(b, 120)
    assert s.axes[J2].pos == -30 and s.state == State.ENABLED


# ------------------------------------------------------------------ watchdog / acil dur
def test_host_estop_cuts_power_locks_and_needs_rezero(b):
    zero_enable(b)
    assert move(b, J1, 100).result == Result.OK
    b.step(10)
    h0 = b.hal()[1]
    assert b.result(MsgType.CMD_ESTOP).result == Result.OK
    hal, halts = b.hal()
    assert [h[2] for h in hal] == [0, 0, 0] and halts > h0
    b.step(1000)
    assert b.hal()[0][0][0] == 10                                                 # step durdu
    s = st(b, 120)
    assert s.state == State.ESTOPPED and s.reason == EstopReason.HOST_CMD
    assert move(b, J1, 0).result == Result.ESTOP_ACTIVE
    assert b.result(MsgType.CMD_ENABLE).result == Result.ESTOP_ACTIVE
    assert b.result(MsgType.CMD_CLEAR).result == Result.OK
    s = st(b, 180)
    assert s.state == State.UNHOMED and not s.axes[J1].homed
    assert b.result(MsgType.CMD_ENABLE).result == Result.NO_REFERENCE             # yeniden sıfır şart


def test_watchdog_stops_steps_and_locks(b):
    zero_enable(b)
    assert move(b, J1, 200, vmax=300, accel=300).result == Result.OK
    frames = b.at(60 + 400)                                                       # host sustu (> 300 ms)
    assert b.status(frames).state == State.ESTOPPED
    assert b.status(frames).reason == EstopReason.WATCHDOG
    faults = [f for f in frames if f.type == MsgType.FAULT]
    assert faults
    hal, _ = b.hal()
    assert [h[2] for h in hal] == [0, 0, 0]
    b.step(500)
    assert b.hal()[0][0][0] == 0


def test_watchdog_when_disabled_does_not_lock(b):
    b.result(MsgType.CMD_HOME, mask=1 << J1)
    s = b.status(b.at(60 + 400))
    assert s.state == State.HOMED and not s.flags & StatusFlag.HOST_LINK_OK


def test_hw_loop_opening_estops_within_debounce_and_stays_locked(b):
    zero_enable(b)
    assert move(b, J1, 100).result == Result.OK
    b.step(5)
    b.loop(False)
    b.at(61)                                                                      # ilk okuma: bekle
    b.at(63)
    assert b.counters()['state'] == State.ENABLED                                 # 5 ms dolmadan kilitlenmez
    frames = b.at(70)                                                             # >= 5 ms açık
    s = b.status(frames)
    assert s.state == State.ESTOPPED and s.reason == EstopReason.HW_BUTTON
    assert s.flags & StatusFlag.HW_LOOP_OPEN
    assert [h[2] for h in b.hal()[0]] == [0, 0, 0]
    faults = [Fault.unpack(f.payload) for f in frames if f.type == MsgType.FAULT]
    assert faults and faults[0].code == FaultCode.HW_ESTOP_PRESSED
    b.step(100)
    assert b.hal()[0][0][0] == 5
    b.loop(True)                                                                  # buton bırakıldı: otomatik açılmaz
    s = st(b, 130)
    assert s.state == State.ESTOPPED and not s.flags & StatusFlag.HW_LOOP_OPEN
    assert b.result(MsgType.CMD_CLEAR).result == Result.OK


def test_short_loop_glitch_below_debounce_is_ignored(b):
    zero_enable(b)
    b.loop(False)
    b.at(61)
    b.loop(True)
    s = st(b, 120)
    assert s.state == State.ENABLED


def test_enable_refused_while_loop_open(b):
    assert b.result(MsgType.CMD_HOME, mask=1 << J1).result == Result.OK
    b.loop(False)
    assert b.result(MsgType.CMD_ENABLE).result == Result.HW_LOOP_OPEN


def test_disable_states(b):
    assert b.result(MsgType.CMD_DISABLE).result == Result.BAD_STATE               # UNHOMED
    b.result(MsgType.CMD_HOME, mask=1 << J1)
    assert b.result(MsgType.CMD_DISABLE).result == Result.OK                      # HOMED: zaten kapalı
