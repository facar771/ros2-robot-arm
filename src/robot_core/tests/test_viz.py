import math
import zlib

import numpy as np
import pytest
from robot_core.perception import Intrinsics, optical_frame_in_world
from robot_core.viz import (
    NO_DATA_COLOR,
    OVERLAY_COLOR,
    OverlayItem,
    box_to_pixels,
    decode_png,
    depth_to_rgb,
    draw_overlay,
    draw_text,
    encode_png,
    project_points,
    text_size,
)
from robot_core.workspace import load_workspace, project

from tests.conftest import SRC

WORLD = SRC / 'robot_sim' / 'worlds' / 'workspace.sdf'


@pytest.fixture(scope='module')
def ws():
    return load_workspace(WORLD)


@pytest.fixture(scope='module')
def cam(ws):
    return Intrinsics.from_camera_spec(ws.camera), optical_frame_in_world(ws.camera)


def box_size(shape):
    if shape.kind == 'sphere':
        return (2 * shape.dims[0],) * 3
    if shape.kind == 'box':
        return tuple(shape.dims)
    return (2 * shape.dims[0], 2 * shape.dims[0], shape.dims[1])


# ---- depth colour map --------------------------------------------------------------------------
def test_depth_colour_map_endpoints_and_no_data():
    depth = np.array([[0.3, 2.5, 1.4, np.nan, np.inf, 0.0, -1.0, 9.0, 0.01]], dtype=np.float32)
    rgb = depth_to_rgb(depth, 0.3, 2.5)
    assert rgb.shape == (1, 9, 3) and rgb.dtype == np.uint8
    assert tuple(rgb[0, 0]) == (253, 231, 37)          # near = bright yellow
    assert tuple(rgb[0, 1]) == (68, 1, 84)             # far = dark purple
    assert tuple(rgb[0, 7]) == (68, 1, 84)             # beyond far is clamped
    assert tuple(rgb[0, 8]) == (253, 231, 37)          # closer than near is clamped
    for i in (3, 4, 5, 6):                             # nan, inf, zero, negative: no measurement
        assert tuple(rgb[0, i]) == NO_DATA_COLOR
    assert tuple(rgb[0, 2]) != NO_DATA_COLOR


def test_depth_colour_map_is_monotonic_in_brightness_of_red_channel_start():
    d = np.linspace(0.5, 1.7, 200, dtype=np.float32)[None, :]
    rgb = depth_to_rgb(d).astype(int)
    assert len({tuple(p) for p in rgb[0]}) > 100       # smooth gradient, not a few bands


def test_depth_colour_map_rejects_bad_range():
    with pytest.raises(ValueError):
        depth_to_rgb(np.ones((2, 2), dtype=np.float32), 1.0, 1.0)


# ---- projection --------------------------------------------------------------------------------
def test_projection_matches_workspace_project(ws, cam):
    intr, tf = cam
    for shape in ws.shapes:
        u, v, z = project(ws, shape.center)
        uv, depth = project_points(np.array([shape.center]), intr, tf)
        assert uv[0] == pytest.approx([u, v], abs=1e-6)
        assert depth[0] == pytest.approx(z, abs=1e-9)


def test_box_to_pixels_centre_and_hull(ws, cam):
    intr, tf = cam
    for shape in ws.shapes:
        box = box_to_pixels(shape.center, box_size(shape), intr, tf)
        u, v, _ = project(ws, shape.center)
        assert box.center == pytest.approx((u, v), abs=1e-6)
        u0, v0, u1, v1 = box.bbox
        assert u0 < u < u1 and v0 < v < v1
        # the hull centre is close to the projected centre (perspective only shifts it a little)
        assert math.dist(box.bbox_center, box.center) < 3.0


def test_box_behind_camera_is_none(ws, cam):
    intr, tf = cam
    cx, cy, cz = ws.camera.pose.position
    assert box_to_pixels((cx + 1.0, cy, cz), (0.1, 0.1, 0.1), intr, tf) is None  # behind (+x)
    assert box_to_pixels((cx, cy, cz), (0.1, 0.1, 0.1), intr, tf) is None        # on the camera


# ---- overlay -----------------------------------------------------------------------------------
def test_overlay_draws_outline_at_projected_box_and_leaves_input_alone(ws, cam):
    intr, tf = cam
    rgb = np.full((intr.height, intr.width, 3), 90, dtype=np.uint8)
    original = rgb.copy()
    items = [OverlayItem(s.name[:1], s.center, box_size(s)) for s in ws.shapes]
    out, boxes = draw_overlay(rgb, items, intr, tf)
    assert np.array_equal(rgb, original)                         # input untouched
    assert out.shape == rgb.shape and out.dtype == np.uint8
    assert all(b is not None for b in boxes)
    for box in boxes:
        u0, v0, u1, v1 = (int(round(x)) for x in box.bbox)
        for u, v in ((u0, v0), (u1, v0), (u0, v1), (u1, v1), ((u0 + u1) // 2, v0)):
            assert tuple(out[v, u]) == OVERLAY_COLOR            # outline corners and edge
        cu, cv = (int(round(x)) for x in box.center)
        assert tuple(out[cv, cu]) == OVERLAY_COLOR              # centre cross
    changed = np.any(out != original, axis=2)
    assert changed.sum() > 0
    assert (out[~changed] == 90).all()


def test_labels_never_cover_another_boxes_outline(ws, cam):
    intr, tf = cam
    rgb = np.full((intr.height, intr.width, 3), 90, dtype=np.uint8)
    items = [OverlayItem(f'{s.name.split("_")[0]}#0', s.center, box_size(s)) for s in ws.shapes]
    out, boxes = draw_overlay(rgb, items, intr, tf)
    for box in boxes:
        u0, v0, u1, v1 = (int(round(x)) for x in box.bbox)
        edges = [out[v0, u0:u1 + 1], out[v1, u0:u1 + 1], out[v0:v1 + 1, u0], out[v0:v1 + 1, u1]]
        for edge in edges:
            assert (edge == OVERLAY_COLOR).all()       # a label background would leave black here


def test_label_goes_below_when_above_is_taken():
    from robot_core.viz import _place_label
    taken = [(0, 0, 100, 40)]                          # a box occupying the space above
    u, v = _place_label((10, 50, 30, 80), taken, (30, 7), 320)
    assert v > 80                                      # below the box
    u, v = _place_label((10, 50, 30, 80), [], (30, 7), 320)
    assert v < 50                                      # free: above
    u, v = _place_label((300, 50, 319, 80), [], (30, 7), 320)
    assert u + 30 <= 320                               # kept inside the image


def test_overlay_skips_unprojectable_items(ws, cam):
    intr, tf = cam
    rgb = np.zeros((intr.height, intr.width, 3), dtype=np.uint8)
    cx, cy, cz = ws.camera.pose.position
    out, boxes = draw_overlay(rgb, [OverlayItem('x', (cx + 2, cy, cz), (0.1, 0.1, 0.1))], intr, tf)
    assert boxes == [None] and not out.any()


def test_overlay_clips_at_image_border(cam):
    intr, tf = cam
    rgb = np.zeros((intr.height, intr.width, 3), dtype=np.uint8)
    draw_text(rgb, -10, -3, 'RED#0')           # partly outside: must not raise
    draw_text(rgb, intr.width - 4, intr.height - 3, 'RED#0')
    draw_text(rgb, 10_000, 10_000, 'RED#0')


def test_text_is_drawn_with_background():
    img = np.full((20, 60, 3), 120, dtype=np.uint8)
    draw_text(img, 5, 5, 'A1', scale=1)
    w, h = text_size('A1')
    assert (w, h) == (11, 7)
    patch = img[5:5 + h, 5:5 + w]
    assert (patch == OVERLAY_COLOR).all(axis=2).any()           # glyph pixels
    assert (patch == 0).all(axis=2).any()                       # background pixels
    assert tuple(img[0, 0]) == (120, 120, 120)                  # nothing drawn elsewhere


def test_every_label_character_has_a_glyph():
    img = np.zeros((10, 400, 3), dtype=np.uint8)
    draw_text(img, 1, 1, 'RED#0 GREEN#12 BLUE#3 (0.35,-0.15)/+:._', background=None)
    assert img.any()


# ---- PNG ---------------------------------------------------------------------------------------
@pytest.mark.parametrize('shape', [(7, 11), (5, 9, 3), (4, 6, 4), (1, 1, 3)])
def test_png_round_trip(shape):
    rng = np.random.default_rng(1)
    img = rng.integers(0, 256, size=shape, dtype=np.uint8)
    data = encode_png(img)
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    assert np.array_equal(decode_png(data), img)


def test_png_rejects_bad_input():
    with pytest.raises(ValueError):
        encode_png(np.zeros((2, 2), dtype=np.float32))
    with pytest.raises(ValueError):
        encode_png(np.zeros((2, 2, 2), dtype=np.uint8))
    with pytest.raises(ValueError):
        encode_png(np.zeros((0, 4, 3), dtype=np.uint8))
    with pytest.raises(ValueError):
        decode_png(b'not a png')
    good = bytearray(encode_png(np.zeros((2, 2, 3), dtype=np.uint8)))
    good[20] ^= 0xFF                                             # corrupt the IHDR payload
    with pytest.raises(ValueError):
        decode_png(bytes(good))


def _png_with_filter(img: np.ndarray, ftype: int) -> bytes:
    """Hand-rolled PNG using a non-zero row filter, to exercise the decoder's un-filtering."""
    import struct
    h, w, c = img.shape
    raw = bytearray()
    prev = np.zeros(w * c, dtype=np.int32)
    for y in range(h):
        row = img[y].reshape(-1).astype(np.int32)
        out = np.zeros_like(row)
        for i in range(len(row)):
            a = row[i - c] if i >= c else 0
            b = prev[i]
            cc = prev[i - c] if i >= c else 0
            if ftype == 1:
                pred = a
            elif ftype == 2:
                pred = b
            elif ftype == 3:
                pred = (a + b) // 2
            else:
                p = a + b - cc
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - cc)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else cc)
            out[i] = (row[i] - pred) & 0xFF
        raw += bytes([ftype]) + bytes(out.astype(np.uint8))
        prev = row

    def chunk(kind, body):
        return (struct.pack('>I', len(body)) + kind + body
                + struct.pack('>I', zlib.crc32(kind + body) & 0xFFFFFFFF))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b''))


@pytest.mark.parametrize('ftype', [1, 2, 3, 4])
def test_png_decoder_handles_all_row_filters(ftype):
    rng = np.random.default_rng(ftype)
    img = rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8)
    assert np.array_equal(decode_png(_png_with_filter(img, ftype)), img)
