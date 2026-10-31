"""User-facing hints for MoveIt error code names."""
from __future__ import annotations

from typing import Optional

_COLLISION_HINT = ('hedef büyük ihtimalle engel ya da robotun kendi gövdesiyle çarpışıyor '
                   "ya da ulaşılamaz. Hedefi biraz değiştir veya 'list' ile engellere bak.")

_HINTS = {
    'FAILURE': _COLLISION_HINT,
    'PLANNING_FAILED': _COLLISION_HINT,
    'START_STATE_IN_COLLISION': ('robot şu an bir engelle çarpışıyor; '
                                'engeli silmeyi dene (rmbox/clear).'),
    'GOAL_IN_COLLISION': 'hedef konum bir engelle çarpışıyor.',
    'TIMED_OUT': 'planlama süresi doldu; hedef çok zor ya da ulaşılamaz olabilir.',
}


def hint_for(code_name: str) -> Optional[str]:
    return _HINTS.get(code_name)
