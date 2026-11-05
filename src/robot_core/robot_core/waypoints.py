"""Named waypoints (taught robot poses) and the store that keeps them (pure Python + numpy, no ROS).

A waypoint is a JOINT configuration (the source of truth: it is what the robot goes to) together
with the TCP pose that configuration gives, expressed in a named frame (``World`` or a taught user
frame from ``robot_core.frames``) and the creation time. ``WaypointStore`` persists them in
``waypoints.yaml`` in the data directory (not the repo), with the same rules as ``frames.yaml``:
atomic write (temp file + fsync + rename), a strict schema, a version, validation on load.

Saving REFUSES (``WaypointError``, nothing is written) a point whose joint values are outside the
joint limits or whose TCP lies outside the safe box. The same checks run again on load, so a file
edited by hand, or left over from before the limits/box were narrowed, is refused at start-up with
a message naming the file, the entry and the reason. The robot side has the last word: this store
only keeps points; going to one is a planned, collision-checked motion (O2), never a blind jump.

Derived data: the stored TCP pose is recomputed from the joints (forward kinematics, and the frame
transform) on load and must match the stored one. A waypoint recorded in a user frame needs that
frame to exist (and to be unchanged) when the file is loaded.

``waypoints.yaml``::

    waypoints_version: 1
    joint_names: [joint_1, ..., joint_6]
    waypoints:
      - name: pick_a
        joints: [0.0, -0.1, 1.65, 0.0, 1.2, 0.0]          # rad, in joint_names order
        tcp: {frame: World, position: [x, y, z], quaternion_xyzw: [x, y, z, w]}   # m
        created: '2026-11-05T14:30:00Z'
"""
from __future__ import annotations

import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import yaml

from robot_core.frames import WORLD, FrameError, FrameStore
from robot_core.kinematics import Chain
from robot_core.safezone import SafeZone
from robot_core.se3 import matrix_from_quat, quat_angle, quat_from_matrix

WAYPOINTS_VERSION = 1
MAX_WAYPOINTS = 100                  # SUGGESTED cap; a runaway client cannot fill the disk
POSE_TOLERANCE = 1e-6                # stored vs recomputed pose (m, rad)
_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}$')
_TOP = ('waypoints_version', 'joint_names', 'waypoints')
_ENTRY = ('name', 'joints', 'tcp', 'created')
_TCP = ('frame', 'position', 'quaternion_xyzw')


class WaypointError(ValueError):
    """A waypoint could not be saved, stored or loaded (message: what and why)."""


@dataclass(frozen=True, eq=False)
class Waypoint:
    name: str
    joints: Tuple[float, ...]                       # rad, in the profile's joint order
    frame: str                                      # World or a taught user frame
    position: Tuple[float, float, float]            # TCP in ``frame``, metres
    quaternion: Tuple[float, float, float, float]   # TCP orientation in ``frame`` (x, y, z, w)
    created: str                                    # UTC, ISO 8601, 'YYYY-MM-DDTHH:MM:SSZ'

    def as_dict(self) -> Dict:
        return waypoint_to_dict(self)


def check_name(name: object) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise WaypointError('nokta adı harfle başlamalı, harf/rakam/_ içermeli, en çok 32 '
                            f'karakter: {name!r}')
    return name


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_time(value: object, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise WaypointError(f"{where}: 'YYYY-MM-DDTHH:MM:SSZ' (UTC) olmalı, bulunan: {value!r}")
    try:
        datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError as exc:
        raise WaypointError(f'{where}: geçerli bir tarih değil ({value!r})') from exc
    return value


def _only(data: object, where: str, allowed: Sequence[str]) -> Dict:
    if not isinstance(data, Mapping):
        raise WaypointError(f'{where}: eşleme (anahtar: değer) olmalı')
    unknown = sorted(set(map(str, data)) - set(allowed))
    missing = [k for k in allowed if k not in data]
    if unknown or missing:
        raise WaypointError(f'{where}: bilinmeyen {unknown or "-"}, eksik {missing or "-"}')
    return dict(data)


def _numbers(value: object, count: int, where: str) -> Tuple[float, ...]:
    ok = (isinstance(value, (list, tuple)) and len(value) == count
          and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                  for v in value))
    if not ok:
        raise WaypointError(f'{where}: {count} sonlu sayı olmalı, bulunan: {value!r}')
    return tuple(float(v) for v in value)


def waypoint_to_dict(wp: Waypoint) -> Dict:
    return {'name': wp.name, 'joints': [float(v) for v in wp.joints],
            'tcp': {'frame': wp.frame, 'position': [float(v) for v in wp.position],
                    'quaternion_xyzw': [float(v) for v in wp.quaternion]},
            'created': wp.created}


class WaypointStore:
    """Waypoints in a YAML file; every change is validated, then written atomically.

    ``chain`` gives forward kinematics and the joint limits, ``zone`` the safe box (world frame),
    ``frames`` (optional) the taught user frames a waypoint may be expressed in.
    """

    def __init__(self, path: Union[str, Path], chain: Chain, zone: SafeZone,
                 frames: Optional[FrameStore] = None, max_waypoints: int = MAX_WAYPOINTS,
                 clock: Callable[[], str] = _utc_now) -> None:
        if zone.frame.lower() != WORLD.lower():
            raise WaypointError(f"güvenli kutu '{zone.frame}' çerçevesinde; yalnızca world "
                                'desteklenir')
        if max_waypoints < 1:
            raise WaypointError('max_waypoints >= 1 olmalı')
        self.path = Path(path)
        self.chain = chain
        self.zone = zone
        self.frames = frames
        self.max_waypoints = max_waypoints
        self._clock = clock
        self._points: Dict[str, Waypoint] = {}
        self._lock = threading.RLock()
        self.load()

    # ---- reading ------------------------------------------------------------------------------
    def names(self) -> List[str]:
        with self._lock:
            return list(self._points)

    def list(self) -> List[Waypoint]:
        with self._lock:
            return list(self._points.values())

    def get(self, name: str) -> Waypoint:
        with self._lock:
            if name not in self._points:
                raise WaypointError(f'nokta yok: {name!r}')
            return self._points[name]

    def __len__(self) -> int:
        with self._lock:
            return len(self._points)

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._points

    # ---- validation (shared by save and load) -------------------------------------------------
    def _frame_transform(self, frame: str) -> Tuple[np.ndarray, np.ndarray]:
        """(rotation, origin) of ``frame`` in the base/world frame."""
        if frame.lower() == WORLD.lower():
            return np.eye(3), np.zeros(3)
        if self.frames is None or frame not in self.frames.names():
            raise WaypointError(f'bilinmeyen çerçeve: {frame!r} (World ya da öğretilmiş çerçeve)')
        fr = self.frames.get(frame)
        return fr.rotation_matrix, np.array(fr.origin)

    def check_joints(self, joints: object, where: str = 'eklemler') -> Tuple[float, ...]:
        q = _numbers(joints, self.chain.dof, where)
        for name, v, lo, hi in zip(self.chain.joint_names, q, self.chain.lower, self.chain.upper):
            if not lo <= v <= hi:
                raise WaypointError(f'{where}: {name}={v:.4f} rad, limit [{lo:.4f}, {hi:.4f}] '
                                    'dışında; nokta reddedildi')
        return q

    def _derive(self, joints: Tuple[float, ...], frame: str, where: str
                ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float]]:
        """TCP pose in ``frame`` for ``joints``; refuses a TCP outside the safe box."""
        pose = self.chain.fk(joints)
        world_p, world_r = pose[:3, 3], pose[:3, :3]
        why = self.zone.check(tuple(float(v) for v in world_p))
        if why:
            raise WaypointError(f'{where}: TCP {np.round(world_p, 3).tolist()} m: {why}; '
                                'nokta reddedildi')
        rot, origin = self._frame_transform(frame)
        pos = rot.T @ (world_p - origin)
        quat = quat_from_matrix(rot.T @ world_r)
        return (tuple(float(v) for v in pos), tuple(float(v) for v in quat))  # type: ignore

    def _build(self, name: str, joints: object, frame: str, created: str,
               where: str) -> Waypoint:
        check_name(name)
        if not isinstance(frame, str) or not frame:
            raise WaypointError(f'{where}: çerçeve adı gerekli (World ya da öğretilmiş çerçeve)')
        q = self.check_joints(joints, f'{where}.joints')
        pos, quat = self._derive(q, frame, where)
        return Waypoint(name, q, frame, pos, quat, created)

    # ---- changing -----------------------------------------------------------------------------
    def add(self, name: str, joints: Sequence[float], frame: str = WORLD,
            overwrite: bool = False) -> Waypoint:
        """Validate, add and persist. ``overwrite`` re-teaches an existing name (new time)."""
        with self._lock:
            check_name(name)
            exists = name in self._points
            if exists and not overwrite:
                raise WaypointError(f'{name!r} adlı nokta zaten var (üzerine yazmak için açıkça '
                                    'istenmeli ya da önce silin)')
            if not exists and len(self._points) >= self.max_waypoints:
                raise WaypointError(f'en çok {self.max_waypoints} nokta; önce birini silin')
            wp = self._build(name, joints, frame, self._clock(), f'nokta {name}')
            self._save({**self._points, name: wp})
            self._points[name] = wp
            return wp

    def remove(self, name: str) -> None:
        with self._lock:
            self.get(name)
            rest = {k: v for k, v in self._points.items() if k != name}
            self._save(rest)
            self._points = rest

    def rename(self, old: str, new: str) -> Waypoint:
        """Same joints/pose/time under a new name; order is kept."""
        with self._lock:
            wp = self.get(old)
            check_name(new)
            if new != old and new in self._points:
                raise WaypointError(f'{new!r} adlı nokta zaten var')
            moved = Waypoint(new, wp.joints, wp.frame, wp.position, wp.quaternion, wp.created)
            out = {(new if k == old else k): (moved if k == old else v)
                   for k, v in self._points.items()}
            self._save(out)
            self._points = out
            return moved

    # ---- file ---------------------------------------------------------------------------------
    def load(self) -> 'WaypointStore':
        """Read the file (a missing file = empty store); ``WaypointError`` names the file."""
        with self._lock:
            if not self.path.exists():
                self._points = {}
                return self
            try:
                data = yaml.safe_load(self.path.read_text(encoding='utf-8'))
            except (OSError, yaml.YAMLError) as exc:
                raise WaypointError(f'{self.path}: okunamadı/geçerli YAML değil ({exc})') from exc
            try:
                self._points = self._parse(data)
            except (WaypointError, FrameError) as exc:
                raise WaypointError(f'{self.path}: {exc}') from exc
            return self

    def _parse(self, data: object) -> Dict[str, Waypoint]:
        d = _only(data, 'dosya', _TOP)
        if d['waypoints_version'] != WAYPOINTS_VERSION or isinstance(d['waypoints_version'], bool):
            raise WaypointError(f"waypoints_version {WAYPOINTS_VERSION} olmalı, bulunan: "
                                f"{d['waypoints_version']!r}")
        if list(d['joint_names'] or []) != list(self.chain.joint_names):
            raise WaypointError(f"joint_names profille uyuşmuyor: dosyada {d['joint_names']!r}, "
                                f'profilde {list(self.chain.joint_names)}')
        entries = d['waypoints']
        if not isinstance(entries, list):
            raise WaypointError('waypoints: liste olmalı')
        if len(entries) > self.max_waypoints:
            raise WaypointError(f'en çok {self.max_waypoints} nokta; dosyada {len(entries)} var')
        out: Dict[str, Waypoint] = {}
        for i, entry in enumerate(entries):
            where = f'waypoints[{i}]'
            e = _only(entry, where, _ENTRY)
            tcp = _only(e['tcp'], f'{where}.tcp', _TCP)
            wp = self._build(e['name'], e['joints'], tcp['frame'],
                             _parse_time(e['created'], f'{where}.created'), where)
            if wp.name in out:
                raise WaypointError(f'{where}: ad tekrar ediyor: {wp.name!r}')
            pos = _numbers(tcp['position'], 3, f'{where}.tcp.position')
            quat = _numbers(tcp['quaternion_xyzw'], 4, f'{where}.tcp.quaternion_xyzw')
            try:
                matrix_from_quat(quat)
            except ValueError as exc:
                raise WaypointError(f'{where}.tcp.quaternion_xyzw: {exc}') from exc
            if (np.linalg.norm(np.array(pos) - np.array(wp.position)) > POSE_TOLERANCE
                    or quat_angle(quat, wp.quaternion) > POSE_TOLERANCE):
                raise WaypointError(f'{where} ({wp.name}): kayıtlı TCP pozu eklemlerden yeniden '
                                    'hesaplananla uyuşmuyor (dosya elle bozulmuş, profil ya da '
                                    f'{wp.frame!r} çerçevesi değişmiş olabilir)')
            out[wp.name] = Waypoint(wp.name, wp.joints, wp.frame, pos, wp.quaternion,
                                    wp.created)
        return out

    def _save(self, points: Mapping[str, Waypoint]) -> None:
        """Write the whole file to a temp file in the same directory, fsync, then rename."""
        doc = {'waypoints_version': WAYPOINTS_VERSION,
               'joint_names': list(self.chain.joint_names),
               'waypoints': [waypoint_to_dict(p) for p in points.values()]}
        text = ('# Taught waypoints (robot_core.waypoints). Joints are the source of truth; the '
                'TCP pose is derived and checked on load. Do not edit by hand.\n'
                + yaml.safe_dump(doc, sort_keys=False))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
