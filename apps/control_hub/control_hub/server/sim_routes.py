"""SİM'e özgü uçlar (UI_CONTRACT §9-B): yalnız SİM arka ucunda çalışır; başka modda 409 ``not_sim``.

* ``POST /api/sim/reset``: robotu home'a döndürür (simülasyon sıfırlama; ACİL DUR'da 423, hareket
  sürerken 409).
* ``POST /api/sim/speed {speed_pct}``: genel hız yüzdesi (``robot.speed_pct``); %100 = profilin izin
  verdiği tavan.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from control_hub.server.errors import read_json
from control_hub.server.guards import guard_motion


def install(app, ctx) -> None:
    hub = ctx.hub

    def not_sim() -> JSONResponse:
        return JSONResponse({'ok': False, 'error': 'not_sim', 'message': 'Bu uç yalnız SİM modunda çalışır'},
                            status_code=409)

    async def call(name: str, *args: Any):
        fn = getattr(hub.backend, name, None)
        if not callable(fn):
            return not_sim()
        status, doc = await asyncio.to_thread(fn, *args)
        return JSONResponse(doc, status_code=status)

    @app.post('/api/sim/reset')
    async def sim_reset(request: Request):
        ctx.need_control(request)
        locked = guard_motion(hub, request)
        if locked is not None:
            return locked
        return await call('sim_reset')

    @app.post('/api/sim/speed')
    async def sim_speed(request: Request):
        ctx.need_control(request)
        body, bad = await read_json(request)
        if bad:
            return bad
        value = body.get('speed_pct') if isinstance(body, dict) else None
        return await call('set_speed', value)
