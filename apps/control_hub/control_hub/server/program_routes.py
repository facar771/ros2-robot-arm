"""Program sayfası uçları (P2): ``/api/program/*``. Mantık ``hub_core.program_store``/``program_runner``'da.

* GET (``need_view``): ``state``, liste, tek program, ``export``. POST/DELETE (``need_control``): ``save``,
  ``import`` (yalnız ayrıştırır), ``check`` (kuru çalıştırma), ``check_move``, ``goto``, ``run``,
  ``step|pause|resume|stop|heartbeat``. Hata gövdesi ``{ok:false, error, message}``.
* Robota hareket giden her yol (``run``, ``step``, ``resume``, ``goto``) ACİL DUR kilidinde
  ``423 estop_latched`` döner. ``run`` kuru çalıştırmayı sunucuda YENİDEN yapar, ihlal varsa reddeder.
* Hareket arka ucu: ``hub.backend.move_*`` (S1, SİM) varsa ``BackendMoveClient``; yoksa (``--fake``)
  SAHTE mock (``hub_core/program_move.py``).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from control_hub.hub_core.program_move import make_move_client
from control_hub.hub_core.program_runner import (
    Body,
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
from control_hub.paths import user_dir
from control_hub.server.errors import fail
from control_hub.server.guards import estop_latched_response, guard_motion

DEFAULT_SPEED_PCT = 25.0


def _no_const(token: str) -> Any:
    raise ValueError(token)                                  # NaN / Infinity


def install(app: FastAPI, ctx: Any) -> None:
    hub = ctx.hub
    store, mover = ProgramStore(user_dir=user_dir), make_move_client(hub)   # paths bağı BURADAN (R7)
    runner = ProgramRunner(mover, hub.events, is_estopped=lambda: hub.estopped)
    hub.add_estop_listener(runner.on_estop)
    app.state.program_runner = runner
    app.state.program_mover = mover                          # Algoritmalar sayfası aynı istemciyle denetler

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

    def locked(request: Request) -> Optional[JSONResponse]:
        return guard_motion(hub, request)

    @app.get('/api/program/state')
    async def state(request: Request):
        ctx.need_view(request)
        return {'ok': True, 'runner': runner.status(), 'pose': mover.pose(), 'estop': hub.estopped,
                'backend': {'label': mover.label, 'measured': mover.measured, 'mock': mover.mock},
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
        return {'ok': True, **await asyncio.to_thread(dry_run, prog, mover.checker(), sp)}

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
        bad = bad or locked(request) or (fail(409, 'busy', 'Program çalışıyor') if runner.running else None)
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
        bad = bad or locked(request) or (fail(409, 'busy', 'Program çalışıyor') if runner.running else None)
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
        # arayüze güvenilmez: kuru çalıştırma sunucuda YENİDEN yapılır
        checked = await asyncio.to_thread(dry_run, prog, mover.checker(), sp)
        if not checked['ok']:
            msg = f"{checked['violations']} ihlal var: önce düzelt"
            return fail(409, 'program_invalid', msg, check=checked)
        try:
            run_id = runner.start(prog, sp, step_mode=mode == 'step')
        except EstopLatchedError:
            return locked(request) or fail(423, 'estop_latched', 'ACİL DUR aktif')
        except BusyError:
            return fail(409, 'busy', 'Başka bir program çalışıyor')
        return {'ok': True, 'run_id': run_id, 'runner': runner.status()}

    def control(op: Callable[[], Any]) -> Dict[str, Any] | JSONResponse:
        try:
            op()
        except EstopLatchedError:
            return estop_latched_response()
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
