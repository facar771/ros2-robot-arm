"""Manual camera-platform control: command validation and the safe rate generator, in closed loop
with the actuator model of :mod:`robot_vision.platform`."""
import json
import math

import pytest
from robot_vision.manual import (
    AXES,
    CommandError,
    ManualConfig,
    ManualController,
    parse_command,
)
from robot_vision.platform import HOME, PlatformLimits, SimPlatform
from robot_vision.servo import Command

NAN, INF = float('nan'), float('inf')
CFG = ManualConfig()
LIM = PlatformLimits()


def refused(cmd, code=None):
    with pytest.raises(CommandError) as exc:
        parse_command(cmd if isinstance(cmd, str) else json.dumps(cmd), CFG)
    if code:
        assert exc.value.code == code, str(exc.value)
    return exc.value


# ---- parsing / validation --------------------------------------------------------------------
def test_valid_commands_parse():
    g = parse_command('{"op":"goto","pan":0.3,"tilt":0.2,"slide":1.0,"speed":0.5}')
    assert g == {'op': 'goto', 'target': {'pan': 0.3, 'tilt': 0.2, 'slide': 1.0}, 'speed': 0.5}
    assert parse_command('{"op":"goto","pan":0}')['target'] == {'pan': 0.0}
    j = parse_command('{"op":"jog","tilt_rate":-0.2}')
    assert j == {'op': 'jog', 'rates': {'tilt': -0.2}, 'ttl_s': CFG.jog_ttl_s}
    assert parse_command('{"op":"stop"}') == {'op': 'stop'}
    assert parse_command({'op': 'stop'}) == {'op': 'stop'}


@pytest.mark.parametrize('text', ['{"op":"goto","pan":NaN}', '{"op":"goto","pan":Infinity}',
                                  '{"op":"goto","pan":-Infinity}', '{"op":"jog","pan_rate":NaN}',
                                  '{"op":"jog","tilt_rate":Infinity}'])
def test_nan_and_infinity_literals_are_refused(text):
    refused(text, 'non_finite')


@pytest.mark.parametrize('cmd', [{'op': 'goto', 'pan': NAN}, {'op': 'goto', 'slide': INF},
                                 {'op': 'jog', 'pan_rate': -INF},
                                 {'op': 'goto', 'pan': 0.1, 'speed': NAN},
                                 {'op': 'jog', 'pan_rate': 0.1, 'ttl_s': INF}])
def test_non_finite_python_values_are_refused_too(cmd):
    refused(cmd, 'non_finite')      # dict input (e.g. from rclpy parameters), not just JSON text


@pytest.mark.parametrize('cmd', [
    {'op': 'goto', 'pan': 1.21}, {'op': 'goto', 'pan': -1.21}, {'op': 'goto', 'slide': -0.01},
    {'op': 'goto', 'slide': 2.51}, {'op': 'goto', 'tilt': 1.21}, {'op': 'goto', 'tilt': -0.61},
    {'op': 'goto', 'pan': 0.0, 'tilt': 5.0}])      # one bad axis refuses the whole command
def test_targets_outside_the_limits_are_refused_not_clamped(cmd):
    refused(cmd, 'out_of_limits')


def test_limits_themselves_are_allowed():
    for a, (lo, hi) in zip(AXES, (LIM.yaw, LIM.slide, LIM.pitch)):
        assert parse_command({'op': 'goto', a: lo})['target'][a] == lo
        assert parse_command({'op': 'goto', a: hi})['target'][a] == hi


def test_jog_rates_above_the_manual_cap_are_refused():
    caps = CFG.caps()
    assert caps == (0.75, 0.4, 0.5)
    parse_command({'op': 'jog', 'pan_rate': caps[0], 'slide_rate': -caps[1], 'tilt_rate': caps[2]})
    refused({'op': 'jog', 'pan_rate': 0.76}, 'rate_too_high')
    refused({'op': 'jog', 'slide_rate': -0.41}, 'rate_too_high')
    refused({'op': 'jog', 'tilt_rate': 1.0}, 'rate_too_high')     # the platform's own limit is 1.0


@pytest.mark.parametrize('cmd', [
    'not json', '[]', '"goto"', '{}', '{"op":"fly"}', '{"op":"goto"}', '{"op":"jog"}',
    '{"op":"goto","pan":"0.1"}', '{"op":"goto","pan":true}', '{"op":"goto","pan":null}',
    '{"op":"goto","pan":[0.1]}', '{"op":"goto","yaw":0.1}', '{"op":"goto","pan":0.1,"x":1}',
    '{"op":"stop","pan":0.1}', '{"op":"jog","pan_rate":0.1,"ttl_s":0}',
    '{"op":"jog","pan_rate":0.1,"ttl_s":2.1}', '{"op":"jog","pan_rate":0.1,"ttl_s":-1}',
    '{"op":"goto","pan":0.1,"speed":0}', '{"op":"goto","pan":0.1,"speed":1.5}',
    '{"op":"jog","pan":0.1}'])
def test_malformed_commands_are_refused(cmd):
    refused(cmd)


# ---- closed loop ---------------------------------------------------------------------------
def run(ctrl, plat, seconds, dt=0.05, t0=0.0, on_step=None):
    """Drive plat with the controller; returns (log of q, ticks)."""
    t, log, ticks = t0, [], []
    for _ in range(int(seconds / dt)):
        tick = ctrl.tick(plat.q, t)
        ticks.append(tick)
        plat.step(Command(yaw_rate=tick.rates[0], slide_rate=tick.rates[1],
                          pitch_rate=tick.rates[2]), dt)
        log.append((t, plat.q, plat.rates))
        if on_step:
            on_step(t, plat, tick)
        t += dt
    return log, ticks


def make(cmd, q=HOME):
    ctrl, plat = ManualController(CFG), SimPlatform(q, LIM)
    ctrl.start(parse_command(cmd, CFG), 0.0)
    return ctrl, plat


def test_goto_reaches_the_target_and_stops_by_itself():
    ctrl, plat = make({'op': 'goto', 'pan': 0.6, 'tilt': 0.5, 'slide': 1.0})
    log, ticks = run(ctrl, plat, 12.0)
    q = plat.q
    assert abs(q[0] - 0.6) < 0.02 and abs(q[1] - 1.0) < 0.02 and abs(q[2] - 0.5) < 0.02
    assert any(t.done == 'reached' for t in ticks) and not ctrl.active
    assert max(abs(v) for v in plat._v) < 1e-6          # at rest after the end


def test_goto_never_exceeds_the_manual_rate_cap_or_the_joint_limits():
    ctrl, plat = make({'op': 'goto', 'pan': -1.2, 'tilt': -0.6, 'slide': 2.5})
    log, _ = run(ctrl, plat, 40.0)
    caps = CFG.caps()
    for _, q, rates in log:
        assert abs(rates.yaw_rate) <= caps[0] + 1e-9
        assert abs(rates.slide_rate) <= caps[1] + 1e-9
        assert abs(rates.pitch_rate) <= caps[2] + 1e-9
        assert LIM.yaw[0] <= q[0] <= LIM.yaw[1] and LIM.slide[0] <= q[1] <= LIM.slide[1]
        assert LIM.pitch[0] <= q[2] <= LIM.pitch[1]
    assert abs(plat.q[0] + 1.2) < 0.02 and abs(plat.q[1] - 2.5) < 0.02    # limits are reachable


def test_goto_speed_fraction_slows_the_motion():
    fast = make({'op': 'goto', 'pan': 1.0, 'speed': 1.0})
    slow = make({'op': 'goto', 'pan': 1.0, 'speed': 0.25})
    lf, _ = run(*fast, 3.0)
    ls, _ = run(*slow, 3.0)
    assert max(abs(r.yaw_rate) for _, _, r in ls) < 0.5 * max(abs(r.yaw_rate) for _, _, r in lf)


def test_commanded_rates_have_no_steps():
    ctrl, plat = make({'op': 'goto', 'pan': 1.0})
    _, ticks = run(ctrl, plat, 3.0)
    step = CFG.accel_scale * CFG.caps()[0] * 0.05
    rates = [t.rates[0] for t in ticks]
    assert all(abs(b - a) <= step + 1e-9 for a, b in zip(rates, rates[1:]))


def test_axes_not_named_are_left_alone():
    ctrl, plat = make({'op': 'goto', 'pan': 0.5})
    run(ctrl, plat, 8.0)
    assert plat.q[1] == HOME[1] and plat.q[2] == HOME[2]


def test_jog_moves_while_refreshed_and_stops_when_it_expires():
    ctrl, plat = make({'op': 'jog', 'pan_rate': 0.4, 'ttl_s': 0.5})
    ticks = []

    def refresh(t, p, tick):
        ticks.append(tick)
        if t < 1.0:
            ctrl.refresh_jog(t)
    run(ctrl, plat, 4.0, on_step=refresh)
    assert plat.q[0] > 0.2                              # it moved
    assert any(t.done == 'jog_expired' for t in ticks) and not ctrl.active
    q_after = plat.q[0]
    run(ctrl, plat, 1.0, t0=10.0)
    assert plat.q[0] == q_after


def test_jog_without_refresh_covers_at_most_ttl_worth_of_motion():
    ctrl, plat = make({'op': 'jog', 'pan_rate': 0.5, 'ttl_s': 0.5})
    run(ctrl, plat, 3.0)
    assert plat.q[0] < 0.5 * 0.5 + 0.1


def test_jog_toward_a_limit_stops_at_the_limit():
    ctrl, plat = make({'op': 'jog', 'tilt_rate': 0.5, 'ttl_s': 2.0}, q=(0.0, 0.0, 1.1))
    for_long = lambda t, p, tick: ctrl.refresh_jog(t)    # noqa: E731 - keep the jog alive
    log, _ = run(ctrl, plat, 10.0, on_step=for_long)
    assert max(q[2] for _, q, _ in log) <= LIM.pitch[1] + 1e-9
    assert plat.q[2] > 1.1


def test_braking_zone_slows_the_approach_to_a_limit():
    ctrl, plat = make({'op': 'jog', 'pan_rate': 0.7, 'ttl_s': 2.0}, q=(1.05, 0.0, 0.1))
    log, _ = run(ctrl, plat, 3.0, on_step=lambda t, p, tick: ctrl.refresh_jog(t))
    last = [r.yaw_rate for _, _, r in log[-5:]]
    assert max(last) < 0.5 * 0.7


def test_stop_is_immediate_even_from_full_speed():
    ctrl, plat = make({'op': 'goto', 'slide': 2.0})
    run(ctrl, plat, 2.0)
    assert plat._v[1] > 0.1
    ctrl.stop()
    tick = ctrl.tick(plat.q, 2.0)
    assert tick.rates == (0.0, 0.0, 0.0) and tick.done == 'stopped' and not ctrl.active


def test_missing_joint_state_stops_the_motion():
    ctrl, plat = make({'op': 'goto', 'pan': 1.0})
    run(ctrl, plat, 1.0)
    tick = ctrl.tick(None, 1.0)
    assert tick.done == 'no_state' and tick.rates == (0.0, 0.0, 0.0) and not ctrl.active
    ctrl2, _ = make({'op': 'goto', 'pan': 1.0})
    assert ctrl2.tick((NAN, 0.0, 0.0), 0.0).done == 'no_state'


def test_goto_times_out():
    cfg = ManualConfig(goto_timeout_s=1.0)
    ctrl = ManualController(cfg)
    ctrl.start(parse_command({'op': 'goto', 'pan': 1.0}, cfg), 0.0)
    plat = SimPlatform(HOME, LIM)
    _, ticks = run(ctrl, plat, 3.0)
    assert any(t.done == 'timeout' for t in ticks) and not ctrl.active
    assert 0.0 < plat.q[0] < 1.0


def test_idle_controller_publishes_nothing_and_cancel_is_silent():
    ctrl = ManualController(CFG)
    assert ctrl.tick(HOME, 0.0).active is False
    ctrl.start(parse_command({'op': 'goto', 'pan': 1.0}, CFG), 0.0)
    ctrl.cancel()
    assert ctrl.tick(HOME, 0.1).active is False


def test_a_new_command_replaces_the_running_one_without_a_jump():
    ctrl, plat = make({'op': 'goto', 'pan': 1.0})
    run(ctrl, plat, 1.0)
    before = ctrl._v[0]
    ctrl.start(parse_command({'op': 'goto', 'pan': -1.0}, CFG), 1.0)
    t = ctrl.tick(plat.q, 1.0)
    assert abs(t.rates[0] - before) <= CFG.accel_scale * CFG.caps()[0] * 0.05 + 1e-9
    assert math.isfinite(t.rates[0])


# ---- the session (what the ROS node does) -----------------------------------------------------
from robot_vision.manual import ManualSession  # noqa: E402

Q0 = (0.0, 0.0, 0.1)


def session():
    return ManualSession(CFG)


def test_session_accepts_a_good_command_and_replies():
    s = session()
    r = s.handle('{"op":"goto","pan":0.3}', 0.0, Q0, busy=False)
    assert r['accepted'] and r['op'] == 'goto' and r['code'] is None and s.mode == 'goto'
    assert s.tick(Q0, 0.05, False) is not None


@pytest.mark.parametrize('text,code', [
    ('{"op":"goto","pan":NaN}', 'non_finite'), ('{"op":"goto","pan":9}', 'out_of_limits'),
    ('{"op":"jog","pan_rate":3}', 'rate_too_high'), ('nonsense', 'bad_json')])
def test_session_refusals_move_nothing(text, code):
    s = session()
    r = s.handle(text, 0.0, Q0, busy=False)
    assert not r['accepted'] and r['code'] == code
    assert s.tick(Q0, 0.05, False) is None and s.mode == 'idle'


def test_a_refused_command_leaves_the_running_motion_alone():
    s = session()
    s.handle('{"op":"goto","pan":0.8}', 0.0, Q0, False)
    assert s.handle('{"op":"goto","pan":NaN}', 0.1, Q0, False)['code'] == 'non_finite'
    assert s.ctrl.active and s.mode == 'goto'


def test_session_refuses_while_tracking_owns_the_joints():
    s = session()
    r = s.handle('{"op":"goto","pan":0.3}', 0.0, Q0, busy=True)
    assert not r['accepted'] and r['code'] == 'busy'
    assert s.handle('{"op":"stop"}', 0.0, Q0, busy=True)['code'] == 'busy'
    assert s.tick(Q0, 0.1, True) is None


def test_tracking_taking_over_drops_a_manual_motion_without_publishing():
    s = session()
    s.handle('{"op":"jog","pan_rate":0.3}', 0.0, Q0, False)
    assert s.tick(Q0, 0.05, False) is not None
    assert s.tick(Q0, 0.1, True) is None            # nothing published: a zero would fight tracking
    assert not s.ctrl.active and s.mode == 'idle' and s.last['code'] == 'busy'
    assert s.tick(Q0, 0.15, False) is None


def test_session_refuses_without_a_fresh_joint_state():
    s = session()
    assert s.handle('{"op":"goto","pan":0.3}', 0.0, None, False)['code'] == 'no_state'
    assert s.handle('{"op":"goto","pan":0.3}', 0.0, (NAN, 0, 0), False)['code'] == 'no_state'


def test_a_finished_motion_publishes_one_final_zero_then_nothing():
    s = session()
    s.handle('{"op":"goto","pan":0.005}', 0.0, Q0, False)     # already within tolerance
    assert s.tick(Q0, 0.05, False) == (0.0, 0.0, 0.0)
    assert s.last['done'] == 'reached' and s.mode == 'idle'
    assert s.tick(Q0, 0.1, False) is None


def test_stop_publishes_zero_at_once():
    s = session()
    s.handle('{"op":"jog","slide_rate":0.3,"ttl_s":2}', 0.0, Q0, False)
    s.tick(Q0, 0.05, False)
    s.tick(Q0, 0.10, False)
    assert s.handle('{"op":"stop"}', 0.11, Q0, False)['accepted']
    assert s.tick(Q0, 0.15, False) == (0.0, 0.0, 0.0)
    assert s.tick(Q0, 0.20, False) is None
