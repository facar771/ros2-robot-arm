"""Robot API v1'i, ROS olmadan, **gerçek sunucu koduyla** çalıştırır (geliştirme/doğrulama aracı).

``robot_core.webview.WebView`` + ``CommandRunner`` + ``JobManager`` + ``Supervisor`` (yani robotun HTTP
sözleşmesinin kendisi: /api/jobs, /api/stop, /api/reset, 409/423 kuralları, hız doğrulamalı acil dur)
gerçek koddur; yalnızca hareket (Skills) ve kol (hız/durdurma) simüle edilir. Hub'ın
``RobotApiBackend``'ini ROS/Docker kurmadan sözleşmeye karşı denemeye yarar.

    python -m tools.robot_stub --port 8080 --speed 0.3

ŞİMDİLİK: sunucu mantığı ``src/robot_core``'dan alınır (repo kökündeki kaynak yolu otomatik eklenir).
Üretim Hub'ı bunu İÇE AKTARMAZ; yalnızca testler ve bu araç kullanır.
"""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROBOT_CORE = Path(__file__).resolve().parents[3] / 'src' / 'robot_core'
if str(ROBOT_CORE) not in sys.path:
    sys.path.append(str(ROBOT_CORE))      # sona: Hub'ın kendi `tests` paketi gölgelenmesin

from robot_core.jobs import JobManager  # noqa: E402
from robot_core.models import ObjectInfo, SkillResult  # noqa: E402
from robot_core.supervisor import CancelFlag, EStop, Supervisor  # noqa: E402
from robot_core.webcmd import CommandRunner  # noqa: E402
from robot_core.webview import SceneObject, Snapshot, WebView  # noqa: E402

JOINTS = ('joint_1', 'joint_2', 'joint_3', 'joint_4', 'joint_5', 'joint_6')
HOME: Tuple[float, float, float] = (0.0, 0.0, 0.62)
OBJECTS = (
    ObjectInfo('obj_red_1', 'red', (0.35, 0.15, 0.03), (0.06, 0.06, 0.06)),
    ObjectInfo('obj_green_1', 'green', (0.40, -0.10, 0.05), (0.06, 0.06, 0.10)),
    ObjectInfo('obj_blue_1', 'blue', (0.45, 0.00, 0.04), (0.08, 0.08, 0.08)),
)
Vec = Tuple[float, float, float]


class SimArm:
    """Simüle kol: hız ölçülür (Supervisor durduğunu bundan doğrular), ``stop()`` hareketi keser."""

    def __init__(self, speed_m_s: float = 0.25, time_scale: float = 1.0):
        self.speed, self.scale = speed_m_s, time_scale
        self.tool: Vec = HOME
        self._vel = 0.0
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self.stop_calls = 0
        self.stop_delay_s = 0.0          # >0: "stop" gecikir (doğrulama süresini denemek için)

    # --- Supervisor portları ---
    def joint_velocities(self) -> Dict[str, float]:
        with self._lock:
            v = self._vel
        return {n: (v if i == 0 else 0.0) for i, n in enumerate(JOINTS)}

    def stop(self) -> None:
        self.stop_calls += 1
        self._halt.set()

    def joints(self) -> Dict[str, float]:
        x, y, z = self.tool
        r = math.hypot(x, y)
        vals = [math.atan2(y, x) if r > 1e-6 else 0.0, 1.1 * r, -1.4 * r - 0.4 * (0.62 - z),
                0.0, 0.5 * r + 0.3 * (0.62 - z), 0.0]
        return dict(zip(JOINTS, vals))

    # --- hareket ---
    def move(self, target: Vec, cancel: CancelFlag, estop: EStop) -> bool:
        """True: vardı. False: iptal/ACİL DUR ile kesildi."""
        self._halt.clear()
        start = self.tool
        dist = math.dist(start, target)
        dur = max(0.4, dist / self.speed) * self.scale
        ticks = max(1, math.ceil(dur / 0.05))
        prev = 0.0
        try:
            for i in range(1, ticks + 1):
                time.sleep(dur / ticks)
                if self._halt.is_set() or cancel.is_set or estop.is_set:
                    if self.stop_delay_s:
                        time.sleep(self.stop_delay_s)
                    return False
                s = (i / ticks) ** 2 * (3 - 2 * i / ticks)
                self.tool = tuple(a + (b - a) * s for a, b in zip(start, target))   # type: ignore
                with self._lock:
                    self._vel = abs(s - prev) * dist / max(dur / ticks, 1e-6) * 2
                prev = s
            return True
        finally:
            with self._lock:
                self._vel = 0.0


class SimSkills:
    def __init__(self, arm: SimArm, cancel: CancelFlag, estop: EStop):
        self.arm, self.cancel, self.estop = arm, cancel, estop
        self.objects: List[ObjectInfo] = list(OBJECTS)

    def list_objects(self) -> List[ObjectInfo]:
        return list(self.objects)

    def _go(self, skill: str, target: Vec, data: dict) -> SkillResult:
        if self.arm.move(target, self.cancel, self.estop):
            return SkillResult(True, skill, 'hedefe ulaşıldı', 'SUCCESS', data)
        return SkillResult(False, skill, 'hareket iptal edildi', 'PREEMPTED')

    def go_to_named_pose(self, name: str) -> SkillResult:
        if name != 'home':
            return SkillResult(False, 'go_to_named_pose', f"bilinmeyen poz '{name}'")
        return self._go('go_to_named_pose', HOME, {'pose': name})

    def go_above(self, object_name: str, height: float = 0.10) -> SkillResult:
        obj = next((o for o in self.objects if object_name in (o.label, o.name)), None)
        if obj is None:
            return SkillResult(False, 'go_above', f"bilinmeyen nesne '{object_name}'")
        target = (obj.position[0], obj.position[1], obj.top_z + height)
        if target[2] > 0.70:        # robotun güvenli kutusu gibi: kutu dışı hedef reddedilir
            return SkillResult(False, 'go_above', 'hedef güvenli çalışma kutusunun dışında',
                               'SAFE_ZONE')
        return self._go('go_above', target, {'object': obj.name, 'target': list(target)})


class _Tool:
    def __init__(self, arm: SimArm):
        self.arm = arm

    def tool_position(self) -> Optional[Vec]:
        return self.arm.tool


class _Source:
    def __init__(self, arm: SimArm, skills: SimSkills):
        self.arm, self.skills = arm, skills

    def snapshot(self) -> Snapshot:
        return Snapshot(
            objects=tuple(SceneObject(o.label, 0, o.position, o.size) for o in self.skills.objects),
            objects_age_s=0.1, joint_names=JOINTS, joint_positions=self.arm.joints(),
            tool_link='link_6', tool_position=self.arm.tool,
            joint_velocities=self.arm.joint_velocities())


class RobotStub:
    """Gerçek WebView sunucusu + simüle robot. ``port=0``: boş port seç (``.port``)."""

    def __init__(self, port: int = 0, speed_m_s: float = 0.25, time_scale: float = 1.0,
                 mode: str = 'gazebo'):
        self.estop, self.cancel = EStop(), CancelFlag()
        self.arm = SimArm(speed_m_s, time_scale)
        self.skills = SimSkills(self.arm, self.cancel, self.estop)
        self.runner = CommandRunner(self.skills, _Tool(self.arm), estop=self.estop)
        self.jobs = JobManager(self.runner, self.cancel, self.estop)
        self.supervisor = Supervisor(self.estop, self.arm, JOINTS, stop_port=self.arm,
                                     runner=self.runner, mode=mode,
                                     safety={'zone': {'frame': 'world', 'box': {
                                         'x': [0.05, 0.75], 'y': [-0.5, 0.5], 'z': [0.02, 0.70]}},
                                         'max_velocity_scale': 0.5, 'velocity_scale': 0.5})
        self.view = WebView(_Source(self.arm, self.skills), port=port, commands=self.runner,
                            supervisor=self.supervisor, mode=mode, jobs=self.jobs)
        self.view.start_background()

    @property
    def port(self) -> int:
        return self.view.port

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    def close(self) -> None:
        self.view.shutdown()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--speed', type=float, default=1.0, help='zaman ölçeği (0.3 = 3 kat hızlı)')
    ap.add_argument('--mode', default='gazebo', choices=('mock', 'gazebo', 'real'))
    a = ap.parse_args()
    stub = RobotStub(a.port, time_scale=a.speed, mode=a.mode)
    print(f'Robot API v1 (simüle kol, gerçek sunucu kodu): {stub.url}  Ctrl-C ile durdur')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stub.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
