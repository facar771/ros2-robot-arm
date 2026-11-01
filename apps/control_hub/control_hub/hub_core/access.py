"""Erişim denetimi: ``open | pin | locked``, PIN ile giriş, oturum, deneme sınırı.

* ``open``   kimlik doğrulama yok (yalnızca güvenilir, kapalı ağlarda).
* ``pin``    görüntüleme de kontrol de geçerli oturum ister (PIN masaüstünde/terminalde gösterilir).
* ``locked`` arayüz **salt okunur**: kontrol uç noktaları 403. Kimlik gerekmez. (İleride "kontroller
             derlenince erişimi kapat" ihtiyacı için kanca.)

ACİL DUR bu denetimin **dışındadır** (her kipte, oturumsuz çalışır): durdurmayı engellemek, durdurmaktan
daha tehlikelidir. Bedeli: ağdaki herkes robotu durdurabilir; bu bilinçli bir seçimdir.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional

MODES = ('open', 'pin', 'locked')


class Decision(Enum):
    ALLOWED = 'allowed'
    NEED_LOGIN = 'need_login'
    LOCKED = 'locked'


@dataclass(frozen=True)
class LoginResult:
    token: Optional[str] = None
    retry_after_s: float = 0.0      # > 0: deneme sınırına takıldı
    attempts_left: int = 0


def generate_pin(digits: int = 6) -> str:
    return f'{secrets.randbelow(10 ** digits):0{digits}d}'


class AccessControl:
    def __init__(self, mode: str = 'pin', pin: Optional[str] = None, *,
                 session_ttl_s: float = 12 * 3600, max_failures: int = 5, lockout_s: float = 60.0,
                 global_max_failures: int = 20, global_lockout_s: float = 300.0,
                 clock: Callable[[], float] = time.monotonic):
        if mode not in MODES:
            raise ValueError(f'erişim kipi {MODES} içinden olmalı: {mode!r}')
        if pin is not None and not (pin.isdigit() and 4 <= len(pin) <= 12):
            raise ValueError('PIN 4-12 haneli rakam olmalı')
        self._mode = mode
        self._pin = pin or generate_pin()
        self._ttl = session_ttl_s
        self._max_fail, self._lockout = max_failures, lockout_s
        self._gmax_fail, self._glockout = global_max_failures, global_lockout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: Dict[str, float] = {}
        self._fails: Dict[str, List[float]] = {}        # istemci -> başarısız deneme zamanları
        self._locked_until: Dict[str, float] = {}
        self._gfails: List[float] = []
        self._glocked_until = 0.0

    # --- kip / PIN -----------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f'erişim kipi {MODES} içinden olmalı: {mode!r}')
        with self._lock:
            self._mode = mode

    @property
    def pin(self) -> str:
        """Yalnızca yerel kullanıcıya (terminal/masaüstü) göstermek için; ağa asla verilmez."""
        return self._pin

    def rotate_pin(self, new_pin: Optional[str] = None) -> str:
        with self._lock:
            self._pin = new_pin or generate_pin()
            self._sessions.clear()
        return self._pin

    # --- giriş ---------------------------------------------------------------------------------
    def login(self, client: str, pin: str) -> LoginResult:
        now = self._clock()
        with self._lock:
            wait = max(self._locked_until.get(client, 0.0) - now, self._glocked_until - now)
            if wait > 0:
                return LoginResult(retry_after_s=wait)
            if isinstance(pin, str) and hmac.compare_digest(pin.encode(), self._pin.encode()):
                self._fails.pop(client, None)
                token = secrets.token_urlsafe(32)
                self._sessions[token] = now + self._ttl
                return LoginResult(token=token)
            recent = [t for t in self._fails.get(client, []) if now - t < self._lockout] + [now]
            self._fails[client] = recent
            self._gfails = [t for t in self._gfails if now - t < self._glockout] + [now]
            if len(self._gfails) >= self._gmax_fail:
                self._glocked_until = now + self._glockout
                self._gfails = []
            if len(recent) >= self._max_fail:
                self._locked_until[client] = now + self._lockout
                self._fails.pop(client, None)
                return LoginResult(retry_after_s=self._lockout)
            return LoginResult(attempts_left=self._max_fail - len(recent))

    def logout(self, token: Optional[str]) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def _valid(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._sessions.get(token)
            if exp is None:
                return False
            if exp <= self._clock():
                del self._sessions[token]
                return False
            return True

    def authenticated(self, token: Optional[str]) -> bool:
        return self._mode != 'pin' or self._valid(token)

    # --- yetki ---------------------------------------------------------------------------------
    def check_view(self, token: Optional[str]) -> Decision:
        if self._mode == 'pin' and not self._valid(token):
            return Decision.NEED_LOGIN
        return Decision.ALLOWED

    def check_control(self, token: Optional[str]) -> Decision:
        if self._mode == 'locked':
            return Decision.LOCKED
        if self._mode == 'pin' and not self._valid(token):
            return Decision.NEED_LOGIN
        return Decision.ALLOWED
