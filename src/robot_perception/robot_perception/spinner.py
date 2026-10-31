"""Background executor thread (same pattern as robot_controller.adapters.BackgroundSpinner, kept
here so perception does not depend on the MoveIt adapters)."""
import threading

from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node


class BackgroundSpinner:
    def __init__(self, node: Node):
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop and join, so no callback runs while the caller destroys the node or shuts rclpy
        down (otherwise: 'terminate called without an active exception')."""
        self._executor.shutdown()
        self._thread.join(timeout=5.0)
