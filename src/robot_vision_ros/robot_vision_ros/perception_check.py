"""H6 check on the real simulator: does a STATIC object keep its world position while the camera
platform pans / tilts / slides?

    ros2 launch robot_vision_ros tracking.launch.py headless:=true manual:=true   # terminal 1
    ros2 run robot_vision_ros perception_check [--out DIR] [--tol 0.01]            # terminal 2

The red ball is put at a known world position (``/world/tracking/set_pose``), the platform is driven
to several poses with ``camera_manual`` (``goto``), and at every pose (after the platform has
settled) the ball is measured from the live frame in the world frame (sphere fit on the red
segment's points) with the pose of EACH frame from the joint states (``RosFrameSource``
``camera='platform'``) and, for contrast, with the old fixed workspace pose (``camera='fixed'``).
Exit status 1 if any platform-pose error or the drift between poses exceeds ``--tol`` (1 cm).
Writes ``perception_h6.json`` / ``.md`` to ``--out`` (default /tmp/h6).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

import numpy as np
import rclpy
from geometry_msgs.msg import Pose
from rclpy.node import Node
from rclpy.parameter import Parameter
from robot_vision.detectors import ColorDetector
from robot_vision.geometry import fit_sphere, segment_points
from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import SetEntityPose
from std_msgs.msg import String

from robot_vision_ros.ros_frame_source import RosFrameSource

TRUTH = (2.6, -0.5, 0.95)           # world position of the ball (m); radius 0.10 in tracking.sdf
POSES = [(0.0, 0.1), (-0.5, 0.4), (-0.3, 0.2), (-0.15, 0.0), (0.0, -0.2), (0.2, 0.0)]
SLIDES = [0.0, 0.0, 0.6, 1.2, 1.8, 0.6]          # platform position per pose (m)
SETTLE_S = 1.5                                    # sim s the platform must rest before a frame


def world_center(frame) -> Optional[np.ndarray]:
    segs = [s for s in ColorDetector().detect(frame) if s.label == 'red']
    if not segs:
        return None
    pts = segment_points(frame, max(segs, key=lambda s: s.pixels))
    return fit_sphere(pts)[0] if len(pts) >= 30 else None


class Check(Node):
    def __init__(self):
        super().__init__('perception_check', parameter_overrides=[
            Parameter('use_sim_time', Parameter.Type.BOOL, True)])
        self.cmd = self.create_publisher(String, '/camera/manual/command', 10)
        self.client = self.create_client(SetEntityPose, '/world/tracking/set_pose')
        self.status: Optional[dict] = None
        self.create_subscription(String, '/camera/manual/status', self._on_status, 10)

    def _on_status(self, msg: String) -> None:
        self.status = json.loads(msg.data)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def spin_for(self, seconds: float) -> None:               # simulated seconds
        end = self.now() + seconds
        while self.now() < end and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)

    def place_target(self) -> bool:
        if not self.client.wait_for_service(timeout_sec=30.0):
            return False
        req = SetEntityPose.Request()
        req.entity.name, req.entity.type = 'target', Entity.MODEL
        req.pose = Pose()
        req.pose.position.x, req.pose.position.y, req.pose.position.z = TRUTH
        req.pose.orientation.w = 1.0
        fut = self.client.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=10.0)
        return bool(fut.done() and fut.result().success)

    def goto(self, pan: float, tilt: float, slide: float, timeout: float = 90.0) -> Optional[dict]:
        self.status = None
        self.cmd.publish(String(data=json.dumps(
            {'op': 'goto', 'pan': pan, 'tilt': tilt, 'slide': slide})))
        end = self.now() + timeout
        while self.now() < end:
            rclpy.spin_once(self, timeout_sec=0.05)
            st = self.status
            if st and st['last'].get('code'):
                return st['last']                              # refused
            if st and st['last'].get('done') and st['state'] == 'idle':
                return st['last']
        return None


def newest_after(source: RosFrameSource, node: Check, t: float, timeout: float = 30.0):
    end = node.now() + timeout
    while node.now() < end:
        f = source.latest()
        if f is not None and f.stamp > t:
            return f
        rclpy.spin_once(node, timeout_sec=0.05)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default='/tmp/h6')
    ap.add_argument('--tol', type=float, default=0.01, help='allowed error / drift (m)')
    args = ap.parse_args(argv)
    rclpy.init()
    node = Check()
    platform_src = RosFrameSource(camera='platform')
    fixed_src = RosFrameSource(camera='fixed')
    truth = np.array(TRUTH)
    rows, ok = [], True
    if not node.place_target():
        print('[hata] hedef yerleştirilemedi (/world/tracking/set_pose)')
        return 1
    node.spin_for(1.0)
    for (pan, tilt), slide in zip(POSES, SLIDES):
        res = node.goto(pan, tilt, slide)
        if res is None or res.get('done') != 'reached':
            print(f'[hata] goto {pan, tilt, slide} bitmedi / reddedildi: {res} '
                  '(camera_manual çalışıyor mu? manual:=true)')
            ok = False
            continue
        node.spin_for(SETTLE_S)
        t_settled = node.now()
        fp = newest_after(platform_src, node, t_settled)
        ff = newest_after(fixed_src, node, t_settled)
        cp = None if fp is None else world_center(fp)
        cf = None if ff is None else world_center(ff)
        def err(c):
            return None if c is None else round(float(np.linalg.norm(c - truth)), 4)

        row = {'pan': pan, 'tilt': tilt, 'slide': slide,
               'platform_pose_center': None if cp is None else [round(float(v), 4) for v in cp],
               'platform_pose_error_m': err(cp), 'fixed_pose_error_m': err(cf)}
        rows.append(row)
    centers = np.array([r['platform_pose_center'] for r in rows if r['platform_pose_center']])
    missing = len(POSES) - len(centers)
    drift = (float(np.linalg.norm(centers[:, None] - centers[None], axis=2).max())
             if len(centers) > 1 else float('nan'))
    errors = [r['platform_pose_error_m'] for r in rows if r['platform_pose_error_m'] is not None]
    worst = max(errors, default=float('nan'))
    passed = ok and missing == 0 and worst <= args.tol and drift <= args.tol
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    doc = {'truth': TRUTH, 'tol_m': args.tol, 'rows': rows, 'worst_error_m': worst,
           'drift_m': drift, 'poses_without_measurement': missing, 'passed': bool(passed)}
    (out / 'perception_h6.json').write_text(json.dumps(doc, indent=2))
    lines = ['| pan | tilt | slide | platform-pose error (cm) | fixed-pose error (cm) |',
             '|---|---|---|---|---|']
    for r in rows:
        f = lambda v: 'yok' if v is None else f'{100 * v:.2f}'     # noqa: E731
        lines.append(f"| {r['pan']} | {r['tilt']} | {r['slide']} | {f(r['platform_pose_error_m'])} "
                     f"| {f(r['fixed_pose_error_m'])} |")
    lines.append(f'\nworst platform-pose error {100 * worst:.2f} cm, drift between poses '
                 f'{100 * drift:.2f} cm, tolerance {100 * args.tol:.1f} cm -> '
                 f'{"GEÇTİ" if passed else "KALDI"}')
    (out / 'perception_h6.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    rclpy.shutdown()
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
