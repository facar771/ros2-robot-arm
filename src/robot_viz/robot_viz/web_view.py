"""Read-only browser view: ROS side. Collects camera frames, detections, joint states and TF into
a :class:`robot_core.webview.Snapshot` and hands it to the (ROS-free) HTTP server in
:mod:`robot_core.webview`.

    ros2 launch robot_viz web_view.launch.py         # then open http://127.0.0.1:8080

Subscribes /camera/{image,depth_image,camera_info}, /perception/objects, /joint_states; TF is
looked up per request (world <- camera_optical_frame for the overlay, world <- tip link for the
tool position). Nothing is published and nothing can be commanded through it.
"""
import signal
import threading
import time
from typing import Optional, Tuple

import numpy as np
import rclpy
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from robot_core.perception import Intrinsics, Transform
from robot_core.webview import SceneObject, Snapshot, WebView
from robot_perception.images import depth_array, rgb_array
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray

TOPIC_OBJECTS = '/perception/objects'


def _age(stamp: Optional[float], now: float) -> Optional[float]:
    return None if stamp is None else max(0.0, now - stamp)


class WebViewNode(Node):
    """Implements :class:`robot_core.webview.ViewSource`."""

    def __init__(self):
        super().__init__('web_view')
        self.declare_parameter('host', '127.0.0.1')
        self.declare_parameter('port', 8080)
        self.declare_parameter('world_frame', 'world')
        self.declare_parameter('optical_frame', 'camera_optical_frame')
        self.declare_parameter('tip_link', '')           # '' = from the robot description
        self.declare_parameter('depth_near', 0.5)
        self.declare_parameter('depth_far', 1.7)
        self.declare_parameter('objects_stale_s', 2.0)   # detections older than this are dropped
        self._world = self.get_parameter('world_frame').value
        self._optical = self.get_parameter('optical_frame').value
        self._stale_s = float(self.get_parameter('objects_stale_s').value)
        config = load_robot_config()
        self._joint_names = tuple(config.joint_names)
        self._tip = self.get_parameter('tip_link').value or config.tip_link

        self._lock = threading.Lock()
        self._rgb: Optional[Tuple[np.ndarray, float]] = None
        self._depth: Optional[Tuple[np.ndarray, float]] = None
        self._objects: Optional[Tuple[Tuple[SceneObject, ...], float]] = None
        self._intr: Optional[Intrinsics] = None
        self._joints = JointStateCache(self)
        self._tf = Buffer()
        self._tf_listener = TransformListener(self._tf, self)
        self.create_subscription(Image, '/camera/image', self._on_rgb, qos_profile_sensor_data)
        self.create_subscription(Image, '/camera/depth_image', self._on_depth,
                                 qos_profile_sensor_data)
        self.create_subscription(CameraInfo, '/camera/camera_info', self._on_info,
                                 qos_profile_sensor_data)
        self.create_subscription(MarkerArray, TOPIC_OBJECTS, self._on_objects, 10)

    @property
    def depth_range(self) -> Tuple[float, float]:
        return (float(self.get_parameter('depth_near').value),
                float(self.get_parameter('depth_far').value))

    # ---- callbacks (executor thread) --------------------------------------------------------
    def _on_rgb(self, msg: Image) -> None:
        try:
            frame = (np.array(rgb_array(msg)), time.monotonic())
        except ValueError as exc:
            self.get_logger().error(f'RGB karesi işlenemedi: {exc}', throttle_duration_sec=5.0)
            return
        with self._lock:
            self._rgb = frame

    def _on_depth(self, msg: Image) -> None:
        try:
            frame = (np.array(depth_array(msg)), time.monotonic())
        except ValueError as exc:
            self.get_logger().error(f'Derinlik karesi işlenemedi: {exc}',
                                    throttle_duration_sec=5.0)
            return
        with self._lock:
            self._depth = frame

    def _on_info(self, msg: CameraInfo) -> None:
        intr = Intrinsics.from_camera_info_k(msg.k, msg.width, msg.height)
        with self._lock:
            self._intr = intr

    def _on_objects(self, msg: MarkerArray) -> None:
        # Every message is the complete set of visible objects (docs/INTERFACES.md).
        objects = tuple(SceneObject(m.ns, int(m.id),
                                    (m.pose.position.x, m.pose.position.y, m.pose.position.z),
                                    (m.scale.x, m.scale.y, m.scale.z))
                        for m in msg.markers if m.action == Marker.ADD)
        with self._lock:
            self._objects = (objects, time.monotonic())

    # ---- ViewSource (HTTP threads) ----------------------------------------------------------
    def _lookup(self, target: str, source: str):
        try:
            return self._tf.lookup_transform(target, source, rclpy.time.Time(), Duration(seconds=0))
        except TransformException:
            return None

    def snapshot(self) -> Snapshot:
        now = time.monotonic()
        with self._lock:
            rgb, depth, objects, intr = self._rgb, self._depth, self._objects, self._intr
        optical = self._lookup(self._world, self._optical)
        world_from_optical = None
        if optical is not None:
            t, q = optical.transform.translation, optical.transform.rotation
            world_from_optical = Transform.from_quaternion(q.x, q.y, q.z, q.w, (t.x, t.y, t.z))
        tool = self._lookup(self._world, self._tip)
        tool_position = None
        if tool is not None:
            p = tool.transform.translation
            tool_position = (p.x, p.y, p.z)
        fresh = objects is not None and now - objects[1] <= self._stale_s
        return Snapshot(
            rgb=None if rgb is None else rgb[0], depth=None if depth is None else depth[0],
            rgb_age_s=_age(None if rgb is None else rgb[1], now),
            depth_age_s=_age(None if depth is None else depth[1], now),
            objects=objects[0] if fresh else (),
            objects_age_s=_age(None if objects is None else objects[1], now),
            intrinsics=intr, world_from_optical=world_from_optical,
            joint_names=self._joint_names, joint_positions=self._joints.joint_positions(),
            tool_link=self._tip, tool_position=tool_position)


def main(args=None):
    rclpy.init(args=args)
    node = WebViewNode()
    host = str(node.get_parameter('host').value)
    port = int(node.get_parameter('port').value)
    try:
        view = WebView(node, host=host, port=port, depth_range=node.depth_range)
    except OSError as exc:
        node.get_logger().error(f'{host}:{port} dinlenemiyor: {exc} (port meşgul mü?)')
        node.destroy_node()
        rclpy.try_shutdown()
        raise SystemExit(1) from exc
    spinner = BackgroundSpinner(node)
    spinner.start()
    view.start_background()
    stop = threading.Event()
    # Replaces rclpy's own handlers: both signals end the loop below and then shut down in order
    # (HTTP server, executor thread, node), so no callback is running while rclpy shuts down.
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    node.get_logger().info(f'web_view hazır: http://{host}:{view.port}/ (salt okunur)')
    try:
        while not stop.wait(0.2) and rclpy.ok():
            pass
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        view.shutdown()
        spinner.stop()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
