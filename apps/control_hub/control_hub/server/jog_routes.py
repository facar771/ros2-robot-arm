"""Jog ve çerçeve uçları (J3): Hub bunları robotun Robot API'sine GEÇİRİR; kurallar robottadır.

* ``POST /api/jog``: komut = heartbeat. Hub TTL'i uzatmaz, kiralamayı kendisi tutmaz; yalnızca iletir.
  Hub'ın kendi eklediği tek şey **daha dar** kurallardır: ACİL DUR kilidinde ``423`` (robota sormadan),
  basılı-tut (``twist``) yalnızca bu bilgisayardan (telefon artımlı kalır).
* ``GET /api/jog`` durum; ``GET/PUT /api/frames`` çerçeve listesi; ``POST /api/frames/reach`` küre erişim
  aralığı (L8, yalnızca geçirilir). **Hub frames.yaml'a yazmaz**: PUT robota
  gider,
  dosyayı robot (veri dizini) yazar.
* Ağ hatası ``502``: komut ulaşmadıysa robot TTL'i dolunca kendiliğinden durur (sunucu tarafı dead-man).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from control_hub.hub_core.robot_api_backend import RobotApiError
from control_hub.server.agent_routes import is_local_request

log = logging.getLogger('control_hub.jog')
MAX_BODY = 16 * 1024


def install_jog_routes(app: FastAPI, hub, *, need_view: Callable, need_control: Callable) -> None:
    def supported(name: str) -> bool:
        return callable(getattr(hub.backend, name, None))

    def fail(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse({'ok': False, 'error': code, 'message': message, **extra}, status_code=status)

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
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return None, fail(413, 'too_large', 'Gövde çok büyük')
        try:
            import json
            return json.loads(raw or b'null'), None
        except ValueError:
            return None, fail(400, 'bad_request', 'Geçersiz JSON')

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

    @app.get('/api/jog')
    async def jog_status(request: Request):
        need_view(request)
        resp = await call('jog_status')
        if resp.status_code != 200:
            return resp
        import json
        doc = json.loads(resp.body)
        local = is_local_request(request)
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
