"""ROS side of the camera platform API (H4): a ``CameraPort`` over the ``camera_manual`` topics.

Commands go to ``/camera/manual/command`` (JSON in ``std_msgs/String``), the node's one reply per
command comes back on ``/camera/manual/reply``, its 5 Hz state on ``/camera/manual/status``. One
command at a time (a lock) so a reply is matched to its command by ``op``. ``camera_manual`` stays
the authority on limits, rate caps and ownership; the limits shown here come from the same
``robot_vision.manual.ManualConfig`` it uses (the profile has no platform section yet).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Optional

from rclpy.node import Node
from robot_vision.manual import ManualConfig
from std_msgs.msg import String


class RosCameraPort:
    def __init__(self, node: Node, config: Optional[ManualConfig] = None):
        self._config = config or ManualConfig()
        self._pub = node.create_publisher(String, '/camera/manual/command', 10)
        node.create_subscription(String, '/camera/manual/reply', self._on_reply, 10)
        node.create_subscription(String, '/camera/manual/status', self._on_status, 10)
        self._lock = threading.Lock()                 # one command in flight
        self._cond = threading.Condition()
        self._pending_op: Optional[str] = None
        self._reply: Optional[Dict[str, Any]] = None
        self._status: Optional[Dict[str, Any]] = None
        self._status_at = 0.0

    # ---- CameraPort ---------------------------------------------------------------------------
    def send(self, command: Dict[str, Any], timeout_s: float = 1.0) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._cond:
                self._pending_op, self._reply = command.get('op'), None
            self._pub.publish(String(data=json.dumps(command)))
            end = time.monotonic() + timeout_s
            with self._cond:
                while self._reply is None and time.monotonic() < end:
                    self._cond.wait(timeout=max(0.0, end - time.monotonic()))
                reply, self._pending_op = self._reply, None
            return reply

    def publish_stop(self) -> None:
        self._pub.publish(String(data='{"op": "stop"}'))

    def status(self) -> Optional[Dict[str, Any]]:
        with self._cond:
            if self._status is None:
                return None
            return {**self._status, 'age_s': time.monotonic() - self._status_at}

    def limits(self) -> Dict[str, Any]:
        lim = self._config.limits
        yaw_cap, slide_cap, pitch_cap = self._config.caps()
        return {'pan': list(lim.yaw), 'tilt': list(lim.pitch), 'slide': list(lim.slide),
                'rate_caps': {'pan': yaw_cap, 'tilt': pitch_cap, 'slide': slide_cap},
                'source': 'robot_vision.manual.ManualConfig (tracking.sdf); profilde bölüm yok'}

    # ---- subscriptions (executor thread) ------------------------------------------------------
    def _on_reply(self, msg: String) -> None:
        try:
            doc = json.loads(msg.data)
        except ValueError:
            return
        with self._cond:
            # a refusal raised while parsing (out_of_limits, rate_too_high, bad_command...) carries
            # op=None; stale replies cannot be confused with it because send() is serialized
            if isinstance(doc, dict) and self._pending_op is not None \
                    and doc.get('op') in (self._pending_op, None):
                self._reply = doc
                self._cond.notify_all()

    def _on_status(self, msg: String) -> None:
        try:
            doc = json.loads(msg.data)
        except ValueError:
            return
        if isinstance(doc, dict):
            with self._cond:
                self._status, self._status_at = doc, time.monotonic()
