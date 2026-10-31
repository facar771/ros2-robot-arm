"""Keeps the planning scene in step with what perception sees (pure Python, no ROS).

Perception gives anonymous, jittery detections every frame. This turns them into stable collision
objects:

- Identity: a detection is matched to the nearest existing track of the same colour within ``gate``
  metres; otherwise it starts a new track. Ids (``obj_<label>_<n>``) are never reused.
- Jitter filter: the track pose/size is an exponential moving average, and the scene is only
  touched when the filtered value moved more than ``move_threshold`` from what the scene holds.
- Confirmation: an object must be seen in ``confirm_frames`` consecutive updates before it is added
  (one-frame false detections never reach the planner).
- Removal: a published object that was not seen for ``remove_after`` seconds is removed.

Only objects this class added are ever removed, so obstacles added by hand (``addbox``) are safe.
A moved object within ``gate`` is tracked as the same object; a jump beyond ``gate`` becomes a new
track while the old one expires after ``remove_after`` (both exist briefly). Failed scene calls are
retried on the next update. If detections stop arriving altogether, nothing is removed (staleness
is the caller's concern: keep feeding ``update`` with empty lists).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from robot_core.models import BoxObstacle
from robot_core.ports import EventSink, NullEventSink, ScenePort

Vec = Tuple[float, float, float]


@dataclass(frozen=True)
class Observation:
    label: str
    position: Vec
    size: Vec


@dataclass
class _Track:
    id: str
    label: str
    position: Vec
    size: Vec
    last_seen: float
    streak: int = 1
    pub_position: Optional[Vec] = None   # what the scene currently holds
    pub_size: Optional[Vec] = None

    @property
    def published(self) -> bool:
        return self.pub_position is not None


def _blend(old: Vec, new: Vec, alpha: float) -> Vec:
    return (old[0] + alpha * (new[0] - old[0]), old[1] + alpha * (new[1] - old[1]),
            old[2] + alpha * (new[2] - old[2]))


class SceneSynchronizer:
    def __init__(self, scene: ScenePort, events: Optional[EventSink] = None, *,
                 frame: str = 'world', prefix: str = 'obj_', gate: float = 0.08,
                 confirm_frames: int = 3, remove_after: float = 2.0,
                 move_threshold: float = 0.01, padding: float = 0.005, alpha: float = 0.4):
        self._scene = scene
        self._events = events or NullEventSink()
        self._frame, self._prefix = frame, prefix
        self._gate, self._confirm = gate, confirm_frames
        self._remove_after, self._move = remove_after, move_threshold
        self._padding, self._alpha = padding, alpha
        self._tracks: Dict[str, _Track] = {}
        self._counter: Dict[str, int] = {}

    # ---- public ---------------------------------------------------------------------------
    def update(self, observations: Sequence[Observation], now: float) -> None:
        """Feed one perception frame (possibly empty) taken at time ``now`` (seconds)."""
        matched = self._associate(observations, now)
        for track in list(self._tracks.values()):
            if track.id in matched:
                continue
            track.streak = 0 if not track.published else track.streak
            if track.published:
                if now - track.last_seen > self._remove_after:
                    self._remove(track)
            elif now - track.last_seen > self._remove_after or track.streak == 0:
                del self._tracks[track.id]    # never confirmed: forget silently
        for track in self._tracks.values():
            self._publish_if_needed(track)

    @property
    def published(self) -> Dict[str, Tuple[str, Vec, Vec]]:
        """id -> (label, position, size) of the objects currently in the scene."""
        return {t.id: (t.label, t.pub_position, t.pub_size)
                for t in self._tracks.values() if t.published}

    # ---- internals ------------------------------------------------------------------------
    def _associate(self, observations: Sequence[Observation], now: float) -> set:
        matched = set()
        pairs: List[Tuple[float, int, str]] = []
        for i, obs in enumerate(observations):
            for track in self._tracks.values():
                if track.label == obs.label:
                    d = math.dist(obs.position, track.position)
                    if d <= self._gate:
                        pairs.append((d, i, track.id))
        used_obs = set()
        for _, i, tid in sorted(pairs):           # greedy, closest pairs first
            if i in used_obs or tid in matched:
                continue
            used_obs.add(i)
            matched.add(tid)
            track, obs = self._tracks[tid], observations[i]
            track.position = _blend(track.position, obs.position, self._alpha)
            track.size = _blend(track.size, obs.size, self._alpha)
            track.last_seen = now
            track.streak += 1
        for i, obs in enumerate(observations):
            if i in used_obs:
                continue
            n = self._counter.get(obs.label, 0) + 1
            self._counter[obs.label] = n
            track = _Track(f'{self._prefix}{obs.label}_{n}', obs.label, obs.position, obs.size,
                           now)
            self._tracks[track.id] = track
            matched.add(track.id)
        return matched

    def _publish_if_needed(self, track: _Track) -> None:
        if track.streak < self._confirm:
            return
        if track.published:
            moved = math.dist(track.position, track.pub_position) > self._move
            resized = max(abs(a - b) for a, b in zip(track.size, track.pub_size)) > self._move
            if not (moved or resized):
                return
        px, py, pz = track.position
        sx, sy, sz = (s + 2 * self._padding for s in track.size)
        box = BoxObstacle(track.id, px, py, pz, sx, sy, sz, self._frame)
        if self._scene.add_box(box):      # ADD of an existing id replaces it in MoveIt
            track.pub_position, track.pub_size = track.position, track.size
            self._events.emit('obstacle_added', {'name': track.id, 'pose': [px, py, pz],
                                                 'size': [sx, sy, sz]})

    def _remove(self, track: _Track) -> None:
        if self._scene.remove_box(track.id):
            del self._tracks[track.id]
            self._events.emit('obstacle_removed', {'name': track.id})
