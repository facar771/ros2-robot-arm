import math

import numpy as np
import pytest
from robot_vision.scan import (
    Panner,
    ScanConfig,
    SyntheticPanner,
    ring_world,
    scan,
    sweep_angles,
)
from robot_vision.scan_validate import main as validate_main
from robot_vision.synth import render

from robot_vision import ColorDetector

DEG = math.radians


@pytest.fixture(scope='module')
def ring():
    return ring_world()


def truth_by_name(shapes):
    return {s.name: s for s in shapes}


def test_sweep_angles_cover_the_range_and_respect_limits():
    cfg = ScanConfig(yaw_min=DEG(-90), yaw_max=DEG(90), steps=7)
    assert sweep_angles(cfg, (-math.pi, math.pi)) == pytest.approx(
        [DEG(a) for a in (-90, -60, -30, 0, 30, 60, 90)])
    clipped = sweep_angles(cfg, (DEG(-45), DEG(45)))
    assert clipped[0] == pytest.approx(DEG(-45)) and clipped[-1] == pytest.approx(DEG(45))
    assert sweep_angles(ScanConfig(steps=1), (-1, 1)) == [0.0]
    assert sweep_angles(cfg, (DEG(120), DEG(150))) == []     # the camera cannot reach the range
    assert sweep_angles(ScanConfig(steps=0), (-1, 1)) == []


def test_no_single_view_sees_the_whole_ring_but_the_scan_does(ring):
    cam, shapes = ring
    panner = SyntheticPanner(cam, shapes)
    singles = []
    for yaw in np.radians(np.arange(-90, 91, 30)):
        panner.move_to(float(yaw))
        singles.append(len(ColorDetector().detect(panner.frame())))
    assert max(singles) <= 3 < 6               # one view never sees the whole ring
    result = scan(SyntheticPanner(cam, shapes), ScanConfig(yaw_min=DEG(-100), yaw_max=DEG(100),
                                                           steps=9))
    assert len(result.objects) == 6 and result.views_used == 9


@pytest.mark.parametrize('sigma, steps', [(0.0, 9), (0.002, 9), (0.002, 7)])
def test_scan_matches_the_world_definition(ring, sigma, steps):
    cam, shapes = ring
    result = scan(SyntheticPanner(cam, shapes, depth_noise=sigma),
                  ScanConfig(yaw_min=DEG(-100), yaw_max=DEG(100), steps=steps))
    assert len(result.objects) == len(shapes)
    kind = {'red': 'box', 'green': 'cylinder', 'blue': 'sphere'}
    unused = list(shapes)
    for obj in result.objects:
        near = min((s for s in unused if s.name.split('_')[0] == obj.label),
                   key=lambda s: math.dist(s.center[:2], obj.center[:2]))
        unused.remove(near)
        assert math.dist(near.center, obj.center) < 0.01
        assert obj.measure.shape == kind[obj.label]
        assert abs(obj.measure.height - near.height) < 0.005
        assert not obj.partial and obj.views >= 1
    assert unused == []                                      # every truth object matched once


def test_overlapping_views_are_merged_not_duplicated(ring):
    cam, shapes = ring
    dense = scan(SyntheticPanner(cam, shapes), ScanConfig(yaw_min=DEG(-100), yaw_max=DEG(100),
                                                          steps=21))
    assert len(dense.objects) == 6
    assert max(o.views for o in dense.objects) >= 5          # seen again and again, counted once


def test_an_object_cut_by_the_image_border_does_not_override_a_whole_view(ring):
    cam, shapes = ring
    # two views only, the second shows the blue ball at the very edge of the image
    panner = SyntheticPanner(cam, shapes)
    whole = scan(panner, ScanConfig(yaw_min=0.0, yaw_max=0.0, steps=1))
    ball = next(o for o in whole.objects if o.label == 'blue')
    assert not ball.partial
    edge = scan(SyntheticPanner(cam, shapes), ScanConfig(yaw_min=DEG(32), yaw_max=DEG(32), steps=1))
    cut = [o for o in edge.objects if o.label == 'blue']
    assert cut and cut[0].partial                                  # reported as cut off, not hidden
    both = scan(SyntheticPanner(cam, shapes), ScanConfig(yaw_min=DEG(0), yaw_max=DEG(32), steps=2))
    kept = [o for o in both.objects if o.label == 'blue' and
            math.dist(o.center[:2], ball.center[:2]) < 0.05]
    assert len(kept) == 1 and not kept[0].partial and kept[0].yaw == pytest.approx(0.0)


def test_stop_request_ends_the_sweep_early_and_is_reported(ring):
    cam, shapes = ring
    panner = SyntheticPanner(cam, shapes)
    calls = []
    def stop_after_three():
        calls.append(1)
        return len(calls) > 3

    result = scan(panner, ScanConfig(steps=9), should_stop=stop_after_three)
    assert result.stopped and len(panner.moves) == 3


def test_missing_frames_are_noted_not_fatal(ring):
    class Blind(Panner):
        limits = (-3.0, 3.0)

        def move_to(self, yaw):
            pass

        def frame(self):
            return None

    result = scan(Blind(), ScanConfig(steps=3))
    assert result.objects == [] and result.views_used == 0 and len(result.notes) == 3


def test_empty_scene_gives_an_empty_list(ring):
    cam, _ = ring
    result = scan(SyntheticPanner(cam, []), ScanConfig(steps=3))
    assert result.objects == [] and result.views_used == 3


def test_same_coloured_objects_further_apart_than_merge_distance_stay_separate(ring):
    cam, shapes = ring
    reds = [s for s in shapes if s.name.startswith('red')]
    result = scan(SyntheticPanner(cam, reds), ScanConfig(yaw_min=DEG(-100), yaw_max=DEG(100),
                                                         steps=9))
    assert len([o for o in result.objects if o.label == 'red']) == 2
    close = scan(SyntheticPanner(cam, reds), ScanConfig(yaw_min=DEG(-100), yaw_max=DEG(100),
                                                        steps=9, merge_distance=3.0))
    assert len(close.objects) == 1                               # a huge gate merges everything


def test_scan_validation_report_passes_and_writes_files(tmp_path):
    assert validate_main(['--out', str(tmp_path)]) == 0
    text = (tmp_path / 'vision_v4_tarama.md').read_text(encoding='utf-8')
    assert text.count('✅') >= 18 and '❌' not in text


def test_render_helper_is_unchanged_by_panning(ring):
    cam, shapes = ring
    a = render(cam, shapes)
    panner = SyntheticPanner(cam, shapes)
    panner.move_to(0.0)
    assert np.array_equal(a.depth, panner.frame().depth, equal_nan=True)
