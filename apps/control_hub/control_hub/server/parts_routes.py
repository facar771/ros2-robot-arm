"""Parça uçları (S2): CAD parça/takım/ortam listesi, yükleme, yeniden adlandırma, yerleştirme, silme.

* ``GET  /api/parts``              liste + sınırlar (okuma).
* ``GET  /api/parts/{id}/mesh``    kanonik ikili STL (okuma).
* ``POST /api/parts``              yükleme; **JSON + base64** (Hub ara katmanı JSON dışı gövdeyi 415 yapar;
  bu mimari karardır ve değişmez, ``multipart`` bağımlılığı eklenmez). Yazma uçları ``need_control``.
* ``PUT  /api/parts/{id}``         kısmi güncelleme (ad, tür, görünürlük, birim, ölçek, poz, takım ofseti).
* ``DELETE /api/parts/{id}``.

K6: uzantı beyaz listesi, ≤ 50 MB, yalnız ``HUB_DATA_DIR`` altı, kullanıcı adı yol olmaz, içerik doğrulanır,
üçgen ≤ 500 000, yüklenen şey asla çalıştırılmaz. Bu uçlar robota HAREKET göndermez (ACİL DUR'dan
etkilenmez); parçaya "git/yaklaş" hareketi tarayıcıdan ``/api/move`` (S1) ile gider. Hata gövdesi
``{ok:false,error,message}``; dosya yolu hiçbir mesajda yoktur.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from typing import Any, List

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core import parts_store as ps

B64_OVERHEAD = 4 / 3
MAX_BODY = int(mt.MAX_FILE_BYTES * B64_OVERHEAD) + 64 * 1024        # base64 + JSON zarfı
UPLOAD_STATUS = {'too_large': 413, 'bad_extension': 415, 'step_unsupported': 415, 'bad_filename': 400,
                 'bad_unit': 400}                                    # diğer mesh hataları: 422


def install(app, ctx) -> None:
    try:
        store = ps.PartsStore(ps.parts_dir())
        data_error = ''
    except ValueError as exc:             # geçersiz HUB_DATA_DIR: Hub açılır, yalnız parça uçları 503 verir
        store, data_error = None, str(exc)

    def need_store():
        if store is None:
            raise ctx.api_error(503, 'data_dir_invalid', f'Veri klasörü geçersiz: {data_error}')
        return store

    async def read_json(request: Request, limit: int) -> Any:
        """Gövdeyi parça parça okur; sınır aşılınca bellekte biriktirmeden keser (413)."""
        declared = request.headers.get('content-length')
        if declared and declared.isdigit() and int(declared) > limit:
            raise ctx.api_error(413, 'too_large', 'Gövde çok büyük')
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > limit:
                raise ctx.api_error(413, 'too_large', 'Gövde çok büyük')
        try:
            return json.loads(bytes(buf) or b'null')
        except ValueError:
            raise ctx.api_error(400, 'bad_request', 'Geçersiz JSON') from None

    def part_error(exc: ps.PartError):
        return ctx.api_error(exc.status, exc.code, str(exc))

    async def run(fn, *args, **kw):
        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except ps.PartError as exc:
            raise part_error(exc) from None

    @app.get('/api/parts')
    async def parts_list(request: Request):
        ctx.need_view(request)
        parts, problems = await run(need_store().list)
        return {'ok': True, 'parts': [ps.public_view(p) for p in parts], 'problems': problems,
                'limits': {'max_parts': ps.MAX_PARTS, 'max_file_mb': mt.MAX_FILE_BYTES // (1024 * 1024),
                           'max_triangles': mt.MAX_TRIANGLES, 'formats': list(mt.ALLOWED_EXT),
                           'units': list(mt.UNIT_FACTORS), 'kinds': ps.KIND_LABELS},
                'step': {'available': False, 'message': mt.STEP_MESSAGE}}

    @app.get('/api/parts/{part_id}/mesh')
    async def part_mesh(part_id: str, request: Request):
        ctx.need_view(request)
        data = await run(need_store().read_mesh, part_id)
        return Response(data, media_type='model/stl', headers={'Cache-Control': 'no-cache'})

    @app.post('/api/parts', status_code=201)
    async def part_add(request: Request):
        ctx.need_control(request)
        body = await read_json(request, MAX_BODY)
        if not isinstance(body, dict):
            raise ctx.api_error(400, 'bad_request', 'Gövde bir nesne olmalı')
        filename, unit = body.get('filename'), body.get('unit', mt.DEFAULT_UNIT)
        kind = body.get('kind', 'workpiece')
        b64 = body.get('data_b64')
        try:
            mt.extension_of(filename)                          # ad/uzantı, base64'ü çözmeden ÖNCE
            mt.unit_factor(unit)
            ps.check_kind(kind)
            if not isinstance(b64, str) or not b64:
                raise mt.MeshToolError('empty', 'Dosya içeriği yok')
            if len(b64) * 3 // 4 > mt.MAX_FILE_BYTES + 3:
                raise mt.MeshToolError(
                    'too_large', f'Dosya çok büyük; en çok {mt.MAX_FILE_BYTES // (1024 * 1024)} MB')
            try:
                raw = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                raise mt.MeshToolError('bad_base64', 'Dosya içeriği geçerli base64 değil') from None
            mesh = await asyncio.to_thread(mt.import_mesh, filename, raw)
        except mt.MeshToolError as exc:
            raise ctx.api_error(UPLOAD_STATUS.get(exc.code, 422), exc.code, str(exc)) from None
        except ps.PartError as exc:
            raise part_error(exc) from None
        name = mt.clean_display_name(body.get('name'), fallback=mt.clean_display_name(
            filename.rsplit('.', 1)[0], fallback='Parça'))
        meta = await run(need_store().add, name=name, kind=kind, unit=unit, filename=filename, mesh=mesh)
        view = ps.public_view(meta)
        warnings: List[str] = list(mesh.warnings)
        if view['size_warning']:
            warnings.append(view['size_warning'])
        hub = ctx.hub
        hub.events.emit('part_added', f'Parça eklendi: {meta["name"]} ({mesh.triangles} üçgen)')
        return JSONResponse({'ok': True, 'part': view, 'warnings': warnings}, status_code=201)

    @app.put('/api/parts/{part_id}')
    async def part_update(part_id: str, request: Request):
        ctx.need_control(request)
        patch = await read_json(request, 64 * 1024)
        meta = await run(need_store().update, part_id, patch)
        return {'ok': True, 'part': ps.public_view(meta)}

    @app.delete('/api/parts/{part_id}')
    async def part_delete(part_id: str, request: Request):
        ctx.need_control(request)
        await run(need_store().delete, part_id)
        ctx.hub.events.emit('part_deleted', 'Parça silindi')
        return {'ok': True}
