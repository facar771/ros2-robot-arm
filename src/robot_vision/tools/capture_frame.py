"""Save RGB + depth + camera_info from the running Gazebo camera to an .npz (needs ROS 2).

    # WSL'de (konteyner içinde, simülasyon çalışırken)
    python3 src/robot_vision/tools/capture_frame.py /tmp/frame.npz [kare_sayısı]

The file holds ``rgb`` (N,H,W,3 uint8), ``depth`` (N,H,W float32 z-depth), ``stamps``, ``k`` (the
CameraInfo matrix) and ``width`` / ``height``. ``robot_vision.validate --frame`` and the unit
tests read the same layout, so a captured frame can be replayed without ROS.
"""
import sys
import threading
import time

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image


def _stamp(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


class Capture(Node):
    def __init__(self):
        super().__init__('capture_frame')
        self.pending = {}
        self.frames = []
        self.info = None
        self.create_subscription(Image, '/camera/image', self._rgb, 5)
        self.create_subscription(Image, '/camera/depth_image', self._depth, 5)
        self.create_subscription(CameraInfo, '/camera/camera_info', self._info, 5)

    def _rgb(self, m):
        data = np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.width, 3).copy()
        self.pending['rgb'] = (data, _stamp(m))
        self._pair()

    def _depth(self, m):
        data = np.frombuffer(bytes(m.data), np.float32).reshape(m.height, m.width).copy()
        self.pending['depth'] = (data, _stamp(m))
        self._pair()

    def _info(self, m):
        self.info = (list(m.k), m.width, m.height)

    def _pair(self):
        rgb, depth = self.pending.get('rgb'), self.pending.get('depth')
        if rgb and depth and abs(rgb[1] - depth[1]) < 0.06:
            self.frames.append((rgb[0], depth[0], rgb[1]))
            self.pending.clear()


def main() -> int:
    out = sys.argv[1]
    wanted = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    rclpy.init()
    node = Capture()
    spin = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin.start()
    t0 = time.time()
    while (len(node.frames) < wanted or node.info is None) and time.time() - t0 < 60:
        time.sleep(0.2)
    rclpy.shutdown()
    spin.join(timeout=3)
    if not node.frames or node.info is None:
        print('kare alınamadı (simülasyon çalışıyor mu? world:=workspace)')
        return 2
    np.savez_compressed(out, rgb=np.stack([f[0] for f in node.frames]),
                        depth=np.stack([f[1] for f in node.frames]),
                        stamps=np.array([f[2] for f in node.frames]),
                        k=np.array(node.info[0]), width=node.info[1], height=node.info[2])
    print(f'kaydedildi: {out} ({len(node.frames)} kare)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
