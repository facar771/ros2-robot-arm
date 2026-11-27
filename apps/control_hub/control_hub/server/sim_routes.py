"""SİM'e özgü uçlar (UI_CONTRACT §9-B): yalnız SİM arka ucunda çalışır; başka modda 409 ``not_sim``
(``--fake`` ve gerçek Robot API ``hub_core.backend.NoSim`` varsayılanını devralmıştır: rota doğrudan çağırır).

* ``POST /api/sim/reset``: robotu home'a döndürür (simülasyon sıfırlama; ACİL DUR'da 423, hareket
  sürerken 409).
* ``POST /api/sim/speed {speed_pct?, scale?, accel_scale?, accel_linked?}``: genel hız yüzdesi
  (``robot.speed_pct``); %100 = hız tavanı. ``scale`` / ``accel_scale`` (tavan ölçekleri) verilirse
  yalnız SİM:
  başka modda 409 ``sim_only``, ACİL DUR'da 423; bir SONRAKİ komutta geçerli. P1e: SİM'de sınırsız; TEK sınır:
  sonlu, > 0, hız % <= 100000, ölçek <= 1000 (dışı 400). Etkin değer profil limitinin ÜSTÜNE çıkarsa/inerse
  Günlük'e bir olay yazılır. Gerçek mod: ``speed_pct`` 1–100, ``scale`` hep reddedilir (409).
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

SCALE_KEYS = ('scale', 'accel_scale', 'accel_linked')
OVER_NOTE = 'SİM: hız profil sınırının ÜSTÜNDE (varsayımsal)'


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

    def above_profile() -> bool:
        status, doc = hub.backend.speed_limits(None)
        return status == 200 and bool(doc.get('above_profile'))

    def log_flip(before: bool) -> None:
        after = above_profile()
        if after != before:
            hub.events.emit('sim_over_profile', OVER_NOTE if after else 'SİM: hız profil sınırı İÇİNE döndü',
                            level='warning' if after else 'info')

    @app.post('/api/sim/speed')
    async def sim_speed(request: Request):
        ctx.need_control(request)
        body, bad = await read_json(request)
        if bad:
            return bad
        if not isinstance(body, dict):
            return await call(hub.backend.set_speed, None)
        if any(k in body and body[k] is None for k in ('speed_pct', *SCALE_KEYS)):
            return fail(400, 'bad_request', 'alanlar null olamaz: göndermeyeceksen anahtarı çıkar')
        value = body.get('speed_pct')
        before = await asyncio.to_thread(above_profile)
        if not any(k in body for k in SCALE_KEYS):
            out = await call(hub.backend.set_speed, value)
        else:
            locked = guard_motion(hub, request)      # tavanı yükseltmek ACİL DUR'da da kilitli (423)
            if locked is not None:
                return locked
            out = await call(hub.backend.set_speed_scale, value, body.get('scale'),
                             body.get('accel_scale'), body.get('accel_linked'))
        if out.status_code == 200:
            await asyncio.to_thread(log_flip, before)
        return out

    @app.get('/api/speed/limits')
    async def speed_limits(request: Request, speed_pct: Optional[float] = None):
        ctx.need_view(request)
        status, doc = await asyncio.to_thread(hub.backend.speed_limits, speed_pct)
        return JSONResponse(doc, status_code=status)
