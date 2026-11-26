"""SİM'e özgü uçlar (UI_CONTRACT §9-B): yalnız SİM arka ucunda çalışır; başka modda 409 ``not_sim``
(``--fake`` ve gerçek Robot API ``hub_core.backend.NoSim`` varsayılanını devralmıştır: rota doğrudan çağırır).

* ``POST /api/sim/reset``: robotu home'a döndürür (simülasyon sıfırlama; ACİL DUR'da 423, hareket
  sürerken 409).
* ``POST /api/sim/speed {speed_pct}``: genel hız yüzdesi (``robot.speed_pct``); %100 = profilin izin
  verdiği tavan.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from control_hub.server.errors import read_json
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
        return await call(hub.backend.set_speed, value)
