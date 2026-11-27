"""SİM'e özgü uçlar (UI_CONTRACT §9-B): yalnız SİM arka ucunda çalışır; başka modda 409 ``not_sim``
(``--fake`` ve gerçek Robot API ``hub_core.backend.NoSim`` varsayılanını devralmıştır: rota doğrudan çağırır).

* ``POST /api/sim/reset``: robotu home'a döndürür (simülasyon sıfırlama; ACİL DUR'da 423, hareket
  sürerken 409).
* ``POST /api/sim/speed {speed_pct?, scale?}``: genel hız yüzdesi (``robot.speed_pct``); %100 = hız tavanı.
  ``scale`` (0,05–1,0; tavan ölçeği) verilirse yalnız SİM: başka modda 409 ``sim_only``, aralık dışı 400,
  ACİL DUR'da 423; bir SONRAKİ komutta geçerli. ``scale`` yoksa eski davranış.
* ``GET /api/speed/limits`` (``need_view``): eklem limitleri, etkin üst hızlar (rad/s, °/s); SUNUCU hesaplar;
  isteğe bağlı ``?speed_pct=`` kartın gösterdiği % ile (yoksa robotun genel hızı).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from control_hub.server.errors import fail, read_json
from control_hub.server.guards import guard_motion


def install(app, ctx) -> None:
    hub = ctx.hub

    async def call(fn: Callable[..., Any], *args: Any):
        status, doc = await asyncio.to_thread(fn, *args)
        return JSONResponse(doc, status_code=status)

    @app.post('/api/sim/reset')
    async def sim_reset(request: Request):
        ctx.need_control(request)
        locked = guard_motion(hub, request)
        if locked is not None:
            return locked
        return await call(hub.backend.sim_reset)

    @app.post('/api/sim/speed')
    async def sim_speed(request: Request):
        ctx.need_control(request)
        body, bad = await read_json(request)
        if bad:
            return bad
        value = body.get('speed_pct') if isinstance(body, dict) else None
        if not isinstance(body, dict) or 'scale' not in body:
            return await call(hub.backend.set_speed, value)
        locked = guard_motion(hub, request)          # tavanı yükseltmek ACİL DUR'da da kilitli (423)
        if locked is not None:
            return locked
        return await call(hub.backend.set_speed_scale, value, body['scale'])

    @app.get('/api/speed/limits')
    async def speed_limits(request: Request, speed_pct: Optional[float] = None):
        ctx.need_view(request)
        if speed_pct is not None and not 1 <= speed_pct <= 100:
            return fail(400, 'bad_request', 'speed_pct 1–100 olmalı')
        status, doc = await asyncio.to_thread(hub.backend.speed_limits, speed_pct)
        return JSONResponse(doc, status_code=status)
