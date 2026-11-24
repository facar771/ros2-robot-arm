"""Parametreler sayfasının uçları (P4). Robota/Robot API'ye UYGULAMA yok; taslak veri klasörüne yazılır.

* ``GET    /api/params``           profil görünümü (alanlar, rozetler, türetilenler) + taslak; salt okunur
* ``POST   /api/params/validate``  ``{edits}`` -> hata/uyarı, diff, türetilenler; hiçbir şey yazılmaz
* ``POST   /api/params/yaml``      ``{edits}`` -> taslak YAML metni (indirilir); doğrulama hatası varsa 422
* ``POST   /api/params/draft``     ``{edits}`` -> ``HUB_DATA_DIR/profiles/<robot>/draft.yaml`` (atomik)
* ``DELETE /api/params/draft``     kayıtlı taslağı siler

Yazan uçlar (draft) denetim ister: kilitli kipte 403, oturumsuz 401. Diğerleri oturum ister.

``edits``: ``{alan anahtarı: sayı | null}`` (arayüz birimi: °, mm, °/s). Depo profil dosyaları ASLA yazılmaz.
Canlı pot okuması bu dosyanın işi değil: Hub'da ``/api/pot`` yok (PR'da İSTEK: Robot_3 / Robot_10).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from control_hub.hub_core import profile_view as pv

MAX_BODY = 256 * 1024
YAML_TYPE = 'text/yaml; charset=utf-8'


def install_params_routes(app: FastAPI, *, need_view: Callable, need_control: Callable) -> None:
    def fail(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse({'ok': False, 'error': code, 'message': message, **extra}, status_code=status)

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


def install(app, ctx) -> None:
    install_params_routes(app, need_view=ctx.need_view, need_control=ctx.need_control)
