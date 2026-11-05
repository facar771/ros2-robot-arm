"""User frames taught from three points, and the store that keeps them (pure Python + numpy).

A frame is taught by three TCP POSITIONS expressed in the base frame (not joint angles):
``origin`` O, a point ``x`` on the +X axis, and a point ``xy`` anywhere in the XY plane (+Y side)::

    x_axis = (Px - O) / |Px - O|       z_axis = x_axis x (Pv - O) / |..|       y_axis = z x x

Rejected: |Px - O| < ``min_x_distance``; sin(angle(x, Pv - O)) < ``min_sin`` (nearly collinear: the
plane is ill-defined). Accepted with a "düşük doğruluk" warning when sin < ``warn_sin``. The numbers
(20 mm, 0.17 ~ 10 deg, 0.5) are SUGGESTIONS to be tuned in simulation: they live in ``FrameLimits``.

Frames available to jog: ``World`` (= the URDF ``world`` link; the profile has no mount transform so
it equals the base frame), ``Tool`` (the TCP frame, here the last link/flange until J4) and the
taught ``User`` frames. ``FrameStore`` persists the user frames in a YAML file whose path the caller
chooses (data directory, not the repo; the repo only has ``frames.example.yaml`` and the schema).
The file keeps the three points (source of truth) and the derived pose; on load the pose is
recomputed from the points and must match the stored one.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import yaml

from robot_core.se3 import (
    as_vec3,
    is_rotation,
    matrix_from_quat,
    quat_angle,
    quat_from_matrix,
    rot_x,
    rot_y,
    rot_z,
)

FRAMES_VERSION = 1
WORLD = 'World'
TOOL = 'Tool'
RESERVED = (WORLD.lower(), TOOL.lower())
_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}$')
MAX_COORDINATE = 5.0           # m: a taught point farther than this is a unit mistake (mm?)
POSE_TOLERANCE = 1e-6          # stored vs recomputed pose (m, rad)


class FrameError(ValueError):
    """A frame could not be taught, stored or loaded (message: what and why)."""


@dataclass(frozen=True)
class FrameLimits:
    """Teaching limits. SUGGESTED defaults: tune in simulation, do not treat as final."""
    min_x_distance: float = 0.020     # m: |Px - O|
    min_sin: float = 0.17             # reject below (~9.8 deg between the X direction and Pv - O)
    warn_sin: float = 0.5             # "low accuracy" below (30 deg)
    max_frames: int = 10

    def __post_init__(self) -> None:
        if not (self.min_x_distance > 0 and 0 < self.min_sin < self.warn_sin <= 1
                and self.max_frames >= 1):
            raise FrameError('FrameLimits: 0 < min_sin < warn_sin <= 1, min_x_distance > 0 ve '
                             'max_frames >= 1 olmalı')


@dataclass(frozen=True, eq=False)
class Frame:
    name: str
    origin: Tuple[float, float, float]
    rotation: Tuple[Tuple[float, ...], ...]       # 3x3, columns = frame axes in the base frame
    points: Mapping[str, Tuple[float, float, float]]    # origin / x / xy as taught
    warnings: Tuple[str, ...] = field(default=())

    @property
    def rotation_matrix(self) -> np.ndarray:
        return np.array(self.rotation, float)

    @property
    def transform(self) -> np.ndarray:
        t = np.eye(4)
        t[:3, :3] = self.rotation_matrix
        t[:3, 3] = self.origin
        return t

    @property
    def quaternion(self) -> np.ndarray:
        return quat_from_matrix(self.rotation_matrix)


# ---- teaching ---------------------------------------------------------------------------------
def check_name(name: object) -> str:
    if not isinstance(name, str) or not _NAME.match(name):
        raise FrameError('çerçeve adı harfle başlamalı, harf/rakam/_ içermeli, en çok 32 '
                         f'karakter: {name!r}')
    if name.lower() in RESERVED:
        raise FrameError(f'{name!r} ayrılmış ad (World ve Tool yerleşik çerçevelerdir)')
    return name


def frame_from_three_points(name: str, origin: Sequence[float], px: Sequence[float],
                            pv: Sequence[float], limits: Optional[FrameLimits] = None) -> Frame:
    """Teach a frame; raises ``FrameError`` for degenerate input, attaches warnings otherwise."""
    check_name(name)
    limits = limits or FrameLimits()
    try:
        o = np.array(as_vec3(origin, 'orijin'))
        x_pt = np.array(as_vec3(px, 'X noktası'))
        v_pt = np.array(as_vec3(pv, 'XY düzlemi noktası'))
    except ValueError as exc:
        raise FrameError(str(exc)) from exc
    for label, pt in (('orijin', o), ('X noktası', x_pt), ('XY noktası', v_pt)):
        if float(np.max(np.abs(pt))) > MAX_COORDINATE:
            raise FrameError(f'{label} {pt.tolist()} m: {MAX_COORDINATE:g} m dışında '
                             '(birim mm mi?)')
    dx = x_pt - o
    dist = float(np.linalg.norm(dx))
    if dist < limits.min_x_distance:
        raise FrameError(f'Orijin ile X noktası {dist * 1000:.1f} mm arada; en az '
                         f'{limits.min_x_distance * 1000:g} mm olmalı (X yönü belirsiz)')
    x_axis = dx / dist
    dv = v_pt - o
    v_len = float(np.linalg.norm(dv))
    cross = np.cross(x_axis, dv)
    sin = float(np.linalg.norm(cross)) / v_len if v_len > 1e-12 else 0.0
    if sin < limits.min_sin:
        raise FrameError(f'XY noktası X ekseniyle neredeyse aynı doğru üzerinde '
                         f'(sin={sin:.3f} < {limits.min_sin:g}); düzlem belirsiz, noktayı X '
                         'ekseninden uzaklaştırın')
    z_axis = cross / np.linalg.norm(cross)
    y_axis = np.cross(z_axis, x_axis)
    rot = np.column_stack([x_axis, y_axis, z_axis])
    if not is_rotation(rot, 1e-9):          # cannot happen after the checks; belt and braces
        raise FrameError('üretilen dönüş matrisi geçerli değil (RᵀR≠I ya da det≠+1)')
    warnings: List[str] = []
    if sin < limits.warn_sin:
        warnings.append(f'düşük doğruluk: XY noktası X ekseninden yalnızca '
                        f'{math.degrees(math.asin(sin)):.0f}° açıda (sin={sin:.2f} < '
                        f'{limits.warn_sin:g}); Y/Z yönü noktalama hatasına duyarlı')
    return Frame(name=name, origin=tuple(float(v) for v in o),
                 rotation=tuple(tuple(float(v) for v in row) for row in rot),
                 points={'origin': tuple(o.tolist()), 'x': tuple(x_pt.tolist()),
                         'xy': tuple(v_pt.tolist())},
                 warnings=tuple(warnings))


# ---- selecting the jog frame ------------------------------------------------------------------
def resolve_rotation(name: str, store: Optional['FrameStore'] = None) -> Optional[np.ndarray]:
    """Jog-frame rotation in the base frame; ``None`` for Tool (it follows the TCP each tick)."""
    if name.lower() == WORLD.lower():
        return np.eye(3)
    if name.lower() == TOOL.lower():
        return None
    if store is None or name not in store.names():
        raise FrameError(f'bilinmeyen çerçeve: {name!r} (World, Tool ya da öğretilmiş çerçeve)')
    return store.get(name).rotation_matrix


# ---- storage ----------------------------------------------------------------------------------
_TOP = ('frames_version', 'frames')
_ENTRY = ('name', 'points', 'pose')
_POINTS = ('origin', 'x', 'xy')
_POSE = ('position', 'quaternion_xyzw')


def _only(data: object, where: str, allowed: Sequence[str]) -> Dict:
    if not isinstance(data, Mapping):
        raise FrameError(f'{where}: eşleme (anahtar: değer) olmalı')
    unknown = sorted(set(map(str, data)) - set(allowed))
    missing = [k for k in allowed if k not in data]
    if unknown or missing:
        raise FrameError(f'{where}: bilinmeyen {unknown or "-"}, eksik {missing or "-"}')
    return dict(data)


def frame_to_dict(frame: Frame) -> Dict:
    return {'name': frame.name,
            'points': {k: [float(v) for v in frame.points[k]] for k in _POINTS},
            'pose': {'position': [float(v) for v in frame.origin],
                     'quaternion_xyzw': [float(v) for v in frame.quaternion]}}


def frame_from_dict(entry: object, where: str, limits: FrameLimits) -> Frame:
    d = _only(entry, where, _ENTRY)
    pts = _only(d['points'], f'{where}.points', _POINTS)
    pose = _only(d['pose'], f'{where}.pose', _POSE)
    frame = frame_from_three_points(d['name'], pts['origin'], pts['x'], pts['xy'], limits)
    try:
        stored_p = np.array(as_vec3(pose['position'], 'pose.position'))
        stored_q = np.array([float(v) for v in pose['quaternion_xyzw']])
        if stored_q.shape != (4,):
            raise ValueError('pose.quaternion_xyzw: 4 sayı olmalı')
        stored_rot = matrix_from_quat(stored_q)
    except (ValueError, TypeError) as exc:
        raise FrameError(f'{where}: {exc}') from exc
    if (np.linalg.norm(stored_p - np.array(frame.origin)) > POSE_TOLERANCE
            or quat_angle(stored_q, frame.quaternion) > POSE_TOLERANCE
            or not np.allclose(stored_rot, frame.rotation_matrix, atol=1e-5)):
        raise FrameError(f'{where} ({frame.name}): kayıtlı poz noktalardan yeniden hesaplananla '
                         'uyuşmuyor (dosya elle bozulmuş olabilir)')
    return frame


class FrameStore:
    """Taught frames in a YAML file; every change is written atomically (tmp + rename)."""

    def __init__(self, path: Union[str, Path], limits: Optional[FrameLimits] = None) -> None:
        self.path = Path(path)
        self.limits = limits or FrameLimits()
        self._frames: Dict[str, Frame] = {}
        self._lock = threading.RLock()
        self.load()

    def names(self) -> List[str]:
        with self._lock:
            return list(self._frames)

    def get(self, name: str) -> Frame:
        with self._lock:
            if name not in self._frames:
                raise FrameError(f'çerçeve yok: {name!r}')
            return self._frames[name]

    def __len__(self) -> int:
        return len(self._frames)

    def load(self) -> 'FrameStore':
        """Read the file (a missing file = empty store); ``FrameError`` names the file."""
        with self._lock:
            if not self.path.exists():
                self._frames = {}
                return self
            try:
                data = yaml.safe_load(self.path.read_text(encoding='utf-8'))
            except (OSError, yaml.YAMLError) as exc:
                raise FrameError(f'{self.path}: okunamadı/geçerli YAML değil ({exc})') from exc
            try:
                self._frames = self._parse(data)
            except FrameError as exc:
                raise FrameError(f'{self.path}: {exc}') from exc
            return self

    def _parse(self, data: object) -> Dict[str, Frame]:
        d = _only(data, 'dosya', _TOP)
        if d['frames_version'] != FRAMES_VERSION:
            raise FrameError(f"frames_version {FRAMES_VERSION} olmalı, bulunan: "
                             f"{d['frames_version']!r}")
        entries = d['frames']
        if not isinstance(entries, list):
            raise FrameError('frames: liste olmalı')
        if len(entries) > self.limits.max_frames:
            raise FrameError(f'en çok {self.limits.max_frames} çerçeve; dosyada {len(entries)} var')
        out: Dict[str, Frame] = {}
        for i, entry in enumerate(entries):
            frame = frame_from_dict(entry, f'frames[{i}]', self.limits)
            if frame.name.lower() in {n.lower() for n in out}:
                raise FrameError(f'frames[{i}]: ad tekrar ediyor: {frame.name!r}')
            out[frame.name] = frame
        return out

    def teach(self, name: str, origin: Sequence[float], px: Sequence[float],
              pv: Sequence[float]) -> Frame:
        """Validate, add and persist. Name must be new; at most ``limits.max_frames`` frames."""
        with self._lock:
            check_name(name)
            if name.lower() in {n.lower() for n in self._frames}:
                raise FrameError(f'{name!r} adlı çerçeve zaten var (önce silin)')
            if len(self._frames) >= self.limits.max_frames:
                raise FrameError(f'en çok {self.limits.max_frames} çerçeve; önce birini silin')
            frame = frame_from_three_points(name, origin, px, pv, self.limits)
            self._save({**self._frames, name: frame})
            self._frames[name] = frame
            return frame

    def remove(self, name: str) -> None:
        with self._lock:
            self.get(name)
            rest = {k: v for k, v in self._frames.items() if k != name}
            self._save(rest)
            self._frames = rest

    def replace_all(self, entries: Iterable[Tuple[str, Sequence[float], Sequence[float],
                                                  Sequence[float]]]) -> List[Frame]:
        """Replace the WHOLE list with frames taught from ``(name, origin, px, pv)`` entries.

        Everything is validated first (names, case-insensitive uniqueness, ``max_frames``, the
        three-point geometry); only then is the file written (atomically) and the in-memory list
        swapped. On any ``FrameError`` the file and the store are untouched; ``[]`` clears it.
        """
        entries = list(entries)
        with self._lock:
            if len(entries) > self.limits.max_frames:
                raise FrameError(f'en çok {self.limits.max_frames} çerçeve')
            new: Dict[str, Frame] = {}
            for i, entry in enumerate(entries):
                try:
                    name, origin, px, pv = entry
                except (TypeError, ValueError) as exc:
                    raise FrameError(f'frames[{i}]: (ad, orijin, x, xy) olmalı') from exc
                name = check_name(name)
                if name.lower() in {n.lower() for n in new}:
                    raise FrameError(f'frames[{i}]: ad tekrar ediyor: {name!r}')
                new[name] = frame_from_three_points(name, origin, px, pv, self.limits)
            self._save(new)
            self._frames = new
            return list(new.values())

    def _save(self, frames: Mapping[str, Frame]) -> None:
        """Write the whole file to a temp file in the same directory, fsync, then rename."""
        doc = {'frames_version': FRAMES_VERSION,
               'frames': [frame_to_dict(f) for f in frames.values()]}
        text = ('# Taught user frames (robot_core.frames). Points are TCP positions in the base '
                'frame (metres); pose is derived and checked on load. Do not edit by hand.\n'
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


# ---- sphere ("dome camera") frame ---------------------------------------------------------------
SPHERE_MAX_ELEVATION_DEG = 85.0      # hard ceiling: at the zenith the azimuth is undefined
SPHERE_MIN_RADIUS = 0.05             # m
SPHERE_MAX_RADIUS = 2.0              # m
_R0 = np.array([[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])   # columns: z, y, -x
_TOOL_ALIGN = {                      # rotation T with T @ axis = +z (tool axis -> look-at axis)
    'z': np.eye(3), '-z': rot_x(math.pi),
    'x': rot_y(-math.pi / 2), '-x': rot_y(math.pi / 2),
    'y': rot_x(math.pi / 2), '-y': rot_x(-math.pi / 2),
}
_TOOL_VEC = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1),
             '-x': (-1, 0, 0), '-y': (0, -1, 0), '-z': (0, 0, -1)}


@dataclass(frozen=True, eq=False)
class SphereFrame:
    """Sphere frame: centre C (base = World frame, metres) and radius r. Not a rotation frame.

    Jog inputs are azimuth (about the vertical axis through C), elevation (TCP at constant r) and
    r itself; the chosen tool axis (default tool z) always looks at C. Build with ``sphere_frame``.
    Azimuth 0 is the +X side of C; elevation is measured up from the horizontal plane through C.
    """
    name: str
    center: Tuple[float, float, float]
    radius: float
    tool_axis: str = 'z'

    @property
    def center_array(self) -> np.ndarray:
        return np.array(self.center, float)

    @property
    def tool_axis_vector(self) -> np.ndarray:
        return np.array(_TOOL_VEC[self.tool_axis], float)

    def direction(self, az: float, el: float) -> np.ndarray:
        """Unit vector from C to the TCP (radians)."""
        return np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])

    def position(self, az: float, el: float, radius: Optional[float] = None) -> np.ndarray:
        return self.center_array + (self.radius if radius is None else radius) * self.direction(
            az, el)

    def rotation(self, az: float, el: float, roll: float = 0.0) -> np.ndarray:
        """TCP orientation: tool axis = -direction (looks at C), horizon level, ``roll`` extra
        rotation about the tool axis. Its angular velocity is az' z - el' e_theta."""
        return rot_z(az) @ rot_y(-el) @ _R0 @ rot_z(roll) @ _TOOL_ALIGN[self.tool_axis]

    def roll_of(self, rotation: np.ndarray, az: float, el: float) -> float:
        """Roll about the tool axis of ``rotation`` relative to the level-horizon orientation."""
        rr = (rot_z(az) @ rot_y(-el) @ _R0).T @ rotation @ _TOOL_ALIGN[self.tool_axis].T
        return math.atan2(rr[1, 0], rr[0, 0])

    def angles(self, point: Sequence[float]) -> Tuple[float, float, float]:
        """(azimuth, elevation, radius) of ``point``; radians / metres."""
        d = np.asarray(point, float) - self.center_array
        dist = float(np.linalg.norm(d))
        if dist < 1e-9:
            raise FrameError('nokta küre merkezinde: azimut/yükselme tanımsız')
        return math.atan2(d[1], d[0]), math.asin(max(-1.0, min(1.0, d[2] / dist))), dist

    def as_dict(self) -> Dict[str, object]:
        return {'type': 'sphere', 'name': self.name, 'center': list(self.center),
                'radius': self.radius, 'tool_axis': self.tool_axis}


def sphere_frame(name: str, center: Sequence[float], radius: float,
                 tool_axis: str = 'z') -> SphereFrame:
    """Validated ``SphereFrame``; raises ``FrameError`` with the reason."""
    check_name(name)
    try:
        c = as_vec3(center, 'küre merkezi')
    except ValueError as exc:
        raise FrameError(str(exc)) from exc
    if float(max(abs(v) for v in c)) > MAX_COORDINATE:
        raise FrameError(f'küre merkezi {list(c)} m: {MAX_COORDINATE:g} m dışında (birim mm mi?)')
    if (isinstance(radius, bool) or not isinstance(radius, (int, float))
            or not math.isfinite(radius)
            or not SPHERE_MIN_RADIUS <= radius <= SPHERE_MAX_RADIUS):
        raise FrameError(f'yarıçap [{SPHERE_MIN_RADIUS:g}, {SPHERE_MAX_RADIUS:g}] m aralığında '
                         f'olmalı, bulunan: {radius!r}')
    if tool_axis not in _TOOL_VEC:
        raise FrameError(f'takım ekseni {sorted(_TOOL_VEC)} içinden biri olmalı: {tool_axis!r}')
    return SphereFrame(name, tuple(float(v) for v in c), float(radius), tool_axis)


def sphere_from_dict(data: Mapping[str, object]) -> SphereFrame:
    """Inverse of ``SphereFrame.as_dict`` (strict: unknown keys are refused). Persistence in
    ``FrameStore`` / the API is L8, not here."""
    if not isinstance(data, Mapping) or data.get('type') != 'sphere':
        raise FrameError("küre çerçevesi {'type': 'sphere', ...} olmalı")
    extra = set(data) - {'type', 'name', 'center', 'radius', 'tool_axis'}
    if extra:
        raise FrameError(f'küre çerçevesinde bilinmeyen anahtar: {sorted(extra)}')
    try:
        return sphere_frame(data['name'], data['center'], data['radius'],  # type: ignore[arg-type]
                            data.get('tool_axis', 'z'))  # type: ignore[arg-type]
    except KeyError as exc:
        raise FrameError(f'küre çerçevesinde eksik anahtar: {exc.args[0]}') from exc
