import numpy as np
import pytest

from robot_vision import Observation, Tracker, TrackerConfig

DT = 0.1


def run(tracker, frames):
    """frames: list of lists of Observations, one list per 0.1 s step."""
    out = []
    for k, obs in enumerate(frames):
        out.append(tracker.update(obs, k * DT))
    return out


def line(x0, v, n, label='red', y=0.0, sigma=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return [[Observation(label, (x0 + v * k * DT + rng.normal(0, sigma), y, 0.03))]
            for k in range(n)]


def test_one_object_keeps_one_id_and_confirms_after_three_hits():
    t = Tracker()
    res = run(t, line(0.4, 0.05, 30))
    assert res[0] == [] and res[1] == []           # tentative tracks are not reported
    assert {tr.id for r in res[2:] for tr in r} == {1}
    assert all(tr.state == 'confirmed' for r in res[2:] for tr in r)


def test_one_frame_false_detection_never_gets_a_reported_track():
    t = Tracker()
    frames = line(0.4, 0.0, 10)
    frames[4] = frames[4] + [Observation('red', (0.9, 0.5, 0.03))]   # appears for one frame only
    res = run(t, frames)
    assert all(len(r) <= 1 for r in res)
    assert {tr.id for r in res for tr in r} == {1}


def test_ids_survive_two_same_colour_objects_that_cross():
    t = Tracker()
    frames = []
    for k in range(60):
        a = Observation('red', (0.30 + 0.20 * k * DT, 0.00, 0.03))
        b = Observation('red', (0.60 - 0.20 * k * DT, 0.06, 0.03))   # passes 6 cm beside a
        frames.append([a, b])
    res = run(t, frames)
    first = {tr.id: tr.position[1] for tr in res[5]}
    last = {tr.id: tr.position[1] for tr in res[-1]}
    for tid, y in first.items():
        assert abs(last[tid] - y) < 0.02          # nobody swapped lanes


def test_labels_never_match_each_other():
    t = Tracker()
    both = [Observation('red', (0.4, 0.0, 0.03)), Observation('blue', (0.4, 0.0, 0.03))]
    res = run(t, [both] * 5)
    assert sorted(tr.label for tr in res[-1]) == ['blue', 'red']
    assert len({tr.id for tr in res[-1]}) == 2


def test_lost_then_found_again_keeps_the_same_id():
    t = Tracker()
    frames = line(0.4, 0.1, 40)
    for k in range(15, 25):
        frames[k] = []                              # 1.0 s without any detection
    res = run(t, frames)
    ids = {tr.id for r in res for tr in r}
    assert ids == {1}
    states = [r[0].state for r in res[3:] if r]
    assert 'lost' in states and states[-1] == 'confirmed'
    lost = [r[0] for r in res if r and r[0].state == 'lost'][-1]
    assert lost.time_since_seen > 0.5
    # while lost it coasted along the learned velocity instead of freezing
    assert lost.position[0] > 0.4 + 0.1 * 1.5 * 0.5


def test_track_dies_after_max_lost_and_ids_are_never_reused():
    cfg = TrackerConfig(max_lost_s=2.0)
    t = Tracker(cfg)
    frames = line(0.4, 0.0, 10) + [[] for _ in range(30)] + line(0.4, 0.0, 10)
    res = run(t, frames)
    assert res[25] != [] and res[25][0].state == 'lost'     # 1.6 s unseen: still coasting
    assert res[32] == []                            # 2.3 s unseen: gone
    assert {tr.id for tr in res[-1]} == {2}         # a re-appearance is a new object


def test_filter_smooths_noise_and_estimates_velocity():
    t = Tracker(TrackerConfig(accel_sigma=0.05))     # a slow, steady target: trust the model more
    sigma = 0.005
    res = run(t, line(0.3, 0.10, 80, sigma=sigma, seed=4))
    truth = np.array([0.3 + 0.10 * k * DT for k in range(80)])
    raw = np.array([f[0].position[0] for f in line(0.3, 0.10, 80, sigma=sigma, seed=4)])
    filt = np.array([r[0].position[0] for r in res[10:]])
    rms_filtered = np.sqrt(np.mean((filt - truth[10:]) ** 2))
    rms_raw = np.sqrt(np.mean((raw[10:] - truth[10:]) ** 2))
    assert rms_filtered < 0.7 * rms_raw
    assert res[-1][0].velocity[0] == pytest.approx(0.10, abs=0.03)


def test_size_is_exponentially_smoothed():
    t = Tracker(TrackerConfig(confirm_hits=1, size_alpha=0.5))
    sizes = [0.06, 0.06, 0.08]
    res = [t.update([Observation('red', (0.4, 0, 0.03), size=(s, s, 0.06))], k * DT)
           for k, s in enumerate(sizes)]
    assert res[-1][0].size[0] == pytest.approx(0.07)


def test_non_increasing_stamps_do_not_blow_up():
    t = Tracker()
    t.update([Observation('red', (0.4, 0, 0.03))], 1.0)
    t.update([Observation('red', (0.4, 0, 0.03))], 1.0)       # same stamp
    out = t.update([Observation('red', (0.4, 0, 0.03))], 0.9)  # clock went backwards
    assert out == [] or np.isfinite(out[0].position[0])


def test_reset_forgets_everything_but_keeps_ids_unique():
    t = Tracker(TrackerConfig(confirm_hits=1))
    t.update([Observation('red', (0.4, 0, 0.03))], 0.0)
    t.reset()
    assert t.tracks() == []
    out = t.update([Observation('red', (0.4, 0, 0.03))], 0.0)
    assert out[0].id == 2


def test_default_tuning_follows_a_sudden_direction_change_within_a_few_frames():
    t = Tracker()
    frames = line(0.4, 0.2, 20) + [[Observation('red', (0.4 + 0.2 * 1.9 - 0.2 * k * DT, 0.0, 0.03))]
                                   for k in range(1, 21)]
    res = run(t, frames)
    truth = frames[-1][0].position[0]
    assert abs(res[-1][0].position[0] - truth) < 0.01
    assert res[-1][0].velocity[0] < -0.1
