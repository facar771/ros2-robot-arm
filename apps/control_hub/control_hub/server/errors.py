"""Hata yanıtı üretimi tek yerde (docs/MIMARI.md §7 R5): gövde her zaman ``{ok:false, error, message, ...}``.
Rota dosyalarındaki ``fail``/``_fail``/``read_json`` kopyalarının yerine geçer; biçim
``tests/test_error_bodies.py`` (altın gövde) ile bayt bayt sabittir. ``app.py`` içe aktarılmaz."""
from __future__ import annotations

import json
from typing import Any, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

DEFAULT_MAX_BODY = 8 * 1024                      # küçük kontrol gövdeleri; büyük isteyen sınır verir


def error_body(code: str, message: str, **extra: Any) -> dict:
    return {'ok': False, 'error': code, 'message': message, **extra}


def fail(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(error_body(code, message, **extra), status_code=status)


class ApiError(Exception):
    """Rota içinden ``raise``; ``app.py`` işleyicisi :func:`api_error_response` ile aynı gövdeyi üretir."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def api_error_response(exc: ApiError) -> JSONResponse:
    wait = exc.extra.get('retry_after_s')
    headers = None if wait is None else {'Retry-After': str(int(wait) + 1)}
    body = error_body(exc.code, exc.message, **exc.extra)
    return JSONResponse(body, status_code=exc.status, headers=headers)


async def read_json(request: Request, limit: int = DEFAULT_MAX_BODY) -> Tuple[Any, Optional[JSONResponse]]:
    """``(gövde, hata_yanıtı)``: sınır aşımı 413, geçersiz JSON 400 ``bad_request``; boş gövde = None."""
    raw = await request.body()
    if len(raw) > limit:
        return None, fail(413, 'too_large', 'Gövde çok büyük')
    try:
        return json.loads(raw or b'null'), None
    except ValueError:
        return None, fail(400, 'bad_request', 'Geçersiz JSON')


async def read_json_stream(request: Request, limit: int) -> Any:
    """Büyük gövdeler (CAD): parça parça okur, sınırda keser (bellekte biriktirmez); hata = ApiError."""
    declared = request.headers.get('content-length')
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(413, 'too_large', 'Gövde çok büyük')
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ApiError(413, 'too_large', 'Gövde çok büyük')
    try:
        return json.loads(bytes(buf) or b'null')
    except ValueError:
        raise ApiError(400, 'bad_request', 'Geçersiz JSON') from None
