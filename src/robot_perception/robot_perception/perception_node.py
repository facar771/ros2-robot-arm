"""Colour-object detection node.

Subscribes /camera/{image,depth_image,camera_info}, looks up world <- camera_optical_frame in TF and
publishes the detected objects on /perception/objects (visualization_msgs/MarkerArray, frame
'world'): one CUBE marker per object, ``ns`` = colour label, ``id`` = blob index within that
colour, ``pose.position`` = estimated object centre, ``scale`` = bounding box. Each message is the
full set of currently visible objects (an object missing from the message is gone). The image
processing itself is ROS-free: :mod:`robot_core.perception`.
"""
import time
from typing import Optional

import rclpy
from geometry_msgs.msg import Pose
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from robot_core.perception import DEFAULT_COLORS, Intrinsics, Transform, detect_objects
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray

from robot_perception.images import depth_array, rgb_array

TOPIC_OBJECTS = '/perception/objects'
MARKER_RGB = {'red': (0.9, 0.1, 0.1), 'green': (0.1, 0.8, 0.1), 'blue': (0.1, 0.2, 0.9)}


def _stamp_key(msg: Image) -> tuple:
    return (msg.header.stamp.sec, msg.header.stamp.nanosec)


class PerceptionNode(Node):
    def __init__(self):
        super().__init__('perception_node')
        self.declare_parameter('world_frame', 'world')
        self.declare_parameter('optical_frame', 'camera_optical_frame')
        self.declare_parameter('min_pixels', 30)
        self.declare_parameter('max_rate_hz', 5.0)
        self._world = self.get_parameter('world_frame').value
        self._optical = self.get_parameter('optical_frame').value
        self._min_pixels = int(self.get_parameter('min_pixels').value)
        self._min_period = 1.0 / float(self.get_parameter('max_rate_hz').value)

        self._rgb: Optional[Image] = None
        self._depth: Optional[Image] = None
        self._info: Optional[CameraInfo] = None
        self._last_key = None
        self._last_run = 0.0
        self._last_summary = None
        self._tf = Buffer()
        self._tf_listener = TransformListener(self._tf, self)
        self._pub = self.create_publisher(MarkerArray, TOPIC_OBJECTS, 10)
        self.create_subscription(Image, '/camera/image', self._on_rgb, qos_profile_sensor_data)
        self.create_subscription(Image, '/camera/depth_image', self._on_depth,
                                 qos_profile_sensor_data)
        self.create_subscription(CameraInfo, '/camera/camera_info', self._on_info,
                                 qos_profile_sensor_data)
        self.get_logger().info(f'perception_node hazır: {TOPIC_OBJECTS} ({self._world} çerçevesi)')

    def _on_info(self, msg: CameraInfo) -> None:
        self._info = msg

    def _on_rgb(self, msg: Image) -> None:
        self._rgb = msg
        self._maybe_process()

    def _on_depth(self, msg: Image) -> None:
        self._depth = msg
        self._maybe_process()

    def _maybe_process(self) -> None:
        rgb, depth, info = self._rgb, self._depth, self._info
        if rgb is None or depth is None or info is None:
            return
        key = _stamp_key(rgb)
        if key != _stamp_key(depth) or key == self._last_key:
            return  # wait for the matching pair; never process a frame twice
        now = time.monotonic()
        if now - self._last_run < self._min_period:
            return
        self._last_key, self._last_run = key, now
        try:
            self._process(rgb, depth, info)
        except ValueError as exc:   # unsupported encoding / size mismatch: report, keep running
            self.get_logger().error(f'görüntü işlenemedi: {exc}', throttle_duration_sec=5.0)

    def _process(self, rgb_msg: Image, depth_msg: Image, info: CameraInfo) -> None:
        try:
            tf = self._tf.lookup_transform(self._world, self._optical, rclpy.time.Time(),
                                           Duration(seconds=0.5))
        except TransformException as exc:
            self.get_logger().warning(f'TF {self._world} <- {self._optical} yok: {exc}',
                                      throttle_duration_sec=5.0)
            return
        t, q = tf.transform.translation, tf.transform.rotation
        world_from_optical = Transform.from_quaternion(q.x, q.y, q.z, q.w, (t.x, t.y, t.z))
        intr = Intrinsics.from_camera_info_k(info.k, info.width, info.height)
        detections = detect_objects(rgb_array(rgb_msg), depth_array(depth_msg), intr,
                                    world_from_optical, DEFAULT_COLORS,
                                    min_pixels=self._min_pixels,
                                    depth_range=(0.05, 100.0))
        self._pub.publish(self._to_markers(detections, rgb_msg.header.stamp))
        summary = sorted((d.label, d.index) for d in detections)
        if summary != self._last_summary:
            self._last_summary = summary
            text = ', '.join(f'{d.label}#{d.index} ({d.position[0]:.3f}, {d.position[1]:.3f}, '
                             f'{d.position[2]:.3f})' for d in detections) or 'hiçbir nesne'
            self.get_logger().info(f'görünen nesneler: {text}')

    def _to_markers(self, detections, stamp) -> MarkerArray:
        out = MarkerArray()
        for d in detections:
            m = Marker()
            m.header.frame_id = self._world
            m.header.stamp = stamp
            m.ns, m.id = d.label, d.index
            m.type, m.action = Marker.CUBE, Marker.ADD
            m.pose = Pose()
            m.pose.position.x, m.pose.position.y, m.pose.position.z = d.position
            m.pose.orientation.w = 1.0
            m.scale.x, m.scale.y, m.scale.z = (max(s, 0.01) for s in d.size)
            r, g, b = MARKER_RGB.get(d.label, (0.7, 0.7, 0.7))
            m.color.r, m.color.g, m.color.b, m.color.a = r, g, b, 0.6
            m.lifetime = Duration(seconds=1.0).to_msg()
            out.markers.append(m)
        return out


def main(args=None):
    rclpy.init(args=args)
    node = PerceptionNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
