"""ROS 2 tracking node: camera images + joint states in, platform rate commands out.

Modes (topic ``/tracking/mode``, std_msgs/String, latched by the node):
  idle  - rates are zero (default)
  track - follow the largest red object: detect -> world-frame tracker -> servo (PID + line-of-sight
          feed-forward) -> rate commands; reports ``/tracking/status`` (JSON string) per image
  home  - drive the three joints back to ``robot_vision.platform.HOME`` (used between scenarios)
All logic lives in ``robot_vision`` (ROS-free, unit-tested); this file only converts messages.
Runs on simulated time (``use_sim_time``); ``latency`` is image stamp -> command, in sim seconds.
"""
from __future__ import annotations

import bisect
import json
import time
from collections import deque
from typing import Deque, List, Optional, Tuple

import numpy as np
import rclpy
from rclpy.node import Node
from robot_core.perception import Intrinsics
from robot_vision.detectors import ColorDetector
from robot_vision.frame import Frame
from robot_vision.platform import (
    HOME,
    PlatformLimits,
    camera_pose,
    camera_velocity,
    optical_in_world,
    world_to_optical,
)
from robot_vision.servo import (
    Command,
    LockMonitor,
    Servo,
    ServoConfig,
    clamp,
    line_of_sight_rates,
)
from robot_vision.tracker import Observation, Tracker
from robot_vision.tracking_sim import (
    CAMERA,
    TRACKER_CONFIG,
    frame_in_camera_frame,
    red_target,
)
from sensor_msgs.msg import CameraInfo, Image, JointState
from std_msgs.msg import Float64, String

JOINTS = ('yaw_joint', 'slide_joint', 'pitch_joint')
MODES = ('idle', 'track', 'home')
HOME_TOLERANCE = (0.02, 0.03, 0.02)       # rad, m, rad
IDENTITY_NAMES = ('yaw', 'slide', 'pitch')


def stamp_s(msg) -> float:
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


class TrackingNode(Node):
    def __init__(self):
        super().__init__('tracking_node')
        for name, default in (('distance', ServoConfig().distance),
                              ('pixel_radius', ServoConfig().pixel_radius),
                              ('distance_tolerance', ServoConfig().distance_tolerance),
                              ('lock_time', ServoConfig().lock_time),
                              ('trace_file', '')):
            self.declare_parameter(name, default)
        trace = str(self.get_parameter('trace_file').value)
        self._trace = open(trace, 'w') if trace else None      # debugging aid, off by default
        self.cfg = ServoConfig(
            distance=float(self.get_parameter('distance').value),
            pixel_radius=float(self.get_parameter('pixel_radius').value),
            distance_tolerance=float(self.get_parameter('distance_tolerance').value),
            lock_time=float(self.get_parameter('lock_time').value))
        self.limits = PlatformLimits()
        self.detector = ColorDetector()
        self.tracker = Tracker(TRACKER_CONFIG)
        self.servo = Servo(self.cfg)
        self.lock = LockMonitor(self.cfg)
        self.mode = 'idle'
        self.intr: Optional[Intrinsics] = None
        self.joint_times: Deque[float] = deque(maxlen=4000)
        self.joint_q: Deque[Tuple[float, float, float]] = deque(maxlen=4000)
        self.rates = Command()
        self._joint_idx = None
        self.pending = {}
        self.pubs = [self.create_publisher(Float64, f'/platform/{j}/cmd_vel', 10) for j in JOINTS]
        self.status_pub = self.create_publisher(String, '/tracking/status', 10)
        self.create_subscription(String, '/tracking/mode', self._on_mode, 10)
        self.create_subscription(CameraInfo, '/camera/camera_info', self._on_info, 5)
        self.create_subscription(Image, '/camera/image', self._on_rgb, 5)
        self.create_subscription(Image, '/camera/depth_image', self._on_depth, 5)
        self.create_subscription(JointState, '/platform/joint_states', self._on_joints, 100)
        self.create_timer(0.05, self._home_tick)
        self.get_logger().info(
            'tracking_node ready (mode idle); send track|home|idle to /tracking/mode')

    # ---- inputs --------------------------------------------------------------------------------
    def _on_mode(self, msg: String) -> None:
        if msg.data not in MODES:
            self.get_logger().warning(f'unknown mode {msg.data!r}; expected one of {MODES}')
            return
        if msg.data != self.mode:
            self.get_logger().info(f'mode {self.mode} -> {msg.data}')
            self.mode = msg.data
            self.tracker.reset()
            self.servo.reset()
            self.lock.reset()
            if self.mode == 'idle':
                self._publish_rates(Command())

    def _on_info(self, msg: CameraInfo) -> None:
        if self.intr is None:
            self.intr = Intrinsics.from_camera_info_k(msg.k, msg.width, msg.height)

    def _on_joints(self, msg: JointState) -> None:
        if self._joint_idx is None:                    # the message layout never changes: look once
            names = list(msg.name)
            if not all(j in names for j in JOINTS) or not msg.position:
                return
            self._joint_idx = tuple(names.index(j) for j in JOINTS)
        i0, i1, i2 = self._joint_idx
        pos = msg.position
        self.joint_times.append(msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9)
        self.joint_q.append((pos[i0], pos[i1], pos[i2]))
        vel = msg.velocity
        if len(vel) == len(msg.name):
            self.rates = Command(yaw_rate=vel[i0], slide_rate=vel[i1], pitch_rate=vel[i2])

    def _on_rgb(self, msg: Image) -> None:
        data = np.frombuffer(bytes(msg.data), np.uint8).reshape(msg.height, msg.width, 3)
        self._store(stamp_s(msg), 'rgb', data)

    def _on_depth(self, msg: Image) -> None:
        data = np.frombuffer(bytes(msg.data), np.float32).reshape(msg.height, msg.width)
        self._store(stamp_s(msg), 'depth', data)

    def _store(self, stamp: float, key: str, data: np.ndarray) -> None:
        slot = self.pending.setdefault(round(stamp, 4), {})
        slot[key] = data
        if 'rgb' in slot and 'depth' in slot:
            del self.pending[round(stamp, 4)]
            for old in [s for s in self.pending if s < stamp - 1.0]:
                del self.pending[old]
            self._process(stamp, slot['rgb'], slot['depth'])

    # ---- helpers -------------------------------------------------------------------------------
    def _q_at(self, stamp: float) -> Optional[Tuple[float, float, float]]:
        """Joint positions at ``stamp`` (linear interpolation of the 1 kHz joint states)."""
        if not self.joint_times:
            return None
        times = self.joint_times
        i = bisect.bisect_left(times, stamp)
        if i <= 0:
            return self.joint_q[0]
        if i >= len(times):
            return self.joint_q[-1]
        t0, t1 = times[i - 1], times[i]
        w = 0.0 if t1 <= t0 else (stamp - t0) / (t1 - t0)
        a, b = self.joint_q[i - 1], self.joint_q[i]
        return tuple(a[k] + w * (b[k] - a[k]) for k in range(3))

    def _publish_rates(self, cmd: Command) -> None:
        lim = self.limits
        values = (clamp(cmd.yaw_rate, -lim.yaw_rate, lim.yaw_rate),
                  clamp(cmd.slide_rate, -lim.slide_rate, lim.slide_rate),
                  clamp(cmd.pitch_rate, -lim.pitch_rate, lim.pitch_rate))
        for pub, v in zip(self.pubs, values):
            pub.publish(Float64(data=float(v)))

    def _status(self, **fields) -> None:
        self.status_pub.publish(String(data=json.dumps(fields)))

    # ---- tracking ------------------------------------------------------------------------------
    def _process(self, stamp: float, rgb: np.ndarray, depth: np.ndarray) -> None:
        if self.mode != 'track' or self.intr is None or not self.joint_q:
            return
        q_img = self._q_at(stamp)
        frame = frame_in_camera_frame(Frame(rgb, depth, self.intr,
                                            optical_in_world(q_img, CAMERA), stamp))
        found = red_target(frame, self.detector)
        obs: List[Observation] = []
        pixel_err = dist_err = None
        if found is not None:
            seg, pts = found
            centroid = pts.mean(axis=0)
            world = optical_in_world(q_img, CAMERA).apply(centroid.reshape(1, 3))[0]
            obs.append(Observation('red', tuple(float(v) for v in world)))
            u, v = seg.centroid
            pixel_err = float(np.hypot(u + 0.5 - self.intr.width / 2.0,
                                       v + 0.5 - self.intr.height / 2.0))
            dist_err = float(centroid[2] - self.cfg.distance)
        tracks = self.tracker.update(obs, stamp)
        q_now = self.joint_q[-1]
        optical = feed_forward = None
        track_state = tracks[0].state if tracks else 'none'
        if tracks and tracks[0].time_since_seen <= self.cfg.coast_time:
            tr = tracks[0]
            optical = tuple(float(v) for v in world_to_optical(q_now, CAMERA, tr.position))
            pose = camera_pose(q_now)
            feed_forward = line_of_sight_rates(
                pose.position, camera_velocity(q_now, self.rates),
                np.array(pose.direction_to_world((1.0, 0.0, 0.0))), tr.position, tr.velocity)
        dt = 0.1 if self._last_dt is None else max(1e-3, stamp - self._last_stamp)
        self._last_stamp, self._last_dt = stamp, dt
        cmd = self.servo.command(None, stamp, dt, optical_point=optical,
                                 feed_forward=feed_forward)
        self._publish_rates(cmd)
        if self._trace:
            self._trace.write(f'{stamp:.3f},{self.joint_times[-1]:.3f},'
                              f'{self.get_clock().now().nanoseconds * 1e-9:.3f},{q_img[1]:.3f},'
                              f'{q_now[1]:.3f},{self.rates.slide_rate:.3f},{cmd.slide_rate:.3f},{time.time():.3f},{cmd.yaw_rate:.3f},'
                              f'{cmd.pitch_rate:.3f}\n')
            self._trace.flush()
        state = self.lock.update(stamp, pixel_err, dist_err)
        latency = self.get_clock().now().nanoseconds * 1e-9 - stamp
        self._status(mode='track', stamp=stamp, visible=found is not None, pixel_error=pixel_err,
                     distance_error=dist_err, in_window=state.in_window, locked=state.locked,
                     timer=state.timer, track=track_state, q=list(q_img), latency=latency,
                     cmd=[cmd.yaw_rate, cmd.slide_rate, cmd.pitch_rate])

    _last_stamp = 0.0
    _last_dt: Optional[float] = None

    # ---- homing --------------------------------------------------------------------------------
    def _home_tick(self) -> None:
        if self.mode != 'home' or not self.joint_q:
            return
        q = self.joint_q[-1]
        err = [HOME[i] - q[i] for i in range(3)]
        cmd = Command(yaw_rate=2.0 * err[0], slide_rate=2.0 * err[1], pitch_rate=2.0 * err[2])
        homed = all(abs(err[i]) < HOME_TOLERANCE[i] for i in range(3))
        if homed:
            cmd = Command()
        self._publish_rates(cmd)
        self._status(mode='home', homed=homed, q=list(q),
                     stamp=self.get_clock().now().nanoseconds * 1e-9)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = TrackingNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        try:
            node._publish_rates(Command())    # never leave the joints moving
        except Exception:                      # noqa: BLE001 - context may already be gone
            pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
