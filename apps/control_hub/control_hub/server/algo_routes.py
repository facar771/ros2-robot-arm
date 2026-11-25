"""Algoritmalar uçları (A1): ``/api/algo/*``. Üreteçler ``hub_core/algo_gen.py``'dadır (saf, testli).

* ``GET  /api/algo/kinds`` (``need_view``): üreteç tanımları (alan, varsayılan, sınır); arayüz formu
  bundan kurulur.
* ``POST /api/algo/generate`` (``need_control``): ``{kind, params, speed_pct?}`` -> program adımları, 3B
  önizleme (``points``, ``marks``) ve KURU DOĞRULAMA (``check``). Robot HAREKET ETMEZ: her adım
  ``/api/move/check`` anlamında denetlenir, ZİNCİRLEME ve robotun şu anki pozundan başlayarak
  (``program_move.ChainCheck``). İhlalli adım atılmaz: ``check.rows``'ta ``ok:false`` gelir, kullanıcı
  parametreyi düzeltir.
* Hata gövdesi ``{ok:false, error, message}``; parametre hatasında ``errors:[{field, message}]``.

Denetim ÖNERİDİR: asıl kapı programı çalıştırırken her adımdaki ``move_submit``'tedir ve Program sayfası
adımları kendi bağlamında yeniden önizler (K3c: Hub robotun kurallarını gevşetemez, yalnız gösterir).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from control_hub.hub_core.algo_gen import AlgoError, generate, kinds_doc
from control_hub.hub_core.program_move import make_move_client
from control_hub.hub_core.program_runner import dry_run
from control_hub.hub_core.program_store import MAX_STEPS, validate_program
from control_hub.server.move_routes import read_json

# Denetim hızı ulaşılabilirliği değil yalnız süreyi etkiler; Program kendi hızıyla yeniden önizler.
DEFAULT_SPEED_PCT = 25.0


def fail(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({'ok': False, 'error': code, 'message': message, **extra}, status_code=status)


def install(app: FastAPI, ctx: Any) -> None:
    hub = ctx.hub
    own: Dict[str, Any] = {}

    def mover() -> Any:
        """Program sayfasının hareket istemcisi (aynı arka uç/mock durumu); yoksa kendi istemcimiz.
        Kurulum sırası alfabetik (algo < program) olduğundan arama İSTEK anında yapılır."""
        shared = getattr(app.state, 'program_mover', None)
        if shared is not None:
            return shared
        if 'client' not in own:
            own['client'] = make_move_client(hub)
        return own['client']

    @app.get('/api/algo/kinds')
    async def kinds(request: Request):
        ctx.need_view(request)
        return {'ok': True, 'kinds': kinds_doc(), 'limits': {'max_steps': MAX_STEPS}}

    @app.post('/api/algo/generate')
    async def generate_route(request: Request):
        ctx.need_control(request)
        body, bad = await read_json(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict):
            return fail(400, 'bad_request', 'Gövde nesne olmalı')
        speed = body.get('speed_pct', DEFAULT_SPEED_PCT)
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 1 <= speed <= 100:
            return fail(400, 'bad_request', 'speed_pct 1–100 olmalı')
        kind = body.get('kind')
        try:
            gen = generate(kind if isinstance(kind, str) else '', body.get('params'))
        except AlgoError as exc:
            return fail(422, 'invalid_params', str(exc), errors=exc.errors)
        doc = {'version': 1, 'name': 'Algoritma', 'waypoints': [], 'steps': gen.steps}
        prog, errs = validate_program(doc)
        if prog is None:                    # üreteç program modeline uymayan adım üretti: hata bizim
            return fail(500, 'generated_invalid', 'Üretilen adımlar program modeline uymuyor',
                        errors=errs[:10])
        client = mover()
        check = await asyncio.to_thread(dry_run, prog, client.checker(), float(speed))
        return {'ok': True, 'kind': kind, 'count': len(prog['steps']), 'steps': prog['steps'],
                'marks': gen.marks, 'points': gen.points, 'check': check,
                'backend': {'label': client.label, 'measured': client.measured, 'mock': client.mock}}
