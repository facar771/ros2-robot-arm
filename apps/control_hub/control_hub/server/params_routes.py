"""Parametreler sayfasının uçları (P4). Robota/Robot API'ye UYGULAMA yok; taslak veri klasörüne yazılır.

* ``GET    /api/params``           profil görünümü (alanlar, rozetler, türetilenler) + taslak; salt okunur
* ``POST   /api/params/validate``  ``{edits}`` -> hata/uyarı, diff, türetilenler; hiçbir şey yazılmaz
* ``POST   /api/params/yaml``      ``{edits}`` -> taslak YAML metni (indirilir); doğrulama hatası varsa 422
* ``POST   /api/params/draft``     ``{edits}`` -> ``HUB_DATA_DIR/profiles/<robot>/draft.yaml`` (atomik)
* ``DELETE /api/params/draft``     kayıtlı taslağı siler
* ``GET    /api/params/sim``       SİM'e uygulama durumu: ``can_apply`` + nedeni, etkin geçersiz kılma özeti
* ``POST   /api/params/sim/apply`` KAYITLI taslağı SİM'e GEÇİCİ yükler (bellek; dosya/robot yazılmaz)
* ``POST   /api/params/sim/reset`` gerçek profile döner

Yazan uçlar (draft, sim/apply, sim/reset) denetim ister: kilitli kipte 403, oturumsuz 401.
Diğerleri oturum ister.
SİM uçları: ``mode=real`` 409 ``real_mode``, SİM değilse 409 ``not_sim``, ACİL DUR 423 ``estop_latched``,
hareket/jog/senaryo/program sürerken 409 ``busy``; geçersiz taslak 422 ``invalid`` (nedenleriyle).

``edits``: ``{alan anahtarı: sayı | null}`` (arayüz birimi: °, mm, °/s). Depo profil dosyaları ASLA yazılmaz.
Canlı pot okuması bu dosyanın işi değil: Hub'da ``/api/pot`` yok (PR'da İSTEK: Robot_3 / Robot_10).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from control_hub.hub_core import profile_apply
from control_hub.hub_core import profile_view as pv
from control_hub.server.errors import fail

MAX_BODY = 256 * 1024
YAML_TYPE = 'text/yaml; charset=utf-8'


def sim_blocker(hub: Any) -> Optional[Tuple[int, str, str]]:
    """``(durum, kod, ileti)``: SİM profili şu an değiştirilemiyorsa nedeni; değiştirilebilirse ``None``.

    Arka uç kendi kilidi altında ACİL DUR/hareket/jog'u ayrıca denetler (burası erken ve açıklayıcı ret).
    Program koşucusu Hub'a bağlı değil: ``hub.program_runner`` varsa (İSTEK P2) o da sayılır."""
    backend = hub.backend
    if (backend.snapshot() or {}).get('mode') == 'real':
        return 409, 'real_mode', 'Gerçek robotta kapalı: taslak yalnız SİM arka ucuna uygulanır'
    gate = getattr(backend, 'profile_override_gate', None)
    if not callable(getattr(backend, 'profile_override_apply', None)) or gate is None:
        return 409, 'not_sim', 'Bu özellik yalnız SİM arka ucunda (--sim) çalışır'
    if hub.estopped:
        return 423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç'
    if hub.runner.running:
        return 409, 'busy', 'Senaryo sürerken profil değiştirilemez'
    program = getattr(hub, 'program_runner', None)
    if program is not None and getattr(program, 'running', False):
        return 409, 'busy', 'Program sürerken profil değiştirilemez'
    refused = gate()
    if refused is None:
        return None
    status, doc = refused
    return status, doc.get('error', 'busy'), doc.get('message', '')


def install_params_routes(app: FastAPI, *, need_view: Callable, need_control: Callable,
                          hub: Any = None) -> None:
    def view_fail(exc: pv.ViewError) -> JSONResponse:
        return fail(exc.status, exc.code, exc.message, **exc.extra)

    async def edits_of(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
        try:
            if int(request.headers.get('content-length') or 0) > MAX_BODY:
                return None, fail(413, 'too_big', 'istek çok büyük')
            body = await request.json()
        except ValueError:
            return None, fail(400, 'bad_json', 'geçerli bir JSON gövdesi gerekli')
        edits = body.get('edits') if isinstance(body, dict) else None
        if not isinstance(edits, dict) or not all(isinstance(k, str) and len(k) <= 80 for k in edits):
            return None, fail(400, 'bad_edits', 'gövde {"edits": {alan: sayı | null}} olmalı')
        return edits, None

    async def run(fn: Callable, *args: Any) -> Any:
        return await asyncio.to_thread(fn, *args)

    def source(request: Request) -> pv.Source:
        return pv.load_source(request.query_params.get('robot') or None)

    @app.get('/api/params')
    async def params_get(request: Request):
        need_view(request)
        try:
            def build():
                src = source(request)
                return {**pv.view_model(src), 'draft': pv.read_draft(src)}
            return await run(build)
        except pv.ViewError as exc:
            return view_fail(exc)

    @app.post('/api/params/validate')
    async def params_validate(request: Request):
        need_view(request)
        edits, bad = await edits_of(request)
        if bad is not None:
            return bad
        try:
            val = await run(lambda: pv.validate(source(request), edits))
        except pv.ViewError as exc:
            return view_fail(exc)
        return val.payload()

    @app.post('/api/params/yaml')
    async def params_yaml(request: Request):
        need_view(request)
        edits, bad = await edits_of(request)
        if bad is not None:
            return bad
        try:
            def build():
                src = source(request)
                return src.robot, pv.yaml_text(src, edits)
            robot, text = await run(build)
        except pv.ViewError as exc:
            return view_fail(exc)
        return Response(text, media_type=YAML_TYPE,
                        headers={'Content-Disposition': f'attachment; filename="{robot}-taslak.yaml"'})

    @app.post('/api/params/draft')
    async def params_draft_save(request: Request):
        need_control(request)
        edits, bad = await edits_of(request)
        if bad is not None:
            return bad
        try:
            return await run(lambda: pv.save_draft(source(request), edits))
        except pv.ViewError as exc:
            return view_fail(exc)

    @app.delete('/api/params/draft')
    async def params_draft_delete(request: Request):
        need_control(request)
        try:
            return await run(lambda: pv.delete_draft(source(request)))
        except pv.ViewError as exc:
            return view_fail(exc)


    if hub is None:                                    # yalnız SİM uçları Hub ister
        return

    @app.get('/api/params/sim')
    async def sim_state(request: Request):
        need_view(request)
        snap = await run(hub.backend.snapshot) or {}
        blocked = await run(sim_blocker, hub)
        return {'ok': True, 'mode': snap.get('mode'), 'can_apply': blocked is None,
                'blocker': None if blocked is None else blocked[1],
                'reason': None if blocked is None else blocked[2], 'override': snap.get('profile_override')}

    @app.post('/api/params/sim/apply')
    async def sim_apply(request: Request):
        need_control(request)
        blocked = await run(sim_blocker, hub)
        if blocked is not None:
            return fail(*blocked)
        if request.query_params.get('robot') not in (None, '', hub.backend.robot):
            return fail(409, 'robot_mismatch', 'SİM başka bir robot profiliyle çalışıyor')
        try:
            prepared = await run(profile_apply.prepare, hub.backend.robot)
        except pv.ViewError as exc:
            return view_fail(exc)
        status, doc = await run(hub.backend.profile_override_apply, prepared.profile, prepared.info)
        if status == 200:
            hub.events.emit('sim_profile', f"SİM'e taslak profil uygulandı ({prepared.info['changes']} alan)",
                            level='warning', changes=prepared.info['changes'])
        return JSONResponse(doc, status_code=status)

    @app.post('/api/params/sim/reset')
    async def sim_reset(request: Request):
        need_control(request)
        blocked = await run(sim_blocker, hub)
        if blocked is not None:
            return fail(*blocked)
        status, doc = await run(hub.backend.profile_override_reset)
        if status == 200 and not doc.get('noop'):
            hub.events.emit('sim_profile', 'SİM gerçek profile döndü (taslak kaldırıldı)', level='info')
        return JSONResponse(doc, status_code=status)


def install(app, ctx) -> None:
    install_params_routes(app, need_view=ctx.need_view, need_control=ctx.need_control, hub=ctx.hub)
