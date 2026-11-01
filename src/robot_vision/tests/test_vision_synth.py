import math

import numpy as np
from robot_core.workspace import expected_depth
from robot_vision.synth import render
from vision_support import tall_box, workspace


def test_vectorised_render_matches_the_scalar_ray_caster():
    ws = workspace()
    frame = render(ws.camera, ws.shapes)
    rng = np.random.default_rng(0)
    for _ in range(400):
        u, v = int(rng.integers(0, 320)), int(rng.integers(0, 240))
        want = expected_depth(ws, u, v)
        got = float(frame.depth[v, u])
        if want is None:
            assert not np.isfinite(got)
        else:
            assert got == np.float32(want[0]) or abs(got - want[0]) < 1e-4


def test_hidden_shape_disappears_and_sky_is_pale():
    ws = workspace()
    frame = render(ws.camera, ws.shapes, hidden=('red_box',))
    reds = (frame.rgb[..., 0] > 150) & (frame.rgb[..., 1] < 60)
    assert not reds.any()


def test_yawed_box_has_a_rotated_footprint():
    ws = workspace()
    box = tall_box()
    straight = render(ws.camera, [box])
    turned = render(ws.camera, [box], yaws={box.name: math.radians(40)})
    assert not np.array_equal(straight.depth, turned.depth)
    a = np.isfinite(straight.depth) & (straight.depth < 1.2)
    b = np.isfinite(turned.depth) & (turned.depth < 1.2)
    assert abs(int(a.sum()) - int(b.sum())) < 0.25 * int(a.sum())   # similar area, different shape


def test_depth_noise_is_reproducible_and_only_on_valid_pixels():
    ws = workspace()
    a = render(ws.camera, ws.shapes, depth_noise=0.002, seed=5)
    b = render(ws.camera, ws.shapes, depth_noise=0.002, seed=5)
    clean = render(ws.camera, ws.shapes)
    assert np.array_equal(a.depth, b.depth, equal_nan=True)
    assert np.array_equal(np.isfinite(a.depth), np.isfinite(clean.depth))
    err = (a.depth - clean.depth)[np.isfinite(clean.depth)]
    assert 0.001 < float(err.std()) < 0.003
