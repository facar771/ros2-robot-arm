"""Masaüstü ayarları: doğrulama + ``%APPDATA%\\ControlHub\\settings.json`` kalıcılığı."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from control_hub.paths import user_dir

DEFAULT_ROBOT_URL = 'http://127.0.0.1:8080'
DEFAULT_PORT = 8765
ACCESS_MODES = ('pin', 'open', 'locked')


@dataclass
class Settings:
    robot_url: str = DEFAULT_ROBOT_URL
    fake: bool = False                 # sahte robot (robot yokken arayüzü denemek için)
    port: int = DEFAULT_PORT
    access_mode: str = 'pin'
    pin: str = ''                      # '' = her açılışta rastgele
    remember_pin: bool = False         # True: PIN settings.json'a düz metin yazılır
    serial_port: str = ''              # ESP32 köprüsü: 'COM5' ya da 'socket://host:port'; boş = köprü yok
    serial_baud: int = 921600
    bridge_port: int = 8770            # köprünün TCP portu (ROS düğümü buna bağlanır)
    bridge_open: bool = False          # True: 0.0.0.0 (Docker/başka makine) + token zorunlu
    # Yapay zekâ ajanı: yalnızca SIR OLMAYAN ayarlar. API anahtarı buraya ASLA yazılmaz
    # (bellek ya da isteğe bağlı Windows Credential Manager).
    agent_provider: str = 'anthropic'  # anthropic | openai | offline
    agent_model: str = ''
    agent_base_url: str = ''           # openai uç adresi (https)
    agent_daily_limit: int = 200
    agent_max_motions: int = 3
    agent_dry_run: bool = False

    def problems(self) -> List[str]:
        out: List[str] = []
        if not self.fake:
            parts = urlsplit(self.robot_url if '//' in self.robot_url else f'http://{self.robot_url}')
            if parts.scheme not in ('http', 'https') or not parts.hostname:
                out.append('Robot adresi http://adres:port biçiminde olmalı (ör. http://127.0.0.1:8080)')
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
            out.append('Port 1024-65535 arasında olmalı')
        if self.access_mode not in ACCESS_MODES:
            out.append(f'Erişim kipi {ACCESS_MODES} içinden olmalı')
        if self.pin and not (self.pin.isdigit() and 4 <= len(self.pin) <= 12):
            out.append('PIN 4-12 haneli rakam olmalı (boş bırakırsan rastgele üretilir)')
        if self.serial_port:
            if not 300 <= self.serial_baud <= 4_000_000:
                out.append('Seri baud 300-4000000 arasında olmalı')
            if not 1024 <= self.bridge_port <= 65535 or self.bridge_port == self.port:
                out.append('Köprü portu 1024-65535 olmalı ve Hub portundan farklı olmalı')
        out.extend(self.agent_config().problems())
        return out

    def agent_config(self):
        from control_hub.agent import AgentConfig
        return AgentConfig(provider=self.agent_provider, model=self.agent_model,
                           base_url=self.agent_base_url, daily_limit=self.agent_daily_limit,
                           max_motions=self.agent_max_motions, dry_run=self.agent_dry_run)


def settings_path(base: Optional[Path] = None) -> Path:
    return (base or user_dir()) / 'settings.json'


def load(path: Optional[Path] = None) -> Settings:
    """Bozuk/eksik dosyada varsayılanlara döner; bilinmeyen anahtarlar yok sayılır."""
    path = path or settings_path()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    default = Settings()
    s = Settings()
    for f in fields(Settings):
        if f.name not in raw:
            continue
        v, want = raw[f.name], type(getattr(default, f.name))
        if isinstance(v, want) and not (want is int and isinstance(v, bool)):
            setattr(s, f.name, v)
    return s


def save(s: Settings, path: Optional[Path] = None) -> Path:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(s)
    if not s.remember_pin:
        data['pin'] = ''
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='settings.', suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)                       # yarım yazılmış dosya bırakma
    return path
