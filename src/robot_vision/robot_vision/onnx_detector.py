"""``OnnxDetector``: any ONNX object-detection model behind the :class:`Detector` interface.

You bring the model (and its labels); nothing is downloaded and no weights ship with the repo.
The detector turns the model's *boxes* into :class:`Segment` s: the pixels inside a box whose depth
puts them above the ground plane (``ground_margin``), so the geometry code measures the object and
not the floor around it. Needs ``onnxruntime`` (``pip install onnxruntime``); it is imported
lazily, so the rest of ``robot_vision`` works without it.

Supported output layouts (``decoder=``):
  ``'xyxy'``    ``[N, 6]`` or ``[1, N, 6]``: x1, y1, x2, y2, score, class (model input pixels)
  ``'yolox'``   ``[1, N, 5 + C]``: cx, cy, w, h, objectness + class scores, boxes already decoded
                to input pixels (YOLOX with decoding in the graph); score = objectness * class
  ``'yolov8'``  ``[1, 4 + C, N]`` or ``[1, N, 4 + C]``: cx, cy, w, h + one score per class (this is
                what Ultralytics-style and YOLOX-style exports with decoded boxes produce); NMS is
                applied here. Models with an objectness column or built-in NMS need ``'xyxy'``.
Input: ``[1, 3, H, W]`` float32, RGB, 0-1; the frame is letterboxed (aspect kept, grey padding)
and boxes are mapped back. A fixed ``H, W`` is read from the model; give ``input_size`` for
dynamic ones.

**Licensing:** the code here is Apache-2.0-compatible; the *model* is yours and carries its own
licence. Ultralytics YOLO weights/code are AGPL-3.0 (see ``docs/vision-model-licenses.md``).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from robot_vision.detectors import Detector
from robot_vision.frame import Frame, Segment

PAD_VALUE = 114.0 / 255.0


def letterbox(rgb: np.ndarray,
              size: Tuple[int, int]) -> Tuple[np.ndarray, float, Tuple[float, float]]:
    """Resize ``rgb`` (H, W, 3 uint8) to fit ``size = (H, W)`` keeping the aspect ratio. Returns the
    padded float32 CHW image in [0, 1], the scale, and the (pad_x, pad_y) offset in input pixels."""
    h, w = rgb.shape[:2]
    th, tw = size
    scale = min(th / h, tw / w)
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    ys = np.clip((np.arange(nh) + 0.5) / scale - 0.5, 0, h - 1)
    xs = np.clip((np.arange(nw) + 0.5) / scale - 0.5, 0, w - 1)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    wy, wx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    img = rgb.astype(np.float32) / 255.0
    top = img[y0][:, x0] * (1 - wx) + img[y0][:, x1] * wx
    bottom = img[y1][:, x0] * (1 - wx) + img[y1][:, x1] * wx
    resized = top * (1 - wy) + bottom * wy
    out = np.full((th, tw, 3), PAD_VALUE, dtype=np.float32)
    pad_y, pad_x = (th - nh) / 2.0, (tw - nw) / 2.0
    oy, ox = int(pad_y), int(pad_x)
    out[oy:oy + nh, ox:ox + nw] = resized
    return out.transpose(2, 0, 1)[None], scale, (float(ox), float(oy))


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU of one box ``a`` (4,) against boxes ``b`` (M, 4); all xyxy."""
    ix = np.maximum(0.0, np.minimum(a[2], b[:, 2]) - np.maximum(a[0], b[:, 0]))
    iy = np.maximum(0.0, np.minimum(a[3], b[:, 3]) - np.maximum(a[1], b[:, 1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) - inter
    return inter / np.maximum(union, 1e-9)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
    """Indices kept by greedy non-maximum suppression (highest score first)."""
    order = list(np.argsort(-scores))
    keep: List[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        if order:
            overlap = iou(boxes[i], boxes[order])
            order = [j for j, o in zip(order, overlap) if o <= iou_threshold]
    return keep


def decode_xyxy(out: np.ndarray, score_threshold: float) -> np.ndarray:
    """-> (K, 6) rows x1, y1, x2, y2, score, class."""
    arr = np.asarray(out, dtype=np.float32).reshape(-1, out.shape[-1])
    if arr.shape[1] < 6:
        raise ValueError(f"'xyxy' çıktısı en az 6 sütun olmalı, {arr.shape[1]} geldi")
    return arr[arr[:, 4] >= score_threshold][:, :6]


def decode_yolox(out: np.ndarray, score_threshold: float, nms_iou: float) -> np.ndarray:
    """``[.., N, 5 + C]`` (cx, cy, w, h, objectness, class scores) -> (K, 6) after per-class NMS."""
    arr = np.asarray(out, dtype=np.float32)
    arr = arr.reshape(arr.shape[-2], arr.shape[-1])
    if arr.shape[1] < 6:
        raise ValueError(f"'yolox' çıktısı en az 6 sütun (4 kutu + nesnelik + sınıf) olmalı, "
                         f'{arr.shape[1]} geldi')
    scores = arr[:, 4:5] * arr[:, 5:]
    merged = np.concatenate([arr[:, :4], scores], axis=1)             # same shape as yolov8 rows
    return decode_yolov8(merged[None], score_threshold, nms_iou, 'channels_last')


def decode_yolov8(out: np.ndarray, score_threshold: float, nms_iou: float,
                  layout: str = 'auto') -> np.ndarray:
    """``layout``: ``channels_first`` = ``[.., 4 + C, N]``, ``channels_last`` = ``[.., N, 4 + C]``,
    ``auto`` = the smaller of the two axes holds the channels (right for real models: N is in the
    thousands; give it explicitly for toy outputs with very few boxes)."""
    if layout not in ('auto', 'channels_first', 'channels_last'):
        raise ValueError(f'layout auto/channels_first/channels_last olmalı, {layout!r} verildi')
    arr = np.asarray(out, dtype=np.float32)
    arr = arr.reshape(arr.shape[-2], arr.shape[-1])
    if layout == 'channels_first' or (layout == 'auto' and arr.shape[0] < arr.shape[1]):
        arr = arr.T                                      # [4 + C, N] -> [N, 4 + C]
    if arr.shape[1] < 5:
        raise ValueError(
            f"'yolov8' çıktısı en az 5 sütun (4 kutu + sınıf) olmalı, {arr.shape[1]} geldi")
    boxes_c, scores = arr[:, :4], arr[:, 4:]
    cls = scores.argmax(axis=1)
    conf = scores[np.arange(len(scores)), cls]
    keep = conf >= score_threshold
    boxes_c, conf, cls = boxes_c[keep], conf[keep], cls[keep]
    if not len(conf):
        return np.empty((0, 6), dtype=np.float32)
    xyxy = np.stack([boxes_c[:, 0] - boxes_c[:, 2] / 2, boxes_c[:, 1] - boxes_c[:, 3] / 2,
                     boxes_c[:, 0] + boxes_c[:, 2] / 2, boxes_c[:, 1] + boxes_c[:, 3] / 2], axis=1)
    rows = []
    for c in np.unique(cls):
        idx = np.flatnonzero(cls == c)
        for k in nms(xyxy[idx], conf[idx], nms_iou):
            j = idx[k]
            rows.append([*xyxy[j], conf[j], c])
    return np.array(rows, dtype=np.float32).reshape(-1, 6)


def box_to_segment(frame: Frame, label: str, box: Tuple[float, float, float, float], score: float,
                   ground_z: float = 0.0,
                   ground_margin: Optional[float] = 0.012) -> Optional[Segment]:
    """Pixels of ``box`` (xyxy, image pixels) that belong to the object: valid depth and, when
    ``ground_margin`` is not None, higher than ``ground_z + ground_margin`` in the world frame."""
    h, w = frame.depth.shape
    u0, v0 = max(0, int(np.floor(box[0]))), max(0, int(np.floor(box[1])))
    u1, v1 = min(w, int(np.ceil(box[2]))), min(h, int(np.ceil(box[3])))
    if u1 <= u0 or v1 <= v0:
        return None
    rows, cols = np.mgrid[v0:v1, u0:u1]
    rows, cols = rows.ravel(), cols.ravel()
    z = frame.depth[rows, cols].astype(np.float64)
    ok = np.isfinite(z) & (z > 0.0)
    if ground_margin is not None and ok.any():
        intr = frame.intrinsics
        x = (cols[ok] + 0.5 - intr.cx) * z[ok] / intr.fx
        y = (rows[ok] + 0.5 - intr.cy) * z[ok] / intr.fy
        world = frame.world_from_optical.apply(np.stack([x, y, z[ok]], axis=1))
        above = np.zeros(len(rows), dtype=bool)
        above[np.flatnonzero(ok)[world[:, 2] > ground_z + ground_margin]] = True
        ok = above
    if int(ok.sum()) < 10:
        return None
    return Segment(label=label, rows=rows[ok], cols=cols[ok], score=float(score))


class OnnxDetector(Detector):
    name = 'onnx'

    def __init__(self, model_path: str, labels: Sequence[str], *, decoder: str = 'yolov8',
                 input_size: Optional[Tuple[int, int]] = None, score_threshold: float = 0.4,
                 nms_iou: float = 0.5, ground_z: float = 0.0,
                 ground_margin: Optional[float] = 0.012, layout: str = 'auto', session=None,
                 providers: Sequence[str] = ('CPUExecutionProvider',)):
        if decoder not in ('yolov8', 'yolox', 'xyxy'):
            raise ValueError(f"decoder 'yolov8', 'yolox' ya da 'xyxy' olmalı, {decoder!r} verildi")
        if session is None:
            try:
                import onnxruntime as ort
            except ImportError as exc:
                raise ImportError(
                    'OnnxDetector için onnxruntime gerekli: pip install onnxruntime') from exc
            session = ort.InferenceSession(str(model_path), providers=list(providers))
        self.session = session
        self.labels = list(labels)
        self.decoder = decoder
        self.layout = layout
        self.score_threshold, self.nms_iou = score_threshold, nms_iou
        self.ground_z, self.ground_margin = ground_z, ground_margin
        inp = session.get_inputs()[0]
        shape = list(inp.shape)
        if len(shape) != 4 or shape[1] != 3:
            raise ValueError(f'model girdisi [1, 3, H, W] olmalı, {shape} bulundu')
        if isinstance(shape[2], int) and isinstance(shape[3], int):
            self.input_size = (shape[2], shape[3])
        elif input_size is not None:
            self.input_size = tuple(input_size)
        else:
            raise ValueError(f'girdi boyutu dinamik ({shape}); input_size=(H, W) ver')
        self._input_name = inp.name

    def detect(self, frame: Frame) -> List[Segment]:
        tensor, scale, (pad_x, pad_y) = letterbox(frame.rgb, self.input_size)
        raw = self.session.run(None, {self._input_name: tensor})[0]
        if self.decoder == 'xyxy':
            dets = decode_xyxy(raw, self.score_threshold)
        elif self.decoder == 'yolox':
            dets = decode_yolox(raw, self.score_threshold, self.nms_iou)
        else:
            dets = decode_yolov8(raw, self.score_threshold, self.nms_iou, self.layout)
        out: List[Segment] = []
        for x1, y1, x2, y2, score, cls in dets[np.argsort(-dets[:, 4])] if len(dets) else []:
            if not 0 <= int(cls) < len(self.labels):
                continue                              # a class the caller gave no label for
            box = ((x1 - pad_x) / scale, (y1 - pad_y) / scale, (x2 - pad_x) / scale,
                   (y2 - pad_y) / scale)
            seg = box_to_segment(frame, self.labels[int(cls)], box, float(score), self.ground_z,
                                 self.ground_margin)
            if seg is not None:
                out.append(seg)
        return out
