"""Program sayfası uçları (P2): ``/api/program/*``. Mantık ``hub_core.program_store``/``program_runner``'da.

* GET (``need_view``): ``state``, liste, tek program, ``export``. POST/DELETE (``need_control``): ``save``,
  ``import`` (yalnız ayrıştırır), ``check`` (kuru çalıştırma), ``check_move``, ``goto``, ``run``,
  ``step|pause|resume|stop|heartbeat``. Hata gövdesi ``{ok:false, error, message}``.
* Robota hareket giden her yol (``run``, ``step``, ``resume``, ``goto``) ACİL DUR kilidinde
  ``423 estop_latched`` döner. ``run`` kuru çalıştırmayı sunucuda YENİDEN yapar, ihlal varsa reddeder.
* Hareket arka ucu: S1 ``hub.move_service`` (``MoveClient``) bağlayana dek ``SimMoveClient`` (SAHTE).
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from control_hub.hub_core.program_runner import (
    Body,
    MoveClient,
    MoveRefused,
    ProgramRunner,
    dry_run,
    move_body,
)
from control_hub.hub_core.program_store import (
    MAX_JSON_BYTES,
    MAX_LOOP_COUNT,
    MAX_STEPS,
    MAX_WAYPOINTS,
    ProgramError,
    ProgramStore,
    count_actions,
    parse_text,
    validate_program,
)
from control_hub.hub_core.runner import BusyError, EstopLatchedError

DEFAULT_SPEED_PCT = 25.0


def _vec(v: Any, n: int) -> Optional[List[float]]:
    nums = (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            for x in (v if isinstance(v, list) else []))
    return [float(x) for x in v] if isinstance(v, list) and len(v) == n and all(nums) else None


class SimMoveClient:
    """SAHTE hareket arka ucu (mock): EK-1 B anlamını taklit eder (kontrol, iş, duraklat, durdur).

    GERÇEK KİNEMATİK YOK: eklemler ve TCP birbirinden bağımsız sahte durumdur, sınırlar uydurmadır;
    ölçüm DEĞİLDİR (K3e). S1'in SİM arka ucu (``hub.move_service``) gelince kullanılmaz."""
    label, measured = 'SAHTE (mock)', False
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


def make_move_client(hub: Any) -> MoveClient:
    """S1 ``hub.move_service`` bağlarsa o; yoksa SAHTE mock (``--fake-speed`` ile aynı hızlanma)."""
    svc = getattr(hub, 'move_service', None)
    if svc is not None:
        return svc
    scale = getattr(hub.backend, 'time_scale', 1.0) or 1.0
    return SimMoveClient(speed_scale=1.0 / scale)


def _no_const(token: str) -> Any:
    raise ValueError(token)                                  # NaN / Infinity


def fail(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': code, 'message': message, **extra}, status_code=status)


def install(app: FastAPI, ctx: Any) -> None:
    hub = ctx.hub
    store, mover = ProgramStore(), make_move_client(hub)
    runner = ProgramRunner(mover, hub.events, is_estopped=lambda: hub.estopped)
    hub.add_estop_listener(runner.on_estop)
    app.state.program_runner = runner

    async def body_of(request: Request) -> Tuple[Optional[Body], Optional[JSONResponse]]:
        raw = await request.body()
        if len(raw) > MAX_JSON_BYTES + 65536:
            return None, fail(413, 'too_large', 'Gövde çok büyük')
        try:
            doc = json.loads(raw or b'{}', parse_constant=_no_const)
        except (ValueError, RecursionError):
            return None, fail(400, 'bad_request', 'Geçersiz JSON')
        if not isinstance(doc, dict):
            return None, fail(400, 'bad_request', 'Gövde nesne olmalı')
        return doc, None

    def speed_of(body: Body) -> Tuple[float, Optional[JSONResponse]]:
        sp = body.get('speed_pct', DEFAULT_SPEED_PCT)
        if isinstance(sp, bool) or not isinstance(sp, (int, float)) or not 1 <= sp <= 100:
            return 0.0, fail(400, 'bad_request', 'speed_pct 1–100 olmalı')
        return float(sp), None

    def program_of(body: Body) -> Tuple[Optional[Body], Optional[JSONResponse]]:
        clean, errs = validate_program(body.get('program'))
        if clean is None:
            msg = f'Program geçersiz ({len(errs)} hata)'
            return None, fail(422, 'invalid_program', msg, errors=errs[:50])
        return clean, None

    def single(body: Body) -> Tuple[Optional[Body], Optional[JSONResponse]]:
        """Tek hedef ``{step: {move_j|move_l, değerler}, speed_pct}`` -> hareket gövdesi."""
        sp, bad = speed_of(body)
        step = body.get('step')
        if bad is None and not (isinstance(step, dict) and step.get('kind') in ('move_j', 'move_l')
                                and 'waypoint' not in step):
            bad = fail(400, 'bad_request', 'step: değerli move_j ya da move_l olmalı')
        if bad is not None:
            return None, bad
        clean, errs = validate_program({'version': 1, 'name': 'tek', 'waypoints': [], 'steps': [step]})
        if clean is None:
            return None, fail(422, 'invalid_program', 'Hedef geçersiz', errors=errs[:10])
        return move_body(clean['steps'][0], {}, sp)[0], None

    def store_call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except ProgramError as exc:
            return fail(exc.status, exc.code, exc.message, **exc.extra)

    def locked() -> Optional[JSONResponse]:
        if hub.estopped:
            return fail(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        return None

    @app.get('/api/program/state')
    async def state(request: Request):
        ctx.need_view(request)
        return {'ok': True, 'runner': runner.status(), 'pose': mover.pose(), 'estop': hub.estopped,
                'backend': {'label': mover.label, 'measured': mover.measured},
                'limits': {'max_steps': MAX_STEPS, 'max_waypoints': MAX_WAYPOINTS,
                           'max_loop': MAX_LOOP_COUNT}}

    @app.get('/api/program')
    async def listing(request: Request):
        ctx.need_view(request)
        return {'ok': True, 'programs': await asyncio.to_thread(store.list)}

    @app.get('/api/program/{slug}')
    async def read(slug: str, request: Request):
        ctx.need_view(request)
        res = store_call(lambda: store.load(slug))
        return res if isinstance(res, JSONResponse) else {'ok': True, 'slug': slug, 'program': res}

    @app.get('/api/program/{slug}/export')
    async def export(slug: str, request: Request):
        ctx.need_view(request)
        res = store_call(lambda: store.load(slug))
        if isinstance(res, JSONResponse):
            return res
        return Response(json.dumps(res, ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{slug}.json"'})

    @app.post('/api/program/save')
    async def save(request: Request):
        ctx.need_control(request)
        body, bad = await body_of(request)
        prog, bad = (None, bad) if bad else program_of(body)
        if bad is not None:
            return bad
        res = store_call(lambda: store.save(prog))
        return res if isinstance(res, JSONResponse) else {'ok': True, 'slug': res, 'program': prog}

    @app.delete('/api/program/{slug}')
    async def delete(slug: str, request: Request):
        ctx.need_control(request)
        res = store_call(lambda: store.delete(slug))
        return res if isinstance(res, JSONResponse) else {'ok': True}

    @app.post('/api/program/import')
    async def import_(request: Request):
        ctx.need_control(request)
        body, bad = await body_of(request)
        if bad is None and not isinstance(body.get('text'), str):
            bad = fail(400, 'bad_request', 'text (JSON metni) gerekli')
        if bad is not None:
            return bad
        res = store_call(lambda: parse_text(body['text']))
        return res if isinstance(res, JSONResponse) else {'ok': True, 'program': res}

    @app.post('/api/program/check')
    async def check(request: Request):
        ctx.need_control(request)
        body, bad = await body_of(request)
        sp, bad = (0.0, bad) if bad else speed_of(body)
        prog, bad = (None, bad) if bad else program_of(body)
        if bad is not None:
            return bad
        return {'ok': True, **await asyncio.to_thread(dry_run, prog, mover.check, sp)}

    @app.post('/api/program/check_move')
    async def check_move(request: Request):
        ctx.need_control(request)
        body, bad = await body_of(request)
        mv, bad = (None, bad) if bad else single(body)
        if bad is not None:
            return bad
        return {'ok': True, **await asyncio.to_thread(mover.check, mv)}

    @app.post('/api/program/goto', status_code=202)
    async def goto(request: Request):
        ctx.need_control(request)
        body, bad = await body_of(request)
        mv, bad = (None, bad) if bad else single(body)
        bad = bad or locked() or (fail(409, 'busy', 'Program çalışıyor') if runner.running else None)
        if bad is not None:
            return bad
        try:
            job = await asyncio.to_thread(mover.start, mv)
        except MoveRefused as exc:
            return fail(exc.status, exc.error, exc.message)
        return {'ok': True, 'job_id': job}

    @app.post('/api/program/run', status_code=202)
    async def run(request: Request):
        ctx.need_control(request)
        body, bad = await body_of(request)
        bad = bad or locked() or (fail(409, 'busy', 'Program çalışıyor') if runner.running else None)
        sp, bad = (0.0, bad) if bad else speed_of(body)
        prog, bad = (None, bad) if bad else program_of(body)
        mode = None if bad else body.get('mode', 'run')
        if bad is None and mode not in ('run', 'step'):
            bad = fail(400, 'bad_request', "mode 'run' ya da 'step' olmalı")
        if bad is None and count_actions(prog) == 0:
            bad = fail(409, 'empty_program', 'Program boş: çalıştırılacak adım yok')
        if bad is None and (hub.backend.snapshot() or {}).get('mode') == 'real' \
                and body.get('confirm_real') is not True:
            bad = fail(409, 'confirm_required', 'Gerçek robot hareket edecek: onay gerekli')
        if bad is not None:
            return bad
        checked = await asyncio.to_thread(dry_run, prog, mover.check, sp)   # arayüze güvenilmez: yeniden sına
        if not checked['ok']:
            msg = f"{checked['violations']} ihlal var: önce düzelt"
            return fail(409, 'program_invalid', msg, check=checked)
        try:
            run_id = runner.start(prog, sp, step_mode=mode == 'step')
        except EstopLatchedError:
            return locked() or fail(423, 'estop_latched', 'ACİL DUR aktif')
        except BusyError:
            return fail(409, 'busy', 'Başka bir program çalışıyor')
        return {'ok': True, 'run_id': run_id, 'runner': runner.status()}

    def control(op: Callable[[], Any]) -> Dict[str, Any] | JSONResponse:
        try:
            op()
        except EstopLatchedError:
            return fail(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        except BusyError:
            return fail(409, 'not_paused', 'Program duraklatılmış olmalı')
        return {'ok': True, 'runner': runner.status()}

    def control_route(name: str, op: Callable[[], Any]) -> None:
        def handler(request: Request):
            ctx.need_control(request)
            return control(op)
        app.add_api_route(f'/api/program/{name}', handler, methods=['POST'], name=f'program_{name}')

    for name, op in (('step', runner.step), ('resume', runner.resume), ('pause', runner.pause),
                     ('heartbeat', runner.heartbeat)):
        control_route(name, op)

    @app.post('/api/program/stop')
    async def stop(request: Request):
        ctx.need_control(request)
        cancelled = runner.cancel('kullanıcı durdurdu')
        await asyncio.to_thread(mover.stop)                  # tek tek git (goto) işi de dursun
        return {'ok': True, 'cancelled': cancelled, 'runner': runner.status()}
