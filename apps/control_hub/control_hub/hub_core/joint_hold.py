"""Eklem basılı-tut jog (P1): TEK eklemi sürekli hareket ettirir, bırakınca/kopunca DURUR.

Kartezyen basılı-tut robotun jog motorunda (TTL 250 ms, sunucu dead-man). Eklem jog'u orada yok; Hub'da
mevcut ``move_*`` uçlarıyla kurulur: bastığında eklemi limite (ya da ``HOLD_SPAN_DEG`` ileriye) giden TEK bir
eklem hareketi başlar, ``ttl_ms`` içinde yenilenmezse ya da bırakılırsa hareket iptal edilir. Dead-man bu
sınıftadır (sunucu tarafı): tarayıcı kapansa/bağlantı kesilse de ``tick()`` hareketi durdurur (K3-h, ≤ 3 sn).
Doğrulama (limit, kutu, yol) başlatmada backend'in ``move_submit``'indedir; ret = hareket yok.
Saf mantık: saat ve arka uç dışarıdan verilir (sahte saatle test edilir)."""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Tuple

HOLD_TTL_DEFAULT_MS = 250       # robot_core jog ile aynı: komut = heartbeat
HOLD_TTL_MAX_MS = 500           # istemci ne isterse istesin üst sınır (dead-man hep kısa)
HOLD_SPAN_DEG = 60.0            # basılı tutunca hedef: mevcut ± bu kadar (limite kırpılır)
MIN_SPAN_DEG = 0.5              # bundan kısa hedef = zaten limitte
SHRINK = 0.5                    # güvenli kutu/yol reddinde hedef mesafesi bu çarpanla kısaltılır
MAX_SHRINKS = 6
REJECTS_SHRINK = ('outside_safe_zone', 'path_blocked', 'unreachable')
Reply = Tuple[int, Dict[str, Any]]


def clamp_ttl_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return HOLD_TTL_DEFAULT_MS
    return int(min(max(value, 1), HOLD_TTL_MAX_MS))


def hold_target(deg: float, lo: float, hi: float, sign: int, span: float) -> float:
    """Eklem hedefi (°): ``deg`` + ``sign``·``span``, [lo, hi] içine kırpılır."""
    return min(max(deg + sign * span, lo), hi)


class JointHold:
    def __init__(self, backend: Any, clock: Callable[[], float]) -> None:
        self.backend, self.clock = backend, clock
        self._lock = threading.Lock()
        self.lease: Optional[str] = None
        self.joint: Optional[int] = None
        self.sign = 0
        self.job_id: Optional[str] = None
        self.deadline = 0.0

    @property
    def active(self) -> bool:
        return self.lease is not None

    def _err(self, status: int, code: str, message: str, **extra: Any) -> Reply:
        return status, {'ok': False, 'error': code, 'message': message, **extra}

    def _clear(self) -> None:
        self.lease, self.joint, self.sign, self.job_id = None, None, 0, None

    def _stop_locked(self) -> None:
        if self.job_id is not None:
            self.backend.move_stop()
        self._clear()

    def release(self) -> None:
        with self._lock:
            self._stop_locked()

    def tick(self) -> bool:
        """Dead-man: süre doldu ya da iş bitti -> bırak. Durdurulduysa ``True``."""
        with self._lock:
            if self.lease is None:
                return False
            if self.clock() > self.deadline:
                self._stop_locked()
                return True
            state = self.backend.move_get(self.job_id)[1].get('job', {}).get('state')
            if state not in ('running', 'paused'):
                self._clear()          # limite varıldı ya da E-STOP/başka iş kesti
                return True
            return False

    def _start(self, joint: int, sign: int, speed_pct: Optional[float]) -> Reply:
        joints = self.backend.snapshot()['joints']
        if not 0 <= joint < len(joints):
            return self._err(400, 'bad_request', f'eklem 0..{len(joints) - 1} olmalı')
        cur = [j['deg'] for j in joints]
        span = HOLD_SPAN_DEG
        for _ in range(MAX_SHRINKS):
            goal = hold_target(cur[joint], joints[joint]['min_deg'], joints[joint]['max_deg'], sign, span)
            if abs(goal - cur[joint]) < MIN_SPAN_DEG:
                return self._err(409, 'joint_limit', 'Eklem bu yönde limitte', joint=joint)
            body: Dict[str, Any] = {'type': 'joint', 'joints_deg': [*cur[:joint], goal, *cur[joint + 1:]]}
            if speed_pct is not None:
                body['speed_pct'] = speed_pct
            status, doc = self.backend.move_submit(body)
            if status == 202:
                self.job_id = doc['job_id']
                return 200, {'ok': True, 'state': 'holding', 'job_id': self.job_id}
            if status == 409 and doc.get('error') in REJECTS_SHRINK:
                span *= SHRINK          # kutuya çarptı: daha kısa hedefle dene (hareket zaten başlamadı)
                continue
            return status, doc
        return self._err(409, 'outside_safe_zone', 'Bu yönde güvenli kutuda hareket payı yok', joint=joint)

    def command(self, body: Any) -> Reply:
        if not isinstance(body, dict) or not isinstance(body.get('lease'), str) or not body['lease']:
            return self._err(400, 'bad_request', 'gövde {"lease", "joint", "dir", "ttl_ms"} olmalı')
        ttl = body.get('ttl_ms')
        with self._lock:
            if ttl == 0:                                  # açık bırakma
                if self.lease == body['lease']:
                    self._stop_locked()
                return 200, {'ok': True, 'state': 'released'}
            joint, sign = body.get('joint'), body.get('dir')
            if isinstance(joint, bool) or not isinstance(joint, int) or sign not in (-1, 1):
                return self._err(400, 'bad_request', 'joint tam sayı, dir -1 ya da 1 olmalı')
            now = self.clock()
            if self.lease is not None and now > self.deadline:
                self._stop_locked()                       # eski tutuş süresi dolmuş
            if self.lease not in (None, body['lease']):
                return self._err(409, 'busy', 'Başka bir istemci eklem jog yapıyor')
            if self.lease == body['lease'] and (self.joint, self.sign) == (joint, sign):
                state = self.backend.move_get(self.job_id)[1].get('job', {}).get('state')
                if state in ('running', 'paused'):
                    self.deadline = now + clamp_ttl_ms(ttl) / 1000.0
                    return 200, {'ok': True, 'state': 'holding', 'job_id': self.job_id}
                self._clear()
                return 200, {'ok': True, 'state': 'at_limit'}
            self._stop_locked()                           # yön/eklem değişti: öncekini kes
            status, doc = self._start(joint, sign, body.get('speed_pct'))
            if status == 200:
                self.lease, self.joint, self.sign = body['lease'], joint, sign
                self.deadline = now + clamp_ttl_ms(ttl) / 1000.0
            return status, doc
