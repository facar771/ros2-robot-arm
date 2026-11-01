import pytest
from robot_core.scene_sync import Observation, SceneSynchronizer

from tests.fakes import FakeScene, RecordingSink

RED = Observation('red', (0.35, 0.15, 0.03), (0.06, 0.06, 0.06))


def at(obs, dx=0.0, dy=0.0):
    x, y, z = obs.position
    return Observation(obs.label, (x + dx, y + dy, z), obs.size)


def make(**kw):
    scene, sink = FakeScene(), RecordingSink()
    return SceneSynchronizer(scene, sink, **kw), scene, sink


def feed(sync, obs_list, t0=0.0, n=3, dt=0.2):
    for k in range(n):
        sync.update(obs_list, t0 + k * dt)
    return t0 + n * dt


def test_object_is_added_only_after_confirmation():
    sync, scene, sink = make(confirm_frames=3)
    sync.update([RED], 0.0)
    sync.update([RED], 0.2)
    assert scene.added == []
    sync.update([RED], 0.4)
    assert [b.name for b in scene.added] == ['obj_red_1']
    box = scene.added[0]
    assert (box.x, box.y, box.z, box.frame) == (0.35, 0.15, 0.03, 'world')
    assert box.size_x == pytest.approx(0.06 + 0.01)    # padding on both sides
    assert sink.events[0][0] == 'obstacle_added' and sink.events[0][1]['name'] == 'obj_red_1'


def test_one_frame_false_detection_never_reaches_the_scene():
    sync, scene, _ = make()
    sync.update([RED], 0.0)
    for k in range(1, 20):
        sync.update([], 0.2 * k)
    assert scene.added == [] and scene.removed == [] and sync.published == {}


def test_a_gap_resets_confirmation():
    sync, scene, _ = make(confirm_frames=3)
    sync.update([RED], 0.0)
    sync.update([RED], 0.2)
    sync.update([], 0.4)
    sync.update([RED], 0.6)
    sync.update([RED], 0.8)
    assert scene.added == []


def test_jitter_below_threshold_does_not_touch_the_scene():
    sync, scene, _ = make()
    t = feed(sync, [RED])
    assert len(scene.added) == 1
    for k in range(30):
        sync.update([at(RED, 0.003 if k % 2 else -0.003)], t + 0.2 * k)
    assert len(scene.added) == 1


def test_real_motion_updates_the_same_object_without_a_new_id():
    sync, scene, _ = make()
    t = feed(sync, [RED])
    for k in range(30):
        sync.update([at(RED, dx=0.05)], t + 0.2 * k)
    assert {b.name for b in scene.added} == {'obj_red_1'}
    assert len(scene.added) >= 2
    assert scene.added[-1].x == pytest.approx(0.40, abs=0.01)
    assert scene.removed == []


def test_removed_after_timeout_and_not_before():
    sync, scene, sink = make(remove_after=2.0)
    t = feed(sync, [RED])
    sync.update([], t + 1.0)
    assert scene.removed == []
    sync.update([], t + 2.5)
    assert scene.removed == ['obj_red_1'] and sync.published == {}
    assert sink.events[-1] == ('obstacle_removed', {'name': 'obj_red_1'})


def test_reappearing_object_gets_a_fresh_id_and_ids_are_never_reused():
    sync, scene, _ = make(remove_after=1.0)
    t = feed(sync, [RED])
    sync.update([], t + 2.0)
    feed(sync, [RED], t0=t + 3.0)
    assert [b.name for b in scene.added] == ['obj_red_1', 'obj_red_2']


def test_two_objects_of_one_colour_keep_separate_identities():
    a, b = RED, at(RED, dy=-0.30)
    sync, scene, _ = make()
    t = feed(sync, [a, b])
    assert {x.name for x in scene.added} == {'obj_red_1', 'obj_red_2'}
    for k in range(10):                        # detection order flips between frames
        sync.update([b, a] if k % 2 else [a, b], t + 0.2 * k)
    assert len(scene.added) == 2 and scene.removed == []


def test_different_colours_never_match_each_other():
    sync, scene, _ = make()
    green = Observation('green', RED.position, RED.size)
    feed(sync, [RED, green])
    assert {b.name for b in scene.added} == {'obj_red_1', 'obj_green_1'}


def test_jump_beyond_gate_is_a_new_object_and_the_old_one_expires():
    sync, scene, _ = make(gate=0.08, remove_after=1.0)
    t = feed(sync, [RED])
    far = at(RED, dx=0.2)
    for k in range(10):
        sync.update([far], t + 0.2 * k)
    assert {b.name for b in scene.added} == {'obj_red_1', 'obj_red_2'}
    assert scene.removed == ['obj_red_1']
    assert set(sync.published) == {'obj_red_2'}


def test_failed_add_is_retried_and_failed_remove_is_retried():
    sync, scene, sink = make(remove_after=1.0)
    scene.ok = False
    t = feed(sync, [RED], n=3)
    assert sync.published == {} and sink.events == []
    scene.ok = True
    sync.update([RED], t)
    assert set(sync.published) == {'obj_red_1'}
    scene.ok = False
    sync.update([], t + 2.0)
    assert set(sync.published) == {'obj_red_1'}      # remove failed: still tracked
    scene.ok = True
    sync.update([], t + 2.2)
    assert sync.published == {} and scene.removed[-1] == 'obj_red_1'


def test_never_touches_objects_it_did_not_add():
    sync, scene, _ = make()
    feed(sync, [RED])
    sync.update([], 10.0)
    assert scene.removed == ['obj_red_1']      # only its own id, e.g. never a hand-made 'box1'
