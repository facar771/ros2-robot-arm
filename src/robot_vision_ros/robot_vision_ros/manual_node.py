"""Manual control of the camera platform (pan / tilt / platform position) over ROS 2.

    ros2 run robot_vision_ros camera_manual      # or: tracking.launch.py manual:=true

Topics (JSON in ``std_msgs/String`` so no custom message package is needed):

* ``/camera/manual/command``  (in)  ``{"op":"goto","pan":0.3,"tilt":0.2,"slide":1.0}`` |
  ``{"op":"jog","pan_rate":0.2,"ttl_s":0.5}`` | ``{"op":"stop"}`` (see :mod:`robot_vision.manual`)
* ``/camera/manual/reply``    (out) one JSON per command: ``accepted``, ``code``, ``message``
* ``/camera/manual/status``   (out, 5 Hz) ``state`` (idle/goto/jog), ``q`` {pan, slide, tilt},
  ``last`` (last reply, with ``done`` when a motion ended), ``tracking_busy``
* publishes the rate commands ``/platform/{yaw,slide,pitch}_joint/cmd_vel`` ONLY while a manual
  motion runs (and one final zero); nothing at all when idle, so it never fights ``tracking_node``.

It refuses (``busy``) while ``/tracking/mode`` is not ``idle`` or ``/tracking/status`` is arriving,
and drops a running motion when tracking takes over. Needs fresh ``/platform/joint_states``.
"""
from __future__ import annotations

import json
from typing import Optional, Tuple

import rclpy
from rclpy.node import Node
from robot_vision.manual import ManualSession
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64, String

JOINTS = ('yaw_joint', 'slide_joint', 'pitch_joint')
STATE_MAX_AGE_S = 0.5          # joint state older than this (clock seconds) = unknown
TRACKING_STATUS_MAX_AGE_S = 1.0
PERIOD_S = 0.05


class ManualNode(Node):
    def __init__(self):
        super().__init__('camera_manual')
        self.session = ManualSession()
        self._q: Optional[Tuple[float, float, float]] = None
        self._q_time = -1e9
        self._idx = None
        self._tracking_mode = 'idle'
        self._tracking_status_time = -1e9
        self._pubs = [self.create_publisher(Float64, f'/platform/{j}/cmd_vel', 10) for j in JOINTS]
        self._reply = self.create_publisher(String, '/camera/manual/reply', 10)
        self._status = self.create_publisher(String, '/camera/manual/status', 10)
        self.create_subscription(String, '/camera/manual/command', self._on_command, 10)
        self.create_subscription(String, '/tracking/mode', self._on_mode, 10)
        self.create_subscription(String, '/tracking/status', self._on_tracking_status, 10)
        self.create_subscription(JointState, '/platform/joint_states', self._on_joints, 100)
        self.create_timer(PERIOD_S, self._tick)
        self.create_timer(0.2, self._publish_status)
        self.get_logger().info('camera_manual ready: send JSON to /camera/manual/command')

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _fresh_q(self):
        return self._q if self._now() - self._q_time <= STATE_MAX_AGE_S else None

    def _busy(self) -> bool:
        return (self._tracking_mode != 'idle'
                or self._now() - self._tracking_status_time <= TRACKING_STATUS_MAX_AGE_S)

    def _on_joints(self, msg: JointState) -> None:
        if self._idx is None:
            names = list(msg.name)
            if not all(j in names for j in JOINTS) or not msg.position:
                return
            self._idx = tuple(names.index(j) for j in JOINTS)
        i0, i1, i2 = self._idx
        self._q = (msg.position[i0], msg.position[i1], msg.position[i2])
        self._q_time = self._now()

    def _on_mode(self, msg: String) -> None:
        self._tracking_mode = msg.data

    def _on_tracking_status(self, msg: String) -> None:
        self._tracking_status_time = self._now()

    def _on_command(self, msg: String) -> None:
        reply = self.session.handle(msg.data, self._now(), self._fresh_q(), self._busy())
        if not reply['accepted']:
            self.get_logger().warning(f"command refused: {reply['code']}: {reply['message']}")
        self._reply.publish(String(data=json.dumps(reply)))

    def _publish_rates(self, rates) -> None:
        for pub, v in zip(self._pubs, rates):
            pub.publish(Float64(data=float(v)))

    def _tick(self) -> None:
        rates = self.session.tick(self._fresh_q(), self._now(), self._busy())
        if rates is not None:
            self._publish_rates(rates)

    def _publish_status(self) -> None:
        q = self._fresh_q()
        doc = {'state': self.session.mode, 'last': self.session.last,
               'tracking_busy': self._busy(),
               'q': None if q is None else dict(zip(('pan', 'slide', 'tilt'), q))}
        self._status.publish(String(data=json.dumps(doc)))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ManualNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        try:
            if node.session.ctrl.active:           # never leave the joints moving
                node._publish_rates((0.0, 0.0, 0.0))
        except Exception:                          # noqa: BLE001 - context may already be gone
            pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
