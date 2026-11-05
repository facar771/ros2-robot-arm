"""Waypoints over the Robot API (O2), ROS-free: the rules around the O1 ``WaypointStore``.

    GET    /api/waypoints                 all points (+ joint names, limit, usable frames)
    GET    /api/waypoints/<name>          one point
    PUT    /api/waypoints/<name>          {"joints": [6 rad], "frame": "World"}  create or replace
                                          {"rename_to": "new_name"}              rename
    DELETE /api/waypoints/<name>
    POST   /api/waypoints/record          {"name", "frame"?, "overwrite"?}  save the MEASURED pose

Saving is the store's job and it REFUSES a point outside the joint limits or whose TCP is outside
the safe box (``400``, nothing written). Going to a point is not here: ``goto_waypoint <name>`` is a
white-listed job command that asks MoveIt for a planned, collision-checked joint goal
(``robot_core.webcmd``); there is no unchecked joint target anywhere. Editing the list moves no
motor, so the endpoints stay usable while the e-stop is latched; ``record`` additionally refuses
while a job or a jog lease is active (the measured pose would be a moving one).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from robot_core.frames import WORLD, Frame
from robot_core.waypoints import WaypointError, WaypointStore, check_name

MAX_BODY = 1024
RESERVED = ('record',)       # /api/waypoints/record is the POST door, not a point


class WaypointDenied(Exception):
    """A request that is refused (``status``: HTTP code, ``message``: why, in Turkish)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class FrameInUse(Exception):
    """A frame edit would orphan waypoints that are expressed in it."""


def waypoint_document(store: WaypointStore, name: str) -> Dict[str, Any]:
    return _get(store, name).as_dict()


def waypoints_document(store: WaypointStore) -> Dict[str, Any]:
    frames = [WORLD] + (store.frames.names() if store.frames is not None else [])
    return {'waypoints_version': 1, 'joint_names': list(store.chain.joint_names),
            'max_waypoints': store.max_waypoints, 'frames': frames,
            'waypoints': [p.as_dict() for p in store.list()]}


def _name(name: Any) -> str:
    try:
        check_name(name)
    except WaypointError as exc:
        raise WaypointDenied(400, str(exc)) from exc
    if name.lower() in RESERVED:
        raise WaypointDenied(400, f'{name!r} ayrılmış ad')
    return name


def _get(store: WaypointStore, name: str):
    _name(name)
    if name not in store:
        raise WaypointDenied(404, f'nokta yok: {name!r}')
    return store.get(name)


def _only(body: Any, allowed: Sequence[str], required: Sequence[str], hint: str) -> Dict:
    if not isinstance(body, dict) or set(body) - set(allowed) or any(k not in body
                                                                    for k in required):
        raise WaypointDenied(400, hint)
    return body


def _frame(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise WaypointDenied(400, "'frame' bir çerçeve adı olmalı (World ya da öğretilmiş çerçeve)")
    return value


class WaypointApi:
    """The request rules. ``joints``: latest measured joint positions (``joint_positions()``);
    ``busy``: returns a reason text while a job or a jog lease makes recording meaningless."""

    def __init__(self, store: WaypointStore, joints: Any = None,
                 busy: Optional[Callable[[], Optional[str]]] = None):
        self.store = store
        self._joints = joints
        self._busy = busy or (lambda: None)

    def document(self) -> Dict[str, Any]:
        return waypoints_document(self.store)

    def get(self, name: str) -> Dict[str, Any]:
        return waypoint_document(self.store, name)

    def put(self, name: str, body: Any) -> Tuple[int, Dict[str, Any]]:
        hint = ('gövde {"joints": [6 sayı], "frame": "World"} ya da {"rename_to": "yeni_ad"} '
                'olmalı')
        if isinstance(body, dict) and 'rename_to' in body:
            _only(body, ('rename_to',), ('rename_to',), hint)
            return 200, self._rename(name, body['rename_to'])
        d = _only(body, ('joints', 'frame'), ('joints',), hint)
        _name(name)
        existed = name in self.store
        if not existed and len(self.store) >= self.store.max_waypoints:
            raise WaypointDenied(409, f'en çok {self.store.max_waypoints} nokta; önce birini silin')
        frame = _frame(d.get('frame', WORLD))
        try:
            wp = self.store.add(name, d['joints'], frame, overwrite=True)
        except WaypointError as exc:
            raise WaypointDenied(400, str(exc)) from exc
        return (200 if existed else 201), {**wp.as_dict(), 'replaced': existed}

    def _rename(self, old: str, new: Any) -> Dict[str, Any]:
        _get(self.store, old)
        _name(new)
        if new != old and new in self.store:
            raise WaypointDenied(409, f'{new!r} adlı nokta zaten var')
        try:
            return self.store.rename(old, new).as_dict()
        except WaypointError as exc:
            raise WaypointDenied(400, str(exc)) from exc

    def delete(self, name: str) -> Dict[str, Any]:
        _get(self.store, name)
        self.store.remove(name)
        return {'ok': True, 'deleted': name, 'count': len(self.store)}

    def record(self, body: Any) -> Tuple[int, Dict[str, Any]]:
        d = _only(body, ('name', 'frame', 'overwrite'), ('name',),
                  'gövde {"name": "...", "frame": "World", "overwrite": false} olmalı')
        overwrite = d.get('overwrite', False)
        if not isinstance(overwrite, bool):
            raise WaypointDenied(400, "'overwrite' true ya da false olmalı")
        _name(d['name'])
        frame = _frame(d.get('frame', WORLD))
        reason = self._busy()
        if reason:
            raise WaypointDenied(409, f'{reason}: ölçülen poz hareket halinde olabilir, kayıt '
                                      'yapılmadı')
        q = self._measured()
        existed = d['name'] in self.store
        if existed and not overwrite:
            raise WaypointDenied(409, f"{d['name']!r} adlı nokta zaten var (üzerine yazmak için "
                                      '"overwrite": true)')
        if not existed and len(self.store) >= self.store.max_waypoints:
            raise WaypointDenied(409, f'en çok {self.store.max_waypoints} nokta; önce birini silin')
        try:
            wp = self.store.add(d['name'], q, frame, overwrite=overwrite)
        except WaypointError as exc:
            raise WaypointDenied(400, str(exc)) from exc
        return (200 if existed else 201), {**wp.as_dict(), 'replaced': existed,
                                           'source': 'measured'}

    def _measured(self) -> List[float]:
        pos = self._joints.joint_positions() if self._joints is not None else None
        names = self.store.chain.joint_names
        if not pos or any(n not in pos for n in names):
            raise WaypointDenied(503, 'eklem durumu okunamıyor: nokta kaydedilemedi')
        q = [float(pos[n]) for n in names]
        if not all(math.isfinite(v) for v in q):
            raise WaypointDenied(503, 'eklem durumu geçersiz (sonlu değil): nokta kaydedilemedi')
        return q


def frames_guard(api: Optional[WaypointApi]) -> Optional[Callable[[Mapping[str, Frame]], None]]:
    """For ``PUT /api/frames``: refuse a new frame list that removes or changes a frame that a
    waypoint is expressed in (the waypoint file would no longer load)."""
    if api is None:
        return None

    def guard(new: Mapping[str, Frame]) -> None:
        store = api.store
        broken = []
        for wp in store.list():
            if wp.frame.lower() == WORLD.lower() or store.frames is None:
                continue
            cur = store.frames.get(wp.frame) if wp.frame in store.frames.names() else None
            nxt = new.get(wp.frame)
            if (cur is None or nxt is None
                    or np.linalg.norm(np.array(cur.origin) - np.array(nxt.origin)) > 1e-9
                    or np.max(np.abs(cur.rotation_matrix - nxt.rotation_matrix)) > 1e-9):
                broken.append(wp.name)
        if broken:
            raise FrameInUse('bu çerçeve değişikliği şu noktaları geçersiz kılar: '
                             f'{", ".join(broken)} (önce bu noktaları silin ya da World\'de '
                             'yeniden kaydedin)')

    return guard


def verified_joint_goal(store: WaypointStore, name: str) -> Tuple[Optional[Tuple[float, ...]], str]:
    """(joints, '') for ``goto_waypoint``, or (None, why). The point is checked AGAIN against the
    limits and the safe box now (the file or the box may have changed since it was saved)."""
    try:
        wp = store.get(name)
        q = store.check_joints(list(wp.joints), f'nokta {name}')
    except WaypointError as exc:
        return None, str(exc)
    world = store.chain.fk(q)[:3, 3]
    why = store.zone.check(tuple(float(v) for v in world))
    if why:
        return None, f'nokta {name}: {why}'
    return q, ''
