"""The robot's skills: one typed, documented class that a language model will call as tools.

Rules every skill follows (so a caller that cannot read a traceback still gets an answer):
- It never raises. Every outcome, including bad arguments, unknown objects, unreachable goals and
  failures inside the ports, comes back as a :class:`~robot_core.models.SkillResult`.
- ``SkillResult.message`` says in plain words what happened or why it failed and what to try.
- Every call emits a ``skill_result`` event (``skill``, ``args``, ``ok``, ``message``,
  ``code_name``) so runs can be reviewed later.
- Units: metres and radians; positions are in the ``world`` frame (robot base at the origin, z up).

The docstrings are the tool descriptions: keep them accurate. :meth:`Skills.catalog` lists the
skills with their signatures for building tool specs.
"""
from __future__ import annotations

import inspect
import math
from typing import Any, Dict, List, Optional, Tuple

from robot_core.config import RobotConfig
from robot_core.errors import hint_for
from robot_core.models import (
    JointTarget,
    MotionResult,
    ObjectInfo,
    PoseTarget,
    SkillResult,
)
from robot_core.ports import EventSink, MotionPort, NullEventSink, ObjectsPort, PosePort
from robot_core.safezone import SafeZone

# Tool pointing straight down: link z axis = world -z (a half turn about y), quaternion (x,y,z,w).
TOOL_DOWN: Tuple[float, float, float, float] = (0.0, 1.0, 0.0, 0.0)
# Failures that say nothing about reachability: no "try another height" hint for them.
NO_REACH_HINT = ('', 'SAFE_ZONE', 'PREEMPTED', 'TIMED_OUT')
SKILLS = ('list_objects', 'go_to_named_pose', 'go_to_position', 'go_above')


class Skills:
    def __init__(self, config: RobotConfig, motion: MotionPort, poses: PosePort,
                 objects: ObjectsPort, events: Optional[EventSink] = None, *,
                 world_frame: str = 'world', max_height: float = 0.5,
                 workspace_radius: float = 1.5, zone: Optional[SafeZone] = None):
        """``max_height``: largest accepted clearance for go_above. ``workspace_radius``: goals
        farther than this from the base (m) are refused before asking MoveIt. ``zone``: safe
        working box; a tool goal whose target is outside it is refused and the robot stays put."""
        self._config = config
        self._motion = motion
        self._poses = poses
        self._objects = objects
        self._events = events or NullEventSink()
        self._frame = world_frame
        self._max_height = max_height
        self._radius = workspace_radius
        self._zone = zone

    # ---- skills ---------------------------------------------------------------------------
    def list_objects(self) -> List[ObjectInfo]:
        """List the objects the robot currently knows about (detected by the camera or added by
        hand): name, colour label, centre position and size in metres, world frame. Use the
        ``name`` (e.g. 'obj_red_1') or the colour label (e.g. 'red', if unique) in other skills.
        Returns an empty list when the scene cannot be read."""
        try:
            found = self._objects.objects()
        except Exception:    # noqa: BLE001 - a skill never raises
            return []
        objs = sorted((found or {}).values(), key=lambda o: o.name)
        self._emit('list_objects', {}, SkillResult(True, 'list_objects', f'{len(objs)} nesne'))
        return objs

    def go_to_named_pose(self, name: str) -> SkillResult:
        """Move all joints to a named pose from the robot description (e.g. 'home').
        Args: name: pose name; call with an unknown name to get the list of valid names."""
        args = {'name': name}
        known = sorted(self._config.poses)
        values = self._config.poses.get(str(name).lower())
        if values is None:
            return self._done('go_to_named_pose', args, SkillResult(
                False, 'go_to_named_pose',
                f"bilinmeyen poz '{name}'. Geçerli pozlar: {', '.join(known)}"))
        return self._done('go_to_named_pose', args, self._joint_move(
            'go_to_named_pose', JointTarget(tuple(values), str(name).lower())))

    def go_to_position(self, x: float, y: float, z: float) -> SkillResult:
        """Move the tool tip to (x, y, z) metres in the world frame with the tool pointing
        straight down. Fails with an explanation if the point is outside the arm's reach, collides
        with something, or has no inverse-kinematics solution; the robot then does not move.
        Args: x, y, z: target position in metres (z is height above the table/ground)."""
        args = {'x': x, 'y': y, 'z': z}
        return self._done('go_to_position', args, self._pose_move('go_to_position', args, x, y, z))

    def go_above(self, object_name: str, height: float = 0.10) -> SkillResult:
        """Move the tool tip to ``height`` metres above the top of an object, tool pointing down.
        Args: object_name: object name from list_objects ('obj_red_1') or its colour label
        ('red') when that colour is unique; height: clearance above the object's top surface
        (collision box) in metres, between 0 and the configured maximum (default 0.10).
        On success ``data`` holds the target position and the object used."""
        args = {'object_name': object_name, 'height': height}
        skill = 'go_above'
        if not _finite(height) or not 0.0 <= float(height) <= self._max_height:
            return self._done(skill, args, SkillResult(
                False, skill, f'height 0 ile {self._max_height} m arasında olmalı, verilen: '
                              f'{height!r}'))
        obj, problem = self._resolve(object_name)
        if obj is None:
            return self._done(skill, args, SkillResult(False, skill, problem))
        x, y = obj.position[0], obj.position[1]
        z = obj.top_z + float(height)
        result = self._pose_move(skill, args, x, y, z)
        data = dict(result.data, object=obj.name, target=[x, y, z])
        if result.ok:
            message = f'{obj.name} üzerinde {height:.3f} m yükseklikte'
        elif result.code_name not in NO_REACH_HINT:                       # MoveIt said no
            message = (result.message + ' Alet aşağı bakarken ulaşılabilir yükseklik aralığı '
                                        'dardır (çok alçak da çok yüksek de olmaz): 0.35 dene.')
        else:
            message = result.message
        result = SkillResult(result.ok, skill, message, result.code_name, data)
        return self._done(skill, args, result)

    @classmethod
    def catalog(cls) -> List[Dict[str, Any]]:
        """The skills as plain data: name, signature and description (for building tool specs)."""
        out = []
        for name in SKILLS:
            method = getattr(cls, name)
            out.append({'name': name, 'signature': str(inspect.signature(method)),
                        'description': inspect.getdoc(method) or ''})
        return out

    # ---- internals ------------------------------------------------------------------------
    def _resolve(self, name: str) -> Tuple[Optional[ObjectInfo], str]:
        try:
            found = self._objects.objects()
        except Exception as exc:    # noqa: BLE001
            return None, f'sahne okunamadı: {type(exc).__name__}: {exc}'
        if found is None:
            return None, 'planlama sahnesi okunamadı (move_group çalışıyor mu?)'
        if name in found:
            return found[name], ''
        by_label = [o for o in found.values() if o.label and o.label == str(name).lower()]
        if len(by_label) == 1:
            return by_label[0], ''
        names = ', '.join(sorted(found)) or 'hiç nesne yok'
        if len(by_label) > 1:
            return None, (f"'{name}' birden fazla nesneye uyuyor "
                          f"({', '.join(sorted(o.name for o in by_label))}); tam adı kullan")
        return None, f"bilinmeyen nesne '{name}'. Bilinen nesneler: {names}"

    def _pose_move(self, skill: str, args: Dict[str, Any], x: float, y: float,
                   z: float) -> SkillResult:
        if not all(_finite(v) for v in (x, y, z)):
            return SkillResult(False, skill, f'x, y, z sonlu sayı olmalı, verilen: {x!r}, {y!r}, '
                                             f'{z!r}')
        x, y, z = float(x), float(y), float(z)
        if z < 0.0:
            return SkillResult(False, skill, f'z={z:.3f} m zemin seviyesinin altında')
        distance = math.sqrt(x * x + y * y + z * z)
        if distance > self._radius:
            return SkillResult(False, skill, (
                f'hedef ({x:.2f}, {y:.2f}, {z:.2f}) tabandan {distance:.2f} m uzakta; erişim '
                f'alanı dışında (en fazla {self._radius:.2f} m), robot hareket etmedi'))
        if self._zone is not None:
            outside = self._zone.check((x, y, z))
            if outside:
                return SkillResult(False, skill, outside, 'SAFE_ZONE', {'target': [x, y, z]})
        target = PoseTarget(x, y, z, self._frame, TOOL_DOWN)
        try:
            result = self._poses.move_to_pose(target)
        except Exception as exc:    # noqa: BLE001
            return SkillResult(False, skill, f'hareket katmanı hatası: {type(exc).__name__}: {exc}')
        return self._from_motion(skill, result, {'target': [x, y, z]})

    def _joint_move(self, skill: str, target: JointTarget) -> SkillResult:
        try:
            result = self._motion.move_to(target)
        except Exception as exc:    # noqa: BLE001
            return SkillResult(False, skill, f'hareket katmanı hatası: {type(exc).__name__}: {exc}')
        return self._from_motion(skill, result, {'pose': target.label})

    @staticmethod
    def _from_motion(skill: str, result: MotionResult, data: Dict[str, Any]) -> SkillResult:
        if result.ok:
            return SkillResult(True, skill, 'hedefe ulaşıldı', result.code_name, data)
        hint = hint_for(result.code_name)
        why = result.code_name or result.message or 'bilinmeyen neden'
        text = f'hareket başarısız ({why})' + (f': {hint}' if hint else '')
        if result.message and result.code_name:
            text += f' [{result.message}]'
        return SkillResult(False, skill, text + ' Robot hareket etmedi.', result.code_name, data)

    def _done(self, skill: str, args: Dict[str, Any], result: SkillResult) -> SkillResult:
        self._emit(skill, args, result)
        return result

    def _emit(self, skill: str, args: Dict[str, Any], result: SkillResult) -> None:
        self._events.emit('skill_result', {'skill': skill, 'args': args, 'ok': result.ok,
                                           'message': result.message,
                                           'code_name': result.code_name})


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

