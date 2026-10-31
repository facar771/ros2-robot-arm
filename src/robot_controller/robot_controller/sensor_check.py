"""Sensor check for the simulated RGB-D camera. Run while
`ros2 launch robot_moveit_config gazebo.launch.py world:=workspace` is up:

    ros2 run robot_controller sensor_check

It does not just check that images arrive: the expected depth at several probe pixels is computed
analytically from the world file (ray casting against the known objects) and compared with what the
camera actually measured, and the static TF is compared with the camera pose in the world file.
Exit code 0 only if every check passed.
"""
import math
import sys
import threading
import time

import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from robot_core.workspace import GROUND, load_workspace, stable_probes
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformListener

from robot_controller.adapters import BackgroundSpinner

WAIT_S = 40.0
DEPTH_TOLERANCE_ABS = 0.02   # m
DEPTH_TOLERANCE_REL = 0.01


class CameraListener:
    def __init__(self, node: Node):
        self._lock = threading.Lock()
        self.rgb = self.depth = self.info = None
        node.create_subscription(Image, '/camera/image', self._set('rgb'), qos_profile_sensor_data)
        node.create_subscription(Image, '/camera/depth_image', self._set('depth'),
                                 qos_profile_sensor_data)
        node.create_subscription(CameraInfo, '/camera/camera_info', self._set('info'),
                                 qos_profile_sensor_data)

    def _set(self, attr):
        def callback(msg):
            with self._lock:
                setattr(self, attr, msg)
        return callback

    def wait_all(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self.rgb is not None and self.depth is not None and self.info is not None:
                    return True
            time.sleep(0.1)
        return False


def rgb_array(msg: Image) -> np.ndarray:
    raw = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(msg.height, msg.step)
    return raw[:, :msg.width * 3].reshape(msg.height, msg.width, 3)


def depth_array(msg: Image) -> np.ndarray:
    return np.frombuffer(bytes(msg.data), dtype=np.float32).reshape(msg.height, msg.step // 4)[
        :, :msg.width]


def quaternion_forward(q) -> tuple:
    """Rotate the x axis by quaternion q (x, y, z, w)."""
    x, y, z, w = q.x, q.y, q.z, q.w
    return (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w))


def run(node: Node, ws, listener: CameraListener, tf_buffer: Buffer) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    if not record('kamera topic\'leri geliyor (image, depth_image, camera_info)',
                  listener.wait_all(WAIT_S), '(world:=workspace ile başlattın mı?)'):
        return results
    cam = ws.camera
    info, rgb_msg, depth_msg = listener.info, listener.rgb, listener.depth

    record('camera_info boyut ve odak uzaklığı dünya dosyasıyla uyumlu',
           (info.width, info.height) == (cam.width, cam.height)
           and abs(info.k[0] - cam.fx) / cam.fx < 0.01 and info.header.frame_id == cam.frame_id,
           f'(fx ölçülen {info.k[0]:.1f}, beklenen {cam.fx:.1f}, frame {info.header.frame_id})')

    rgb = rgb_array(rgb_msg)
    record('renkli görüntü dolu ve boş değil', rgb_msg.encoding == 'rgb8'
           and 20 < rgb.mean() < 250 and rgb.std() > 10,
           f'(enc {rgb_msg.encoding}, ortalama {rgb.mean():.0f}, std {rgb.std():.0f})')

    depth = depth_array(depth_msg)
    finite = np.isfinite(depth)
    record('derinlik görüntüsü 32FC1 ve çoğu piksel geçerli',
           depth_msg.encoding == '32FC1' and finite.mean() > 0.5,
           f'(enc {depth_msg.encoding}, geçerli oran {finite.mean():.2f})')

    probes = stable_probes(ws)
    record('dünya modelinden yeterli sınama pikseli çıkarıldı (zemin + nesneler)',
           GROUND in probes and len(probes) >= 3, f'({sorted(probes)})')
    all_ok = True
    for name, (u, v, expected) in sorted(probes.items()):
        patch = depth[v - 1:v + 2, u - 1:u + 2]
        measured = float(np.median(patch[np.isfinite(patch)])) if np.isfinite(patch).any() \
            else math.nan
        tol = max(DEPTH_TOLERANCE_ABS, DEPTH_TOLERANCE_REL * expected)
        ok = math.isfinite(measured) and abs(measured - expected) <= tol
        all_ok &= ok
        print(f"       {'ok ' if ok else 'BAD'} {name:15s} piksel ({u:3d},{v:3d}) "
              f'beklenen {expected:.3f} m, ölçülen {measured:.3f} m (tolerans {tol:.3f})')
    record('derinlik, analitik ışın izleme ile uyumlu (her nesne ve zemin)', all_ok)

    try:
        tf = tf_buffer.lookup_transform('world', cam.frame_id, rclpy.time.Time(),
                                        rclpy.duration.Duration(seconds=10.0))
        t = tf.transform.translation
        pos_err = math.dist((t.x, t.y, t.z), cam.pose.position)
        fwd = quaternion_forward(tf.transform.rotation)
        exp_fwd = cam.pose.direction_to_world((1, 0, 0))
        fwd_err = math.dist(fwd, exp_fwd)
        record('TF world -> camera_link kamera pozuyla uyumlu', pos_err < 0.01 and fwd_err < 0.01,
               f'(konum hatası {pos_err * 1000:.1f} mm, yön hatası {fwd_err:.4f})')
        tf_buffer.lookup_transform('world', 'camera_optical_frame', rclpy.time.Time(),
                                   rclpy.duration.Duration(seconds=5.0))
        record('TF camera_optical_frame mevcut', True)
    except Exception as exc:  # tf2 raises several exception types
        record('TF world -> camera_link', False, f'-> {type(exc).__name__}: {exc}')
    return results


def main(args=None):
    rclpy.init(args=args)
    node = Node('sensor_check_node')
    sdf = f"{get_package_share_directory('robot_sim')}/worlds/workspace.sdf"
    ws = load_workspace(sdf)
    listener = CameraListener(node)
    tf_buffer = Buffer()
    TransformListener(tf_buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(node, ws, listener, tf_buffer)
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 8 else 1)


if __name__ == '__main__':
    main()
