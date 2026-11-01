"""ROS'suz, donanımsız robot simülasyonu.

Gerçek robotun komut beyaz listesini (``home``, ``objects``, ``above``) uygular; eklemler ve uç konum
zamanla değişir, ``cancel``/ACİL DUR hareketi ortasında keser. Eklem değerleri **yalnızca görsel**:
gerçek ters kinematik yoktur, amaç arayüzün canlı akışını denemektir.
"""
from __future__ import annotations

import math
import threading
from typing import Any, Dict, List, Tuple

from control_hub.hub_core.backend import BackendResult
from control_hub.hub_core.commands import CommandError, parse_robot_command

Vec = Tuple[float, float, float]
JOINTS = ('joint_1', 'joint_2', 'joint_3', 'joint_4', 'joint_5', 'joint_6')
HOME_TOOL: Vec = (0.0, 0.0, 0.62)
# workspace.sdf'teki nesneler (name -> merkez, boyut)
OBJECTS: Dict[str, Tuple[Vec, Vec]] = {
    'red#0': ((0.35, 0.15, 0.03), (0.06, 0.06, 0.06)),
    'green#0': ((0.40, -0.10, 0.05), (0.06, 0.06, 0.10)),
    'blue#0': ((0.45, 0.00, 0.04), (0.08, 0.08, 0.08)),
}


def _fake_joints(tool: Vec) -> List[float]:
    """Uç konumdan görsel olarak makul (ama sahte) eklem açıları."""
    x, y, z = tool
    r = math.hypot(x, y)
    return [math.atan2(y, x) if r > 1e-6 else 0.0, 1.1 * r, -1.4 * r - 0.4 * (0.62 - z),
            0.0, 0.5 * r + 0.3 * (0.62 - z), 0.0]


class FakeBackend:
    name = 'fake'
    can_interrupt = True

    def __init__(self, time_scale: float = 1.0, tool_speed_m_s: float = 0.25, tick_s: float = 0.05):
        self.time_scale = time_scale
        self.tool_speed = tool_speed_m_s
        self.tick_s = tick_s
        self.objects = dict(OBJECTS)
        self._tool: Vec = HOME_TOOL
        self._joints: List[float] = [0.0] * 6
        self._moving = False
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._failures: List[Tuple[str, str]] = []
        self.history: List[str] = []

    # --- test/geliştirme kancaları -------------------------------------------------------------
    def inject_failure(self, substring: str, message: str = 'hedefe ulaşılamadı') -> None:
        """İçinde ``substring`` geçen sonraki komutlar robotu hiç oynatmadan başarısız olur."""
        self._failures.append((substring, message))

    # --- RobotBackend --------------------------------------------------------------------------
    def execute(self, command: str, cancel: threading.Event) -> BackendResult:
        self.history.append(command)
        try:
            cmd = parse_robot_command(command)
        except CommandError as exc:
            return BackendResult(False, str(exc), {'rejected': True})
        if self._halt.is_set():
            return BackendResult(False, 'ACİL DUR kilidi açık: komut çalıştırılmadı', {'estop': True})
        text = cmd.text()
        for sub, msg in self._failures:
            if sub in text:
                return BackendResult(False, msg)
        if cmd.name == 'objects':
            return BackendResult(True, f'{len(self.objects)} nesne', {'objects': self._objects_list()})
        if cmd.name == 'home':
            return self._move_to(HOME_TOOL, cancel, 'home pozuna gidildi')
        key = next((n for n in self.objects if n.startswith(cmd.color + '#')), None)
        if key is None:
            return BackendResult(False, f'{cmd.color} renkte nesne yok')
        (ox, oy, oz), _ = self.objects[key]
        clearance = 0.10 if cmd.clearance_m is None else cmd.clearance_m
        target = (ox, oy, oz + clearance)
        res = self._move_to(target, cancel, f'{key} nesnesinin {clearance * 100:.0f} cm üstüne gidildi')
        return BackendResult(res.ok, res.message, {**res.data, 'target': list(target)})

    def emergency_stop(self) -> None:
        self._halt.set()

    def reset_emergency_stop(self) -> None:
        self._halt.clear()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            joints = list(self._joints)
            tool = list(self._tool)
            moving = self._moving
        return {
            'name': self.name, 'connected': True, 'can_interrupt': self.can_interrupt,
            'moving': moving, 'tool': [round(v, 4) for v in tool],
            'joints': [{'name': n, 'rad': round(v, 4), 'deg': round(math.degrees(v), 1)}
                       for n, v in zip(JOINTS, joints)],
            'objects': self._objects_list(),
        }

    def close(self) -> None:
        self._halt.set()

    # --- iç ------------------------------------------------------------------------------------
    def _objects_list(self) -> List[Dict[str, Any]]:
        return [{'name': n, 'label': n.split('#')[0], 'position': list(p), 'size': list(s)}
                for n, (p, s) in self.objects.items()]

    def _move_to(self, target: Vec, cancel: threading.Event, done: str) -> BackendResult:
        with self._lock:
            start, start_j = self._tool, list(self._joints)
        end_j = _fake_joints(target)
        dist = math.dist(start, target)
        duration = max(0.4, dist / self.tool_speed) * self.time_scale
        ticks = max(1, math.ceil(duration / self.tick_s))
        with self._lock:
            self._moving = True
        try:
            for i in range(1, ticks + 1):
                # cancel.wait(): kesilince beklemeden döner
                if cancel.wait(duration / ticks) or self._halt.is_set():
                    return BackendResult(False, 'hareket kesildi', {'interrupted': True})
                t = i / ticks
                s = t * t * (3 - 2 * t)                       # yumuşak başla/bitir
                with self._lock:
                    self._tool = tuple(a + (b - a) * s for a, b in zip(start, target))   # type: ignore
                    self._joints = [a + (b - a) * s for a, b in zip(start_j, end_j)]
            return BackendResult(True, done)
        finally:
            with self._lock:
                self._moving = False

