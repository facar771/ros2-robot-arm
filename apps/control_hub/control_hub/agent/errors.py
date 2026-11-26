"""Ajan katmanının hataları (R10: service.py'den ayrıldı; davranış aynı)."""
from __future__ import annotations


class AgentError(Exception):
    """Kullanıcıya gösterilebilir ajan hatası; ``code`` HTTP katmanı için."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Cancelled(Exception):
    """Tur iptal edildi (dur düğmesi / ACİL DUR): bir sonraki LLM isteğinde yukarı atılır."""


class LimitReached(Exception):
    """Günlük LLM istek sınırı doldu."""
