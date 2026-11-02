"""Tracking selftest on the real simulator: runs the scripted target scenarios against the
camera platform, measures lock behaviour and judges it with the same thresholds as the
closed-loop unit simulation (``robot_vision.tracking_sim``).

    ros2 run robot_vision_ros tracking_check [--out DIR] [--only linear,sine,abrupt,blackout]

Needs ``ros2 launch robot_vision_ros tracking.launch.py`` running. For each scenario: the platform
is driven home, the target is placed at the scenario's start, ``/tracking/mode`` -> track, the
target then follows its deterministic trajectory through ``/world/tracking/set_pose`` (sim time)
while the node's ``/tracking/status`` is recorded. Exit status 1 if any scenario misses its
thresholds. Writes ``takip_gazebo.md`` / ``.json`` / ``.png`` to ``--out`` (default /tmp/tracking).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

import numpy as np
import rclpy
from geometry_msgs.msg import Pose
from rclpy.node import Node
from rclpy.parameter import Parameter
from robot_vision.platform import camera_pose
from robot_vision.servo import Sample, summarize
from robot_vision.tracking_sim import (
    CAMERA,
    SCENARIOS,
    SimResult,
    plot,
    results_to_json,
    to_markdown,
    truth_pixel_error,
    verdict,
)
from robot_vision.trajectories import HIDDEN, Scenario
from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import SetEntityPose
from std_msgs.msg import String

HOME_SETTLE = 1.0           # s of 'homed' reports in a row before a scenario starts
HOME_TIMEOUT = 60.0         # sim s


class Orchestrator(Node):
    def __init__(self, scenarios: List[Scenario]):
        super().__init__('tracking_check', parameter_overrides=[
            Parameter('use_sim_time', Parameter.Type.BOOL, True)])    # everything runs on /clock
        self.scenarios = scenarios
        self.results: List[SimResult] = []
        self.failures: List[str] = []
        self.idx = 0
        self.phase = 'init'
        self.phase_start = 0.0
        self.t0 = 0.0
        self.samples: List[Sample] = []
        self.homed_since: Optional[float] = None
        self.last_status: Optional[dict] = None
        self.outstanding = 0
        self.pose_failures = 0
        self.done = False
        self.mode_pub = self.create_publisher(String, '/tracking/mode', 10)
        self.client = self.create_client(SetEntityPose, '/world/tracking/set_pose')
        self.create_subscription(String, '/tracking/status', self._on_status, 100)
        self.create_timer(0.05, self._tick)

    # ---- helpers -------------------------------------------------------------------------------
    def now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def set_mode(self, mode: str) -> None:
        self.mode_pub.publish(String(data=mode))

    def set_target(self, pos) -> None:
        if self.outstanding > 4:
            return                                    # bridge busy: skip this update, never queue
        req = SetEntityPose.Request()
        req.entity.name = 'target'
        req.entity.type = Entity.MODEL
        p = HIDDEN if pos is None else pos
        req.pose = Pose()
        req.pose.position.x, req.pose.position.y, req.pose.position.z = (float(v) for v in p)
        req.pose.orientation.w = 1.0
        self.outstanding += 1
        future = self.client.call_async(req)
        future.add_done_callback(self._pose_done)

    def _pose_done(self, future) -> None:
        self.outstanding -= 1
        try:
            if not future.result().success:
                self.pose_failures += 1
        except Exception:                              # noqa: BLE001
            self.pose_failures += 1

    # ---- status stream -------------------------------------------------------------------------
    def _on_status(self, msg: String) -> None:
        st = json.loads(msg.data)
        self.last_status = st
        sc = self.scenarios[self.idx] if self.idx < len(self.scenarios) else None
        if self.phase == 'home' and st.get('mode') == 'home':
            now = self.now()
            if st.get('homed'):
                self.homed_since = self.homed_since if self.homed_since is not None else now
            else:
                self.homed_since = None
        elif self.phase == 'run' and st.get('mode') == 'track' and sc is not None:
            st['latency'] = self.now() - st['stamp']     # image stamp -> status seen here (sim s)
            t = st['stamp'] - self.t0
            if t < 0.0:
                return
            pos = sc.position(t)
            truth = None
            if pos is not None:
                cam = replace(CAMERA, pose=camera_pose(tuple(st['q'])))
                truth = truth_pixel_error(cam, pos)
            self.samples.append(Sample(
                t=t, visible=bool(st['visible']), pixel_error=st['pixel_error'],
                distance_error=st['distance_error'], in_window=bool(st['in_window']),
                locked=bool(st['locked']), latency=st.get('latency'), truth_pixel_error=truth))

    # ---- state machine -------------------------------------------------------------------------
    def _tick(self) -> None:
        if self.done:
            return
        now = self.now()
        if self.idx >= len(self.scenarios):
            self.set_mode('idle')
            self.done = True
            return
        sc = self.scenarios[self.idx]
        if self.phase == 'init':
            if not self.client.service_is_ready() or now <= 0.0:
                return
            self._begin_home(sc, now)
        elif self.phase == 'home':
            self.set_mode('home')
            self.set_target(sc.position(0.0))
            if self.homed_since is not None and now - self.homed_since >= HOME_SETTLE:
                self.set_mode('track')
                self.t0 = now
                self.samples = []
                self.phase = 'run'
                self.get_logger().info(f'[{sc.name}] tracking starts (sim t={now:.1f})')
            elif now - self.phase_start > HOME_TIMEOUT:
                self.failures.append(
                    f'{sc.name}: platform did not reach home in {HOME_TIMEOUT:g} s')
                self._finish(sc, now, ok=False)
        elif self.phase == 'run':
            t = now - self.t0
            self.set_target(sc.position(t))
            if t >= sc.duration:
                self._finish(sc, now, ok=True)

    def _begin_home(self, sc: Scenario, now: float) -> None:
        self.phase, self.phase_start, self.homed_since = 'home', now, None
        self.get_logger().info(f'[{sc.name}] homing platform ({sc.description})')

    def _finish(self, sc: Scenario, now: float, ok: bool) -> None:
        self.set_mode('idle')
        if ok and self.samples:
            metrics = summarize(sc.name, self.samples, disturbance=sc.disturbance)
            samples = self.samples
            trace = {'t': np.array([s.t for s in samples]),
                     'pixel_error': np.array([np.nan if s.pixel_error is None else s.pixel_error
                                              for s in samples]),
                     'distance_error': np.array([np.nan if s.distance_error is None
                                                 else s.distance_error for s in samples]),
                     'locked': np.array([s.locked for s in samples])}
            self.results.append(SimResult(metrics, samples, trace))
            why = verdict(metrics, sc)
            outcome = 'OK' if not why else 'FAILED: ' + '; '.join(why)
            self.get_logger().info(f'[{sc.name}] {outcome}')
            self.failures += [f'{sc.name}: {w}' for w in why]
        elif ok:
            self.failures.append(f'{sc.name}: no tracking status received')
        self.idx += 1
        if self.idx < len(self.scenarios):
            self._begin_home(self.scenarios[self.idx], now)


def write_reports(results: List[SimResult], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    title = 'Takip selftest (Gazebo, tracking.sdf, yazılım görüntüleme)'
    (out / 'takip_gazebo.md').write_text(to_markdown(results, title), encoding='utf-8')
    (out / 'takip_gazebo.json').write_text(json.dumps(results_to_json(results)),
                                           encoding='utf-8')
    plot(results, out / 'takip_gazebo.png', title)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path, default=Path('/tmp/tracking'))
    ap.add_argument('--only', default='', help='virgülle ayrılmış senaryo adları')
    args = ap.parse_args(argv)
    names = [n for n in args.only.split(',') if n] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f'bilinmeyen senaryo: {unknown}; var olanlar: {list(SCENARIOS)}')
        return 64
    rclpy.init()
    node = Orchestrator([SCENARIOS[n] for n in names])
    wall_start = time.time()
    try:
        while rclpy.ok() and not node.done and time.time() - wall_start < 3000:
            rclpy.spin_once(node, timeout_sec=0.1)
    finally:
        node.set_mode('idle')
        node.destroy_node()
        rclpy.shutdown()
    if not node.done:
        print('[!] selftest bitmeden zaman aşımı')
        return 2
    write_reports(node.results, args.out)
    print(to_markdown(node.results, 'Takip selftest (Gazebo)'))
    if node.pose_failures:
        print(f'[uyarı] {node.pose_failures} set_pose çağrısı başarısız oldu')
    for f in node.failures:
        print(f'KALDI: {f}')
    return 1 if node.failures else 0


if __name__ == '__main__':
    sys.exit(main())
