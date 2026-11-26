"""Ajan ayarları (R10: service.py'den ayrıldı; davranış aynı)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List
from urllib.parse import urlsplit

from control_hub.agent.keystore import PROVIDERS

ALL_PROVIDERS = PROVIDERS + ('offline',)


@dataclass
class AgentConfig:
    provider: str = 'anthropic'
    model: str = ''                  # anthropic: boş = robot_agent varsayılanı; openai: zorunlu
    base_url: str = ''               # yalnızca openai: https (http yalnızca yerel makinede)
    daily_limit: int = 200           # günlük LLM isteği (yerel takvim günü); offline sayılmaz
    max_motions: int = 3             # tur başına hareket çağrısı
    approval_timeout_s: float = 60.0
    dry_run: bool = False            # True: hareket araçları robota GİTMEZ, ne olacağını söyler
    max_steps: int = 6

    def problems(self) -> List[str]:
        out: List[str] = []
        if self.provider not in ALL_PROVIDERS:
            out.append(f'sağlayıcı {ALL_PROVIDERS} içinden olmalı')
        if not 1 <= self.daily_limit <= 100_000:
            out.append('günlük istek sınırı 1-100000 olmalı')
        if not 0 <= self.max_motions <= 20:
            out.append('tur başına hareket tavanı 0-20 olmalı')
        if not 5 <= self.approval_timeout_s <= 600:
            out.append('onay zaman aşımı 5-600 sn olmalı')
        if not 1 <= self.max_steps <= 20:
            out.append('adım sınırı 1-20 olmalı')
        if len(self.model) > 100 or any(c.isspace() for c in self.model):
            out.append('model adı boşluksuz ve en çok 100 karakter olmalı')
        if self.base_url:
            parts = urlsplit(self.base_url)
            loop = parts.hostname in ('127.0.0.1', 'localhost', '::1')
            if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username \
                    or parts.password or (parts.scheme == 'http' and not loop):
                out.append('uç adresi https olmalı (http yalnızca yerel makinede), '
                           'kullanıcı/parola içermemeli')
        return out
