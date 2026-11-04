"""``/api/agent/*``: ajan sohbet paneli uçları.

Kimlik/yetki:
* hepsi PIN oturumu ister (``open`` kipinde oturum yok, ``locked`` kipinde yazma uçları 403);
* **tüm POST'lar** ayrıca ``X-CSRF-Token`` ister (``GET /api/agent/status`` verir; oturuma bağlı HMAC);
* anahtar girişi ve sağlayıcı/model/uç adresi ayarı **yalnızca yerelden**: bağlantı 127.0.0.1/::1'den,
  ``Host`` loopback adı ve vekil başlığı yok. Telefon (LAN) anahtarı giremez, silemez, göremez, uç adresini
  değiştiremez (uç adresi anahtarın gideceği yerdir: değiştirebilen anahtarı çalabilir);
* hiçbir yanıt anahtar içermez (yalnızca ``{set, remembered}`` bayrakları) ve her yanıt ``scrub`` edilir.

ACİL DUR bu uçlardan bağımsızdır (``POST /api/estop``): ajan sohbeti/onay/sınır ona engel olamaz.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from control_hub.agent.service import AgentError, AgentService
from control_hub.server.app import ApiError

LOOPBACK_CLIENTS = ('127.0.0.1', '::1', '::ffff:127.0.0.1')
LOOPBACK_HOSTS = ('localhost', '127.0.0.1', '::1')
PROXY_HEADERS = ('x-forwarded-for', 'x-forwarded-host', 'forwarded', 'x-real-ip')
STATUS_FOR = {'busy': 409, 'no_key': 409, 'unavailable': 503, 'bad_key': 400, 'bad_config': 400,
              'bad_request': 400}


class MessageBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approve: bool


class KeyBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str
    key: str
    remember: bool = False


class ClearKeyBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str
    forget: bool = True


class ConfigBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    daily_limit: Optional[int] = None
    max_motions: Optional[int] = None
    approval_timeout_s: Optional[float] = None
    dry_run: Optional[bool] = None


class EmptyBody(BaseModel):
    pass


def is_local_request(request: Request) -> bool:
    client = request.client.host if request.client else ''
    host = (request.headers.get('host') or '').strip().lower()
    if host.startswith('['):
        name = host[1:].split(']', 1)[0]
    else:
        name = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    return (client in LOOPBACK_CLIENTS and name in LOOPBACK_HOSTS
            and not any(h in request.headers for h in PROXY_HEADERS))


def install_agent_routes(app: FastAPI, hub, *, token_of, need_view, need_control) -> None:
    csrf_key = secrets.token_bytes(32)

    def svc() -> Optional[AgentService]:
        return getattr(hub, 'agent', None)

    def need_agent() -> AgentService:
        service = svc()
        if service is None or not service.enabled:
            raise ApiError(503, 'unavailable', (service.unavailable if service else None)
                           or 'Ajan bu Hub\'ta kapalı')
        return service

    def clean(payload: Any) -> Any:
        service = svc()
        return service._scrub(payload) if service is not None else payload

    def reply(payload: Dict[str, Any], status: int = 200) -> JSONResponse:
        return JSONResponse(clean({'ok': True, **payload}), status_code=status)

    def csrf_of(request: Request) -> str:
        return hmac.new(csrf_key, (token_of(request) or '').encode(), hashlib.sha256).hexdigest()

    def need_csrf(request: Request) -> None:
        got = request.headers.get('x-csrf-token', '')
        if not hmac.compare_digest(got.encode(), csrf_of(request).encode()):
            raise ApiError(403, 'csrf', 'CSRF belirteci eksik ya da geçersiz (sayfayı yenile)')

    def need_local(request: Request) -> None:
        if not is_local_request(request):
            raise ApiError(403, 'local_only', 'Bu işlem yalnızca bu bilgisayardan yapılabilir '
                           '(masaüstü pencere ya da 127.0.0.1); telefondan yapılamaz')

    def run(fn):
        try:
            return fn()
        except AgentError as exc:
            raise ApiError(STATUS_FOR.get(exc.code, 400), exc.code, str(clean(exc.message))) from None

    @app.get('/api/agent/status')
    async def status(request: Request):
        need_view(request)
        service = svc()
        local = is_local_request(request)
        if service is None or not service.enabled:
            return reply({'enabled': False, 'reason': (service.unavailable if service else None)
                          or 'Ajan kapalı', 'csrf': csrf_of(request), 'can_configure': False})
        return reply({**service.status(), 'csrf': csrf_of(request), 'can_configure': local})

    @app.get('/api/agent/chat')
    async def chat(request: Request, since: int = 0):
        need_view(request)
        service = svc()
        return reply({'messages': service.chat(max(0, since)) if service else []})

    @app.post('/api/agent/message', status_code=202)
    async def message(request: Request, body: MessageBody):
        need_control(request)
        need_csrf(request)
        service = need_agent()
        run(lambda: service.send(body.text))
        return reply({'accepted': True}, 202)

    @app.post('/api/agent/approvals/{approval_id}')
    async def approval(approval_id: str, request: Request, body: ApprovalBody):
        need_control(request)
        need_csrf(request)
        service = need_agent()
        if body.approve and hub.estopped:
            raise ApiError(423, 'estop_latched', 'ACİL DUR aktif: onay verilemez')
        if not service.approvals.decide(approval_id, body.approve):
            raise ApiError(404, 'no_such_approval', 'Onay yok ya da süresi doldu (REDDEDİLDİ)')
        return reply({'approved': body.approve})

    @app.post('/api/agent/cancel')
    async def cancel(request: Request, _: EmptyBody):
        need_view(request)
        need_csrf(request)
        service = need_agent()
        return reply({'cancelled': service.cancel()})

    # ---- yalnızca yerel: anahtar ve ayarlar ---------------------------------------------------
    @app.post('/api/agent/key')
    async def set_key(request: Request, body: KeyBody):
        need_control(request)
        need_csrf(request)
        need_local(request)
        service = need_agent()
        result = run(lambda: service.set_key(body.provider, body.key, body.remember))
        return reply(result)

    @app.post('/api/agent/key/clear')
    async def clear_key(request: Request, body: ClearKeyBody):
        need_control(request)
        need_csrf(request)
        need_local(request)
        service = need_agent()
        run(lambda: service.clear_key(body.provider, body.forget))
        return reply({})

    @app.post('/api/agent/config')
    async def config(request: Request, body: ConfigBody):
        need_control(request)
        need_csrf(request)
        need_local(request)
        service = need_agent()
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        run(lambda: service.configure(**changes))
        return reply({k: v for k, v in service.status().items() if k in (
            'provider', 'model', 'base_url', 'dry_run', 'limits')})
