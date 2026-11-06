"""Camera platform (pan / tilt / slide) over the Robot API (H4), ROS-free: the rules around a port.

    GET  /api/camera    state, pose, limits, rate caps, last reply, e-stop flag
    POST /api/camera    one command per request:
        {"op": "goto", "pan"?, "tilt"?, "slide"?, "speed"?}      absolute target (rad / rad / m)
        {"op": "step", "pan"?, "tilt"?, "slide"?, "speed"?}      RELATIVE: measured pose + delta
        {"op": "jog",  "pan_rate"?, "tilt_rate"?, "slide_rate"?, "ttl_ms"?}   hold-to-move
        {"op": "stop"}

The platform is the separate pan/tilt/slide rig of ``tracking.sdf`` (not the arm); the node that
moves it is ``camera_manual`` (H3), which stays the authority: it validates every command again
(limits, rate caps, dead-man, ownership by tracking) and refuses a bad one as a whole. This module
adds the Robot-API side: strict bodies, a server-side TTL clamp for ``jog`` (default 250 ms, at most
500 ms), 423 while the e-stop is latched, and a stop that is always allowed.

Camera motion does not touch the arm's safety, but the EMERGENCY STOP stops the platform too
(``CameraStop`` is part of the stop path) and a latched e-stop refuses new camera motion; ``stop``
itself is always accepted. Software only: not a replacement for a hardware stop.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Dict, Optional, Protocol, Tuple

from robot_core.supervisor import ESTOP_MESSAGE, EStop

AXES = ('pan', 'tilt', 'slide')
GOTO_KEYS = ('op', 'pan', 'tilt', 'slide', 'speed')
JOG_KEYS = ('op', 'pan_rate', 'tilt_rate', 'slide_rate', 'ttl_ms')
DEFAULT_TTL_MS = 250
MAX_TTL_MS = 500
MIN_TTL_MS = 50
STALE_S = 2.0            # no status from the camera node for this long = not available
MAX_BODY = 512
# node reply code -> HTTP status
_CODE_STATUS = {'busy': 409, 'no_state': 503}


class CameraDenied(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class CameraPort(Protocol):
    def send(self, command: Dict[str, Any], timeout_s: float = 1.0) -> Optional[Dict[str, Any]]:
        """Publish one command and return the node's reply, None when none arrived in time."""

    def publish_stop(self) -> None:
        """Fire-and-forget ``{"op":"stop"}`` (must not block; safe from any thread)."""

    def status(self) -> Optional[Dict[str, Any]]:
        """Latest ``/camera/manual/status`` with ``age_s``; None before the first one."""

    def limits(self) -> Dict[str, Any]:
        """``{"pan": [lo, hi], "tilt": [lo, hi], "slide": [lo, hi], "rate_caps": {...}}``."""


class CameraStop:
    """A ``StopPort`` for the Supervisor: stops the arm first, then the camera platform."""

    def __init__(self, arm: Any, camera: CameraPort):
        self._arm, self._camera = arm, camera

    def stop(self) -> None:
        try:
            if self._arm is not None:
                self._arm.stop()
        finally:                                  # the camera is stopped even if the arm raised
            try:
                self._camera.publish_stop()
            except Exception:                     # noqa: BLE001 - the arm's stop result matters
                pass


def _num(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CameraDenied(400, f'{key}: sonlu bir sayı olmalı, bulunan: {value!r}')
    return float(value)


class CameraApi:
    def __init__(self, port: CameraPort, estop: EStop,
                 reply_timeout_s: float = 1.0):
        self.port = port
        self.estop = estop
        self._timeout = reply_timeout_s
        self._lock = threading.Lock()

    # ---- reading ------------------------------------------------------------------------------
    def document(self) -> Dict[str, Any]:
        st = self.port.status()
        fresh = bool(st) and st.get('age_s', 1e9) <= STALE_S
        doc: Dict[str, Any] = {'available': fresh, 'estop': self.estop.is_set,
                               'limits': self.port.limits(), 'state': None, 'q': None,
                               'last': None, 'tracking_busy': None,
                               'status_age_s': None if not st else round(st.get('age_s', 0.0), 2),
                               'tilt_note': 'tilt: artı = kamera AŞAĞI bakar'}
        if fresh:
            q = st.get('q') or {}
            doc.update(state=st.get('state'), last=st.get('last'),
                       tracking_busy=st.get('tracking_busy'),
                       q=None if not q else {k: q.get(k) for k in AXES})
        if self.estop.is_set and fresh and st.get('state') not in (None, 'idle'):
            self.port.publish_stop()              # a motion that outlived the latch is stopped
        return doc

    # ---- commands -----------------------------------------------------------------------------
    def command(self, body: Any) -> Tuple[int, Dict[str, Any]]:
        if not isinstance(body, dict) or body.get('op') not in ('goto', 'step', 'jog', 'stop'):
            raise CameraDenied(400, 'gövde {"op": "goto"|"step"|"jog"|"stop", …} olmalı')
        op = body['op']
        with self._lock:
            if op == 'stop':
                return self._stop()
            if self.estop.is_set:
                raise CameraDenied(423, f'{ESTOP_MESSAGE}: kamera hareketi reddedildi',
                                   state='estopped')
            node_cmd = self._to_node(op, body)
            reply = self.port.send(node_cmd, self._timeout)
            if self.estop.is_set:                 # the latch landed while we were sending
                self.port.publish_stop()
                raise CameraDenied(423, f'{ESTOP_MESSAGE}: kamera durduruldu', state='estopped')
            return self._answer(op, reply)

    def _stop(self) -> Tuple[int, Dict[str, Any]]:
        reply = self.port.send({'op': 'stop'}, self._timeout)
        if reply is None:
            self.port.publish_stop()              # still try: a stop must not be lost
            raise CameraDenied(504, 'kamera platformu yanıt vermedi; durdurma yine de gönderildi')
        return 200, {'ok': True, 'op': 'stop', 'reply': reply}

    def _to_node(self, op: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if op == 'jog':
            self._keys(body, JOG_KEYS)
            cmd: Dict[str, Any] = {'op': 'jog'}
            for key in ('pan_rate', 'tilt_rate', 'slide_rate'):
                if key in body:
                    cmd[key] = _num(body[key], key)
            if len(cmd) == 1:
                raise CameraDenied(400, 'jog için en az bir hız gerekli (pan_rate, tilt_rate, '
                                        'slide_rate)')
            ttl = _num(body.get('ttl_ms', DEFAULT_TTL_MS), 'ttl_ms')
            cmd['ttl_s'] = min(max(ttl, MIN_TTL_MS), MAX_TTL_MS) / 1000.0     # server-side clamp
            return cmd
        self._keys(body, GOTO_KEYS)
        cmd = {'op': 'goto'}
        deltas = {k: _num(body[k], k) for k in AXES if k in body}
        if not deltas:
            raise CameraDenied(400, f'{op} için en az bir eksen gerekli (pan, tilt, slide)')
        if 'speed' in body:
            speed = _num(body['speed'], 'speed')
            if not 0.0 < speed <= 1.0:
                raise CameraDenied(400, 'speed 0 ile 1 arasında olmalı')
            cmd['speed'] = speed
        if op == 'step':
            st = self.port.status()
            q = (st or {}).get('q') if st and st.get('age_s', 1e9) <= STALE_S else None
            if not q or any(q.get(k) is None for k in deltas):
                raise CameraDenied(503, 'kamera konumu okunamıyor: göreli komut verilemez')
            deltas = {k: float(q[k]) + v for k, v in deltas.items()}
        cmd.update(deltas)                        # a target outside the limits is REFUSED by the
        return cmd                                # node (never clamped)

    @staticmethod
    def _keys(body: Dict[str, Any], allowed: Tuple[str, ...]) -> None:
        extra = sorted(set(body) - set(allowed))
        if extra:
            raise CameraDenied(400, f'bilinmeyen anahtar: {extra}; izinli: {list(allowed)}')

    def _answer(self, op: str, reply: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
        if reply is None:
            raise CameraDenied(504, 'kamera platformu yanıt vermedi (camera_manual çalışıyor mu?)')
        if reply.get('accepted'):
            return 200, {'ok': True, 'op': op, 'reply': reply}
        code = str(reply.get('code'))
        raise CameraDenied(_CODE_STATUS.get(code, 400),
                           f'kamera komutu reddedildi ({code}): {reply.get("message")}',
                           code=code)
