"""Closed-loop tracking simulation without Gazebo: the synthetic renderer produces the camera
images, the real detector / tracker / servo / lock code runs on them, and a kinematic platform
model moves the camera. It exists to tune the control law and to unit-test it; the Gazebo
selftest (``robot_vision_ros``) repeats the same scenarios on the real simulator.
``python -m robot_vision.tracking_sim --out DIR`` writes a table and plots.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from robot_core.perception import Transform
from robot_core.workspace import CameraSpec, Pose, Shape

from robot_vision.detectors import ColorDetector
from robot_vision.frame import Frame, Segment
from robot_vision.geometry import segment_points
from robot_vision.platform import (
    HOME,
    SimPlatform,
    camera_pose,
    camera_velocity,
    optical_in_world,
    world_to_optical,
)
from robot_vision.servo import (
    Command,
    LockMonitor,
    Metrics,
    Sample,
    Servo,
    ServoConfig,
    line_of_sight_rates,
    summarize,
)
from robot_vision.synth import render
from robot_vision.tracker import Observation, Tracker, TrackerConfig
from robot_vision.trajectories import HIDDEN, SCENARIOS, Scenario

TARGET_RADIUS = 0.10
TARGET_COLOR = (230.0, 26.0, 26.0)
CAMERA = CameraSpec(pose=Pose(0, 0, 1.0), horizontal_fov=1.047, width=320, height=240,
                    near=0.1, far=8.0, frame_id='camera_link')
_OPTICAL = Transform(np.eye(3), (0.0, 0.0, 0.0))     # observations stay in the camera frame
TRACKER_CONFIG = TrackerConfig(gate=0.4, reacquire_gate=1.0, confirm_hits=2, max_coast_s=0.3,
                               max_lost_s=3.0, meas_sigma=0.01, accel_sigma=1.0)


def red_target(frame: Frame, detector: ColorDetector) -> Optional[Tuple[Segment, np.ndarray]]:
    """Largest red segment and its optical-frame points (None if nothing usable)."""
    segs = [s for s in detector.detect(frame) if s.label == 'red']
    if not segs:
        return None
    seg = max(segs, key=lambda s: s.pixels)
    pts = segment_points(frame, seg)       # identity transform: points stay in the optical frame
    return (seg, pts) if len(pts) >= 10 else None


def frame_in_camera_frame(frame: Frame) -> Frame:
    """The same image with an identity pose, so geometry works in the optical frame."""
    return Frame(frame.rgb, frame.depth, frame.intrinsics, _OPTICAL, frame.stamp)


@dataclass
class SimResult:
    metrics: Metrics
    samples: List[Sample]
    trace: dict           # arrays for plotting: t, q (n,3), target (n,3), pixel_error, distance


def run_scenario(sc: Scenario, config: Optional[ServoConfig] = None, *, dt: float = 0.1,
                 delay_frames: int = 1, resolution: float = 1.0, start_q=HOME,
                 seed: int = 0) -> SimResult:
    config = config or ServoConfig()
    cam = replace(CAMERA, width=int(CAMERA.width * resolution),
                  height=int(CAMERA.height * resolution))
    detector = ColorDetector(min_pixels=int(30 * resolution ** 2),
                             min_valid=int(10 * resolution ** 2))
    tracker, servo, lock = Tracker(TRACKER_CONFIG), Servo(config), LockMonitor(config)
    platform = SimPlatform(start_q)
    pending: List[Command] = []
    samples: List[Sample] = []
    trace = {'t': [], 'q': [], 'target': [], 'pixel_error': [], 'distance_error': [], 'locked': []}
    centre = (cam.width / 2.0, cam.height / 2.0)
    steps = int(round(sc.duration / dt))
    for k in range(steps + 1):
        t = k * dt
        pos = sc.position(t)
        target = Shape('target', 'sphere', pos if pos is not None else HIDDEN, (TARGET_RADIUS,),
                       None)
        frame_cam = replace(cam, pose=camera_pose(platform.q))
        raw = render(frame_cam, [] if pos is None else [target], colors={'target': TARGET_COLOR},
                     stamp=t, seed=seed)
        frame = frame_in_camera_frame(raw)
        found = red_target(frame, detector)
        observation = None
        pixel_err = dist_err = None
        if found is not None:
            seg, pts = found
            centroid = pts.mean(axis=0)
            to_world = optical_in_world(platform.q, cam)
            observation = Observation('red', tuple(float(v) for v in to_world.apply(
                centroid.reshape(1, 3))[0]))
            u, v = seg.centroid
            pixel_err = math.hypot(u + 0.5 - centre[0], v + 0.5 - centre[1]) / resolution
            dist_err = float(centroid[2] - config.distance)
        tracks = tracker.update([observation] if observation else [], t)
        optical = feed_forward = None
        if tracks and tracks[0].time_since_seen <= config.coast_time:
            tr = tracks[0]
            optical = tuple(float(v) for v in world_to_optical(platform.q, cam, tr.position))
            pose = camera_pose(platform.q)
            feed_forward = line_of_sight_rates(
                pose.position, camera_velocity(platform.q, platform.rates),
                np.array(pose.direction_to_world((1.0, 0.0, 0.0))), tr.position, tr.velocity)
        cmd = servo.command(None, t, dt, optical_point=optical, feed_forward=feed_forward)
        state = lock.update(t, pixel_err, dist_err)
        pending.append(cmd)
        applied = pending.pop(0) if len(pending) > delay_frames else Command()
        platform.step(applied, dt)
        truth_px = truth_pixel_error(frame_cam, pos) if pos is not None else None
        samples.append(Sample(t=t, visible=found is not None, pixel_error=pixel_err,
                              distance_error=dist_err, in_window=state.in_window,
                              locked=state.locked, latency=delay_frames * dt,
                              truth_pixel_error=(None if truth_px is None
                                                 else truth_px / resolution)))
        trace['t'].append(t)
        trace['q'].append(platform.q)
        trace['target'].append(pos if pos is not None else (math.nan,) * 3)
        trace['pixel_error'].append(math.nan if pixel_err is None else pixel_err)
        trace['distance_error'].append(math.nan if dist_err is None else dist_err)
        trace['locked'].append(state.locked)
    return SimResult(summarize(sc.name, samples, disturbance=sc.disturbance), samples,
                     {k: np.array(v) for k, v in trace.items()})


def truth_pixel_error(cam: CameraSpec, pos) -> Optional[float]:
    """Pixel distance between the image centre and the true centre of the target."""
    from robot_core.workspace import project

    class _Ws:
        camera = cam
    hit = project(_Ws, pos)               # type: ignore[arg-type]
    if hit is None:
        return None
    return math.hypot(hit[0] + 0.5 - cam.width / 2.0, hit[1] + 0.5 - cam.height / 2.0)


# ---- report ------------------------------------------------------------------------------------
def verdict(m: Metrics, sc: Scenario) -> List[str]:
    """Reasons a scenario fails its thresholds (empty list = pass)."""
    why = []
    if m.acquisition_time is None:
        return ['hiç kilitlenmedi']
    if m.acquisition_time > sc.max_acquisition:
        why.append(f'yakalama {m.acquisition_time:.1f} s > {sc.max_acquisition:g} s')
    if m.lock_hold_ratio < sc.min_hold_ratio:
        why.append(f'kilit tutma {m.lock_hold_ratio:.2f} < {sc.min_hold_ratio:g}')
    if sc.disturbance is not None and (m.relock_time is None or m.relock_time > sc.max_relock):
        why.append('yeniden kilit ' + ('yok' if m.relock_time is None else f'{m.relock_time:.1f} s')
                   + f' > {sc.max_relock:g} s')
    if m.loss_events > sc.max_loss_events:
        why.append(f'{m.loss_events} kayıp > beklenen {sc.max_loss_events}')
    return why


def to_markdown(results: List[SimResult], title: str) -> str:
    out = [f'# {title}', '',
           '| Senaryo | Yakalama (s) | Kilit tutma | Pencerede (tüm kare) | RMS piksel '
           '| RMS mesafe (m) | Kayıp | Gecikme (s) | Sonuç |',
           '|---|---|---|---|---|---|---|---|---|']
    for r in results:
        m, sc = r.metrics, SCENARIOS[r.metrics.scenario]
        why = verdict(m, sc)
        acq = '-' if m.acquisition_time is None else f'{m.acquisition_time:.1f}'
        lat = '-' if m.median_latency is None else f'{m.median_latency:.2f}'
        out.append(f'| {m.scenario} | {acq} | {m.lock_hold_ratio:.2f} | {m.in_window_ratio:.2f} | '
                   f'{m.rms_pixel_error:.1f} | {m.rms_distance_error:.3f} | {m.loss_events} | '
                   f'{lat} | '
                   f'{"✅" if not why else "❌ " + "; ".join(why)} |')
    return '\n'.join(out) + '\n'


def plot(results: List[SimResult], path: Path, title: str,
         config: Optional[ServoConfig] = None) -> bool:
    config = config or ServoConfig()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, axes = plt.subplots(3, len(results), figsize=(3.6 * len(results), 7.5), squeeze=False)
    for col, r in enumerate(results):
        t = r.trace['t']
        ax = axes[0][col]
        ax.plot(t, r.trace['pixel_error'], color='#4C78A8', lw=1)
        ax.axhline(config.pixel_radius, color='#D62728', lw=0.8, ls='--')
        ax.set_title(r.metrics.scenario, fontsize=10)
        ax.set_ylabel('piksel hatası (px)' if col == 0 else '')
        ax.set_ylim(0, 80)
        ax = axes[1][col]
        ax.plot(t, r.trace['distance_error'], color='#F58518', lw=1)
        ax.axhline(config.distance_tolerance, color='#D62728', lw=0.8, ls='--')
        ax.axhline(-config.distance_tolerance, color='#D62728', lw=0.8, ls='--')
        ax.set_ylabel('mesafe hatası (m)' if col == 0 else '')
        ax.set_ylim(-1.0, 1.5)
        ax = axes[2][col]
        ax.fill_between(t, 0, np.asarray(r.trace['locked'], dtype=float), color='#54A24B',
                        alpha=0.5, step='post')
        ax.set_ylabel('kilit' if col == 0 else '')
        ax.set_xlabel('t (s)')
        ax.set_ylim(0, 1.1)
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return True


def results_to_json(results: List[SimResult]) -> list:
    """Metrics + the plotted traces, so a report can be rebuilt without the simulator."""
    def clean(a):
        return [None if (isinstance(v, float) and math.isnan(v)) else v
                for v in np.asarray(a).tolist()]
    return [{'metrics': {k: getattr(r.metrics, k) for k in r.metrics.__dataclass_fields__},
             'trace': {k: clean(r.trace[k]) for k in ('t', 'pixel_error', 'distance_error',
                                                      'locked')}} for r in results]


def results_from_json(data: list) -> List[SimResult]:
    out = []
    for item in data:
        tr = item['trace']
        trace = {'t': np.array(tr['t'], dtype=float),
                 'pixel_error': np.array([np.nan if v is None else v for v in tr['pixel_error']]),
                 'distance_error': np.array([np.nan if v is None else v
                                             for v in tr['distance_error']]),
                 'locked': np.array(tr['locked'], dtype=bool)}
        out.append(SimResult(Metrics(**item['metrics']), [], trace))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', type=Path, default=Path('.'))
    ap.add_argument('--resolution', type=float, default=0.5, help='kamera çözünürlük çarpanı')
    ap.add_argument('--from-json', type=Path, default=None,
                    help='Gazebo selftest\'inin takip_gazebo.json dosyasından rapor + grafik üret')
    args = ap.parse_args(argv)
    if args.from_json is not None:
        import json
        results = results_from_json(json.loads(args.from_json.read_text(encoding='utf-8')))
        title = 'Takip selftest (Gazebo, tracking.sdf, yazılım görüntüleme)'
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / 'takip_gazebo.md').write_text(to_markdown(results, title), encoding='utf-8')
        plot(results, args.out / 'takip_gazebo.png', title)
        return 0
    results = [run_scenario(sc, resolution=args.resolution) for sc in SCENARIOS.values()]
    title = 'Takip simülasyonu (Gazebo\'suz, sentetik görüntü + gerçek servo/izleyici kodu)'
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'takip_sim.md').write_text(to_markdown(results, title), encoding='utf-8')
    plot(results, args.out / 'takip_sim.png', title)
    bad = [(r.metrics.scenario, verdict(r.metrics, SCENARIOS[r.metrics.scenario])) for r in results]
    for name, why in bad:
        print(f'{name}: {"geçti" if not why else "KALDI " + "; ".join(why)}')
    return 1 if any(why for _, why in bad) else 0


if __name__ == '__main__':
    sys.exit(main())
