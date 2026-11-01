"""Regression on a REAL Gazebo frame (captured with tools/capture_frame.py, workspace world)."""
import numpy as np
import pytest
from robot_vision.validate import DEFAULT_FRAME, load_frame
from vision_support import shapes_by_name, workspace

from robot_vision import ColorDetector, measure


@pytest.fixture(scope='module')
def measures():
    frame = load_frame(DEFAULT_FRAME, workspace())
    return {s.label: measure(frame, s) for s in ColorDetector().detect(frame)}


def test_real_frame_is_the_workspace_camera_size():
    frame = load_frame(DEFAULT_FRAME, workspace())
    assert frame.rgb.shape == (240, 320, 3) and frame.depth.shape == (240, 320)
    assert np.isfinite(frame.depth).mean() > 0.99


def test_gazebo_render_is_measured_to_the_millimetre_scale(measures):
    shapes = shapes_by_name()
    assert (measures['red'].shape, measures['green'].shape, measures['blue'].shape) == \
        ('box', 'cylinder', 'sphere')
    for label, name in (('red', 'red_box'), ('green', 'green_cylinder'), ('blue', 'blue_ball')):
        err = np.linalg.norm(np.array(measures[label].center) - np.array(shapes[name].center))
        assert err <= 0.005, (label, err)
    assert np.allclose(measures['red'].size, shapes['red_box'].dims, atol=0.005)
    assert abs(measures['green'].radius - 0.03) <= 0.003
    assert abs(measures['green'].height - 0.10) <= 0.003
    assert abs(measures['blue'].radius - 0.04) <= 0.003
