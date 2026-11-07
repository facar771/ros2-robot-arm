"""Çalışan bir Hub'ı dışarıdan dener (CI'da .exe'ye karşı; elle de kullanılır). Sahte robot gerekir.

    python -m tools.smoke_check --url http://127.0.0.1:8765 --pin 123456

Kontroller: /health, arayüz dosyaları (gömülü web), PIN'siz erişim reddi (401), yanlış PIN, giriş, durum,
küre çerçevesi yolu (L8), WebSocket'ten ilk durum mesajı, bir senaryo (`home`) ve
ACİL DUR + kilit + sıfırlama. Çıkış kodu 0 = geçti.
"""
from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import sys
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

FAILED = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    print(f"  [{'GEÇTİ' if ok else 'KALDI'}] {name}" + (f': {detail}' if detail else ''))
    if not ok:
        FAILED.append(name)
    return ok


class Client:
    def __init__(self, url: str):
        p = urlsplit(url)
        self.host, self.port = p.hostname or '127.0.0.1', p.port or 80
        self.cookie: Optional[str] = None

    def request(self, method: str, path: str, body: Optional[dict] = None
                ) -> Tuple[int, Any, Dict[str, str]]:
        c = http.client.HTTPConnection(self.host, self.port, timeout=15)
        headers = {}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body)
        if self.cookie:
            headers['Cookie'] = self.cookie
        try:
            c.request(method, path, body=data, headers=headers)
            r = c.getresponse()
            raw = r.read()
            sc = r.getheader('Set-Cookie')
            if sc:
                self.cookie = sc.split(';')[0]
            ctype = r.getheader('Content-Type') or ''
            doc = json.loads(raw) if raw and 'json' in ctype else raw.decode('utf-8', 'replace')
            return r.status, doc, dict(r.getheaders())
        finally:
            c.close()


async def ws_first_message(host: str, port: int, cookie: str) -> str:
    import websockets
    async with websockets.connect(f'ws://{host}:{port}/ws', additional_headers={'Cookie': cookie}) as ws:
        return json.loads(await asyncio.wait_for(ws.recv(), 10))['type']


def wait_for(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--url', default='http://127.0.0.1:8765')
    ap.add_argument('--pin', default='123456')
    ap.add_argument('--wait', type=float, default=30.0, help='Hub açılana dek en çok bekleme (sn)')
    a = ap.parse_args()
    c = Client(a.url)

    def health_ok() -> bool:
        try:
            return c.request('GET', '/health')[0] == 200
        except OSError:
            return False
    if not check('Hub açıldı (/health)', wait_for(health_ok, a.wait)):
        return 1
    _, doc, _ = c.request('GET', '/health')
    check('sahte robot', doc.get('backend') == 'fake', str(doc))
    for path, needle in (('/', 'Robot Kontrol Merkezi'), ('/app.js', 'WebSocket'),
                         ('/style.css', '--accent'), ('/manifest.webmanifest', 'standalone'),
                         ('/sphere.js', 'FrameTypes'), ('/frames.js', 'FrameWizard')):
        st, body, _ = c.request('GET', path)
        check(f'gömülü dosya {path}', st == 200 and needle in str(body), f'HTTP {st}')
    check('PIN\'siz durum reddedilir', c.request('GET', '/api/state')[0] == 401)
    check('yanlış PIN reddedilir', c.request('POST', '/api/login', {'pin': '000000'})[0] == 401)
    st, _, _ = c.request('POST', '/api/login', {'pin': a.pin})
    if not check('PIN ile giriş', st == 200 and bool(c.cookie), f'HTTP {st}'):
        return 1
    st, state, _ = c.request('GET', '/api/state')
    check('durum', st == 200 and state['robot']['connected'], f'HTTP {st}')
    # L8: küre çerçevesi yolu (sahte robot): tür bildirimi, erişim aralığı, toplu PUT'ta diğer türler korunur
    st, frames, _ = c.request('GET', '/api/frames')
    check('çerçeveler: sphere türü bildirilir',
          st == 200 and 'sphere' in (frames.get('types') or []), f'HTTP {st}')
    st, reach, _ = c.request('POST', '/api/frames/reach', {'center': [0.0, 0.0, 0.3], 'radius': 0.3})
    check('küre erişim aralığı', st == 200 and reach.get('reachable') is True, f'HTTP {st}')
    sphere = {'type': 'sphere', 'name': 'SmokeKure', 'center': [0.0, 0.0, 0.3], 'radius': 0.3,
              'tool_axis': 'z'}
    st, put, _ = c.request('PUT', '/api/frames', {'frames': [], 'spheres': [sphere]})
    check('küre kaydı', st == 200 and [x['name'] for x in put.get('spheres', [])] == ['SmokeKure'],
          f'HTTP {st}')
    st, put, _ = c.request('PUT', '/api/frames', {'frames': []})
    check('yalnız frames yazınca küre korunur', st == 200 and len(put.get('spheres', [])) == 1, f'HTTP {st}')
    st, _, _ = c.request('PUT', '/api/frames', {'frames': [], 'spheres': []})
    check('küre silindi (temizlik)', st == 200, f'HTTP {st}')
    try:
        first = asyncio.run(ws_first_message(c.host, c.port, c.cookie or ''))
        check('WebSocket ilk mesaj', first == 'state', first)
    except Exception as exc:                                              # noqa: BLE001
        check('WebSocket', False, repr(exc))
    check('senaryo başlat (home)', c.request('POST', '/api/scenarios/home/run', {})[0] == 202)
    check('senaryo bitti', wait_for(lambda: not c.request('GET', '/api/state')[1]['runner']['running'], 20))
    last = c.request('GET', '/api/state')[1]['runner']['last']
    check('senaryo başarılı', bool(last and last['ok']), str(last and last['message']))
    check('ACİL DUR', c.request('POST', '/api/estop')[0] == 200)
    check('kilitliyken senaryo 423', c.request('POST', '/api/scenarios/home/run', {})[0] == 423)
    check('kilidi aç', wait_for(lambda: c.request('POST', '/api/estop/reset', {})[0] == 200, 10))
    print('\nSONUÇ:', 'HEPSİ GEÇTİ' if not FAILED else f'KALDI: {FAILED}')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
