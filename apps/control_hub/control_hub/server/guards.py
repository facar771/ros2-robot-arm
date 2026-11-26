"""ACİL DUR kilit denetimi tek yerde (docs/MIMARI.md §7 R3). Hareket üreten rotalar (move,
sim/reset, jog, program)
kilidi KENDİ yazmaz; ``guard_motion`` çağırır. 423 gövdesi ve jog "bırak" istisnası burada sabittir;
``tests/test_guard_motion.py`` ve ``tests/test_route_contract.py`` (her hareket rotası kilitte
423) bunu korur.

Hub kuralı gevşetmez, yalnız DARALTIR: kilit açıkken hiçbir hareket isteği robota/arka uca gitmez."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from control_hub.server.errors import fail

log = logging.getLogger('control_hub.guards')


def estop_latched_response() -> JSONResponse:
    """423 ``estop_latched`` gövdesi (tek tanım); koşu sırasındaki yarış (EstopLatchedError) de
    bunu kullanır."""
    return fail(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')


def is_release(body: Any) -> bool:
    """Jog bırakma isteği: ``ttl_ms == 0`` olan nesne gövdesi. Bırakma hareket BAŞLATMAZ, yalnız
    durdurur."""
    return isinstance(body, dict) and body.get('ttl_ms') == 0


def guard_motion(hub: Any, request: Request, allow_release: bool = False,
                 body: Any = None) -> Optional[JSONResponse]:
    """Hareket üreten bir istekten ÖNCE çağır: kilitliyse 423 yanıtı döner, değilse ``None``.

    ``allow_release=True`` (yalnız jog): ``body`` bırakma isteğiyse
    (``is_release``) kilitte de geçer; ACİL DUR'da
    süren tutuşun bırakılabilmesi gerekir. Varsayılan KAPALI: gövde ne olursa olsun 423."""
    if not hub.estopped:
        return None
    if allow_release and is_release(body):
        return None
    log.debug('hareket reddedildi (ACİL DUR kilidi): %s', request.url.path)
    return estop_latched_response()
