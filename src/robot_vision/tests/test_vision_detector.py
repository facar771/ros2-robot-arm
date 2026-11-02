import numpy as np
import pytest
from robot_vision.synth import render
from vision_support import workspace

from robot_vision import ColorDetector, Frame


@pytest.fixture(scope='module')
def frame():
    ws = workspace()
    return render(ws.camera, ws.shapes, depth_noise=0.001)


def test_finds_the_three_world_objects(frame):
    segs = ColorDetector().detect(frame)
    assert sorted(s.label for s in segs) == ['blue', 'green', 'red']
    assert all(300 < s.pixels < 800 for s in segs)


def test_segment_centroids_sit_on_the_projected_objects(frame):
    from robot_core.workspace import project
    ws = workspace()
    truth = {s.name.split('_')[0]: s for s in ws.shapes}
    for seg in ColorDetector().detect(frame):
        s = truth[seg.label]
        u, v, _ = project(ws, s.center)
        cu, cv = seg.centroid
        assert abs(cu - u) < 6 and abs(cv - v) < 8   # centroid of the visible blob, not the centre


def test_empty_scene_and_all_invalid_depth_do_not_raise(frame):
    ws = workspace()
    assert ColorDetector().detect(render(ws.camera, [])) == []
    blind = Frame(frame.rgb, np.full_like(frame.depth, np.nan), frame.intrinsics,
                  frame.world_from_optical)
    assert ColorDetector().detect(blind) == []


def test_specks_below_min_pixels_are_ignored(frame):
    rgb = frame.rgb.copy()
    rgb[10:12, 10:12] = (230, 26, 26)    # 4-pixel red speck on the sky
    speck = Frame(rgb, frame.depth, frame.intrinsics, frame.world_from_optical)
    assert len([s for s in ColorDetector().detect(speck) if s.label == 'red']) == 1


def test_mismatched_shapes_are_rejected(frame):
    with pytest.raises(ValueError):
        Frame(frame.rgb[:100], frame.depth, frame.intrinsics, frame.world_from_optical)
