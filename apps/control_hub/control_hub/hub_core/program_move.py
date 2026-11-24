"""Program koşucusunun hareket istemcileri (``MoveClient``): GERÇEK arka uç adaptörü + SAHTE mock (yedek).

* ``BackendMoveClient``: ``hub.backend.move_submit / move_check / move_get / move_stop / move_pause /
  move_resume`` (S1; ``server/move_routes.py`` ile AYNI yöntemler, her biri ``(durum, gövde)`` döner) ve poz
  için ``snapshot()``. SİM'de program 3B robotu gerçekten oynatır. Kurallar (limit, güvenli kutu, yol,
  ACİL DUR) arka uçtadır; burada gevşetilmez, yalnız çevrilir: ret -> ``MoveRefused``, ACİL DUR ->
  ``EstopLatchedError``.
* ``SimMoveClient``: yalnız arka uç bu yöntemleri bilmiyorsa (``--fake``) devreye girer; SAHTE, ölçüm
  DEĞİLDİR.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from control_hub.hub_core.program_runner import Body, MoveClient, MoveRefused
from control_hub.hub_core.runner import EstopLatchedError

MOVE_METHODS = ('move_submit', 'move_check', 'move_get', 'move_stop', 'move_pause', 'move_resume')
# Arka ucun iş durumu -> koşucunun sözlüğü. 'paused': iş bitmedi, koşucu duraklattı.
STATE_MAP = {'running': 'running', 'paused': 'running', 'done': 'done', 'failed': 'failed',
             'cancelled': 'stopped'}
MODE_LABELS = {'sim': 'SİM', 'bench': 'TEZGÂH', 'real': 'GERÇEK'}
NOT_RUNNING = 'not_running'              # duraklat/devam/durdur: çalışan iş yok (zaten bitmiş) = zararsız
Reply = Tuple[int, Dict[str, Any]]


def supports_move(backend: Any) -> bool:
    """Arka uç ``/api/move*`` yöntemlerinin TÜMÜNÜ biliyor mu? (``--fake`` ve gerçek Robot API bilmez.)"""
    return all(callable(getattr(backend, name, None)) for name in MOVE_METHODS)


class ChainCheck:
    """Kuru çalıştırma zinciri: her hareket adımı ÖNCEKİ adımın bitiş duruşundan planlanır.

    Arka ucun ``move_check``'i yalnız robotun O ANKİ duruşundan planlar; yol ve ulaşılabilirlik ise başlangıca
    bağlıdır: J ile uzağa gidip L ile 5 cm inen GEÇERLİ bir program "şu anki duruştan" bakınca yanlışlıkla
    reddedilir. Burada arka ucun AYNI saf planlayıcıları (``sim_plan``) kullanılır; robota ve arka uç durumuna
    DOKUNULMAZ. Asıl denetim yine ``move_submit``'tedir: her adım gerçek duruştan sınanır, ihlalde durur.
    """

    def __init__(self, backend: Any) -> None:
        self._b, self._q = backend, None

    def __call__(self, body: Body) -> Body:
        from control_hub.hub_core.sim_jobs import parse_move
        from control_hub.hub_core.sim_plan import PlanError, plan_joint, plan_linear, violations_for
        b = self._b
        try:
            kind, args, speed = parse_move(body, b.speed_pct)
        except PlanError as exc:
            return {'ok': False, 'violations': [{'kind': exc.kind, 'at_pct': 0.0, 'message': exc.message}]}
        if kind == 'tool':
            return {'ok': True, 'violations': []}
        if self._q is None:
            self._q = list(b.joint_dict().values())            # ilk adım: robotun gerçek duruşu
        planner = plan_joint if kind == 'joint' else plan_linear
        viol, plan = violations_for(planner, b.sm, self._q, *args, speed)
        if plan is not None:
            self._q = plan.q_at(plan.duration_s)               # geçerli adım: sonraki adım buradan başlar
        return {'ok': not viol, 'violations': viol}


class BackendMoveClient:
    """``MoveClient`` -> ``hub.backend.move_*``. Durumsuz: işi arka uç tutar, burada yalnız çeviri var."""
    mock = False

    def __init__(self, backend: Any) -> None:
        self._b = backend
        snap = backend.snapshot() or {}
        self.label = MODE_LABELS.get(snap.get('mode'), 'ARKA UÇ')
        self.measured = snap.get('maturity') == 'olculdu'      # SİM 'tahmini': ölçüm değil

    def check(self, body: Body) -> Body:
        status, doc = self._b.move_check(body)
        if status == 200 and isinstance(doc.get('violations'), list):
            return doc
        return {'ok': False, 'violations': [{'kind': doc.get('error') or 'backend_error', 'at_pct': None,
                                             'message': doc.get('message') or f'arka uç {status} döndü'}]}

    def start(self, body: Body) -> str:
        status, doc = self._b.move_submit(body)
        if status == 202 and doc.get('job_id'):
            return str(doc['job_id'])
        raise MoveRefused(status, doc.get('error') or 'backend_error',
                          doc.get('message') or f'arka uç {status} döndü')

    def status(self, job_id: str) -> Body:
        status, doc = self._b.move_get(job_id)
        job = doc.get('job') if status == 200 else None
        if not isinstance(job, dict):
            return {'state': 'failed', 'error': doc.get('error') or 'unknown_job',
                    'message': doc.get('message') or 'iş bulunamadı'}
        return {'state': STATE_MAP.get(job.get('state'), 'failed'), 'error': job.get('error'),
                'message': job.get('message') or '', 'progress_pct': job.get('progress_pct')}

    def checker(self) -> Callable[[Body], Body]:
        """Bir kuru çalıştırma için denetçi: zincirleyen (SİM: planlayıcı var) ya da düz ``check``."""
        if all(hasattr(self._b, name) for name in ('sm', 'joint_dict', 'speed_pct')):
            return ChainCheck(self._b)
        return self.check

    @staticmethod
    def _ack(reply: Reply) -> None:
        status, doc = reply
        if status == 200 or (status == 409 and doc.get('error') == NOT_RUNNING):
            return
        if status == 423:
            raise EstopLatchedError(doc.get('message') or 'ACİL DUR kilidi açık')
        raise RuntimeError(f"{doc.get('error') or status}: {doc.get('message') or ''}")

    def pause(self) -> None:
        self._ack(self._b.move_pause())

    def resume(self) -> None:
        self._ack(self._b.move_resume())

    def stop(self) -> None:
        self._ack(self._b.move_stop())

    def pose(self) -> Optional[Body]:
        snap = self._b.snapshot() or {}
        joints = [j.get('deg') for j in snap.get('joints') or []]
        tcp = snap.get('tcp') or {}
        pos, rpy = tcp.get('pos_m'), tcp.get('rpy_deg')
        if not joints or None in joints or pos is None or rpy is None:
            return None
        return {'joints_deg': joints, 'pos_m': list(pos), 'rpy_deg': list(rpy),
                'open_pct': (snap.get('gripper') or {}).get('open_pct')}


def make_move_client(hub: Any) -> MoveClient:
    """Arka uç ``move_*`` biliyorsa GERÇEK adaptör; yoksa SAHTE mock (``--fake-speed`` ile aynı hızlanma)."""
    if supports_move(hub.backend):
        return BackendMoveClient(hub.backend)
    scale = getattr(hub.backend, 'time_scale', 1.0) or 1.0
    return SimMoveClient(speed_scale=1.0 / scale)


def _vec(v: Any, n: int) -> Optional[List[float]]:
    nums = (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            for x in (v if isinstance(v, list) else []))
    return [float(x) for x in v] if isinstance(v, list) and len(v) == n and all(nums) else None


class SimMoveClient:
    """SAHTE hareket arka ucu (mock): EK-1 B anlamını taklit eder (kontrol, iş, duraklat, durdur).

    GERÇEK KİNEMATİK YOK: eklemler ve TCP birbirinden bağımsız sahte durumdur, sınırlar uydurmadır;
    ölçüm DEĞİLDİR (K3e). Yalnız arka uç ``move_*`` bilmiyorsa (``--fake``) kullanılır."""
    label, measured, mock = 'SAHTE (mock)', False, True
    DEG_S, M_S, PCT_S, MIN_S, LIMIT_DEG, REACH_M = 60.0, 0.25, 100.0, 0.2, 170.0, 0.9
    BOX = ((-0.6, -0.6, 0.0), (0.6, 0.6, 0.8))
    OBSTACLE = ((0.15, -0.05, 0.0), (0.25, 0.05, 0.2))          # yol engeli denemesi için sahte kutu

    def __init__(self, clock: Callable[[], float] = time.monotonic, speed_scale: float = 1.0,
                 n_joints: int = 6):
        self._clock, self._k, self._n = clock, max(speed_scale, 1e-6), n_joints
        self._lock = threading.RLock()
        self._pose: Body = {'joints_deg': [0.0] * n_joints, 'pos_m': [0.30, 0.0, 0.40],
                            'rpy_deg': [0.0, 0.0, 0.0], 'open_pct': 0.0}
        self._job: Optional[Body] = None
        self._seq = 0

    def _sync(self) -> None:
        j = self._job
        if j is None or j['state'] != 'running':
            return
        spent = j['acc'] + (0.0 if j['frozen'] else self._clock() - j['t0'])
        f = min(1.0, spent / j['dur'])
        self._pose = {k: ([x + (y - x) * f for x, y in zip(a, j['to'][k])] if isinstance(a, list)
                          else a + (j['to'][k] - a) * f) for k, a in j['from'].items()}
        if f >= 1.0:
            j['state'] = 'done'

    def pose(self) -> Optional[Body]:
        with self._lock:
            self._sync()
            return {**copy.deepcopy(self._pose), 'mock': True}

    def checker(self) -> Callable[[Body], Body]:
        return self.check                                       # mock: zincir yok (durumları bağımsız sahte)

    def check(self, body: Body) -> Body:
        with self._lock:
            self._sync()
            v: List[Body] = []

            def bad(kind: str, msg: str, at: Optional[float] = None) -> None:
                v.append({'kind': kind, 'at_pct': at, 'message': msg})

            sp, kind = body.get('speed_pct'), body.get('type')
            if isinstance(sp, bool) or not isinstance(sp, (int, float)) or not 1 <= sp <= 100:
                bad('bad_request', 'hız yüzdesi 1–100 olmalı')
            if kind == 'joint':
                j = _vec(body.get('joints_deg'), self._n)
                if j is None:
                    bad('bad_request', f'{self._n} eklem değeri gerekli')
                for i, x in enumerate(j or []):
                    if abs(x) > self.LIMIT_DEG:
                        bad('joint_limit', f'eklem {i + 1}: {x:g}° (limit ±{self.LIMIT_DEG:g}°)')
            elif kind == 'linear':
                p, r = _vec(body.get('pos_m'), 3), _vec(body.get('rpy_deg'), 3)
                if p is None or r is None:
                    bad('bad_request', 'pos_m ve rpy_deg (3 sayı) gerekli')
                else:
                    lo, hi = self.BOX
                    if any(not a <= x <= b for x, a, b in zip(p, lo, hi)):
                        bad('outside_safe_zone', 'hedef güvenli kutunun dışında')
                    if math.hypot(*p) > self.REACH_M:
                        bad('unreachable', f'hedef erişim yarıçapı ({self.REACH_M:g} m) dışında')
                    a = self._pose['pos_m']
                    for s in range(1, 21):
                        q = [x + (y - x) * s / 20 for x, y in zip(a, p)]
                        if all(o <= c <= e for c, o, e in zip(q, *self.OBSTACLE)):
                            bad('path_blocked', 'yol engele çarpıyor', round(s * 5.0, 1))
                            break
            elif kind == 'tool':
                o = body.get('open_pct')
                if isinstance(o, bool) or not isinstance(o, (int, float)) or not 0 <= o <= 100:
                    bad('bad_request', 'alet açıklığı 0–100 olmalı')
            else:
                bad('bad_request', 'hareket türü joint | linear | tool olmalı')
            return {'ok': not v, 'violations': v}

    def start(self, body: Body) -> str:
        with self._lock:
            self._sync()
            if self._job is not None and self._job['state'] == 'running':
                raise MoveRefused(409, 'busy', 'başka bir hareket sürüyor')
            bad = self.check(body)['violations']
            if bad:
                raise MoveRefused(409, bad[0]['kind'], bad[0]['message'])
            frm, sp = copy.deepcopy(self._pose), body['speed_pct'] / 100
            to = copy.deepcopy(frm)
            if body['type'] == 'joint':
                to['joints_deg'] = [float(x) for x in body['joints_deg']]
                raw = max(abs(a - b) for a, b in zip(frm['joints_deg'], to['joints_deg'])) / (self.DEG_S * sp)
            elif body['type'] == 'linear':
                to['pos_m'] = [float(x) for x in body['pos_m']]
                to['rpy_deg'] = [float(x) for x in body['rpy_deg']]
                raw = math.dist(frm['pos_m'], to['pos_m']) / (self.M_S * sp)
            else:
                to['open_pct'] = float(body['open_pct'])
                raw = abs(to['open_pct'] - frm['open_pct']) / (self.PCT_S * sp)
            self._seq += 1
            self._job = {'id': f'mock-{self._seq}', 'state': 'running', 'from': frm, 'to': to, 'acc': 0.0,
                         't0': self._clock(), 'frozen': False, 'dur': max(self.MIN_S, raw) / self._k}
            return self._job['id']

    def status(self, job_id: str) -> Body:
        with self._lock:
            self._sync()
            j = self._job
            if j is None or j['id'] != job_id:
                return {'state': 'failed', 'error': 'unknown_job', 'message': 'iş bulunamadı'}
            return {'state': j['state'], 'error': None, 'message': ''}

    def pause(self) -> None:
        with self._lock:
            self._sync()
            j = self._job
            if j is not None and j['state'] == 'running' and not j['frozen']:
                j['acc'] += self._clock() - j['t0']
                j['frozen'] = True

    def resume(self) -> None:
        with self._lock:
            j = self._job
            if j is not None and j['state'] == 'running' and j['frozen']:
                j['t0'], j['frozen'] = self._clock(), False

    def stop(self) -> None:
        with self._lock:
            self._sync()
            if self._job is not None and self._job['state'] == 'running':
                self._job['state'] = 'stopped'
