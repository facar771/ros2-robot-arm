"""The demo scenario: home -> above green -> above red -> home, narrated in Turkish, with the tool
position *measured* (TF, through :class:`~robot_core.ports.ToolPositionPort`) after every move.

ROS-free. It stops at the first step that fails (a robot that did not reach a point is not sent on
to the next one) and never raises; the outcome is a :class:`ScenarioResult`.

The clearances are the lowest this arm reaches reliably (see ``pose_check`` and the access note in
docs/INTERFACES.md): joint 3 is limited to +-2.0 rad, so over objects close to the base the tool
cannot point straight down at low height.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from robot_core.ports import EventSink, NullEventSink, ToolPositionPort
from robot_core.skills import Skills

Vec = Tuple[float, float, float]


@dataclass(frozen=True)
class ScenarioStep:
    say: str                              # narration, shown before the move
    action: Tuple[Any, ...]               # ('home',) or ('above', colour, clearance_m)


DEFAULT_STEPS: Tuple[ScenarioStep, ...] = (
    ScenarioStep("Başlangıç: robotu 'home' pozuna götürüyorum.", ('home',)),
    ScenarioStep('Yeşil silindirin 25 cm üstüne gidiyorum; alet aşağı bakıyor.',
                 ('above', 'green', 0.25)),
    ScenarioStep('Şimdi kırmızı kutunun 30 cm üstüne geçiyorum.', ('above', 'red', 0.30)),
    ScenarioStep("İş bitti: 'home' pozuna dönüyorum.", ('home',)),
)
TOLERANCE_M = 0.015      # tool vs target (the planner's own tolerance is 5 mm)
# Second 'home' vs the first: the straight-up arm still sways ~1 cm after the controller is done,
# so it gets its own, wider bound (a wrong pose would be about a metre off).
HOME_TOLERANCE_M = 0.03
SETTLE_S = 1.0           # let the controller settle; TF follows /joint_states


@dataclass(frozen=True)
class StepReport:
    index: int                            # 1-based
    say: str
    ok: bool
    message: str
    target: Optional[Vec] = None
    measured: Optional[Vec] = None
    error_m: Optional[float] = None       # |measured - target| (or - the first home position)

    def as_dict(self) -> Dict[str, Any]:
        return {'step': self.index, 'say': self.say, 'ok': self.ok, 'message': self.message,
                'target': None if self.target is None else [round(v, 4) for v in self.target],
                'measured': None if self.measured is None else [round(v, 4) for v in self.measured],
                'error_cm': None if self.error_m is None else round(self.error_m * 100, 2)}


@dataclass(frozen=True)
class ScenarioResult:
    ok: bool
    message: str
    steps: Tuple[StepReport, ...]


def _fmt(v: Vec) -> str:
    return f'({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}) m'


def run_demo_scenario(skills: Skills, tool: ToolPositionPort,
                      say: Callable[[str], None] = print, *,
                      steps: Tuple[ScenarioStep, ...] = DEFAULT_STEPS,
                      tolerance: float = TOLERANCE_M, home_tolerance: float = HOME_TOLERANCE_M,
                      settle_s: float = SETTLE_S,
                      events: Optional[EventSink] = None,
                      sleep: Callable[[float], None] = time.sleep) -> ScenarioResult:
    sink = events or NullEventSink()
    reports: List[StepReport] = []
    reference: Optional[Vec] = None          # tool position at the first 'home': home must repeat
    total = len(steps)

    def finish(ok: bool, message: str) -> ScenarioResult:
        say(('[+] ' if ok else '[-] ') + message)
        result = ScenarioResult(ok, message, tuple(reports))
        try:
            sink.emit('scenario_result', {'name': 'demo_scenario', 'ok': ok, 'message': message,
                                          'steps': [r.as_dict() for r in reports]})
        except Exception:    # noqa: BLE001 - an event sink never stops the robot
            pass
        return result

    for number, step in enumerate(steps, start=1):
        say(f'[{number}/{total}] {step.say}')
        kind = step.action[0]
        try:
            if kind == 'home':
                outcome = skills.go_to_named_pose('home')
            else:
                outcome = skills.go_above(step.action[1], step.action[2])
        except Exception as exc:    # noqa: BLE001 - skills do not raise; a broken fake must not either
            outcome = None
            failure = f'beklenmeyen hata: {type(exc).__name__}: {exc}'
        else:
            failure = '' if outcome.ok else outcome.message
        if failure:
            reports.append(StepReport(number, step.say, False, failure))
            say(f'    Adım başarısız: {failure}')
            return finish(False, f'{number}. adımda durdu: {failure}')

        sleep(settle_s)
        measured = tool.tool_position()
        if measured is None:
            reports.append(StepReport(number, step.say, False, 'TF ile uç konum ölçülemedi'))
            return finish(False, f'{number}. adımda uç konum TF ile ölçülemedi (hareket yapıldı '
                                 'ama doğrulanamadı)')
        if kind == 'home':
            target = reference
            if reference is None:
                reference = measured
        else:
            target = tuple(outcome.data.get('target', ()))
            if len(target) != 3:
                target = None
        if target is None and kind != 'home':
            reports.append(StepReport(number, step.say, False, 'hedef konum bilinmiyor', None,
                                      measured))
            return finish(False, f'{number}. adımda hedef konum bilinmiyor, doğrulanamadı')
        if target is None:                                   # first home: the reference itself
            reports.append(StepReport(number, step.say, True, 'referans uç konum alındı', None,
                                      measured))
            say(f'    Ölçülen uç konum {_fmt(measured)} (home referansı).')
            continue
        error = math.dist(measured, target)
        what = 'hedefe' if kind != 'home' else "ilk 'home' konumuna"
        limit = tolerance if kind != 'home' else home_tolerance
        verified = error <= limit
        reports.append(StepReport(number, step.say, verified,
                                  'doğrulandı' if verified else f'{what} {error * 100:.1f} cm uzak',
                                  target, measured, error))
        say(f'    Ölçülen uç konum {_fmt(measured)}; {what} uzaklık {error * 100:.1f} cm '
            f'-> {"doğrulandı" if verified else "TOLERANS AŞILDI"}.')
        if not verified:
            return finish(False, f'{number}. adımda uç konum {what} {error * 100:.1f} cm uzakta '
                                 f'(en fazla {limit * 100:.1f} cm)')
    return finish(True, f'senaryo tamamlandı: {total} adımın hepsi TF ölçümüyle doğrulandı')
