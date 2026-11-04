"""ROS side of the jog (J2): publishes the short setpoints of :mod:`robot_core.jogapi` to the
trajectory controller topic (``/arm_controller/joint_trajectory``, ros2_control's default; the B2
bridge subscribes to the same topic on real hardware). No long trajectory is ever published: each
message replaces the previous one and covers <= 100 ms, so a dead node means a standing robot."""

from typing import Sequence

from builtin_interfaces.msg import Duration
from rclpy.node import Node
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

JOG_TOPIC = '/arm_controller/joint_trajectory'


def _duration(seconds: float) -> Duration:
    sec = int(seconds)
    return Duration(sec=sec, nanosec=int(round((seconds - sec) * 1e9)))


class RosSetpointSink:
    """Implements :class:`robot_core.jogapi.SetpointPort`."""

    def __init__(self, node: Node, topic: str = JOG_TOPIC):
        self._pub = node.create_publisher(JointTrajectory, topic, 1)

    def _publish(self, names, positions, times_s) -> None:
        msg = JointTrajectory()
        msg.joint_names = list(names)  # header.stamp stays 0: "start now"
        for q, t in zip(positions, times_s):
            point = JointTrajectoryPoint()
            point.positions = [float(v) for v in q]
            point.time_from_start = _duration(t)
            msg.points.append(point)
        self._pub.publish(msg)

    def send(self, names: Sequence[str], positions, times_s) -> None:
        self._publish(names, positions, times_s)

    def hold(self, names: Sequence[str], position: Sequence[float]) -> None:
        self._publish(names, [position], [0.1])
