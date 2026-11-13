"""Tezgâh uçları (KM3): ``tools/bench`` mantığını Hub'dan sürer. Gerçek robot DEĞİL; Robot API'ye bağlı değil.

* Varsayılan KAPALI (Hub ayarı); açık olsa da **yalnız ``pin`` erişim kipinde** ve geçerli oturumla çalışır
  (``open`` kipinde PIN olmaz: reddedilir; ``locked`` salt okunur: reddedilir).
* ``GET /api/bench`` durum; oturumlu arayüz sorgusu canlılık işaretidir (izleyici tazelemez).
  ``POST /api/bench/open|close|command`` denetim ister.
* ``POST /api/bench/estop`` oturum ve kilit denetiminin DIŞINDADIR (``/api/estop`` gibi): durdurmayı
  engellemek durdurmaktan daha tehlikelidir. Hub genel ACİL DUR'u da tezgâhı durdurur (dinleyici).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from pydantic import BaseModel

from control_hub.hw.bench_service import BenchError


class CommandBody(BaseModel):
    cmd: str
    axis: Optional[str] = None
    amount: Optional[str] = None
    confirm: bool = False


class EmptyBody(BaseModel):
    pass


def install_bench_routes(app: FastAPI, hub, *, need_view: Callable, need_control: Callable,
                         api_error: Any) -> None:
    def svc():
        b = getattr(hub, 'bench', None)
        if b is None:
            raise api_error(404, 'bench_unavailable', 'Bu Hub tezgâh desteği olmadan başlatıldı')
        return b

    def guard(request: Request):
        need_control(request)                         # oturum (pin) ve kilit
        if hub.access.mode != 'pin':
            raise api_error(403, 'bench_needs_pin',
                            'Tezgâh yalnızca PIN erişim kipinde çalışır (--access-mode pin); '
                            f'şu an kip: {hub.access.mode}')
        return svc()

    def _controls(request: Request) -> bool:
        try:
            need_control(request)
        except Exception:                             # noqa: BLE001 - ApiError: yetkisiz/izleyici
            return False
        return hub.access.mode == 'pin'

    async def run(fn, *a):
        try:
            return await asyncio.to_thread(fn, *a)
        except BenchError as exc:
            raise api_error(exc.status, exc.code, exc.message) from None

    @app.get('/api/bench')
    async def bench_info(request: Request):
        need_view(request)
        b = svc()
        if _controls(request):        # yalnız denetim yetkili OTURUM canlılığı tazeler;
            b.touch()                 # salt izleyici (locked kipi) sürücü sekmesini ayakta tutamaz
        return {'ok': True, **b.info()}

    @app.post('/api/bench/open')
    async def bench_open(request: Request, _: EmptyBody):
        b = guard(request)
        info = await run(b.open)
        return {'ok': True, **info}

    @app.post('/api/bench/close')
    async def bench_close(request: Request, _: EmptyBody):
        b = guard(request)
        await asyncio.to_thread(b.close)
        return {'ok': True, **b.info()}

    @app.post('/api/bench/command')
    async def bench_command(request: Request, body: CommandBody):
        b = guard(request)
        return await run(lambda: b.command(body.cmd, body.axis, body.amount, body.confirm))

    @app.post('/api/bench/estop')
    async def bench_estop(request: Request):
        b = getattr(hub, 'bench', None)
        sent = bool(b and await asyncio.to_thread(b.estop))
        return {'ok': True, 'sent': sent}
