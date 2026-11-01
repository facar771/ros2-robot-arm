"""Interactive MoveIt CLI. All logic lives in robot_core.CommandInterpreter; this file only wires
the ROS 2 adapters into it."""
import rclpy
from rclpy.node import Node

from robot_controller.adapters import (
    BackgroundSpinner,
    JointStateCache,
    MoveItMotion,
    PlanningSceneAdapter,
)
from robot_controller.config_loader import load_robot_config
from robot_core import CommandInterpreter
from robot_data import close_sink, event_sink_from_env


def main(args=None):
    rclpy.init(args=args)
    node = Node('cli_moveit_node')
    config = load_robot_config()
    state = JointStateCache(node)
    scene = PlanningSceneAdapter(node)
    motion = MoveItMotion(node, config)
    events = event_sink_from_env('cli_moveit')    # ROBOT_DB_URL yoksa: kayıt yok
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        CommandInterpreter(config, motion, scene, state, events=events).repl()
    finally:
        close_sink(events)
        spinner.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
