"""Jog ve çerçeve uçları (J3): Hub bunları robotun Robot API'sine GEÇİRİR; kurallar robottadır.

* ``POST /api/jog``: komut = heartbeat. Hub TTL'i uzatmaz, kiralamayı kendisi tutmaz; yalnızca iletir.
  Hub'ın kendi eklediği tek şey **daha dar** kurallardır: ACİL DUR kilidinde ``423`` (robota sormadan),
  basılı-tut (``twist``) yalnızca bu bilgisayardan (telefon artımlı kalır).
* ``GET /api/jog`` durum; ``GET/PUT /api/frames`` çerçeve listesi; ``POST /api/frames/reach`` küre erişim
  aralığı (L8, yalnızca geçirilir). **Hub frames.yaml'a yazmaz**: PUT robota
  gider,
  dosyayı robot (veri dizini) yazar.
* ``POST /api/jog/joint`` (P1): TEK eklemi basılı-tutla sürer (``hub_core.joint_hold``; SİM). Aynı kurallar:
  ACİL DUR ``423``, yalnız bu bilgisayardan, ``ttl_ms`` içinde yenilenmezse sunucu durdurur.
* Ağ hatası ``502``: komut ulaşmadıysa robot TTL'i dolunca kendiliğinden durur (sunucu tarafı dead-man).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from control_hub.hub_core.joint_hold import JointHold
from control_hub.hub_core.robot_api_backend import RobotApiError
from control_hub.server.errors import fail, read_json
from control_hub.server.netcheck import is_local_request

log = logging.getLogger('control_hub.jog')
MAX_BODY = 16 * 1024


def install_jog_routes(app: FastAPI, hub, *, need_view: Callable, need_control: Callable) -> None:
    holder = JointHold(hub.backend, getattr(hub.backend, 'clock', None) or time.monotonic)
    hub.joint_hold = holder                       # testler sahte saatle holder.tick() çağırır
    watchdog: dict = {'task': None}

    async def watch() -> None:
        # Dead-man: istemci susarsa (sekme kapandı, Wi-Fi koptu) eklem hareketi birkaç on ms içinde durur
        while holder.active:
            await asyncio.sleep(0.05)
            holder.tick()
        watchdog['task'] = None

    def supported(name: str) -> bool:
        return callable(getattr(hub.backend, name, None))

    async def call(name: str, *args: Any, **kw: Any):
        if not supported(name):
            return fail(501, 'jog_unsupported', 'Bu robot arka ucu jog/çerçeve ucunu desteklemiyor')
        try:
            status, doc = await asyncio.to_thread(getattr(hub.backend, name), *args, **kw)
        except RobotApiError as exc:
            return fail(502, 'robot_unreachable', f'Robota ulaşılamadı: {exc}')
        # robotun gövdesi aynen gider (zaten 'error' metni taşır); Hub yalnızca 'message' anahtarı ekler
        if status >= 400 and isinstance(doc, dict):
            doc = {**doc, 'message': doc.get('message') or doc.get('error') or f'HTTP {status}'}
        return JSONResponse(doc, status_code=status)

    async def body_of(request: Request) -> Tuple[Any, JSONResponse | None]:
        return await read_json(request, MAX_BODY)

    @app.post('/api/jog')
    async def jog(request: Request):
        need_control(request)
        body, bad = await body_of(request)
        if bad is not None:
            return bad
        is_release = isinstance(body, dict) and body.get('ttl_ms') == 0
        if hub.estopped and not is_release:
            return fail(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        local = is_local_request(request)
        if isinstance(body, dict) and 'twist' in body and not local:
            return fail(403, 'hold_local_only', 'Basılı-tut jog yalnızca bu bilgisayardan yapılır; '
                        'telefondan artımlı jog kullan', state='hold_denied')
        return await call('jog_command', body, remote=not local)

    @app.post('/api/jog/joint')
    async def jog_joint(request: Request):
        need_control(request)
        body, bad = await body_of(request)
        if bad is not None:
            return bad
        if not supported('move_submit'):
            return fail(501, 'jog_unsupported', 'Bu robot arka ucu eklem jog\'unu desteklemiyor')
        is_release = isinstance(body, dict) and body.get('ttl_ms') == 0
        if hub.estopped and not is_release:
            holder.release()
            return fail(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        if not is_release and not is_local_request(request):
            return fail(403, 'hold_local_only', 'Basılı-tut jog yalnızca bu bilgisayardan yapılır; '
                        'telefondan adım kullan', state='hold_denied')
        status, doc = await asyncio.to_thread(holder.command, body)
        if holder.active and watchdog['task'] is None:
            watchdog['task'] = asyncio.create_task(watch())
        return JSONResponse(doc, status_code=status)

    @app.get('/api/jog')
    async def jog_status(request: Request):
        need_view(request)
        resp = await call('jog_status')
        if resp.status_code != 200:
            return resp
        import json
        doc = json.loads(resp.body)
        local = is_local_request(request)
        doc['joint_hold'] = supported('move_submit') and local     # P1: eklem ± basılı-tut (yalnız yerel)
        doc['hold_allowed'] = bool(doc.get('hold_enabled')) and local
        doc['hold_reason'] = (None if doc['hold_allowed'] else
                              'Basılı-tut bu sunucuda kapalı (gerçek mod ya da yapılandırma: jog_hold); '
                              'yalnızca artımlı jog' if not doc.get('hold_enabled') else
                              'Basılı-tut yalnızca bu bilgisayardan açılır; telefonda yalnızca artımlı jog')
        return JSONResponse(doc)

    @app.get('/api/model')
    async def model(request: Request):
        """Robot modeli (L9b) olduğu gibi; 503 gövdesi nedeni taşır. Salt-okunur (komut yolu değil)."""
        need_view(request)
        return await call('model')

    @app.get('/api/frames')
    async def frames_get(request: Request):
        need_view(request)
        return await call('frames_get')

    @app.put('/api/frames')
    async def frames_put(request: Request):
        need_control(request)
        body, bad = await body_of(request)
        if bad is not None:
            return bad
        return await call('frames_put', body)

    @app.post('/api/frames/reach')
    async def frames_reach(request: Request):
        """Küre sihirbazı (L8): erişim aralığı. Hub yalnızca geçirir; hesap ve 409/423/403 robottadır."""
        need_control(request)
        body, bad = await body_of(request)
        if bad is not None:
            return bad
        return await call('frames_reach', body)


def install(app, ctx) -> None:
    install_jog_routes(app, ctx.hub, need_view=ctx.need_view, need_control=ctx.need_control)
