"""FastAPI uygulaması: çekirdeği (hub_core) HTTP + WebSocket'e açar. Mantık burada yok.

Güvenlik katmanları (ayrıntı: README "Güvenlik"):
* ``Host`` beyaz listesi (IP adresi, ``localhost``, ``*.local`` ya da açıkça verilen ad):
  DNS rebinding ile bir web sayfasının LAN'daki Hub'a ulaşmasını engeller.
* ``/api`` altındaki POST'lar ``application/json`` ister (başka sitenin ön uçuşsuz gönderebileceği
  form türleri 415), yabancı ``Origin`` / ``Sec-Fetch-Site: cross-site`` 403.
* Oturum çerezi ``HttpOnly; SameSite=Strict``.
* ACİL DUR oturumsuz da çalışır (``hub_core.access`` açıklaması).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from control_hub import __version__
from control_hub.hub_core import BusyError, EstopLatchedError, Hub, UnknownScenarioError
from control_hub.hub_core.access import Decision

log = logging.getLogger('control_hub.server')
COOKIE = 'hub_session'
WEB_DIR = Path(__file__).resolve().parent.parent / 'web'
WS_PERIOD_S = 0.2
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self' ws: wss:; manifest-src 'self'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        self.status, self.code, self.message, self.extra = status, code, message, extra


class LoginBody(BaseModel):
    pin: str


class EmptyBody(BaseModel):
    pass


def host_allowed(host_header: Optional[str], extra: Iterable[str] = ()) -> bool:
    """IP literal'i, ``localhost``, ``*.local`` ve ``extra`` adlarını kabul eder (port yok sayılır)."""
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith('['):                       # [::1]:8765
        host = host[1:].split(']', 1)[0]
    elif host.count(':') == 1:                     # ad:port / ipv4:port
        host = host.split(':', 1)[0]
    if not host:
        return False
    if host == 'localhost' or host.endswith('.local') or host in {h.lower() for h in extra}:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _origin_ok(origin: Optional[str], host: Optional[str]) -> bool:
    return origin is None or (urlsplit(origin).netloc.lower() == (host or '').lower())


def create_app(hub: Hub, *, extra_allowed_hosts: Iterable[str] = (),
               web_dir: Path = WEB_DIR) -> FastAPI:
    extra = tuple(extra_allowed_hosts)
    app = FastAPI(title='Robot Kontrol Merkezi', version=__version__, docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.state.hub = hub
    access = hub.access

    def token_of(conn) -> Optional[str]:
        return conn.cookies.get(COOKIE)

    def me(conn) -> Dict[str, Any]:
        return {'can_control': access.check_control(token_of(conn)) is Decision.ALLOWED,
                'authenticated': access.authenticated(token_of(conn))}

    def need_view(request: Request) -> None:
        if access.check_view(token_of(request)) is Decision.NEED_LOGIN:
            raise ApiError(401, 'login_required', 'PIN ile giriş gerekli')

    def need_control(request: Request) -> None:
        d = access.check_control(token_of(request))
        if d is Decision.LOCKED:
            raise ApiError(403, 'locked', 'Arayüz salt okunur kipte: kontroller kilitli')
        if d is Decision.NEED_LOGIN:
            raise ApiError(401, 'login_required', 'PIN ile giriş gerekli')

    # --- hatalar ve ara katman -----------------------------------------------------------------
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        headers = {'Retry-After': str(int(exc.extra['retry_after_s']) + 1)} \
            if 'retry_after_s' in exc.extra else None
        return JSONResponse({'ok': False, 'error': exc.code, 'message': exc.message, **exc.extra},
                            status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse({'ok': False, 'error': 'bad_request', 'message': 'Geçersiz istek gövdesi'},
                            status_code=400)

    @app.middleware('http')
    async def _guard(request: Request, call_next):
        host = request.headers.get('host')
        if not host_allowed(host, extra):
            return JSONResponse({'ok': False, 'error': 'bad_host', 'message': 'Host reddedildi'},
                                status_code=403)
        if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD'):
            if not _origin_ok(request.headers.get('origin'), host) or \
                    request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'ok': False, 'error': 'cross_origin',
                                     'message': 'Başka kaynaktan istek reddedildi'}, status_code=403)
            # ACİL DUR gövdesiz/içerik türsüz de çalışmalı (acilde "curl -X POST" yetmeli)
            if request.url.path != '/api/estop' and \
                    request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                return JSONResponse({'ok': False, 'error': 'unsupported_media_type',
                                     'message': "Content-Type 'application/json' olmalı"},
                                    status_code=415)
        resp = await call_next(request)
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['Referrer-Policy'] = 'no-referrer'
        resp.headers['Content-Security-Policy'] = CSP
        resp.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') \
            else 'no-cache'
        return resp

    # --- açık uç noktalar ----------------------------------------------------------------------
    @app.get('/health')
    async def health():
        return {'ok': True, 'version': __version__, 'backend': hub.backend.name}

    @app.get('/api/session')
    async def session(request: Request):
        return {'ok': True, 'mode': access.mode, 'read_only': access.mode == 'locked',
                'title': hub.ui['title'], 'theme': hub.ui['theme'], **me(request)}

    @app.post('/api/login')
    async def login(request: Request, body: LoginBody):
        if access.mode != 'pin':
            raise ApiError(400, 'login_not_required', 'Bu kipte giriş gerekmez')
        client = request.client.host if request.client else 'unknown'
        res = access.login(client, body.pin)
        if res.token is None:
            if res.retry_after_s > 0:
                hub.events.emit('login_throttled', f'Giriş sınırı: {client}', level='warning')
                raise ApiError(429, 'rate_limited', 'Çok fazla deneme; biraz bekle',
                               retry_after_s=round(res.retry_after_s, 1))
            hub.events.emit('login_failed', f'Hatalı PIN: {client}', level='warning')
            raise ApiError(401, 'bad_pin', 'PIN hatalı', attempts_left=res.attempts_left)
        hub.events.emit('login', f'Giriş yapıldı: {client}')
        resp = JSONResponse({'ok': True})
        resp.set_cookie(COOKIE, res.token, httponly=True, samesite='strict', path='/',
                        max_age=12 * 3600)
        return resp

    @app.post('/api/logout')
    async def logout(request: Request):
        access.logout(token_of(request))
        resp = JSONResponse({'ok': True})
        resp.delete_cookie(COOKIE, path='/')
        return resp

    # ACİL DUR: oturum ve kilit denetiminin dışında (hub_core.access açıklaması)
    @app.post('/api/estop')
    async def estop(request: Request):
        hub.estop(source=request.client.host if request.client else 'api')
        return {'ok': True, 'estop': True, 'interrupts_motion': hub.backend.can_interrupt}

    # --- oturum gerektirenler ------------------------------------------------------------------
    def state_for(conn) -> Dict[str, Any]:
        return {**hub.snapshot(), 'me': me(conn)}

    @app.get('/api/ui')
    async def ui(request: Request):
        need_view(request)
        return {'ok': True, 'title': hub.ui['title'], 'theme': hub.ui['theme'],
                'layout': hub.ui['layout'],
                'scenarios': {k: s.public() for k, s in hub.scenarios.items()}}

    @app.get('/api/state')
    async def state(request: Request):
        need_view(request)
        return state_for(request)

    @app.get('/api/events')
    async def events(request: Request, since: int = 0):
        need_view(request)
        return {'ok': True, 'events': [e.as_dict() for e in hub.events.since(max(0, since))]}

    @app.post('/api/scenarios/{scenario_id}/run', status_code=202)
    async def run(scenario_id: str, request: Request, _: EmptyBody):
        need_control(request)
        try:
            hub.run_scenario(scenario_id)
        except UnknownScenarioError:
            raise ApiError(404, 'unknown_scenario', 'Böyle bir senaryo yok') from None
        except BusyError as exc:
            raise ApiError(409, 'busy', f'Başka bir senaryo çalışıyor ({exc})') from None
        except EstopLatchedError:
            raise ApiError(423, 'estop_latched', 'ACİL DUR aktif: önce sıfırla') from None
        return {'ok': True, 'scenario': scenario_id}

    @app.post('/api/scenarios/cancel')
    async def cancel(request: Request, _: EmptyBody):
        need_control(request)
        return {'ok': True, 'cancelled': hub.cancel_scenario()}

    @app.post('/api/estop/reset')
    async def estop_reset(request: Request, _: EmptyBody):
        need_control(request)
        try:
            hub.reset_estop(source=request.client.host if request.client else 'api')
        except BusyError:
            raise ApiError(409, 'busy', 'Senaryo henüz durmadı; birkaç saniye sonra tekrar dene') \
                from None
        return {'ok': True, 'estop': False}

    # --- canlı akış ----------------------------------------------------------------------------
    @app.websocket('/ws')
    async def ws(sock: WebSocket):
        if not host_allowed(sock.headers.get('host'), extra) or \
                not _origin_ok(sock.headers.get('origin'), sock.headers.get('host')):
            await sock.close(code=4403)
            return
        if access.check_view(token_of(sock)) is Decision.NEED_LOGIN:
            await sock.close(code=4401)
            return
        await sock.accept()
        last_json, last_seq = '', 0
        try:
            while True:
                if access.check_view(token_of(sock)) is Decision.NEED_LOGIN:    # oturum süresi doldu
                    await sock.close(code=4401)
                    return
                snap = state_for(sock)
                dumped = json.dumps({k: v for k, v in snap.items() if k not in ('time',)},
                                    sort_keys=True)
                if dumped != last_json:
                    last_json = dumped
                    await sock.send_json({'type': 'state', 'state': snap})
                new = hub.events.since(last_seq)
                if new:
                    last_seq = new[-1].seq
                    await sock.send_json({'type': 'events', 'events': [e.as_dict() for e in new]})
                try:                               # hem uyku hem kopma algılama
                    msg = await asyncio.wait_for(sock.receive(), WS_PERIOD_S)
                    if msg.get('type') == 'websocket.disconnect':
                        return
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            return

    if web_dir.is_dir():
        app.mount('/', StaticFiles(directory=str(web_dir), html=True), name='web')
    return app
