"""Browser view: ROS side. Collects camera frames, detections, joint states and TF into a
:class:`robot_core.webview.Snapshot` and hands it to the (ROS-free) HTTP server in
:mod:`robot_core.webview`.

    ros2 launch robot_viz web_view.launch.py                    # read-only, http://127.0.0.1:8080
    ros2 launch robot_viz web_view.launch.py commands:=true     # + POST /api/command, /api/stop

Subscribes /camera/{image,depth_image,camera_info}, /perception/objects, /joint_states; TF is
looked up per request (world <- camera_optical_frame for the overlay, world <- tip link for the
tool position). Read-only by default: nothing is published and nothing can be commanded.
With ``commands:=true`` the white-listed commands of :mod:`robot_core.webcmd` run on the Skills
layer (MoveIt) exactly as in ``cli_moveit``.
"""
import dataclasses
import os
import signal
import threading
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from robot_controller.adapters import (
    BackgroundSpinner,
    JointStateCache,
    MoveItMotion,
    PlanningSceneReader,
    TfToolPosition,
)
from robot_controller.config_loader import load_robot_config, load_safety_config
from robot_controller.guard_setup import build_path_guard
from robot_core.cameraapi import CameraApi, CameraStop
from robot_core.frames import FrameStore
from robot_core.jobs import JobManager
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.jogapi import READY_NAME, READY_POSE, JogApiConfig, JogController
from robot_core.kinematics import Chain
from robot_core.modelapi import ModelApi
from robot_core.perception import Intrinsics, Transform
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.skills import Skills
from robot_core.spheres import SphereStore
from robot_core.supervisor import CancelFlag, EStop, GuardedMotion, Supervisor
from robot_core.waypointapi import WaypointApi
from robot_core.waypoints import WaypointStore
from robot_core.webcmd import CommandRunner
from robot_core.webview import SceneObject, Snapshot, WebView
from robot_perception.images import depth_array, rgb_array
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray

from robot_data import close_sink, event_sink_from_env
from robot_viz.camera_ros import RosCameraPort
from robot_viz.jog_ros import RosSetpointSink

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
        self.declare_parameter('commands', False)        # True: POST /api/command is enabled
        self.declare_parameter('mode', 'gazebo')         # mock | gazebo | real
        # ---- jog (J2). Defaults lean narrow; every number is a suggestion to tune in simulation.
        self.declare_parameter('jog', True)              # POST /api/jog (needs commands:=true)
        self.declare_parameter('jog_speed_scale', 0.2)       # fraction of the jog speed cap
        self.declare_parameter('jog_speed_scale_real', 0.1)  # mode: real: min(this, scale)
        self.declare_parameter('jog_hold', 'auto')       # auto (off in real) | on | off
        self.declare_parameter('jog_hold_remote', False) # hold-to-run for non-loopback clients
        self.declare_parameter('data_dir', '')           # frames.yaml dir ('' = ROBOT_DATA_DIR)
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

        self.mode = str(self.get_parameter('mode').value)
        self.events = None
        self.runner = None
        self.supervisor = None
        self.jobs = None
        self.jog = None
        self.frames = None
        self.camera = None
        self.waypoints = None
        self.waypoints_error = None
        self.spheres = None
        self.spheres_error = None
        self.model = self._load_model()           # L9b: always (read-only, no commands needed)
        if bool(self.get_parameter('commands').value):
            self.events = event_sink_from_env('web_view')    # ROBOT_DB_URL yoksa: kayıt yok
            safety = load_safety_config()                    # missing/bad file: refuse to start
            # named pose for jogging (inside the safe box, away from the wrist singularity); the
            # profile/SRDF does not know it yet (request to Robot_2), so it is added here
            config = dataclasses.replace(config, poses={**config.poses, READY_NAME: READY_POSE})
            estop, cancel = EStop(), CancelFlag()
            motion = MoveItMotion(self, config, estop=estop, cancel=cancel,
                                  max_velocity_scale=safety.max_velocity_scale)
            # F4b: every goal is planned, checked along its path against the safe box, then run
            path_guard = build_path_guard(motion, self._joints, config, safety, self.events,
                                          self.get_logger().error)
            guarded = GuardedMotion(motion, estop, cancel, path_guard)   # + e-stop / cancel
            skills = Skills(config, guarded, guarded, PlanningSceneReader(self), self.events,
                            zone=safety.zone)
            self.runner = CommandRunner(
                skills, TfToolPosition(self._tf, self._tip, self._world), self.events, estop=estop)
            self.jobs = JobManager(self.runner, cancel, estop)
            if bool(self.get_parameter('jog').value):
                self._setup_jog(estop)
            self._setup_waypoints(safety, guarded)
            camera_port = RosCameraPort(self)        # pan/tilt/slide platform (H4); e-stop stops it
            self.camera = CameraApi(camera_port, estop)
            self.supervisor = Supervisor(estop, self._joints, self._joint_names,
                                         stop_port=CameraStop(motion, camera_port),
                                         runner=self.runner, mode=self.mode, events=self.events,
                                         safety=safety.as_dict(motion.velocity_scale),
                                         jog=self.jog)

    def _load_model(self) -> ModelApi:
        """view_model.json (made by the profile generator). A missing/corrupt file or one whose
        joint names differ from the robot's does not stop the Robot API: /api/model says 503."""
        try:
            path = Path(os.environ.get('ROBOT_VIEW_MODEL') or (
                Path(get_package_share_directory('robot_description')) / 'profile' / 'generated'
                / 'view_model.json'))
        except Exception as exc:    # noqa: BLE001 - package not found: the door explains
            api = ModelApi(error=f'model dosyası bulunamadı ({type(exc).__name__})')
        else:
            api = ModelApi.from_file(path, self._joint_names)
        if api.error:
            self.get_logger().error(f'robot modeli kapalı (GET /api/model 503): {api.error}')
        return api

    def _setup_waypoints(self, safety, guarded) -> None:
        """Waypoint store + API. A broken file does not stop the robot API: the waypoint doors
        answer 503 with the reason and ``goto_waypoint`` fails (nothing is guessed or repaired)."""
        try:
            profile = load_profile(os.environ.get('ROBOT_PROFILE') or PROFILE_PATH)
            data = Path(self.get_parameter('data_dir').value or os.environ.get('ROBOT_DATA_DIR')
                        or Path.home() / '.ros2-robot-arm')
            store = WaypointStore(data / 'waypoints.yaml', Chain(profile), safety.zone,
                                  frames=self.frames)
        except Exception as exc:    # noqa: BLE001
            self.waypoints_error = f'{type(exc).__name__}: {exc}'
            self.get_logger().error(f'nokta deposu kapalı: {self.waypoints_error}')
            return

        def busy() -> Optional[str]:
            if self.jobs is not None and self.jobs.is_running:
                return 'bir iş çalışıyor'
            return 'jog etkin' if self.jog is not None and self.jog.active else None

        self.waypoints = WaypointApi(store, self._joints, busy)
        self.runner.waypoints = store
        self.runner.joint_motion = guarded          # MoveIt plans it; e-stop and cancel apply
        self.get_logger().info(f'nokta deposu hazır: {len(store)} nokta, {store.path}')

    def _setup_jog(self, estop: EStop) -> None:
        try:
            profile = load_profile(os.environ.get('ROBOT_PROFILE') or PROFILE_PATH)
            data = Path(self.get_parameter('data_dir').value or os.environ.get('ROBOT_DATA_DIR')
                        or Path.home() / '.ros2-robot-arm')
            self.frames = FrameStore(data / 'frames.yaml')
            try:     # sphere frames (L8): a corrupt file switches only them off, not the jog
                self.spheres = SphereStore(data / 'spheres.yaml')
            except Exception as exc:    # noqa: BLE001
                self.spheres_error = f'{type(exc).__name__}: {exc}'
                self.get_logger().error(f'küre çerçeveleri kapalı: {self.spheres_error}')
        except Exception as exc:    # noqa: BLE001 - jog stays off; the rest of the API works
            self.get_logger().error(f'jog kapalı: {type(exc).__name__}: {exc}')
            return
        real = self.mode == 'real'
        scale = float(self.get_parameter('jog_speed_scale').value)
        if real:
            scale = min(scale, float(self.get_parameter('jog_speed_scale_real').value))
        hold = str(self.get_parameter('jog_hold').value)
        hold_on = (not real) if hold == 'auto' else hold == 'on'
        jog_config = JogConfig(speed_scale=scale)
        engine = DlsJogEngine(Chain(profile), profile.safety, jog_config)
        self.jog = JogController(
            engine, Chain(profile), self._joints, RosSetpointSink(self), estop, self.frames,
            spheres=self.spheres,
            config=JogApiConfig(hold_enabled=hold_on,
                                hold_remote_enabled=bool(self.get_parameter('jog_hold_remote').value)),
            jog_config=jog_config, busy=lambda: bool(self.jobs and self.jobs.is_running),
            clock=lambda: self.get_clock().now().nanoseconds * 1e-9)    # sim time under Gazebo
        self.jobs.jog_gate = lambda: self.jog.active
        self.jog.start()
        self.get_logger().info(f'jog hazır: hız ölçeği {scale:g}, basılı-tut '
                               f'{"açık" if hold_on else "KAPALI"}, çerçeveler: {self.frames.path}')

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
            joint_velocities=self._joints.joint_velocities(),
            tool_link=self._tip, tool_position=tool_position)


def main(args=None):
    rclpy.init(args=args)
    node = WebViewNode()
    host = str(node.get_parameter('host').value)
    port = int(node.get_parameter('port').value)
    try:
        view = WebView(node, host=host, port=port, depth_range=node.depth_range,
                       commands=node.runner, supervisor=node.supervisor, mode=node.mode,
                       jobs=node.jobs, jog=node.jog, frames=node.frames,
                       waypoints=node.waypoints, waypoints_error=node.waypoints_error,
                       camera=node.camera, model=node.model, spheres=node.spheres,
                       spheres_error=node.spheres_error)
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
    mode = ('KOMUTLAR AÇIK (yalnızca beyaz liste, aynı anda tek komut)' if node.runner
            else 'salt okunur')
    node.get_logger().info(f'web_view hazır: http://{host}:{view.port}/ ({mode})')
    try:
        while not stop.wait(0.2) and rclpy.ok():
            pass
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node.jog is not None:
            node.jog.close()
        view.shutdown()
        spinner.stop()
        if node.events is not None:
            close_sink(node.events)
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
