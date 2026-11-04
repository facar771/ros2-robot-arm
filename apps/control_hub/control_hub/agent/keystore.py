"""API anahtarı deposu. Anahtar **asla** settings.json'a, olaylara, günlüğe ya da HTTP yanıtına yazılmaz.

* Varsayılan: yalnızca bellekte (süreç kapanınca gider).
* ``remember=True``: işletim sisteminin anahtar deposuna (Windows'ta Credential Manager) yazılır;
  ``keyring`` yoksa ya da depo kullanılamıyorsa anahtar yine bellekte tutulur ve *hatırlanmadı*
  bilgisi döner (sessizce düz dosyaya düşülmez).
* Dışarıya yalnızca ``{set, remembered}`` bayrakları verilir; anahtarın kendisi yalnızca ``get`` ile,
  yalnızca ajanın LLM bağdaştırıcısına gider.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger('control_hub.agent')
SERVICE = 'ControlHub-Agent'
PROVIDERS = ('anthropic', 'openai')
MAX_KEY_LEN = 512


class KeyError_(ValueError):
    """Geçersiz anahtar girdisi (anahtar metni mesaja KONMAZ)."""


def _register(secret: str) -> None:
    try:
        from robot_agent.redact import register_secret
        register_secret(secret)
    except ImportError:                                  # ajan kurulu değilse zaten kullanılmaz
        pass


def _default_backend() -> Optional[Any]:
    try:
        import keyring
        return keyring
    except Exception:    # noqa: BLE001 - paket yok / ortam bozuk: bellek-only
        return None


class KeyStore:
    def __init__(self, backend: Optional[Any] = None, *, use_default_backend: bool = True):
        self._backend = backend if backend is not None else (
            _default_backend() if use_default_backend else None)
        self._mem: Dict[str, str] = {}
        self._remembered: Dict[str, bool] = {}
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None
        self._load_remembered()

    # --- yaşam döngüsü -------------------------------------------------------------------------
    def _load_remembered(self) -> None:
        """Daha önce 'hatırla' denmiş anahtarları depodan belleğe alır (açılışta bir kez)."""
        if self._backend is None:
            return
        for provider in PROVIDERS:
            try:
                value = self._backend.get_password(SERVICE, provider)
            except Exception as exc:    # noqa: BLE001 - depo kilitli/yok: yok say
                self.last_error = f'anahtar deposu okunamadı ({type(exc).__name__})'
                self._backend = None            # kullanılamaz depo "var" sayılmaz (bellek-only'e düşer)
                return
            if value:
                self._mem[provider] = value
                self._remembered[provider] = True
                _register(value)

    @property
    def persistent_available(self) -> bool:
        return self._backend is not None

    # --- yazma ---------------------------------------------------------------------------------
    def set(self, provider: str, key: str, remember: bool = False) -> Tuple[bool, Optional[str]]:
        """(hatırlandı mı, uyarı). Anahtar metni hiçbir hata mesajına girmez."""
        if provider not in PROVIDERS:
            raise KeyError_(f'sağlayıcı {PROVIDERS} içinden olmalı')
        key = (key or '').strip()
        if not key or len(key) > MAX_KEY_LEN or any(c.isspace() or ord(c) < 33 or ord(c) > 126
                                                    for c in key):
            raise KeyError_('anahtar boş, çok uzun ya da geçersiz karakter içeriyor')
        _register(key)
        warning: Optional[str] = None
        remembered = False
        with self._lock:
            self._mem[provider] = key
            if remember:
                if self._backend is None:
                    warning = 'anahtar deposu (keyring) yok: anahtar yalnızca bellekte tutuldu'
                else:
                    try:
                        self._backend.set_password(SERVICE, provider, key)
                        remembered = True
                    except Exception as exc:    # noqa: BLE001
                        warning = ('anahtar deposuna yazılamadı '
                                   f'({type(exc).__name__}): anahtar yalnızca bellekte tutuldu')
            elif self._remembered.get(provider):
                self._delete_persisted(provider)           # "hatırlama" işareti kalktı: depodan da sil
            self._remembered[provider] = remembered
        return remembered, warning

    def clear(self, provider: str, forget: bool = True) -> None:
        if provider not in PROVIDERS:
            raise KeyError_(f'sağlayıcı {PROVIDERS} içinden olmalı')
        with self._lock:
            self._mem.pop(provider, None)
            if forget and (self._remembered.get(provider) or self._backend is not None):
                self._delete_persisted(provider)
            self._remembered[provider] = False

    def _delete_persisted(self, provider: str) -> None:
        if self._backend is None:
            return
        try:
            self._backend.delete_password(SERVICE, provider)
        except Exception:    # noqa: BLE001 - zaten yok olabilir
            pass

    # --- okuma ---------------------------------------------------------------------------------
    def get(self, provider: str) -> Optional[str]:
        """YALNIZCA ajanın LLM bağdaştırıcısı için."""
        with self._lock:
            return self._mem.get(provider)

    def status(self) -> Dict[str, Dict[str, bool]]:
        with self._lock:
            return {p: {'set': p in self._mem, 'remembered': bool(self._remembered.get(p))}
                    for p in PROVIDERS}

    def __repr__(self) -> str:
        return f'KeyStore({self.status()})'
