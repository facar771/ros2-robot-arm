"""SİM iş modeli: elle ilerletilen saat, iş kaydı ve ``/api/move`` gövdesi ayrıştırma (saf, testli)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from control_hub.hub_core.sim_plan import Plan, PlanError

MOVE_KEYS = {'joint': {'type', 'joints_deg', 'speed_pct'},
             'linear': {'type', 'pos_m', 'rpy_deg', 'orientation', 'speed_pct'},
             'tool': {'type', 'open_pct', 'speed_pct'}}


class SimClock:
    """Elle ilerletilen saat (testler): ``advance`` dışında zaman akmaz."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t


@dataclass
class Job:
    id: str
    type: str                    # 'joint' | 'linear' | 'tool'
    plan: Optional[Plan]
    duration_s: float
    target_pct: Optional[float] = None       # yalnız 'tool'
    from_pct: float = 0.0
    t: float = 0.0
    state: str = 'running'       # running | paused | done | failed | cancelled
    error: Optional[str] = None
    message: str = ''

    def doc(self) -> Dict[str, Any]:
        return {'id': self.id, 'type': self.type, 'state': self.state, 'error': self.error,
                'message': self.message,
                'progress_pct': round(min(100.0, 100.0 * self.t / self.duration_s), 1)}


def parse_move(body: Any, default_speed_pct: float) -> Tuple[str, tuple, float]:
    """``/api/move`` gövdesi -> ``(tür, argümanlar, speed_pct)``; geçersizse ``PlanError('bad_request')``."""
    if not isinstance(body, dict) or body.get('type') not in MOVE_KEYS:
        raise PlanError('bad_request', 'gövde {"type": "joint" | "linear" | "tool", ...} olmalı')
    extra = set(body) - MOVE_KEYS[body['type']]
    if extra:
        raise PlanError('bad_request', f'bilinmeyen alan: {", ".join(sorted(extra))}')
    speed = body.get('speed_pct', default_speed_pct)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 1 <= speed <= 100:
        raise PlanError('bad_request', 'speed_pct 1-100 arasında bir sayı olmalı')
    kind = body['type']
    if kind == 'joint':
        return kind, (body.get('joints_deg'),), float(speed)
    if kind == 'linear':
        if 'pos_m' not in body:
            raise PlanError('bad_request', 'linear hareket pos_m ister')
        return kind, (body['pos_m'], body.get('rpy_deg'), body.get('orientation', 'fixed')), float(speed)
    pct = body.get('open_pct')
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
        raise PlanError('bad_request', 'open_pct 0-100 arasında bir sayı olmalı')
    return kind, (float(pct),), float(speed)
