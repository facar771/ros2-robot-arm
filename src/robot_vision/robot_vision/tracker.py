"""Multi-object tracker: stable ids, coasting through short losses, re-acquisition, smoothing.

Per axis a constant-velocity Kalman filter (position + velocity). All three axes share the same
dynamics and noise, so one 2x2 covariance serves them all. Association is greedy nearest-neighbour
within a gate, same label only. Ids are never reused.

Life cycle: ``tentative`` (new, unconfirmed) -> ``confirmed`` (seen ``confirm_hits`` times) ->
``lost`` (unseen for more than ``max_coast_s``; still predicted, can be re-acquired within
``reacquire_gate`` metres) -> deleted after ``max_lost_s``. A tentative track that misses a frame is
dropped at once, so a one-frame false detection never gets an id.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Vec = Tuple[float, float, float]

TENTATIVE, CONFIRMED, LOST = 'tentative', 'confirmed', 'lost'


@dataclass(frozen=True)
class Observation:
    label: str
    position: Vec
    size: Optional[Vec] = None
    score: float = 1.0


@dataclass(frozen=True)
class TrackerConfig:
    gate: float = 0.10                # m, match radius for a visible track (grows with speed)
    reacquire_gate: float = 0.30      # m, match radius for a lost track
    confirm_hits: int = 3
    max_coast_s: float = 0.5          # unseen longer than this -> state 'lost'
    max_lost_s: float = 3.0           # unseen longer than this -> deleted
    meas_sigma: float = 0.005         # m, position measurement noise
    accel_sigma: float = 1.0          # m/s^2, how hard targets may manoeuvre
    size_alpha: float = 0.3           # exponential smoothing of the size


@dataclass(frozen=True)
class Track:
    id: int
    label: str
    state: str
    position: Vec                     # filtered
    velocity: Vec
    size: Optional[Vec]
    hits: int
    first_seen: float
    last_seen: float
    time_since_seen: float            # 0.0 when matched in the latest update

    @property
    def speed(self) -> float:
        return math.sqrt(sum(v * v for v in self.velocity))


@dataclass
class _State:
    id: int
    label: str
    pos: np.ndarray
    vel: np.ndarray
    cov: np.ndarray                   # 2x2 (pos, vel), shared by the three axes
    size: Optional[np.ndarray]
    hits: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    state: str = TENTATIVE
    extra: dict = field(default_factory=dict)


class Tracker:
    def __init__(self, config: Optional[TrackerConfig] = None):
        self.cfg = config or TrackerConfig()
        self._tracks: Dict[int, _State] = {}
        self._next_id = 1
        self._stamp: Optional[float] = None

    # ---- public --------------------------------------------------------------------------------
    def update(self, observations: Sequence[Observation], stamp: float) -> List[Track]:
        """Advance to ``stamp`` (seconds), match ``observations`` and return the live tracks
        (confirmed or lost; tentative ones are not reported until confirmed)."""
        dt = 0.0 if self._stamp is None else max(0.0, stamp - self._stamp)
        self._stamp = stamp
        for t in self._tracks.values():
            self._predict(t, dt)
        matches, unmatched_obs = self._associate(observations, stamp)
        for tid, obs in matches:
            self._correct(self._tracks[tid], obs, stamp)
        matched_ids = {tid for tid, _ in matches}
        for tid in list(self._tracks):
            t = self._tracks[tid]
            if tid in matched_ids:
                continue
            unseen = stamp - t.last_seen
            if t.state == TENTATIVE or unseen > self.cfg.max_lost_s:
                del self._tracks[tid]
            elif unseen > self.cfg.max_coast_s:
                t.state = LOST
        for obs in unmatched_obs:
            self._spawn(obs, stamp)
        return self.tracks(stamp)

    def tracks(self, stamp: Optional[float] = None) -> List[Track]:
        now = self._stamp if stamp is None else stamp
        return [self._snapshot(t, now) for t in sorted(self._tracks.values(), key=lambda t: t.id)
                if t.state != TENTATIVE]

    def reset(self) -> None:
        self._tracks.clear()
        self._stamp = None

    # ---- internals -----------------------------------------------------------------------------
    def _predict(self, t: _State, dt: float) -> None:
        if dt <= 0.0:
            return
        f = np.array([[1.0, dt], [0.0, 1.0]])
        q = self.cfg.accel_sigma ** 2 * np.array([[dt ** 4 / 4, dt ** 3 / 2],
                                                  [dt ** 3 / 2, dt ** 2]])
        t.pos = t.pos + t.vel * dt
        t.cov = f @ t.cov @ f.T + q

    def _gate(self, t: _State, stamp: float) -> float:
        if t.state == LOST:
            return self.cfg.reacquire_gate
        speed = float(np.linalg.norm(t.vel))
        return self.cfg.gate + 0.5 * speed * max(0.0, stamp - t.last_seen)

    def _associate(self, observations: Sequence[Observation], stamp: float):
        pairs = []
        for tid, t in self._tracks.items():
            gate = self._gate(t, stamp)
            for k, obs in enumerate(observations):
                if obs.label != t.label:
                    continue
                d = float(np.linalg.norm(np.asarray(obs.position) - t.pos))
                if d <= gate:
                    pairs.append((d, tid, k))
        pairs.sort()
        used_t, used_o, matches = set(), set(), []
        for _, tid, k in pairs:
            if tid in used_t or k in used_o:
                continue
            used_t.add(tid)
            used_o.add(k)
            matches.append((tid, observations[k]))
        return matches, [o for k, o in enumerate(observations) if k not in used_o]

    def _correct(self, t: _State, obs: Observation, stamp: float) -> None:
        r = self.cfg.meas_sigma ** 2
        s = t.cov[0, 0] + r
        k = t.cov[:, 0] / s                       # Kalman gain for (pos, vel)
        innovation = np.asarray(obs.position, dtype=np.float64) - t.pos
        t.pos = t.pos + k[0] * innovation
        t.vel = t.vel + k[1] * innovation
        h_cov = np.array([[1.0, 0.0]])
        t.cov = (np.eye(2) - np.outer(k, h_cov[0])) @ t.cov
        if obs.size is not None:
            size = np.asarray(obs.size, dtype=np.float64)
            a = self.cfg.size_alpha
            t.size = size if t.size is None else (1 - a) * t.size + a * size
        t.hits += 1
        t.last_seen = stamp
        if t.state == LOST or (t.state == TENTATIVE and t.hits >= self.cfg.confirm_hits):
            t.state = CONFIRMED

    def _spawn(self, obs: Observation, stamp: float) -> None:
        tid = self._next_id
        self._next_id += 1
        size = None if obs.size is None else np.asarray(obs.size, dtype=np.float64)
        state = CONFIRMED if self.cfg.confirm_hits <= 1 else TENTATIVE
        self._tracks[tid] = _State(
            id=tid, label=obs.label, pos=np.asarray(obs.position, dtype=np.float64),
            vel=np.zeros(3), cov=np.diag([self.cfg.meas_sigma ** 2, 1.0]), size=size,
            first_seen=stamp, last_seen=stamp, state=state)

    def _snapshot(self, t: _State, now: Optional[float]) -> Track:
        return Track(id=t.id, label=t.label, state=t.state,
                     position=tuple(float(v) for v in t.pos),
                     velocity=tuple(float(v) for v in t.vel),
                     size=None if t.size is None else tuple(float(v) for v in t.size),
                     hits=t.hits, first_seen=t.first_seen, last_seen=t.last_seen,
                     time_since_seen=max(0.0, (t.last_seen if now is None else now) - t.last_seen))
