"""``GET /api/meshes`` ve ``GET /api/meshes/<link>.stl``: 3B görünüm için link mesh'leri (SALT OKUNUR).

Robotun görünüm modelindeki (``/api/model``) robot adı ve link adları kapıdır: modelde olmayan link ya da
başka robot için dosya sunulmaz. Mesh yoksa ``meshes`` boş döner ve ön uç primitifle çizer (hata değildir).
Komut yolu değildir; ACİL DUR kilidi bunu etkilemez.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from control_hub.hub_core.meshes import MeshCatalog, MeshError
from control_hub.hub_core.robot_api_backend import RobotApiError

MODEL_TTL_S = 5.0


def install(app, ctx) -> None:
    hub = ctx.hub
    catalog = MeshCatalog.discover()
    cache: Dict[str, Any] = {'at': 0.0, 'robot': None, 'links': [], 'why': None}

    async def model_names() -> Tuple[Optional[str], List[str], Optional[str]]:
        now = time.monotonic()
        if now - cache['at'] < MODEL_TTL_S:
            return cache['robot'], cache['links'], cache['why']
        robot, links, why = None, [], None
        fn = getattr(hub.backend, 'model', None)
        if not callable(fn):
            why = 'bu robot arka ucu model vermiyor'
        else:
            try:
                status, doc = await asyncio.to_thread(fn)
                if status == 200 and isinstance(doc, dict) and isinstance(doc.get('links'), list):
                    robot = doc.get('robot') if isinstance(doc.get('robot'), str) else None
                    links = [k['name'] for k in doc['links']
                             if isinstance(k, dict) and isinstance(k.get('name'), str)]
                else:
                    why = f'model alınamadı (HTTP {status})'
            except RobotApiError:
                why = 'robota ulaşılamadı'
        cache.update(at=now, robot=robot, links=links, why=why)
        return robot, links, why

    @app.get('/api/meshes')
    async def meshes(request: Request):
        ctx.need_view(request)
        robot, links, why = await model_names()
        found = {}
        if robot and catalog.root is not None:
            found = await asyncio.to_thread(catalog.available, robot, links)
        reason = why or (None if found else
                         ('mesh dizini bulunamadı' if catalog.root is None else 'bu robot için mesh yok'))
        return JSONResponse({'ok': True, 'robot': robot, 'reason': reason,
                             'meshes': {k: {'url': f'/api/meshes/{k}.stl', 'bytes': n}
                                        for k, n in found.items()}})

    @app.get('/api/meshes/{link}.stl')
    async def mesh(link: str, request: Request):
        ctx.need_view(request)
        robot, links, _ = await model_names()
        if not robot or link not in links:
            raise ctx.api_error(404, 'no_mesh', 'Bu link için mesh yok')
        try:
            data = await asyncio.to_thread(catalog.read, robot, link)
        except MeshError as exc:
            raise ctx.api_error(404, 'no_mesh', str(exc)) from None
        return Response(data, media_type='model/stl', headers={'Cache-Control': 'no-cache'})
