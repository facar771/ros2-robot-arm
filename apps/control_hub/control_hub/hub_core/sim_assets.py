"""SİM'in robot verisi: profil YAML'ı ve ``view_model.json`` nerede aranır (geliştirme, ``.exe``, Docker).

Düzen (depoda ``src/robot_description/profile``; ``.exe``'de ``hub.spec`` aynısını
``robot_description/profile`` altına gömer)::

    <kök>/<robot>/<robot>.yaml                    profil (robot_core.profile)
    <kök>/generated/<robot>/view_model.json       3B model (``GET /api/model``)

Arama sırası: ``HUB_PROFILE_DIR``, ``_MEIPASS/robot_description/profile``, depo
``src/robot_description/profile``.
Hub bu dosyalara yalnız OKUR (UI_EMIRLER K2); profile yazılmaz.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import List, Optional

ROBOT_RE = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
PROFILE_ENV = 'HUB_PROFILE_DIR'


class SimAssetsError(ValueError):
    """Profil bulunamadı/geçersiz; ``str(exc)`` kullanıcıya gösterilebilir (dosya yolu içermez)."""


def profile_roots() -> List[Path]:
    roots: List[Path] = []
    if os.environ.get(PROFILE_ENV):
        roots.append(Path(os.environ[PROFILE_ENV]))
    if getattr(sys, 'frozen', False):
        roots.append(Path(getattr(sys, '_MEIPASS', '')) / 'robot_description' / 'profile')
    here = Path(__file__).resolve()
    if len(here.parents) > 4:                       # depo çıkışı: apps/control_hub/control_hub/hub_core/
        roots.append(here.parents[4] / 'src' / 'robot_description' / 'profile')
    return roots


def available_robots(root: Path) -> List[str]:
    """``generated/<robot>/`` klasörü VE ``<robot>/<robot>.yaml`` profili olan robotlar (sıralı)."""
    gen = root / 'generated'
    if not gen.is_dir():
        return []
    return sorted(d.name for d in gen.iterdir()
                  if d.is_dir() and ROBOT_RE.match(d.name) and (root / d.name / f'{d.name}.yaml').is_file())


def resolve(robot: Optional[str] = None):
    """``(robot adı, profil yolu, view_model yolu)``; ``robot`` yoksa ``generated/<robot>/`` olan ilk."""
    if robot is not None and not ROBOT_RE.match(robot):
        raise SimAssetsError(f'geçersiz robot adı: {robot!r}')
    last: Optional[SimAssetsError] = None
    for root in profile_roots():
        if not root.is_dir():
            continue
        name = robot or next(iter(available_robots(root)), None)
        if name and (root / name / f'{name}.yaml').is_file():
            return name, root / name / f'{name}.yaml', root / 'generated' / name / 'view_model.json'
        last = SimAssetsError(f'robot profili bulunamadı: {robot or "(varsayılan)"}')
    raise last or SimAssetsError('robot profil klasörü bulunamadı (HUB_PROFILE_DIR / src/robot_description)')
