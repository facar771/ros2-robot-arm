"""Perception -> MoveIt planning scene. Run while perception_node is publishing:

    ros2 run robot_controller perception_scene_sync

Subscribes /perception/objects and keeps ``obj_<colour>_<n>`` collision boxes in MoveIt's planning
scene up to date through :class:`robot_core.scene_sync.SceneSynchronizer` (stable ids, jitter
filter, confirmation, timeout removal). Scene calls block, so they run in the main thread while a
background executor receives the detections (same threading model as cli_moveit).
"""
import threading
import time

import rclpy
from rclpy.node import Node
from robot_core.ports import NullEventSink
from robot_core.scene_sync import Observation, SceneSynchronizer
from visualization_msgs.msg import MarkerArray

from robot_controller.adapters import BackgroundSpinner, PlanningSceneAdapter

TOPIC_OBJECTS = '/perception/objects'
WORLD_FRAME = 'world'


def markers_to_observations(msg: MarkerArray, logger=None) -> list:
    out = []
    for m in msg.markers:
        if m.header.frame_id != WORLD_FRAME:
            if logger:
                logger.warning(f"işaretçi '{m.header.frame_id}' çerçevesinde, '{WORLD_FRAME}' "
                               'bekleniyordu: atlandı', throttle_duration_sec=5.0)
            continue
        p, s = m.pose.position, m.scale
        out.append(Observation(m.ns, (p.x, p.y, p.z), (s.x, s.y, s.z)))
    return out


class DetectionInbox:
    """Latest detections from the executor thread, picked up by the main thread."""

    def __init__(self, node: Node):
        self._lock = threading.Lock()
        self._latest = None
        self._node = node
        node.create_subscription(MarkerArray, TOPIC_OBJECTS, self._on_message, 10)

    def _on_message(self, msg: MarkerArray) -> None:
        obs = markers_to_observations(msg, self._node.get_logger())
        with self._lock:
            self._latest = obs

    def take(self):
        """The newest unseen frame (a list, possibly empty) or None if nothing new arrived."""
        with self._lock:
            obs, self._latest = self._latest, None
        return obs


def main(args=None):
    rclpy.init(args=args)
    node = Node('perception_scene_sync')
    inbox = DetectionInbox(node)
    # TODO(PR-C): event_sink_from_env() once the data layer exists
    sync = SceneSynchronizer(PlanningSceneAdapter(node), NullEventSink(), frame=WORLD_FRAME)
    spinner = BackgroundSpinner(node)
    spinner.start()
    node.get_logger().info('perception_scene_sync hazır: /perception/objects -> planlama sahnesi')
    try:
        while rclpy.ok():
            obs = inbox.take()
            if obs is None:
                time.sleep(0.05)
                continue
            sync.update(obs, node.get_clock().now().nanoseconds * 1e-9)
    except KeyboardInterrupt:
        pass
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
