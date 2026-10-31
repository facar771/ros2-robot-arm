"""sensor_msgs/Image -> numpy, honouring the row stride (``step``)."""
import numpy as np
from sensor_msgs.msg import Image


def rgb_array(msg: Image) -> np.ndarray:
    """(H, W, 3) uint8 in RGB order."""
    if msg.encoding not in ('rgb8', 'bgr8'):
        raise ValueError(f'unsupported colour encoding {msg.encoding!r} (expected rgb8 or bgr8)')
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(msg.height, msg.step)
    img = raw[:, :msg.width * 3].reshape(msg.height, msg.width, 3)
    return img[..., ::-1] if msg.encoding == 'bgr8' else img


def depth_array(msg: Image) -> np.ndarray:
    """(H, W) float32 z-depth in metres; no-measurement pixels are NaN or inf."""
    if msg.encoding == '32FC1':
        raw = np.frombuffer(bytes(msg.data), dtype=np.float32).reshape(msg.height, msg.step // 4)
        return raw[:, :msg.width]
    if msg.encoding == '16UC1':   # millimetres, 0 = no measurement
        raw = np.frombuffer(bytes(msg.data), dtype=np.uint16).reshape(msg.height, msg.step // 2)
        depth = raw[:, :msg.width].astype(np.float32) / 1000.0
        depth[raw[:, :msg.width] == 0] = np.nan
        return depth
    raise ValueError(f'unsupported depth encoding {msg.encoding!r} (expected 32FC1 or 16UC1)')
