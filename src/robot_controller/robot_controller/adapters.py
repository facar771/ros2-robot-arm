"""ROS 2 implementations of the ports defined in robot_core.ports.

Threading model: the node is spun by a background executor thread; the command loop runs in the
main thread and *polls* futures (it never spins the node itself). That is why callbacks (results,
joint states) are processed while the user is typing.
"""
import threading
import time
from typing import Dict, Optional

from geometry_msgs.msg import Pose
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (
    CollisionObject,
    Constraints,
    JointConstraint,
    MoveItErrorCodes,
    PlanningScene,
)
from moveit_msgs.srv import ApplyPlanningScene
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState
from shape_msgs.msg import SolidPrimitive

from robot_core import BoxObstacle, JointTarget, MotionResult, RobotConfig

# Built from the message definition itself, so names always match the installed MoveIt version.
ERROR_NAMES = {value: name for name, value in vars(MoveItErrorCodes).items()
               if name.isupper() and isinstance(value, int)}


def error_name(code: int) -> str:
    return ERROR_NAMES.get(code, 'UNKNOWN')


def wait_future(future, timeout: float) -> bool:
    """Poll a future that the background executor completes. False on timeout."""
    deadline = time.monotonic() + timeout
    while not future.done():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class BackgroundSpinner:
    def __init__(self, node: Node):
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(node)
        self._thread = threading.Thread(target=self._executor.spin, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Stop spinning and wait for the thread, so no callback is still running when the caller
        destroys the node or calls rclpy.shutdown() (otherwise: 'terminate called ...' abort)."""
        self._executor.shutdown()
        self._thread.join(timeout=5.0)


class JointStateCache:
    """Implements StatePort."""

    def __init__(self, node: Node):
        self._lock = threading.Lock()
        self._positions: Dict[str, float] = {}
        node.create_subscription(JointState, '/joint_states', self._on_message, 10)

    def _on_message(self, msg: JointState) -> None:
        with self._lock:
            self._positions.update(zip(msg.name, msg.position))

    def joint_positions(self) -> Optional[Dict[str, float]]:
        with self._lock:
            return dict(self._positions) or None


class PlanningSceneAdapter:
    """Implements ScenePort via /apply_planning_scene (synchronous, so MoveIt has the object
    before the next plan request)."""

    def __init__(self, node: Node, timeout: float = 5.0, discovery_timeout: float = 10.0):
        self._node = node
        self._timeout = timeout
        self._discovery_timeout = discovery_timeout
        self._client = node.create_client(ApplyPlanningScene, '/apply_planning_scene')

    def add_box(self, obstacle: BoxObstacle) -> bool:
        co = CollisionObject()
        co.id = obstacle.name
        co.header.frame_id = obstacle.frame
        box = SolidPrimitive()
        box.type = SolidPrimitive.BOX
        box.dimensions = [obstacle.size_x, obstacle.size_y, obstacle.size_z]
        pose = Pose()
        pose.position.x, pose.position.y, pose.position.z = obstacle.x, obstacle.y, obstacle.z
        pose.orientation.w = 1.0
        co.primitives.append(box)
        co.primitive_poses.append(pose)
        co.operation = CollisionObject.ADD
        return self._apply(co)

    def remove_box(self, name: str) -> bool:
        co = CollisionObject()
        co.id = name
        co.operation = CollisionObject.REMOVE
        return self._apply(co)

    def _apply(self, collision_object: CollisionObject) -> bool:
        if not self._client.wait_for_service(timeout_sec=self._discovery_timeout):
            self._node.get_logger().error(
                '/apply_planning_scene bulunamadı (move_group çalışıyor mu?)')
            return False
        scene = PlanningScene()
        scene.is_diff = True
        scene.world.collision_objects.append(collision_object)
        request = ApplyPlanningScene.Request()
        request.scene = scene
        future = self._client.call_async(request)
        if not wait_future(future, self._timeout):
            self._node.get_logger().error('Planlama sahnesi servisi zaman aşımı')
            return False
        return bool(future.result() and future.result().success)


class MoveItMotion:
    """Implements MotionPort: plan + execute a joint goal through move_group."""

    def __init__(self, node: Node, config: RobotConfig, velocity_scale: float = 0.5,
                 execution_timeout: float = 60.0, discovery_timeout: float = 15.0):
        self._node = node
        self._discovery_timeout = discovery_timeout
        self._config = config
        self._velocity_scale = velocity_scale
        self._timeout = execution_timeout
        self._client = ActionClient(node, MoveGroup, 'move_action')

    def move_to(self, target: JointTarget) -> MotionResult:
        if not self._client.wait_for_server(timeout_sec=self._discovery_timeout):
            return MotionResult(False, message="MoveIt 'move_action' sunucusu bulunamadı "
                                               '(demo.launch.py çalışıyor mu?)')
        goal = MoveGroup.Goal()
        goal.request.group_name = self._config.planning_group
        goal.request.allowed_planning_time = 5.0
        goal.request.num_planning_attempts = 10
        goal.request.max_velocity_scaling_factor = self._velocity_scale
        goal.request.max_acceleration_scaling_factor = self._velocity_scale
        goal.planning_options.plan_only = False

        constraints = Constraints()
        for name, value in zip(self._config.joint_names, target.values):
            joint = JointConstraint()
            joint.joint_name = name
            joint.position = float(value)
            joint.tolerance_above = 0.01
            joint.tolerance_below = 0.01
            joint.weight = 1.0
            constraints.joint_constraints.append(joint)
        goal.request.goal_constraints.append(constraints)

        send_future = self._client.send_goal_async(goal)
        if not wait_future(send_future, 10.0):
            return MotionResult(False, message='Hedef gönderimi zaman aşımı')
        handle = send_future.result()
        if not handle.accepted:
            return MotionResult(False, message='Hedef MoveIt tarafından reddedildi')
        result_future = handle.get_result_async()
        if not wait_future(result_future, self._timeout):
            return MotionResult(False, message='Sonuç zaman aşımı (planlama/yürütme bitmedi)')
        code = result_future.result().result.error_code.val
        return MotionResult(code == MoveItErrorCodes.SUCCESS, code, error_name(code))
