"""Rendering for the browser view (pure numpy + zlib, no ROS, no OpenCV, no PIL).

- :func:`depth_to_rgb`   depth image -> colour map (near = bright, far = dark, no data = grey)
- :func:`project_points` / :func:`box_to_pixels`   world points / boxes -> pixels (same pixel
  convention as :func:`robot_core.workspace.project`: pixel *index*, centre at +0.5)
- :func:`draw_overlay`   detected objects as labelled boxes on top of the RGB image
- :func:`encode_png` / :func:`decode_png`   dependency-free PNG (8 bit, gray / RGB / RGBA)

Everything works on copies; inputs are never modified.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from robot_core.perception import Intrinsics, Transform

Vec = Tuple[float, float, float]
Color = Tuple[int, int, int]
Rect = Tuple[int, int, int, int]      # u0, v0, u1, v1 (inclusive pixel indices)

# Outline colour of the overlay: exact value, so tests (and people) can find it again.
OVERLAY_COLOR: Color = (255, 255, 255)
LABEL_BACKGROUND: Color = (0, 0, 0)
NO_DATA_COLOR: Color = (48, 48, 48)

# viridis, reversed: near = bright yellow, far = dark purple.
_STOPS = np.array([(253, 231, 37), (94, 201, 98), (33, 145, 140), (59, 82, 139), (68, 1, 84)],
                  dtype=np.float32)


# ---- depth colour map --------------------------------------------------------------------------
def depth_to_rgb(depth: np.ndarray, near: float = 0.5, far: float = 1.7) -> np.ndarray:
    """(H, W) z-depth in metres -> (H, W, 3) uint8. Values outside [near, far] are clamped;
    NaN / inf / non-positive (no measurement) become :data:`NO_DATA_COLOR`."""
    if not far > near:
        raise ValueError('far must be larger than near')
    d = np.asarray(depth, dtype=np.float32)
    valid = np.isfinite(d) & (d > 0)
    t = np.clip((np.where(valid, d, near) - near) / (far - near), 0.0, 1.0)
    pos = t * (len(_STOPS) - 1)
    lo = np.minimum(pos.astype(np.int32), len(_STOPS) - 2)
    frac = (pos - lo)[..., None]
    rgb = _STOPS[lo] * (1.0 - frac) + _STOPS[lo + 1] * frac
    out = np.rint(rgb).astype(np.uint8)
    out[~valid] = NO_DATA_COLOR
    return out


# ---- projection --------------------------------------------------------------------------------
def project_points(points: np.ndarray, intr: Intrinsics,
                   world_from_optical: Transform) -> Tuple[np.ndarray, np.ndarray]:
    """World points (N, 3) -> (uv (N, 2) pixel indices, z-depth (N,)). Points behind the camera
    get a non-positive depth and a meaningless uv."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    local = (pts - world_from_optical.translation) @ world_from_optical.rotation  # R^T (p - t)
    z = local[:, 2]
    safe = np.where(z > 1e-9, z, 1.0)
    u = intr.fx * local[:, 0] / safe + intr.cx - 0.5
    v = intr.fy * local[:, 1] / safe + intr.cy - 0.5
    return np.stack([u, v], axis=1), z


@dataclass(frozen=True)
class PixelBox:
    """A world-aligned box as seen by the camera."""
    center: Tuple[float, float]               # projection of the box centre (u, v)
    bbox: Tuple[float, float, float, float]   # u0, v0, u1, v1: hull of the 8 projected corners

    @property
    def bbox_center(self) -> Tuple[float, float]:
        return ((self.bbox[0] + self.bbox[2]) / 2.0, (self.bbox[1] + self.bbox[3]) / 2.0)


def box_to_pixels(position: Sequence[float], size: Sequence[float], intr: Intrinsics,
                  world_from_optical: Transform, min_depth: float = 0.05) -> Optional[PixelBox]:
    """Project an axis-aligned world box. None if any part of it is closer than ``min_depth``
    (behind or on top of the camera), where the projection is meaningless."""
    c = np.asarray(position, dtype=np.float64)
    half = np.asarray(size, dtype=np.float64) / 2.0
    signs = np.array([(sx, sy, sz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    corners = c + signs * half
    uv, z = project_points(np.vstack([c, corners]), intr, world_from_optical)
    if np.any(z < min_depth):
        return None
    hull = uv[1:]
    return PixelBox(center=(float(uv[0, 0]), float(uv[0, 1])),
                    bbox=(float(hull[:, 0].min()), float(hull[:, 1].min()),
                          float(hull[:, 0].max()), float(hull[:, 1].max())))


# ---- drawing -----------------------------------------------------------------------------------
# 5x7 bitmap font: upper case, digits and a few symbols. Lower case is drawn as upper case.
_FONT_ROWS = {
    'A': '.###. #...# #...# ##### #...# #...# #...#',
    'B': '####. #...# #...# ####. #...# #...# ####.',
    'C': '.###. #...# #.... #.... #.... #...# .###.',
    'D': '####. #...# #...# #...# #...# #...# ####.',
    'E': '##### #.... #.... ####. #.... #.... #####',
    'F': '##### #.... #.... ####. #.... #.... #....',
    'G': '.###. #...# #.... #.### #...# #...# .####',
    'H': '#...# #...# #...# ##### #...# #...# #...#',
    'I': '##### ..#.. ..#.. ..#.. ..#.. ..#.. #####',
    'J': '..### ...#. ...#. ...#. ...#. #..#. .##..',
    'K': '#...# #..#. #.#.. ##... #.#.. #..#. #...#',
    'L': '#.... #.... #.... #.... #.... #.... #####',
    'M': '#...# ##.## #.#.# #.#.# #...# #...# #...#',
    'N': '#...# ##..# #.#.# #..## #...# #...# #...#',
    'O': '.###. #...# #...# #...# #...# #...# .###.',
    'P': '####. #...# #...# ####. #.... #.... #....',
    'Q': '.###. #...# #...# #...# #.#.# #..#. .##.#',
    'R': '####. #...# #...# ####. #.#.. #..#. #...#',
    'S': '.#### #.... #.... .###. ....# ....# ####.',
    'T': '##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..',
    'U': '#...# #...# #...# #...# #...# #...# .###.',
    'V': '#...# #...# #...# #...# #...# .#.#. ..#..',
    'W': '#...# #...# #...# #.#.# #.#.# ##.## #...#',
    'X': '#...# #...# .#.#. ..#.. .#.#. #...# #...#',
    'Y': '#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..',
    'Z': '##### ....# ...#. ..#.. .#... #.... #####',
    '0': '.###. #...# #..## #.#.# ##..# #...# .###.',
    '1': '..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.',
    '2': '.###. #...# ....# ...#. ..#.. .#... #####',
    '3': '####. ....# ....# .###. ....# ....# ####.',
    '4': '...#. ..##. .#.#. #..#. ##### ...#. ...#.',
    '5': '##### #.... ####. ....# ....# #...# .###.',
    '6': '..##. .#... #.... ####. #...# #...# .###.',
    '7': '##### ....# ...#. ..#.. .#... .#... .#...',
    '8': '.###. #...# #...# .###. #...# #...# .###.',
    '9': '.###. #...# #...# .#### ....# ...#. .##..',
    '_': '..... ..... ..... ..... ..... ..... #####',
    '#': '.#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.',
    '.': '..... ..... ..... ..... ..... .##.. .##..',
    ',': '..... ..... ..... ..... .##.. .##.. .#...',
    '-': '..... ..... ..... ##### ..... ..... .....',
    '+': '..... ..#.. ..#.. ##### ..#.. ..#.. .....',
    ':': '..... .##.. .##.. ..... .##.. .##.. .....',
    '(': '...#. ..#.. .#... .#... .#... ..#.. ...#.',
    ')': '.#... ..#.. ...#. ...#. ...#. ..#.. .#...',
    '/': '....# ....# ...#. ..#.. .#... #.... #....',
    ' ': '..... ..... ..... ..... ..... ..... .....',
}
GLYPH_W, GLYPH_H, GLYPH_GAP = 5, 7, 1


_GLYPHS: Dict[str, np.ndarray] = {
    ch: np.array([[c == '#' for c in row] for row in rows.split()], dtype=bool)
    for ch, rows in _FONT_ROWS.items()}


def text_size(text: str, scale: int = 1) -> Tuple[int, int]:
    """(width, height) in pixels of ``text`` drawn by :func:`draw_text`."""
    if not text:
        return (0, 0)
    return ((GLYPH_W + GLYPH_GAP) * len(text) * scale - GLYPH_GAP * scale, GLYPH_H * scale)


def draw_rect(img: np.ndarray, u0: int, v0: int, u1: int, v1: int, color: Color,
              thickness: int = 1) -> None:
    """Rectangle outline (inclusive corners), clipped to the image, drawn in place."""
    h, w = img.shape[:2]
    t = max(1, thickness)
    for a, b in ((v0, v0 + t), (v1 - t + 1, v1 + 1)):          # top, bottom
        img[max(a, 0):min(b, h), max(u0, 0):min(u1 + 1, w)] = color
    for a, b in ((u0, u0 + t), (u1 - t + 1, u1 + 1)):          # left, right
        img[max(v0, 0):min(v1 + 1, h), max(a, 0):min(b, w)] = color


def draw_cross(img: np.ndarray, u: int, v: int, color: Color, arm: int = 2) -> None:
    """Plus sign centred on pixel (u, v), clipped to the image, drawn in place."""
    h, w = img.shape[:2]
    if 0 <= v < h:
        img[v, max(u - arm, 0):min(u + arm + 1, w)] = color
    if 0 <= u < w:
        img[max(v - arm, 0):min(v + arm + 1, h), u] = color


def draw_text(img: np.ndarray, u: int, v: int, text: str, color: Color = OVERLAY_COLOR,
              background: Optional[Color] = LABEL_BACKGROUND, scale: int = 1) -> None:
    """Text with its top-left corner at (u, v), drawn in place and clipped to the image."""
    if not text:
        return
    h, w = img.shape[:2]
    width, height = text_size(text, scale)
    if background is not None:
        img[max(v - 1, 0):min(v + height + 1, h), max(u - 1, 0):min(u + width + 1, w)] = background
    for i, ch in enumerate(text):
        glyph = np.kron(_GLYPHS.get(ch.upper(), _GLYPHS['-']), np.ones((scale, scale), dtype=bool))
        gu = u + i * (GLYPH_W + GLYPH_GAP) * scale
        gh, gw = glyph.shape
        u_lo, v_lo = max(gu, 0), max(v, 0)
        u_hi, v_hi = min(gu + gw, w), min(v + gh, h)
        if u_hi <= u_lo or v_hi <= v_lo:
            continue
        sub = glyph[v_lo - v:v_hi - v, u_lo - gu:u_hi - gu]
        img[v_lo:v_hi, u_lo:u_hi][sub] = color


@dataclass(frozen=True)
class OverlayItem:
    """One object to draw: its label text and world-frame box."""
    text: str
    position: Vec
    size: Vec


def _intersects(a: Rect, b: Rect) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _place_label(rect: Rect, obstacles: List[Rect], size: Tuple[int, int],
                 image_width: int) -> Tuple[int, int]:
    """Top-left of the label for the box ``rect``: above it if that spot is free of other boxes and
    earlier labels, else below it, else above anyway. Kept inside the image horizontally."""
    w, h = size
    u = int(min(max(rect[0], 1), max(image_width - w - 1, 1)))
    above, below = (u, rect[1] - h - 3), (u, rect[3] + 3)
    for cand in (above, below):
        label = (cand[0] - 1, cand[1] - 1, cand[0] + w, cand[1] + h)
        if cand[1] >= 1 and not any(_intersects(label, o) for o in obstacles):
            return cand
    return above if above[1] >= 1 else below


def draw_overlay(rgb: np.ndarray, items: Sequence[OverlayItem], intr: Intrinsics,
                 world_from_optical: Transform) -> Tuple[np.ndarray, List[Optional[PixelBox]]]:
    """Copy of ``rgb`` with each item's projected box outline, a centre cross and its label.

    Returns (image, boxes); ``boxes[i]`` is the :class:`PixelBox` of ``items[i]`` or None if it
    could not be projected (then nothing is drawn for it). Labels avoid covering other boxes and
    each other when there is room.
    """
    out = np.array(rgb, dtype=np.uint8, copy=True)
    boxes = [box_to_pixels(i.position, i.size, intr, world_from_optical) for i in items]
    rects: List[Optional[Rect]] = [
        None if b is None else tuple(int(round(x)) for x in b.bbox) for b in boxes]
    placed: List[Rect] = []
    for i, (item, box, rect) in enumerate(zip(items, boxes, rects)):
        if box is None:
            continue
        draw_rect(out, *rect, OVERLAY_COLOR)
        draw_cross(out, int(round(box.center[0])), int(round(box.center[1])), OVERLAY_COLOR)
        others = [r for j, r in enumerate(rects) if j != i and r is not None]
        size = text_size(item.text)
        u, v = _place_label(rect, others + placed, size, out.shape[1])
        draw_text(out, u, v, item.text)
        placed.append((u - 1, v - 1, u + size[0], v + size[1]))
    return out, boxes


# ---- PNG ---------------------------------------------------------------------------------------
_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_COLOR_TYPE = {2: 0, 3: 2, 4: 6}     # number of channels (ndim==2: gray) -> PNG colour type
_CHANNELS = {0: 1, 2: 3, 6: 4}


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))


def encode_png(img: np.ndarray, level: int = 1) -> bytes:
    """uint8 (H, W) gray, (H, W, 3) RGB or (H, W, 4) RGBA -> PNG bytes (filter 0, one IDAT)."""
    a = np.ascontiguousarray(img)
    if a.dtype != np.uint8:
        raise ValueError('encode_png needs a uint8 array')
    if a.ndim == 2:
        color_type, channels = 0, 1
    elif a.ndim == 3 and a.shape[2] in (3, 4):
        channels = a.shape[2]
        color_type = 2 if channels == 3 else 6
    else:
        raise ValueError(f'unsupported image shape {a.shape}')
    h, w = a.shape[:2]
    if h == 0 or w == 0:
        raise ValueError('empty image')
    rows = a.reshape(h, w * channels)
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), rows], axis=1).tobytes()
    header = struct.pack('>IIBBBBB', w, h, 8, color_type, 0, 0, 0)
    return (_PNG_SIGNATURE + _chunk(b'IHDR', header)
            + _chunk(b'IDAT', zlib.compress(raw, level)) + _chunk(b'IEND', b''))


def decode_png(data: bytes) -> np.ndarray:
    """PNG bytes -> uint8 (H, W) or (H, W, C). Supports 8-bit gray / RGB / RGBA, not interlaced
    (enough for what :func:`encode_png` writes and for tests); raises ValueError otherwise."""
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError('not a PNG')
    pos, header, idat = len(_PNG_SIGNATURE), None, bytearray()
    while pos + 8 <= len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack('>I', data[pos + 8 + length:pos + 12 + length])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValueError(f'bad CRC in {kind!r} chunk')
        pos += 12 + length
        if kind == b'IHDR':
            header = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            idat += body
        elif kind == b'IEND':
            break
    if header is None:
        raise ValueError('IHDR missing')
    w, h, depth, color_type, _, _, interlace = header
    if depth != 8 or color_type not in _CHANNELS or interlace != 0:
        raise ValueError(f'unsupported PNG (depth={depth}, colour type={color_type}, '
                         f'interlace={interlace})')
    channels = _CHANNELS[color_type]
    stride = w * channels
    raw = zlib.decompress(bytes(idat))
    if len(raw) != h * (stride + 1):
        raise ValueError('PNG data has the wrong length')
    out = np.zeros((h, stride), dtype=np.uint8)
    prev = np.zeros(stride, dtype=np.int32)
    for y in range(h):
        base = y * (stride + 1)
        ftype = raw[base]
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=base + 1).astype(np.int32)
        if ftype == 0:
            cur = line
        elif ftype == 2:                                   # Up
            cur = (line + prev) & 0xFF
        elif ftype in (1, 3, 4):                           # Sub / Average / Paeth: sequential
            cur = line.copy()
            for i in range(stride):
                a = cur[i - channels] if i >= channels else 0
                b = prev[i]
                if ftype == 1:
                    pred = a
                elif ftype == 3:
                    pred = (a + b) // 2
                else:
                    c = prev[i - channels] if i >= channels else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[i] = (cur[i] + pred) & 0xFF
        else:
            raise ValueError(f'bad PNG filter type {ftype}')
        out[y] = cur
        prev = cur.astype(np.int32)
    return out.reshape(h, w) if channels == 1 else out.reshape(h, w, channels)
