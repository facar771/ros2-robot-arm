import math

import numpy as np
import pytest
from robot_vision.platform import (
    HOME,
    PlatformLimits,
    SimPlatform,
    camera_pose,
    camera_velocity,
    world_to_optical,
)
from robot_vision.servo import (
    Command,
    LockMonitor,
    Pid,
    PidGains,
    Sample,
    Servo,
    ServoConfig,
    line_of_sight_rates,
    summarize,
)
from robot_vision.tracking_sim import CAMERA

CFG = ServoConfig()


# ---- PID ---------------------------------------------------------------------------------------
def test_pid_output_is_clamped_and_integral_does_not_wind_up():
    pid = Pid(PidGains(kp=1.0, ki=5.0, i_limit=0.4, out_limit=0.5))
    for _ in range(200):
        out = pid.update(10.0, 0.1)
    assert out == 0.5
    pid.update(-1.0, 0.1)                       # error flips: must leave saturation at once
    assert pid.update(-1.0, 0.1) < 0.0


def test_pid_integral_removes_a_constant_offset():
    pid = Pid(PidGains(kp=0.5, ki=2.0, out_limit=5.0))
    plant, target = 0.0, 1.0
    for _ in range(600):
        plant += (pid.update(target - plant, 0.05) - 0.4) * 0.05    # constant drift of -0.4
    assert plant == pytest.approx(target, abs=1e-3)


# ---- servo signs and geometry ------------------------------------------------------------------
@pytest.mark.parametrize('point, yaw_sign, pitch_sign, slide_sign', [
    ((0.3, 0.0, 2.0), -1, 0, 0),        # target right of centre: turn right (negative yaw)
    ((-0.3, 0.0, 2.0), +1, 0, 0),
    ((0.0, 0.3, 2.0), 0, +1, 0),        # below centre: tilt down (positive pitch)
    ((0.0, -0.3, 2.0), 0, -1, 0),
    ((0.0, 0.0, 3.0), 0, 0, +1),        # too far: move forward
    ((0.0, 0.0, 1.2), 0, 0, -1),
])
def test_servo_pushes_the_error_towards_zero(point, yaw_sign, pitch_sign, slide_sign):
    cmd = Servo(CFG).command(None, 0.0, 0.1, optical_point=point)
    for value, sign in ((cmd.yaw_rate, yaw_sign), (cmd.pitch_rate, pitch_sign),
                        (cmd.slide_rate, slide_sign)):
        assert (np.sign(value) == sign) if sign else abs(value) < 1e-9


def test_no_target_gives_zero_rates_and_resets_after_a_long_loss():
    servo = Servo(CFG)
    for k in range(10):
        servo.command(None, k * 0.1, 0.1, optical_point=(0.5, 0.2, 3.0))
    assert servo.command(None, 1.0, 0.1) == Command()
    assert servo._yaw._i != 0.0                 # short loss: keeps its memory
    servo.command(None, 3.0, 0.1)
    assert servo._yaw._i == 0.0                 # long loss: integrators cleared


def test_rates_never_exceed_the_actuator_limits():
    cmd = Servo(CFG).command(None, 0.0, 0.1, optical_point=(5.0, 5.0, 30.0),
                             feed_forward=Command(9.0, 9.0, 9.0))
    assert abs(cmd.yaw_rate) <= CFG.yaw.out_limit
    assert abs(cmd.pitch_rate) <= CFG.pitch.out_limit
    assert abs(cmd.slide_rate) <= CFG.slide.out_limit


# ---- line-of-sight feed-forward ----------------------------------------------------------------
def test_line_of_sight_rates_match_finite_differences():
    cam = np.array([0.0, 0.0, 1.0])
    target, v = np.array([3.5, 1.0, 1.2]), np.array([0.0, 0.5, 0.1])
    fwd = np.array([1.0, 0.0, 0.0])
    got = line_of_sight_rates(cam, np.zeros(3), fwd, target, v)
    eps = 1e-5

    def angles(p):
        rel = p - cam
        return math.atan2(rel[1], rel[0]), math.atan2(-rel[2], math.hypot(rel[0], rel[1]))

    a0, a1 = angles(target), angles(target + v * eps)
    assert got.yaw_rate == pytest.approx((a1[0] - a0[0]) / eps, rel=1e-3)
    assert got.pitch_rate == pytest.approx((a1[1] - a0[1]) / eps, rel=1e-3)
    assert got.slide_rate == pytest.approx(0.0, abs=1e-9)   # moving across the line of sight


def test_line_of_sight_is_independent_of_how_the_camera_itself_moves():
    cam, target = np.array([0.0, 0.0, 1.0]), np.array([3.0, 0.0, 1.0])
    still = line_of_sight_rates(cam, np.zeros(3), np.array([1.0, 0, 0]), target, np.zeros(3))
    follow = line_of_sight_rates(cam, np.array([0.0, 0.4, 0.0]), np.array([1.0, 0, 0]), target,
                                 np.array([0.0, 0.4, 0.0]))     # both move together
    assert still == Command() and follow.yaw_rate == pytest.approx(0.0, abs=1e-12)


# ---- lock monitor ------------------------------------------------------------------------------
def feed(lock, seconds, error=0.0, dist=0.0, start=0.0, dt=0.1):
    state = None
    for k in range(int(round(seconds / dt))):
        state = lock.update(start + k * dt, error, dist)
    return state


def test_lock_needs_the_full_time_in_window():
    lock = LockMonitor(CFG)
    state = feed(lock, 4.4)
    assert state.in_window and not state.locked
    assert lock.update(4.5, 0.0, 0.0).locked


def test_out_of_window_detection_resets_the_timer():
    lock = LockMonitor(CFG)
    feed(lock, 4.0)
    assert not lock.update(4.0, CFG.pixel_radius + 1, 0.0).in_window
    assert lock.update(4.1, 0.0, 0.0).timer == 0.0
    assert not feed(lock, 4.0, start=4.2).locked


def test_distance_error_outside_tolerance_is_not_in_window():
    lock = LockMonitor(CFG)
    assert not lock.update(0.0, 0.0, CFG.distance_tolerance + 0.01).in_window


def test_short_gaps_do_not_break_but_long_ones_do():
    lock = LockMonitor(CFG)
    feed(lock, 3.0)
    lock.update(3.0, None, None)                          # two lost frames (0.3 s): tolerated
    lock.update(3.1, None, None)
    assert lock.update(3.2, 0.0, 0.0).timer == pytest.approx(3.2)
    assert feed(lock, 1.6, start=3.3).locked is True
    lock.update(5.0, None, None)
    state = lock.update(5.6, None, None)                  # 0.9 s without detections: broken
    assert state.locked is False
    assert lock.update(5.7, 0.0, 0.0).timer == 0.0


def test_lock_drops_as_soon_as_the_window_is_left():
    lock = LockMonitor(CFG)
    feed(lock, 5.0)
    assert lock.update(5.0, 0.0, 0.0).locked
    assert not lock.update(5.1, 50.0, 0.0).locked


# ---- platform ----------------------------------------------------------------------------------
def test_platform_pose_is_yaw_slide_then_pitch():
    pose = camera_pose((math.pi / 2, 2.0, 0.3))
    assert pose.position == pytest.approx((0.0, 2.0, 1.0), abs=1e-9)
    forward = pose.direction_to_world((1.0, 0.0, 0.0))
    assert forward[0] == pytest.approx(0.0, abs=1e-9) and forward[1] > 0 and forward[2] < 0


def test_world_to_optical_puts_a_point_ahead_on_the_axis():
    q = (0.4, 1.0, 0.2)
    pose = camera_pose(q)
    ahead = np.array(pose.position) + 2.0 * np.array(pose.direction_to_world((1.0, 0, 0)))
    opt = world_to_optical(q, CAMERA, ahead)
    assert opt == pytest.approx((0.0, 0.0, 2.0), abs=1e-9)


def test_sim_platform_respects_rate_and_joint_limits():
    p = SimPlatform(HOME, PlatformLimits(slide=(0.0, 1.0)))
    for _ in range(100):
        p.step(Command(yaw_rate=99.0, pitch_rate=-99.0, slide_rate=99.0), 0.1)
    yaw, slide, pitch = p.q
    assert (yaw, slide, pitch) == pytest.approx((1.2, 1.0, -0.6))
    one = SimPlatform(HOME)
    one.step(Command(yaw_rate=99.0), 0.1)
    assert abs(one.q[0] - HOME[0]) < 1.5 * 0.1       # never faster than the rate limit


def test_camera_velocity_follows_the_slide_and_the_swing():
    v = camera_velocity((0.0, 2.0, 0.0), Command(yaw_rate=0.5, slide_rate=0.3))
    assert v == pytest.approx((0.3, 1.0, 0.0))        # forward 0.3, swing 2 m * 0.5 rad/s sideways


# ---- metrics -----------------------------------------------------------------------------------
def _samples(spec):
    return [Sample(t=k * 0.1, visible=v, pixel_error=pe, distance_error=de, in_window=iw,
                   locked=lk, latency=0.1) for k, (v, pe, de, iw, lk) in enumerate(spec)]


def test_summarize_counts_acquisition_hold_losses_and_rms():
    on = (True, 3.0, 0.1, True, True)
    off = (False, None, None, False, False)
    spec = [(True, 20.0, 0.5, False, False)] * 5 + [on] * 10 + [off] * 5 + [on] * 5 + [off] * 2
    m = summarize('demo', _samples(spec))
    assert m.acquisition_time == pytest.approx(0.5)
    assert m.loss_events == 1                       # the 0.5 s gap; the 0.2 s tail is too short
    assert 0.5 < m.lock_hold_ratio < 1.0
    assert m.rms_pixel_error == pytest.approx(3.0) and m.rms_distance_error == pytest.approx(0.1)
    assert m.median_latency == pytest.approx(0.1)


def test_summarize_reports_a_run_that_never_locked():
    m = summarize('x', _samples([(True, 30.0, 1.0, False, False)] * 20))
    assert m.acquisition_time is None and m.lock_hold_ratio == 0.0


def test_relock_time_zero_when_the_lock_never_dropped_and_none_when_it_never_returned():
    held = _samples([(True, 1.0, 0.0, True, True)] * 40)
    assert summarize('a', held, disturbance=2.0).relock_time == 0.0
    dead = _samples([(True, 1.0, 0.0, True, True)] * 20 + [(False, None, None, False, False)] * 20)
    assert summarize('b', dead, disturbance=1.0).relock_time is None
