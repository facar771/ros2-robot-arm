"""Visual servo: image error -> yaw / pitch rates, depth error -> forward/back rate, plus the
lock criterion and the tracking metrics. Pure Python, no ROS: the same code runs in the Gazebo node
and in the closed-loop simulation used by the unit tests.

Conventions (camera optical frame: x right, y down, z forward = depth)
- ``yaw`` turns the platform left (counter-clockwise about world z): a target on the right
  (X > 0) needs a NEGATIVE yaw rate. ``pitch`` positive tilts the camera DOWN: a target below
  the centre (Y > 0) needs a POSITIVE pitch rate. ``slide`` positive moves the camera forward along
  its heading: a target farther than the set distance (Z > d*) needs a POSITIVE slide rate.
- Errors are angles / metres, so the gains do not depend on the image size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class PidGains:
    kp: float
    ki: float = 0.0
    kd: float = 0.0
    i_limit: float = 0.5           # |integral term| cap (output units: rate)
    out_limit: float = 1.0         # |output| cap (rate limit of the actuator)


class Pid:
    """PID with a clamped integral (anti-windup), conditional integration while the output is
    saturated, and a first-order filtered derivative. ``reset`` clears memory (after a loss)."""

    def __init__(self, gains: PidGains, d_filter: float = 0.05):
        self.g = gains
        self.d_filter = d_filter
        self.reset()

    def reset(self) -> None:
        self._i = 0.0
        self._prev: Optional[float] = None
        self._d = 0.0

    def update(self, error: float, dt: float) -> float:
        g = self.g
        if dt <= 0.0:
            return clamp(g.kp * error + self._i + g.kd * self._d, -g.out_limit, g.out_limit)
        if self._prev is not None:
            raw = (error - self._prev) / dt
            a = dt / (self.d_filter + dt)
            self._d += a * (raw - self._d)
        self._prev = error
        out = g.kp * error + self._i + g.kd * self._d
        if abs(out) < g.out_limit or (out > 0) != (error > 0):    # integrate unless pushing further
            self._i = clamp(self._i + g.ki * error * dt, -g.i_limit, g.i_limit)
            out = g.kp * error + self._i + g.kd * self._d
        return clamp(out, -g.out_limit, g.out_limit)


@dataclass(frozen=True)
class ServoConfig:
    # set point
    distance: float = 2.0            # m, wanted z-depth of the target's visible surface
    # lock criterion
    pixel_radius: float = 12.0       # px, |image error| must stay below this
    distance_tolerance: float = 0.15  # m
    lock_time: float = 4.5           # s of uninterrupted in-window detections
    gap_tolerance: float = 0.35      # s without a detection that does NOT break the lock timer
    # controller
    yaw: PidGains = PidGains(kp=3.0, ki=1.5, kd=0.15, i_limit=0.6, out_limit=1.5)
    pitch: PidGains = PidGains(kp=3.0, ki=1.5, kd=0.15, i_limit=0.5, out_limit=1.0)
    slide: PidGains = PidGains(kp=4.0, ki=0.3, kd=0.1, i_limit=0.3, out_limit=0.8)
    coast_time: float = 0.4          # s: keep following the tracker's prediction after a loss
    feedforward: float = 0.8         # 0..1: share of the line-of-sight rate fed forward
    search_rate: float = 0.0         # rad/s yaw sweep while lost for long (0 = hold still)


@dataclass(frozen=True)
class Command:
    yaw_rate: float = 0.0            # rad/s
    pitch_rate: float = 0.0          # rad/s
    slide_rate: float = 0.0          # m/s


class Servo:
    """Turns the target's position in the camera optical frame into platform rate commands."""

    def __init__(self, config: Optional[ServoConfig] = None):
        config = config or ServoConfig()
        self.cfg = config
        self._yaw, self._pitch, self._slide = Pid(config.yaw), Pid(config.pitch), Pid(config.slide)
        self._last_seen: Optional[float] = None

    def reset(self) -> None:
        for p in (self._yaw, self._pitch, self._slide):
            p.reset()
        self._last_seen = None

    @staticmethod
    def errors(position: Tuple[float, float, float], distance: float) -> Tuple[float, float, float]:
        """(yaw error rad, pitch error rad, distance error m) for a target at ``position``."""
        x, y, z = position
        z = max(z, 1e-3)
        return -math.atan2(x, z), math.atan2(y, z), z - distance

    def command(self, target: Optional[Tuple[float, float, float]], stamp: float, dt: float,
                *, optical_point: Optional[Tuple[float, float, float]] = None,
                feed_forward: Optional[Command] = None) -> Command:
        """``optical_point``: the target in the camera optical frame *now* (None = nothing to
        follow: rates are zero, integrators are cleared after a long loss). ``feed_forward``: the
        rates that would keep the target's line of sight fixed (see :func:`line_of_sight_rates`);
        scaled by ``config.feedforward`` and added to the PID output."""
        del target     # kept for call-site readability; the optical point carries the geometry
        if optical_point is None:
            if self._last_seen is not None and stamp - self._last_seen > 1.0:
                self.reset()
            return Command()
        self._last_seen = stamp
        e_yaw, e_pitch, e_dist = self.errors(optical_point, self.cfg.distance)
        ff = feed_forward or Command()
        g = self.cfg.feedforward
        c = self.cfg
        return Command(
            yaw_rate=clamp(self._yaw.update(e_yaw, dt) + g * ff.yaw_rate,
                           -c.yaw.out_limit, c.yaw.out_limit),
            pitch_rate=clamp(self._pitch.update(e_pitch, dt) + g * ff.pitch_rate,
                             -c.pitch.out_limit, c.pitch.out_limit),
            slide_rate=clamp(self._slide.update(e_dist, dt) + g * ff.slide_rate,
                             -c.slide.out_limit, c.slide.out_limit))


def line_of_sight_rates(camera_position, camera_velocity, forward, target,
                        target_velocity) -> Command:
    """Rates of yaw / pitch / slide that keep the line of sight from the camera to a target fixed
    in the world (target velocity measured in the *world* frame, so it does not depend on how the
    camera itself is moving). ``forward``: unit vector of the camera x axis in the world."""
    rel = np.asarray(target, dtype=np.float64) - np.asarray(camera_position, dtype=np.float64)
    vrel = np.asarray(target_velocity, dtype=np.float64) - np.asarray(camera_velocity,
                                                                      dtype=np.float64)
    r2 = float(rel[0] ** 2 + rel[1] ** 2)
    if r2 < 1e-6:
        return Command()
    azimuth_rate = float((rel[0] * vrel[1] - rel[1] * vrel[0]) / r2)
    r_h = math.sqrt(r2)
    down, down_rate = -rel[2], -vrel[2]                       # positive = below the camera
    rh_rate = float((rel[0] * vrel[0] + rel[1] * vrel[1]) / r_h)
    elevation_rate = float((r_h * down_rate - down * rh_rate) / (r_h * r_h + down * down))
    return Command(yaw_rate=azimuth_rate, pitch_rate=elevation_rate,
                   slide_rate=float(np.dot(vrel, forward)))


@dataclass
class LockState:
    locked: bool
    in_window: bool                  # the latest detection satisfied the criterion
    timer: float                     # s of consecutive in-window detections so far
    pixel_error: Optional[float]
    distance_error: Optional[float]


class LockMonitor:
    """Lock = ``lock_time`` seconds of *uninterrupted* in-window detections.

    A detection is in-window when ``|pixel error| < pixel_radius`` and ``|distance error| <
    distance_tolerance``. A gap without detections up to ``gap_tolerance`` (up to two dropped
    frames at 10 Hz) neither counts nor breaks the timer; a longer gap or an out-of-window
    detection resets it to zero. The lock drops the moment the timer resets.
    """

    def __init__(self, config: Optional[ServoConfig] = None):
        self.cfg = config or ServoConfig()
        self.reset()

    def reset(self) -> None:
        self._start: Optional[float] = None
        self._last_detection: Optional[float] = None
        self._locked = False

    def update(self, stamp: float, pixel_error: Optional[float],
               distance_error: Optional[float]) -> LockState:
        c = self.cfg
        if pixel_error is None or distance_error is None:
            if self._last_detection is None or stamp - self._last_detection > c.gap_tolerance:
                self._start, self._locked = None, False
            return self._state(stamp, False, None, None)
        in_window = pixel_error < c.pixel_radius and abs(distance_error) < c.distance_tolerance
        gap = None if self._last_detection is None else stamp - self._last_detection
        self._last_detection = stamp
        if not in_window or (gap is not None and gap > c.gap_tolerance):
            self._start, self._locked = (stamp, False) if in_window else (None, False)
        elif self._start is None:
            self._start = stamp
        if self._start is not None and stamp - self._start >= c.lock_time:
            self._locked = True
        return self._state(stamp, in_window, pixel_error, distance_error)

    def _state(self, stamp, in_window, pe, de) -> LockState:
        timer = 0.0 if self._start is None else stamp - self._start
        return LockState(self._locked, in_window, timer, pe, de)


@dataclass(frozen=True)
class Sample:
    t: float                         # seconds since the scenario started
    visible: bool
    pixel_error: Optional[float]
    distance_error: Optional[float]
    in_window: bool
    locked: bool
    latency: Optional[float] = None  # s from image stamp to the command that used it
    truth_pixel_error: Optional[float] = None
    truth_distance_error: Optional[float] = None


@dataclass(frozen=True)
class Metrics:
    scenario: str
    duration: float
    acquisition_time: Optional[float]      # s until the first lock (None = never locked)
    lock_hold_ratio: float                 # share of the time after first lock spent locked
    in_window_ratio: float                 # share of frames in the criterion window (whole run)
    rms_pixel_error: float                 # px, detections after acquisition (visible only)
    rms_distance_error: float              # m
    loss_events: int                       # visible -> not visible gaps longer than ``loss_gap``
    median_latency: Optional[float]        # s
    rms_truth_pixel_error: Optional[float]
    rms_truth_distance_error: Optional[float]
    relock_time: Optional[float] = None    # s disturbance -> next lock (0 = never lost)


def _rms(values: Sequence[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else float('nan')


def _rms_or_none(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return _rms(vals) if vals else None


def summarize(scenario: str, samples: List[Sample], loss_gap: float = 0.3,
              disturbance: Optional[float] = None) -> Metrics:
    """Reduce a run's samples to the numbers the report and the selftest judge."""
    if not samples:
        raise ValueError('no samples')
    first_lock = next((s.t for s in samples if s.locked), None)
    after = [s for s in samples if first_lock is not None and s.t >= first_lock]
    hold = sum(s.locked for s in after) / len(after) if after else 0.0
    start = first_lock if first_lock is not None else samples[0].t
    seen = [s for s in samples if s.visible and s.t >= start and s.pixel_error is not None]
    truth = [s for s in seen if s.truth_pixel_error is not None]
    losses, gap_start = 0, None
    for s in samples:
        if not s.visible and gap_start is None:
            gap_start = s.t
        elif s.visible and gap_start is not None:
            losses += (s.t - gap_start) > loss_gap
            gap_start = None
    if gap_start is not None and samples[-1].t - gap_start > loss_gap:
        losses += 1
    lat = sorted(s.latency for s in samples if s.latency is not None)
    relock = None
    if disturbance is not None:
        relock = _relock_time(samples, disturbance)
    return Metrics(
        scenario=scenario, duration=samples[-1].t - samples[0].t, acquisition_time=first_lock,
        lock_hold_ratio=hold, in_window_ratio=sum(s.in_window for s in samples) / len(samples),
        rms_pixel_error=_rms([s.pixel_error for s in seen]),
        rms_distance_error=_rms([s.distance_error for s in seen]),
        loss_events=losses, median_latency=lat[len(lat) // 2] if lat else None,
        rms_truth_pixel_error=_rms_or_none([s.truth_pixel_error for s in truth]),
        rms_truth_distance_error=_rms_or_none([s.truth_distance_error for s in seen]),
        relock_time=relock)


def _relock_time(samples: List[Sample], disturbance: float) -> Optional[float]:
    """0.0 if the lock never dropped after ``disturbance``; else seconds until it was regained
    (None = never regained)."""
    dropped = None
    for s in samples:
        if s.t < disturbance:
            continue
        if dropped is None and not s.locked:
            dropped = s.t
        elif dropped is not None and s.locked:
            return s.t - disturbance
    return 0.0 if dropped is None else None


def as_dict(m: Metrics) -> Dict[str, object]:
    return {k: getattr(m, k) for k in m.__dataclass_fields__}
