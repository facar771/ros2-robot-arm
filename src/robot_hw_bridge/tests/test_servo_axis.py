"""K6b: servo ekseni (hobby_servo) protokolü ve simülatörü. docs/HARDWARE.md §7a.

Birim mikrosaniye darbe; aynalı çift servo tek mantıksal eksen; HOME = operatör onayı (hareket yok);
ENABLE sıçramasız; hız sınırı; ACİL DUR/DISABLE onayı düşürür. GERÇEK servo, firmware ya da köprü yoktur:
burada doğrulanan, protokolün ve simülatörün (çalıştırılabilir belirtim) tutarlılığıdır.
"""
import math

import pytest
from helpers import Host

from robot_hw_bridge.protocol import (
    MAX_AXES,
    MAX_PAYLOAD,
    AxisInfo,
    AxisKind,
    ConfigKey,
    EncoderMode,
    EstopReason,
    Hello,
    MsgType,
    ProtocolError,
    Result,
    State,
)
from robot_hw_bridge.sim import SimAxis

STEP, SERVO, MIRROR = 0, 1, 2           # eksen indeksleri
#ref darbesi; aynalı toplam (eş = 2900 - birincil; tam aralıkta tek geçerli toplam)
REF, SUM = 1500, 2900


def servo_host(**kw):
    return Host([SimAxis(), SimAxis.servo(), SimAxis.servo(mirrored=True)], **kw)


def ack(h, t, **kw):
    return h.send(t, **kw).result


def cfg(h, key, axis, value):
    return ack(h, MsgType.CMD_CONFIG, key=int(key), axis=axis, value=value)


def calibrate(h):
    """Operatör duruşu (ref) ve aynalı toplam CONFIG ile yazılır; servo YOK sayılan onay HOME ile verilir."""
    assert cfg(h, ConfigKey.SERVO_REF_US, SERVO, REF) == Result.OK
    assert cfg(h, ConfigKey.SERVO_REF_US, MIRROR, REF) == Result.OK
    assert cfg(h, ConfigKey.MIRROR_SUM_US, MIRROR, SUM) == Result.OK


def home_step(h):
    assert ack(h, MsgType.CMD_HOME, mask=0b001) == Result.OK
    h.adv(3.0, beat=True)
    assert h.status().axes[STEP].homed


def ready(h):
    """step homed + iki servo onaylı -> HOMED."""
    h.send(MsgType.HEARTBEAT)
    calibrate(h)
    home_step(h)
    assert ack(h, MsgType.CMD_HOME, mask=0b110) == Result.OK
    assert h.status().state == State.HOMED
    return h


def enabled(h):
    ready(h)
    assert ack(h, MsgType.CMD_ENABLE) == Result.OK
    return h


# --- HELLO v2 -------------------------------------------------------------------------------------
def test_step_only_hello_stays_byte_identical_proto1():
    h = Hello(1, 0, 3, 7, b'ESP32-A1', (AxisInfo(EncoderMode.NONE, True, True, False),
                                        AxisInfo(EncoderMode.ABSOLUTE, False)))
    raw = h.pack()
    assert raw[0] == 1 and len(raw) == 6 + len(b"ESP32-A1") + 2 * 2   # eski düzen: eksen başına 2 bayt
    assert Hello.unpack(raw) == h


def test_servo_hello_is_proto2_and_roundtrips():
    h = servo_host().hello()
    assert h.proto == 2 and [a.kind for a in h.axes] == [AxisKind.STEP, AxisKind.SERVO,
                                                         AxisKind.SERVO_MIRRORED]
    assert (h.axes[SERVO].hard_min_us, h.axes[SERVO].hard_max_us, h.axes[SERVO].max_slew_us_s) \
        == (500, 2400, 4000)
    assert Hello.unpack(h.pack()) == h and h.axes[SERVO].referenceable


def test_hello_wire_size_fits_the_uno_payload_cap_even_with_max_axes():
    servo = AxisInfo(EncoderMode.NONE, False, False, False, AxisKind.SERVO_MIRRORED, 500, 2400, 4000)
    h = Hello(2, 0, 1, 1, b'UNO', (servo,) * MAX_AXES)
    assert len(h.pack()) <= 96 < MAX_PAYLOAD            # Robot_10'un Uno yük sınırı önerisi (FAZ0_PLAN §5)


@pytest.mark.parametrize('mutate, why', [
    (lambda r: bytes([1]) + r[1:], 'proto 1 ile servo bildirilemez'),
    (lambda r: bytes([3]) + r[1:], 'bilinmeyen proto'),
    (lambda r: r[:-1], 'kesik'),
    (lambda r: r[:16 + 8 * 1 + 1] + bytes([0x18 | 0x18]) + r[16 + 8 * 1 + 2:], 'kind=3 / ayrılmış bit'),
])
def test_bad_hello_is_rejected_not_guessed(mutate, why):
    raw = servo_host().hello().pack()
    with pytest.raises(ProtocolError):
        Hello.unpack(mutate(raw))


def test_servo_axisinfo_invariants():
    for bad in (dict(hard_min_us=2400, hard_max_us=500, max_slew_us_s=1000),      # min >= max
                dict(hard_min_us=100, hard_max_us=2000, max_slew_us_s=1000),       # makul aralığın dışı
                dict(hard_min_us=500, hard_max_us=2400, max_slew_us_s=0)):         # hız sınırsız
        with pytest.raises(ProtocolError):
            AxisInfo(EncoderMode.NONE, False, False, False, AxisKind.SERVO, **bad)
    with pytest.raises(ProtocolError):                                             # servoda encoder olmaz
        AxisInfo(EncoderMode.INCREMENTAL, False, False, False, AxisKind.SERVO, 500, 2400, 1000)
    with pytest.raises(ProtocolError):                                             # step'te servo alanı olmaz
        AxisInfo(EncoderMode.NONE, True, hard_min_us=500)
    with pytest.raises(ProtocolError):
        Hello(1, 0, 1, 1, b'x', (servo_host().hello().axes[SERVO],)).pack()


# --- HOME = operatör onayı; hareket yok --------------------------------------------------------------
def test_servo_cannot_be_confirmed_without_calibration():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    assert ack(h, MsgType.CMD_HOME, mask=0b010) == Result.NO_REFERENCE            # ref yok
    cfg(h, ConfigKey.SERVO_REF_US, MIRROR, REF)
    assert ack(h, MsgType.CMD_HOME, mask=0b100) == Result.NO_REFERENCE            # aynalıda toplam yok
    assert ack(h, MsgType.CMD_ENABLE) in (Result.BAD_STATE, Result.NO_REFERENCE)
    assert h.sim.state == State.UNHOMED and h.sim.servo_pulses(SERVO) is None


def test_home_on_servo_is_instant_and_moves_nothing():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    calibrate(h)
    assert ack(h, MsgType.CMD_HOME, mask=0b010) == Result.OK
    st = h.status()
    assert st.axes[SERVO].homed and not st.axes[SERVO].moving and st.axes[SERVO].pos == REF
    assert h.sim.servo_pulses(SERVO) is None                                      # PWM hâlâ KAPALI
    assert st.axes[SERVO].open_loop and st.open_loop_any                          # geri besleme yok: kestirim


def test_unconfirmed_servo_reports_no_position():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    calibrate(h)
    assert h.status().axes[SERVO].pos == 0 and not h.status().axes[SERVO].homed


def test_changing_the_reference_drops_the_confirmation():
    h = ready(servo_host())
    assert cfg(h, ConfigKey.SERVO_REF_US, SERVO, REF + 100) == Result.OK          # BAŞKA duruş
    st = h.status()
    assert not st.axes[SERVO].homed and st.state == State.UNHOMED
    assert ack(h, MsgType.CMD_ENABLE) == Result.BAD_STATE
    assert cfg(h, ConfigKey.SERVO_REF_US, SERVO, REF + 100) == Result.OK          # aynı değer: onay düşmez
    assert ack(h, MsgType.CMD_HOME, mask=0b010) == Result.OK
    assert h.status().state == State.HOMED


# --- ENABLE sıçramasız, MOVE hız sınırlı ---------------------------------------------------------------
def test_enable_starts_pwm_at_the_confirmed_pulse_without_a_jump():
    h = enabled(servo_host())
    assert h.sim.servo_pulses(SERVO) == (REF, None)
    p, partner = h.sim.servo_pulses(MIRROR)
    assert (p, partner) == (REF, SUM - REF)
    assert h.status().state == State.ENABLED


def test_move_units_and_argument_checks():
    h = enabled(servo_host())
    ok = dict(axis=SERVO, target=1600, vmax=500, accel=0)
    assert ack(h, MsgType.CMD_MOVE, **{**ok, 'accel': 5000}) == Result.BAD_ARG    # adım-tarzı ivme: reddet
    assert ack(h, MsgType.CMD_MOVE, **{**ok, 'vmax': 0}) == Result.BAD_ARG        # sınırsız hız yok
    assert ack(h, MsgType.CMD_MOVE, **{**ok, 'vmax': 4001}) == Result.BAD_ARG     # firmware tavanı aşılamaz
    assert ack(h, MsgType.CMD_MOVE, **{**ok, 'target': 2401}) == Result.LIMIT      # derleme sınırı
    assert ack(h, MsgType.CMD_MOVE, **{**ok, 'target': 499}) == Result.LIMIT
    assert ack(h, MsgType.CMD_MOVE, **ok) == Result.OK
    # step ekseni aynen: ivme > 0 şart, accel = 0 reddedilir
    assert ack(h, MsgType.CMD_MOVE, axis=STEP, target=100, vmax=1000, accel=0) == Result.BAD_ARG


def test_slew_is_linear_never_exceeds_vmax_and_never_overshoots():
    h = enabled(servo_host())
    assert ack(h, MsgType.CMD_MOVE, axis=SERVO, target=1800, vmax=600, accel=0) == Result.OK
    prev, t0 = h.sim.servo_pulses(SERVO)[0], h.clock.t
    for _ in range(40):
        h.adv(0.05, beat=True)
        now = h.sim.servo_pulses(SERVO)[0]
        assert 0 <= now - prev <= 600 * 0.05 + 1e-6 and now <= 1800
        prev = now
    assert prev == 1800 and h.clock.t - t0 == pytest.approx(2.0)
    assert not h.status().axes[SERVO].moving


def test_mirrored_partner_always_equals_sum_minus_primary_inside_hard_limits():
    h = enabled(servo_host())
    assert ack(h, MsgType.CMD_MOVE, axis=MIRROR, target=2000, vmax=1000, accel=0) == Result.OK
    for _ in range(30):
        h.adv(0.05, beat=True)
        p, q = h.sim.servo_pulses(MIRROR)
        assert q == pytest.approx(SUM - p) and 500 <= p <= 2400 and 500 <= q <= 2400


def test_mirrored_move_whose_partner_would_leave_the_hard_range_is_refused():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    calibrate(h)
    cfg(h, ConfigKey.MIRROR_SUM_US, MIRROR, 2800)                                   # eş = 2800 - p
    #p=500 -> eş 2300 ok, p=2400 -> eş 400 NO
    assert ack(h, MsgType.CMD_HOME, mask=0b100) == Result.BAD_ARG
    cfg(h, ConfigKey.SOFT_MAX, MIRROR, 2300)                                        # daralt: eş en az 500
    assert ack(h, MsgType.CMD_HOME, mask=0b100) == Result.OK


def test_stop_holds_the_servo_where_it_is():
    h = enabled(servo_host())
    ack(h, MsgType.CMD_MOVE, axis=SERVO, target=2000, vmax=500, accel=0)
    h.adv(0.5, beat=True)
    here = h.sim.servo_pulses(SERVO)[0]
    assert ack(h, MsgType.CMD_STOP, mask=0b010) == Result.OK
    h.adv(1.0, beat=True)
    assert h.sim.servo_pulses(SERVO)[0] == here and not h.status().axes[SERVO].moving


# --- CONFIG yalnız daraltır; servo anahtarları ----------------------------------------------------------
def test_config_rules_for_servo_keys():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    assert cfg(h, ConfigKey.SOFT_MIN, SERVO, 400) == Result.BAD_ARG                 # derleme sınırının altı
    assert cfg(h, ConfigKey.SOFT_MAX, SERVO, 2500) == Result.BAD_ARG
    assert cfg(h, ConfigKey.SOFT_MIN, SERVO, 900) == Result.OK
    assert cfg(h, ConfigKey.FOLLOW_ERR_STEPS, SERVO, 10) == Result.BAD_ARG          # geri besleme yok
    assert cfg(h, ConfigKey.MIRROR_SUM_US, SERVO, SUM) == Result.BAD_ARG            # aynalı değil
    assert cfg(h, ConfigKey.SERVO_REF_US, STEP, REF) == Result.BAD_ARG              # step eksende anlamsız
    assert cfg(h, ConfigKey.MIRROR_SUM_US, STEP, SUM) == Result.BAD_ARG
    assert cfg(h, ConfigKey.SERVO_REF_US, SERVO, 100) == Result.BAD_ARG
    assert cfg(h, ConfigKey.MIRROR_SUM_US, MIRROR, 100) == Result.BAD_ARG


def test_servo_calibration_cannot_change_while_enabled():
    h = enabled(servo_host())
    for key, ax, v in ((ConfigKey.SERVO_REF_US, SERVO, 1400), (ConfigKey.MIRROR_SUM_US, MIRROR, 2900),
                       (ConfigKey.SOFT_MIN, SERVO, 800)):
        assert cfg(h, key, ax, v) == Result.BAD_STATE


def test_reference_must_lie_inside_the_soft_range():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    calibrate(h)
    #ref aralığın dışında kaldı
    cfg(h, ConfigKey.SOFT_MIN, SERVO, REF + 100)
    assert ack(h, MsgType.CMD_HOME, mask=0b010) == Result.BAD_ARG


# --- onay düşer: ACİL DUR, DISABLE, watchdog -------------------------------------------------------------
def test_estop_cuts_pwm_and_drops_the_confirmation_until_the_operator_confirms_again():
    h = enabled(servo_host())
    assert ack(h, MsgType.CMD_ESTOP) == Result.OK
    st = h.status()
    assert st.state == State.ESTOPPED and h.sim.servo_pulses(SERVO) is None
    assert not st.axes[SERVO].homed and not st.axes[MIRROR].homed                  # kol düşmüş olabilir
    assert ack(h, MsgType.CMD_CLEAR) == Result.OK
    assert h.status().state == State.UNHOMED
    assert ack(h, MsgType.CMD_ENABLE) == Result.BAD_STATE                          # onaysız ENABLE yok
    home_step(h)
    assert ack(h, MsgType.CMD_HOME, mask=0b110) == Result.OK                       # yeni operatör onayı
    assert ack(h, MsgType.CMD_ENABLE) == Result.OK


def test_disable_frees_the_arm_so_the_confirmation_drops():
    h = enabled(servo_host())
    assert ack(h, MsgType.CMD_DISABLE) == Result.OK
    st = h.status()
    assert h.sim.servo_pulses(SERVO) is None and not st.axes[SERVO].homed
    assert st.axes[STEP].homed and st.state == State.UNHOMED                       # step onayı korunur
    assert ack(h, MsgType.CMD_HOME, mask=0b110) == Result.OK
    assert h.status().state == State.HOMED


def test_disable_is_refused_while_a_servo_is_still_moving():
    h = enabled(servo_host())
    ack(h, MsgType.CMD_MOVE, axis=SERVO, target=2200, vmax=100, accel=0)
    assert ack(h, MsgType.CMD_DISABLE) == Result.BUSY


def test_host_silence_trips_the_watchdog_and_cuts_servo_pwm():
    h = enabled(servo_host())
    h.adv(0.6)                                                                      # kalp atışı yok
    st = h.status()
    assert st.state == State.ESTOPPED and st.reason == EstopReason.WATCHDOG
    assert h.sim.servo_pulses(MIRROR) is None


def test_hw_loop_open_blocks_servo_enable():
    h = ready(servo_host())
    h.sim.hw_loop_closed = False
    assert ack(h, MsgType.CMD_ENABLE) == Result.HW_LOOP_OPEN


def test_step_axes_in_a_mixed_robot_behave_as_before():
    h = servo_host()
    h.send(MsgType.HEARTBEAT)
    #step homing hâlâ anahtarla
    assert ack(h, MsgType.CMD_HOME, mask=0b001) == Result.OK
    h.adv(3.0, beat=True)
    s = h.status()
    assert s.axes[STEP].homed and math.isclose(s.axes[STEP].pos, 0.0, abs_tol=1) and s.state == State.UNHOMED
