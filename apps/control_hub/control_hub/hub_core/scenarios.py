"""Senaryo tanımları (scenarios.yaml) ve arayüz yapılandırması (ui.config.json).

Tasarım değişikliği = küçük dosya değişikliği: butonlar ``scenarios.yaml``'dan, yerleşim ve tema
``ui.config.json``'dan gelir. Yükleme **tüm hataları birden** raporlar (ilkinde durmaz) ve Hub'ı hatalı
dosyayla başlatmaz.

Adım türleri: ``robot: <komut>`` (beyaz liste: commands.py) ve ``wait: <saniye>``; ikisinde de isteğe
bağlı ``say`` (adım başında arayüzde gösterilen anlatım). PR-H4 ``device`` adımını ekler.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from control_hub.hub_core.commands import CommandError, parse_robot_command

ID_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
CSS_KEY_RE = re.compile(r'^--[a-z0-9-]{1,32}$')
CSS_VAL_RE = re.compile(r'^[#\w\s%.,()/-]{1,64}$')
MAX_STEPS = 50
MAX_WAIT_S = 60.0


class ConfigError(ValueError):
    """Yapılandırma geçersiz; ``problems`` tüm sorunları listeler."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__('Yapılandırma hataları:\n  - ' + '\n  - '.join(problems))


@dataclass(frozen=True)
class Step:
    kind: str                       # 'robot' | 'wait'
    command: str = ''               # robot: normalleştirilmiş komut
    seconds: float = 0.0            # wait
    say: str = ''


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    steps: Tuple[Step, ...]
    icon: str = ''
    color: str = ''
    description: str = ''
    confirm: bool = False           # arayüzde "emin misin" ikinci dokunuşu

    def public(self) -> Dict[str, Any]:
        return {'id': self.id, 'label': self.label, 'icon': self.icon, 'color': self.color,
                'description': self.description, 'confirm': self.confirm, 'steps': len(self.steps)}


def _step(raw: Any, where: str, problems: List[str]) -> Optional[Step]:
    if not isinstance(raw, dict):
        problems.append(f'{where}: adım bir sözlük olmalı')
        return None
    unknown = set(raw) - {'robot', 'wait', 'say'}
    if unknown:
        problems.append(f'{where}: bilinmeyen anahtar(lar) {sorted(unknown)}')
    say = raw.get('say', '')
    if not isinstance(say, str) or len(say) > 200:
        problems.append(f"{where}: 'say' en çok 200 karakterlik metin olmalı")
        say = ''
    kinds = [k for k in ('robot', 'wait') if k in raw]
    if len(kinds) != 1:
        problems.append(f"{where}: tam olarak biri gerekli: 'robot' ya da 'wait'")
        return None
    if kinds[0] == 'robot':
        try:
            return Step('robot', command=parse_robot_command(raw['robot']).text(), say=say)
        except CommandError as exc:
            problems.append(f'{where}: {exc}')
            return None
    sec = raw['wait']
    if isinstance(sec, bool) or not isinstance(sec, (int, float)) or not 0 < sec <= MAX_WAIT_S:
        problems.append(f"{where}: 'wait' 0-{MAX_WAIT_S:g} saniye arasında sayı olmalı")
        return None
    return Step('wait', seconds=float(sec), say=say)


def parse_scenarios(doc: Any) -> Dict[str, Scenario]:
    problems: List[str] = []
    items = doc.get('scenarios') if isinstance(doc, dict) else None
    if not isinstance(items, list) or not items:
        raise ConfigError(["scenarios.yaml: üst düzeyde boş olmayan bir 'scenarios:' listesi gerekli"])
    out: Dict[str, Scenario] = {}
    for i, raw in enumerate(items):
        where = f'senaryo #{i + 1}'
        if not isinstance(raw, dict):
            problems.append(f'{where}: sözlük olmalı')
            continue
        sid = raw.get('id')
        if not isinstance(sid, str) or not ID_RE.match(sid):
            problems.append(f"{where}: 'id' küçük harfle başlayan [a-z0-9_] olmalı (en çok 40)")
            continue
        where = f"senaryo '{sid}'"
        if sid in out:
            problems.append(f'{where}: id tekrar ediyor')
            continue
        unknown = set(raw) - {'id', 'label', 'icon', 'color', 'description', 'confirm', 'steps'}
        if unknown:
            problems.append(f'{where}: bilinmeyen anahtar(lar) {sorted(unknown)}')
        label = raw.get('label')
        if not isinstance(label, str) or not label.strip() or len(label) > 40:
            problems.append(f"{where}: 'label' 1-40 karakter olmalı")
            label = sid
        color = raw.get('color', '')
        if color and (not isinstance(color, str) or not COLOR_RE.match(color)):
            problems.append(f"{where}: 'color' #rrggbb olmalı")
            color = ''
        desc = raw.get('description', '')
        icon = raw.get('icon', '')
        if not isinstance(desc, str) or len(desc) > 200:
            problems.append(f"{where}: 'description' en çok 200 karakter")
            desc = ''
        if not isinstance(icon, str) or len(icon) > 8:
            problems.append(f"{where}: 'icon' en çok 8 karakter (emoji)")
            icon = ''
        confirm = raw.get('confirm', False)
        if not isinstance(confirm, bool):
            problems.append(f"{where}: 'confirm' true/false olmalı")
            confirm = False
        steps_raw = raw.get('steps')
        if not isinstance(steps_raw, list) or not 1 <= len(steps_raw) <= MAX_STEPS:
            problems.append(f"{where}: 'steps' 1-{MAX_STEPS} adımlık liste olmalı")
            continue
        steps = [_step(s, f'{where}, adım {j + 1}', problems) for j, s in enumerate(steps_raw)]
        if all(s is not None for s in steps):
            out[sid] = Scenario(sid, label.strip(), tuple(steps), icon, color, desc, confirm)  # type: ignore
    if problems:
        raise ConfigError(problems)
    return out


def load_scenarios(path: Path) -> Dict[str, Scenario]:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError([f'{path}: okunamadı/çözümlenemedi: {exc}']) from exc
    return parse_scenarios(doc)


# --- arayüz yapılandırması ---------------------------------------------------------------------
PANELS = ('status', 'robot', 'log')


def parse_ui_config(doc: Any, scenarios: Dict[str, Scenario]) -> Dict[str, Any]:
    problems: List[str] = []
    if not isinstance(doc, dict):
        raise ConfigError(['ui.config.json: üst düzey bir nesne olmalı'])
    title = doc.get('title', 'Robot Kontrol Merkezi')
    if not isinstance(title, str) or not 1 <= len(title) <= 60:
        problems.append("'title' 1-60 karakter olmalı")
        title = 'Robot Kontrol Merkezi'
    theme = doc.get('theme', {})
    clean_theme: Dict[str, str] = {}
    if not isinstance(theme, dict):
        problems.append("'theme' bir nesne olmalı")
    else:
        for k, v in theme.items():
            if not CSS_KEY_RE.match(str(k)) or not isinstance(v, str) or not CSS_VAL_RE.match(v):
                problems.append(f"theme['{k}']: anahtar '--ad', değer güvenli CSS değeri olmalı")
            else:
                clean_theme[k] = v
    layout = doc.get('layout', {})
    if not isinstance(layout, dict):
        problems.append("'layout' bir nesne olmalı")
        layout = {}
    estop = layout.get('estop', {})
    pos = estop.get('position', 'bottom') if isinstance(estop, dict) else None
    if pos not in ('top', 'bottom'):
        problems.append("layout.estop.position 'top' ya da 'bottom' olmalı")
        pos = 'bottom'
    height = layout.get('button_height', '88px')
    if not isinstance(height, str) or not CSS_VAL_RE.match(height):
        problems.append("layout.button_height güvenli CSS değeri olmalı (ör. '88px')")
        height = '88px'
    sections = layout.get('sections')
    clean_sections: List[Dict[str, Any]] = []
    if sections is None:
        clean_sections = [{'title': '', 'columns': 2, 'scenarios': list(scenarios)}]
    elif not isinstance(sections, list):
        problems.append('layout.sections liste olmalı')
    else:
        for i, sec in enumerate(sections):
            where = f'layout.sections[{i}]'
            if not isinstance(sec, dict):
                problems.append(f'{where}: nesne olmalı')
                continue
            ids = sec.get('scenarios')
            cols = sec.get('columns', 2)
            stitle = sec.get('title', '')
            if not isinstance(ids, list) or not ids:
                problems.append(f"{where}: 'scenarios' boş olmayan liste olmalı")
                continue
            missing = [x for x in ids if x not in scenarios]
            if missing:
                problems.append(f'{where}: tanımsız senaryo id(leri) {missing}')
                continue
            if isinstance(cols, bool) or not isinstance(cols, int) or not 1 <= cols <= 4:
                problems.append(f"{where}: 'columns' 1-4 tam sayı olmalı")
                cols = 2
            if not isinstance(stitle, str) or len(stitle) > 40:
                problems.append(f"{where}: 'title' en çok 40 karakter")
                stitle = ''
            clean_sections.append({'title': stitle, 'columns': cols, 'scenarios': ids})
    panels = layout.get('panels', list(PANELS))
    if not isinstance(panels, list) or any(p not in PANELS for p in panels):
        problems.append(f'layout.panels şunlardan oluşan liste olmalı: {PANELS}')
        panels = list(PANELS)
    if problems:
        raise ConfigError(['ui.config.json: ' + p for p in problems])
    return {'title': title, 'theme': clean_theme,
            'layout': {'estop': {'position': pos}, 'button_height': height,
                       'sections': clean_sections, 'panels': panels}}


def load_ui_config(path: Path, scenarios: Dict[str, Scenario]) -> Dict[str, Any]:
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ConfigError([f'{path}: okunamadı/çözümlenemedi: {exc}']) from exc
    return parse_ui_config(doc, scenarios)
