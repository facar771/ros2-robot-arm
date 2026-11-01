"""ROS 2 implementations of the ports defined in robot_core.ports.

Threading model: the node is spun by a background executor thread; the command loop runs in the
main thread and *polls* futures (it never spins the node itself). That is why callbacks (results,
joint states) are processed while the user is typing.
"""
import math
import threading
import time
from typing import Dict, Mapping, Optional

import rclpy
from geometry_msgs.msg import Pose
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (
    CollisionObject,
    Constraints,
    JointConstraint,
    MoveItErrorCodes,
    OrientationConstraint,
    PlanningScene,
    PlanningSceneComponents,
    PositionConstraint,
)
from moveit_msgs.srv import ApplyPlanningScene, GetPlanningScene
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from robot_core.models import ObjectInfo, PoseTarget
from robot_core.safezone import effective_velocity_scale
from sensor_msgs.msg import JointState
from shape_msgs.msg import SolidPrimitive
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException

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
    """Implements StatePort and the supervisor's VelocityPort."""

    VELOCITY_MAX_AGE_S = 1.0

    def __init__(self, node: Node):
        self._lock = threading.Lock()
        self._positions: Dict[str, float] = {}
        self._velocities: Dict[str, float] = {}
        self._velocity_stamp: Optional[float] = None
        node.create_subscription(JointState, '/joint_states', self._on_message, 10)

    def _on_message(self, msg: JointState) -> None:
        with self._lock:
            self._positions.update(zip(msg.name, msg.position))
            if len(msg.velocity) == len(msg.name):
                self._velocities.update(zip(msg.name, msg.velocity))
                self._velocity_stamp = time.monotonic()

    def joint_positions(self) -> Optional[Dict[str, float]]:
        with self._lock:
            return dict(self._positions) or None

    def joint_velocities(self) -> Optional[Dict[str, float]]:
        """Latest velocities (rad/s); None when none arrived or the newest is over 1 s old, so a
        silent joint_states topic can never look like "standing still"."""
        with self._lock:
            if self._velocity_stamp is None or \
                    time.monotonic() - self._velocity_stamp > self.VELOCITY_MAX_AGE_S:
                return None
            return dict(self._velocities)


class TfToolPosition:
    """Implements ToolPositionPort: where the tool link really is, measured from TF (robot_state_
    publisher on the *simulated* joint states), not from what MoveIt believes. ``buffer`` must be
    fed by a TransformListener on a node that is being spun."""

    def __init__(self, buffer: Buffer, link: str, world: str = 'world', timeout: float = 0.5):
        self._buffer, self._link, self._world, self._timeout = buffer, link, world, timeout

    def tool_position(self):
        try:
            t = self._buffer.lookup_transform(
                self._world, self._link, rclpy.time.Time(),
                Duration(seconds=self._timeout)).transform.translation
        except TransformException:
            return None
        return (t.x, t.y, t.z)


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
    """Implements MotionPort (joint goals) and PosePort (Cartesian goals) through move_group."""

    def __init__(self, node: Node, config: RobotConfig, velocity_scale: float = 0.5,
                 execution_timeout: float = 60.0, discovery_timeout: float = 15.0, estop=None,
                 cancel=None, max_velocity_scale: Optional[float] = None):
        self._node = node
        self._estop = estop                    # robot_core.supervisor.EStop (None: no e-stop)
        self._cancel = cancel                  # CancelFlag of the running job (None: no jobs)
        self.velocity_scale = effective_velocity_scale(velocity_scale, max_velocity_scale)
        self._handle_lock = threading.Lock()
        self._handle = None                    # goal handle of the running move_action goal
        self._event_pub = node.create_publisher(String, '/trajectory_execution_event', 10)
        self._discovery_timeout = discovery_timeout
        self._config = config
        self._timeout = execution_timeout
        self._client = ActionClient(node, MoveGroup, 'move_action')

    def move_to(self, target: JointTarget) -> MotionResult:
        constraints = Constraints()
        for name, value in zip(self._config.joint_names, target.values):
            joint = JointConstraint()
            joint.joint_name = name
            joint.position = float(value)
            joint.tolerance_above = 0.01
            joint.tolerance_below = 0.01
            joint.weight = 1.0
            constraints.joint_constraints.append(joint)
        return self._plan_and_execute(constraints)

    def move_to_pose(self, target: PoseTarget) -> MotionResult:
        """Cartesian goal for the tool link; MoveIt solves the inverse kinematics (KDL)."""
        constraints = Constraints()
        position = PositionConstraint()
        position.header.frame_id = target.frame
        position.link_name = self._config.tip_link
        region = SolidPrimitive()
        region.type = SolidPrimitive.SPHERE
        region.dimensions = [float(target.position_tolerance)]
        center = Pose()
        center.position.x, center.position.y, center.position.z = target.x, target.y, target.z
        center.orientation.w = 1.0
        position.constraint_region.primitives.append(region)
        position.constraint_region.primitive_poses.append(center)
        position.weight = 1.0
        constraints.position_constraints.append(position)
        if target.orientation is not None:
            orientation = OrientationConstraint()
            orientation.header.frame_id = target.frame
            orientation.link_name = self._config.tip_link
            (orientation.orientation.x, orientation.orientation.y, orientation.orientation.z,
             orientation.orientation.w) = target.orientation
            orientation.absolute_x_axis_tolerance = float(target.orientation_tolerance)
            orientation.absolute_y_axis_tolerance = float(target.orientation_tolerance)
            orientation.absolute_z_axis_tolerance = math.pi     # rotation about the tool axis: free
            orientation.weight = 1.0
            constraints.orientation_constraints.append(orientation)
        return self._plan_and_execute(constraints)

    def _plan_and_execute(self, constraints: Constraints) -> MotionResult:
        if not self._client.wait_for_server(timeout_sec=self._discovery_timeout):
            return MotionResult(False, message="MoveIt 'move_action' sunucusu bulunamadı "
                                               '(demo.launch.py çalışıyor mu?)')
        goal = MoveGroup.Goal()
        goal.request.group_name = self._config.planning_group
        goal.request.allowed_planning_time = 5.0
        goal.request.num_planning_attempts = 10
        goal.request.max_velocity_scaling_factor = self.velocity_scale
        goal.request.max_acceleration_scaling_factor = self.velocity_scale
        goal.planning_options.plan_only = False
        goal.request.goal_constraints.append(constraints)

        send_future = self._client.send_goal_async(goal)
        if not wait_future(send_future, 10.0):
            return MotionResult(False, message='Hedef gönderimi zaman aşımı')
        handle = send_future.result()
        if not handle.accepted:
            return MotionResult(False, message='Hedef MoveIt tarafından reddedildi')
        with self._handle_lock:
            self._handle = handle
        try:
            result_future = handle.get_result_async()
            if not self._wait_result(result_future, handle):
                return MotionResult(False, message='Sonuç zaman aşımı (planlama/yürütme bitmedi)')
        finally:
            with self._handle_lock:
                self._handle = None
        code = result_future.result().result.error_code.val
        return MotionResult(code == MoveItErrorCodes.SUCCESS, code, error_name(code))

    def _wait_result(self, result_future, handle) -> bool:
        """Poll for the result; while the e-stop is latched or the job is being cancelled keep
        cancelling the goal (so a stop that raced the goal's acceptance cannot be lost)."""
        deadline = time.monotonic() + self._timeout
        last_cancel = 0.0
        while not result_future.done():
            now = time.monotonic()
            if self._abort_requested() and now - last_cancel > 0.5:
                handle.cancel_goal_async()
                self._publish_stop()
                last_cancel = now
            if now > deadline:
                return False
            time.sleep(0.01)
        return True

    def _abort_requested(self) -> bool:
        return bool((self._estop is not None and self._estop.is_set)
                    or (self._cancel is not None and self._cancel.is_set))

    def _publish_stop(self) -> None:
        self._event_pub.publish(String(data='stop'))

    def stop(self) -> None:
        """Implements the supervisor's StopPort: stop trajectory execution now and cancel the
        running goal. Safe from any thread, repeatable."""
        self._publish_stop()
        with self._handle_lock:
            handle = self._handle
        if handle is not None:
            handle.cancel_goal_async()


class PlanningSceneReader:
    """Implements ObjectsPort: the collision objects MoveIt currently holds (the perception
    objects ``obj_<colour>_<n>`` as well as hand-made ones), read through /get_planning_scene.
    Only objects in the world/base frames (identity transform) are returned."""

    FRAMES = ('world', 'base_link')

    def __init__(self, node: Node, timeout: float = 5.0):
        self._node = node
        self._timeout = timeout
        self._client = node.create_client(GetPlanningScene, '/get_planning_scene')

    def objects(self) -> Optional[Mapping[str, ObjectInfo]]:
        if not self._client.wait_for_service(timeout_sec=self._timeout):
            return None
        request = GetPlanningScene.Request()
        request.components.components = PlanningSceneComponents.WORLD_OBJECT_GEOMETRY
        future = self._client.call_async(request)
        if not wait_future(future, self._timeout) or future.result() is None:
            return None
        found: Dict[str, ObjectInfo] = {}
        for co in future.result().scene.world.collision_objects:
            if not co.primitives or co.header.frame_id not in self.FRAMES:
                continue
            c = co.primitive_poses[0].position           # identity orientations
            o = co.pose.position
            dims = _primitive_size(co.primitives[0])
            parts = co.id.split('_')
            label = parts[1] if co.id.startswith('obj_') and len(parts) >= 3 else ''
            found[co.id] = ObjectInfo(co.id, label, (o.x + c.x, o.y + c.y, o.z + c.z), dims,
                                      'world')
        return found


def _primitive_size(primitive: SolidPrimitive):
    d = list(primitive.dimensions)
    if primitive.type == SolidPrimitive.BOX:
        return (d[0], d[1], d[2])
    if primitive.type == SolidPrimitive.SPHERE:
        return (2 * d[0], 2 * d[0], 2 * d[0])
    if primitive.type == SolidPrimitive.CYLINDER:                # (height, radius)
        return (2 * d[1], 2 * d[1], d[0])
    return (0.0, 0.0, 0.0)
