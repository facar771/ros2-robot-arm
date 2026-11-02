"""OnnxDetector with a tiny synthetic model (constant detections): letterbox, tensor layout,
decoders, NMS, box -> segment and the ground filter are exercised without any real weights."""
import math

import numpy as np
import pytest

onnx = pytest.importorskip('onnx')
pytest.importorskip('onnxruntime')

from onnx import TensorProto, helper  # noqa: E402
from robot_core.workspace import project  # noqa: E402
from robot_vision.onnx_detector import (  # noqa: E402
    OnnxDetector,
    box_to_segment,
    decode_xyxy,
    decode_yolov8,
    decode_yolox,
    iou,
    letterbox,
    nms,
)
from robot_vision.synth import render  # noqa: E402
from vision_support import shapes_by_name, workspace  # noqa: E402

from robot_vision import ColorDetector, measure  # noqa: E402

SIZE = 96


def constant_model(path, output: np.ndarray, size=SIZE, dynamic=False):
    """Model whose single output is ``output`` regardless of the image (it still reads the input, as
    a real model does, so the runtime does not drop it)."""
    h = w = 'H' if dynamic else size
    inp = helper.make_tensor_value_info('images', TensorProto.FLOAT, [1, 3, h, w])
    out = helper.make_tensor_value_info('out', TensorProto.FLOAT, list(output.shape))
    nodes = [
        helper.make_node('ReduceSum', ['images'], ['s'], keepdims=0),
        helper.make_node('Mul', ['s', 'zero'], ['z']),
        helper.make_node('Add', ['const', 'z'], ['out']),
    ]
    graph = helper.make_graph(
        nodes, 'const_detector', [inp], [out],
        initializer=[helper.make_tensor('zero', TensorProto.FLOAT, [], [0.0]),
                     helper.make_tensor('const', TensorProto.FLOAT, list(output.shape),
                                        output.flatten().tolist())])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 13)])
    model.ir_version = 8
    onnx.save(model, str(path))
    return path


@pytest.fixture(scope='module')
def scene():
    ws = workspace()
    frame = render(ws.camera, ws.shapes)
    segs = {s.label: s for s in ColorDetector().detect(frame)}
    return ws, frame, segs


def to_model_coords(frame, seg, size=SIZE):
    """The seg's pixel box as a model of ``size`` would report it after letterboxing."""
    h, w = frame.depth.shape
    scale = min(size / h, size / w)
    pad_x, pad_y = (size - w * scale) / 2, (size - h * scale) / 2
    u0, v0, u1, v1 = seg.bbox
    return [(u0 - 1) * scale + pad_x, (v0 - 1) * scale + pad_y,
            (u1 + 2) * scale + pad_x, (v1 + 2) * scale + pad_y]


def test_letterbox_keeps_aspect_ratio_pads_and_is_invertible():
    rgb = np.zeros((240, 320, 3), dtype=np.uint8)
    rgb[:, :, 0] = 255
    tensor, scale, (px, py) = letterbox(rgb, (96, 96))
    assert tensor.shape == (1, 3, 96, 96) and tensor.dtype == np.float32
    assert scale == pytest.approx(0.3) and (px, py) == (0.0, 12.0)
    assert tensor[0, 0, 48, 48] == pytest.approx(1.0) and tensor[0, 1, 48, 48] == pytest.approx(0.0)
    assert tensor[0, 0, 2, 2] == pytest.approx(114 / 255)             # grey padding
    assert (10 - py) / scale == pytest.approx(-6.67, abs=0.01)     # model y=10: above the image


def test_iou_and_nms():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=float)
    assert iou(boxes[0], boxes[1:])[0] == pytest.approx(81 / 119)
    assert nms(boxes, np.array([0.9, 0.8, 0.7]), 0.5) == [0, 2]
    assert nms(boxes, np.array([0.5, 0.8, 0.7]), 0.5) == [1, 2]
    assert nms(boxes[:0], np.array([]), 0.5) == []


def test_decode_yolov8_handles_both_layouts_thresholds_and_suppresses_duplicates():
    rows = np.array([[50, 50, 20, 20, 0.9, 0.1],      # class 0
                     [51, 50, 20, 20, 0.8, 0.1],      # duplicate of the first
                     [20, 80, 10, 10, 0.1, 0.7],      # class 1
                     [90, 90, 10, 10, 0.2, 0.2]],     # below the threshold
                    dtype=np.float32)
    for raw, layout in ((rows[None], 'channels_last'), (rows.T[None], 'channels_first')):
        dets = decode_yolov8(raw, 0.4, 0.5, layout)
        assert len(dets) == 2
        assert sorted(dets[:, 5].tolist()) == [0.0, 1.0]
        assert dets[dets[:, 5] == 0][0, :4].tolist() == pytest.approx([40, 40, 60, 60])
    assert decode_yolov8(rows[None][:, :0], 0.4, 0.5, 'channels_last').shape == (0, 6)
    with pytest.raises(ValueError):
        decode_yolov8(np.zeros((1, 3, 3), dtype=np.float32), 0.4, 0.5, 'channels_last')
    with pytest.raises(ValueError, match='layout'):
        decode_yolov8(rows[None], 0.4, 0.5, 'nchw')
    big = np.zeros((1, 7, 400), dtype=np.float32)                   # real-sized: auto works
    big[0, :4, 5] = [100, 100, 20, 20]
    big[0, 4, 5] = 0.9
    assert len(decode_yolov8(big, 0.4, 0.5)) == 1


def test_decode_yolox_multiplies_objectness_and_class_score():
    rows = np.array([[50, 50, 20, 20, 0.9, 0.9, 0.1],      # 0.81 -> class 0
                     [51, 50, 20, 20, 0.9, 0.8, 0.1],      # duplicate
                     [20, 80, 10, 10, 0.2, 0.9, 0.0],      # 0.18 < threshold
                     [80, 20, 10, 10, 0.95, 0.1, 0.9]],    # 0.855 -> class 1
                    dtype=np.float32)
    dets = decode_yolox(rows[None], 0.4, 0.5)
    assert sorted(dets[:, 5].tolist()) == [0.0, 1.0]
    assert dets[:, 4].max() == pytest.approx(0.855, abs=1e-3)
    with pytest.raises(ValueError):
        decode_yolox(np.zeros((1, 3, 5), dtype=np.float32), 0.4, 0.5)


def test_decode_xyxy_filters_by_score():
    out = np.array([[[1, 2, 3, 4, 0.9, 1], [5, 6, 7, 8, 0.1, 0]]], dtype=np.float32)
    assert decode_xyxy(out, 0.5).tolist() == [[1, 2, 3, 4, pytest.approx(0.9), 1]]
    with pytest.raises(ValueError):
        decode_xyxy(np.zeros((2, 4), dtype=np.float32), 0.5)


def test_box_to_segment_drops_the_floor_around_the_object(scene):
    ws, frame, segs = scene
    seg = segs['red']
    u0, v0, u1, v1 = seg.bbox
    box = (u0 - 15, v0 - 15, u1 + 15, v1 + 15)                       # a generous box
    with_floor = box_to_segment(frame, 'red', box, 0.9, ground_margin=None)
    cleaned = box_to_segment(frame, 'red', box, 0.9, ground_z=0.0, ground_margin=0.012)
    assert with_floor.pixels > 3 * cleaned.pixels
    assert abs(cleaned.pixels - seg.pixels) < 0.25 * seg.pixels         # ~ the object itself
    assert box_to_segment(frame, 'red', (5, 5, 5, 5), 0.5) is None       # empty box


def test_onnx_detector_end_to_end_measures_the_world_file_objects(tmp_path, scene):
    ws, frame, segs = scene
    labels = ['red', 'green', 'blue']
    dets = np.array([[*to_model_coords(frame, segs[name]), 0.9 - 0.1 * i, i]
                     for i, name in enumerate(labels)], dtype=np.float32)[None]
    detector = OnnxDetector(constant_model(tmp_path / 'm.onnx', dets), labels, decoder='xyxy')
    found = detector.detect(frame)
    assert sorted(s.label for s in found) == sorted(labels)
    assert found[0].score == pytest.approx(0.9)
    shapes = shapes_by_name()
    for seg in found:
        m = measure(frame, seg)
        name = {'red': 'red_box', 'green': 'green_cylinder', 'blue': 'blue_ball'}[seg.label]
        truth = shapes[name]
        assert math.dist(m.center, truth.center) < 0.012
    assert detector.input_size == (SIZE, SIZE) and detector.name == 'onnx'


def test_onnx_detector_with_yolov8_layout_and_a_second_object_of_unknown_class(tmp_path, scene):
    ws, frame, segs = scene
    raw = np.zeros((1, 4 + 3, 3), dtype=np.float32)                      # [1, 4+C, N]
    for i, name in enumerate(['red', 'blue']):
        x1, y1, x2, y2 = to_model_coords(frame, segs[name])
        raw[0, :4, i] = [(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1]
        raw[0, 4 + (0 if name == 'red' else 2), i] = 0.9
    raw[0, :4, 2] = [10, 10, 8, 8]
    raw[0, 4 + 1, 2] = 0.95                                             # class 1 has no label below
    detector = OnnxDetector(constant_model(tmp_path / 'y.onnx', raw), ['red', None, 'blue'],
                            decoder='yolov8', layout='channels_first')
    found = detector.detect(frame)
    assert sorted(s.label for s in found) == ['blue', 'red']      # label None / unknown skipped


def test_dynamic_input_needs_a_size_and_bad_models_are_refused(tmp_path):
    out = np.zeros((1, 1, 6), dtype=np.float32)
    path = constant_model(tmp_path / 'd.onnx', out, dynamic=True)
    with pytest.raises(ValueError, match='dinamik'):
        OnnxDetector(path, ['a'], decoder='xyxy')
    assert OnnxDetector(path, ['a'], decoder='xyxy', input_size=(64, 64)).input_size == (64, 64)
    with pytest.raises(ValueError, match='decoder'):
        OnnxDetector(path, ['a'], decoder='ssd')
    OnnxDetector(path, ['a'], decoder='yolox', input_size=(64, 64))      # accepted
    with pytest.raises(Exception, match='(?i)(no such file|not exist|failed|invalid)'):
        OnnxDetector(tmp_path / 'missing.onnx', ['a'])


def test_ground_filter_is_optional_and_a_miss_returns_nothing(tmp_path, scene):
    ws, frame, segs = scene
    u0, v0, u1, v1 = segs['red'].bbox
    empty = np.array([[[0, 0, 5, 5, 0.9, 0]]], dtype=np.float32)          # a box on the sky/floor
    detector = OnnxDetector(constant_model(tmp_path / 'e.onnx', empty), ['red'], decoder='xyxy')
    assert detector.detect(frame) == []                                  # nothing above the floor
    assert project(ws, shapes_by_name()['red_box'].center) is not None
    assert (u1 - u0) > 0 and (v1 - v0) > 0
