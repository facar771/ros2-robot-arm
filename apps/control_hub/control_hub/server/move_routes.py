"""Hareket uçları (UI_CONTRACT §9-B): ``/api/move*``. Sayfalar (Program, Parça, Kontrol) hareketi YALNIZ
bunlarla ister; arka uca doğrudan gitmez.

* ``POST /api/move`` -> 202 ``{job_id}``; ret: 409 ``outside_safe_zone | joint_limit | path_blocked |
  unreachable | busy``, 423 ``estop_latched``, 400 ``bad_request``.
* ``POST /api/move/check``: AYNI doğrulama, robot OYNAMAZ (kuru çalıştırma).
* ``POST /api/move/stop | pause | resume``, ``GET /api/move/{job_id}``.

Kurallar robot/arka uçtadır; Hub yalnız ACİL DUR kilidinde ``423`` ekler (robota sormadan) ve JSON gövdeyi
sınırlar. Arka uç bu uçları bilmiyorsa (``--fake``, gerçek Robot API) 501 ``move_unsupported``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

MAX_BODY = 8 * 1024


def _fail(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': code, 'message': message, **extra}, status_code=status)


async def read_json(request: Request):
    """``(gövde, hata_yanıtı)``: büyük/geçersiz JSON 413/400."""
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return None, _fail(413, 'too_large', 'Gövde çok büyük')
    try:
        return json.loads(raw or b'null'), None
    except ValueError:
        return None, _fail(400, 'bad_request', 'Geçersiz JSON')


def install(app, ctx) -> None:
    hub = ctx.hub

    async def call(name: str, *args: Any):
        fn = getattr(hub.backend, name, None)
        if not callable(fn):
            return _fail(501, 'move_unsupported', 'Bu robot arka ucu hareket uçlarını desteklemiyor')
        status, doc = await asyncio.to_thread(fn, *args)
        return JSONResponse(doc, status_code=status)

    def latched():
        return _fail(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')

    @app.post('/api/move', status_code=202)
    async def move(request: Request):
        ctx.need_control(request)
        if hub.estopped:
            return latched()
        body, bad = await read_json(request)
        return bad or await call('move_submit', body)

    @app.post('/api/move/check')
    async def move_check(request: Request):
        ctx.need_control(request)
        body, bad = await read_json(request)
        return bad or await call('move_check', body)

    @app.post('/api/move/stop')
    async def move_stop(request: Request):
        ctx.need_control(request)                  # durdurmak her zaman serbest (ACİL DUR'da da): güvenli yön
        return await call('move_stop')

    @app.post('/api/move/pause')
    async def move_pause(request: Request):
        ctx.need_control(request)
        return await call('move_pause')

    @app.post('/api/move/resume')
    async def move_resume(request: Request):
        ctx.need_control(request)
        if hub.estopped:
            return latched()
        return await call('move_resume')

    @app.get('/api/move/{job_id}')
    async def move_job(request: Request, job_id: str):
        ctx.need_view(request)
        return await call('move_get', job_id)
