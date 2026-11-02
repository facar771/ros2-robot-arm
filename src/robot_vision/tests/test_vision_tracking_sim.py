import math

import numpy as np
import pytest
from robot_vision.tracking_sim import (
    SCENARIOS,
    main,
    run_scenario,
    to_markdown,
    verdict,
)
from robot_vision.trajectories import HIDDEN


@pytest.fixture(scope='module')
def results():
    return {name: run_scenario(sc, resolution=0.5) for name, sc in SCENARIOS.items()}


@pytest.mark.parametrize('name', list(SCENARIOS))
def test_scenario_meets_its_thresholds(results, name):
    r = results[name]
    assert verdict(r.metrics, SCENARIOS[name]) == [], r.metrics


def test_straight_line_is_followed_to_within_a_few_pixels(results):
    m = results['linear'].metrics
    assert m.rms_pixel_error < 4.0 and m.rms_distance_error < 0.08
    assert m.loss_events == 0 and m.lock_hold_ratio > 0.95


def test_blackout_is_one_loss_and_the_lock_is_regained(results):
    m = results['blackout'].metrics
    assert m.loss_events == 1
    assert m.relock_time is not None and m.relock_time > 0.0     # the lock really did drop


def test_target_in_the_sine_scenario_really_moves_in_three_axes():
    pts = np.array([SCENARIOS['sine'].position(k * 0.5) for k in range(40)])
    assert all(pts[:, i].max() - pts[:, i].min() > 0.4 for i in range(3))


def test_trajectories_are_deterministic_and_blackout_is_exactly_one_second():
    sc = SCENARIOS['blackout']
    assert sc.position(3.0) == sc.position(3.0)
    gone = [k * 0.1 for k in range(0, 220) if sc.position(k * 0.1) is None]
    assert gone[0] == pytest.approx(9.0) and gone[-1] == pytest.approx(9.9)
    assert HIDDEN[2] < 0


def test_abrupt_change_flips_the_velocity_at_the_turn():
    f = SCENARIOS['abrupt'].position
    before = (f(9.0)[1] - f(8.0)[1])
    after = (f(12.0)[1] - f(11.0)[1])
    assert before == pytest.approx(0.35) and after == pytest.approx(-0.35)


def test_a_bad_controller_is_caught(results):
    from dataclasses import replace

    from robot_vision.servo import ServoConfig
    base = ServoConfig()
    dead = replace(base, yaw=replace(base.yaw, kp=0.0, ki=0.0, kd=0.0), feedforward=0.0)
    m = run_scenario(SCENARIOS['sine'], dead, resolution=0.5).metrics
    assert verdict(m, SCENARIOS['sine']) != []              # the thresholds are not rubber stamps


def test_report_and_cli_write_files(results, tmp_path):
    md = to_markdown(list(results.values()), 'x')
    assert md.count('✅') == 4 and 'linear' in md
    assert main(['--out', str(tmp_path)]) == 0
    assert (tmp_path / 'takip_sim.md').exists()
    assert math.isfinite(results['linear'].metrics.rms_pixel_error)
