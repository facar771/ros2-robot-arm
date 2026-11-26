"""Günlük sayfasının uçları (P4): Hub olay günlüğünün (son 500) süzülmüş, SIZINTISIZ görünümü + CSV.

* ``GET /api/log``      ``?level=<en düşük>&source=<tür>&q=<ara>&limit=<1..500>``: yeni olaylar ÜSTTE.
* ``GET /api/log.csv``  aynı süzgeçler; UTF-8 BOM'lu (Excel uyumlu), formül enjeksiyonuna karşı korumalı.

SIZINTI KURALI: PIN, anahtar/jeton/çerez ve mutlak dosya yolu metne HİÇBİR koşulda çıkmaz.
Tek noktada (``redact``) maskelenir; arama da MASKELENMİŞ metinde yapılır (aramayla sır sezilemez).
Bilinen sınır: WebSocket olay akışı (``S.events``, ``server/app.py``) ham gider. Sayfa o veriyi ekranda
göstermez, yalnız "yenile" işareti sayar; WS'in kendisini süzmek ``app.py`` sahibinin işi (PR'da not).
"""
from __future__ import annotations

import csv
import io
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from fastapi import FastAPI, Request
from fastapi.responses import Response

from control_hub.server.errors import fail

LEVELS = ('info', 'warning', 'error', 'critical')              # hub_core.events.LEVELS (testte eşitlik)
MAX_ROWS = 500
MASK = '[GİZLİ]'
PATH_MASK = '[yol]'
MAX_Q = 200
CSV_HEAD = ('zaman', 'sira', 'seviye', 'kaynak', 'mesaj', 'ayrinti')
FORMULA_LEAD = ('=', '+', '-', '@', '\t', '\r')
SENSITIVE_KEY = re.compile(
    r'(?i)(^|_)(pin|pass|passwd|password|sifre|parola|token|secret|api_?key|key|auth\w*|cookie|session|'
    r'credential\w*|path|file|filename|dir|folder|url|cwd|home)($|_)')
ROOTS = ('home', 'Users', 'tmp', 'var', 'etc', 'opt', 'usr', 'root', 'mnt', 'srv', 'proc', 'dev', 'media',
         'run', 'private', 'Volumes', 'app', 'data', 'workspace', 'snap', 'nix', 'lib', 'bin', 'sys', 'boot',
         'Library')
_KEYWORDS = r'x-api-key|api[-_ ]?key|authorization|token|secret|password|passwd|şifre|parola|cookie'
_PATTERNS = (
    (re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-*]{4,}'), lambda m: f'{m[1]} {MASK}'),
    (re.compile(r'(?i)\b(' + _KEYWORDS + r')(["\']?\s*[:=]\s*["\']?)[^\s"\',;}]+'),
     lambda m: f'{m[1]}{m[2]}{MASK}'),
    (re.compile(r'(?i)\bpin\b(\W{0,3})\d{4,12}(?!\d)'), lambda m: f'PIN{m[1]}{MASK}'),
    (re.compile(r'\bsk-[A-Za-z0-9_\-*]{4,}'), lambda m: MASK),
    (re.compile(r'(?i)(\b[a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@'), lambda m: f'{m[1]}{MASK}@'),
    (re.compile(r'\b[A-Za-z0-9_\-]{32,}\b'), lambda m: MASK),            # uzun jeton / özet benzeri diziler
    (re.compile(r'\\\\[^\s\\/:*?"<>|]+\\[^\s"<>|]*'), lambda m: PATH_MASK),         # UNC: \\sunucu\paylaşım
    (re.compile(r'\b[A-Za-z]:[\\/](?:[^\\/:*?"<>|\r\n]+[\\/])*[^\\/:*?"<>|\s]*'),
     lambda m: PATH_MASK),                                                  # C:\...
    (re.compile(r'(?<![\w.:/-])(?:/(?:' + '|'.join(ROOTS) + r')(?:/[^\s"\'<>|:;,)]*)?|~/[^\s"\'<>|:;,)]*)'),
     lambda m: PATH_MASK),
)


_TR_FOLD = str.maketrans('ıİIşŞğĞüÜöÖçÇ', 'iiisSgGuUoOcC')


def fold(text: Any) -> str:
    """Arama katlaması: harf boyu ve Türkçe harf (ı İ ş ğ ü ö ç) duyarsız; ASCII klavyeyle de bulunur."""
    return str(text).translate(_TR_FOLD).casefold()


def redact(text: Any, secrets: Iterable[str] = ()) -> str:
    """Sırları maskeler. ``secrets``: bilinen kesin değerler (Hub PIN'i). URL yolları (/api/...) kalır."""
    out = str(text)
    for secret in sorted({s for s in secrets if isinstance(s, str) and len(s) >= 4}, key=len, reverse=True):
        out = re.sub(r'(?<!\d)' + re.escape(secret) + r'(?!\d)', MASK, out) if secret.isdigit() \
            else out.replace(secret, MASK)
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


def data_text(data: Optional[Dict[str, Any]], secrets: Iterable[str] = ()) -> str:
    """Olayın ``data`` sözlüğü -> ``anahtar=değer`` metni; duyarlı anahtarların değeri hiç gösterilmez."""
    parts = []
    for k, v in (data or {}).items():
        key = redact(k, secrets)
        parts.append(f'{key}={MASK}' if SENSITIVE_KEY.search(str(k)) else f'{key}={redact(v, secrets)[:120]}')
    return ', '.join(parts)[:300]


def row_of(ev: Any, secrets: Iterable[str] = ()) -> Dict[str, Any]:
    secrets = tuple(secrets)
    return {'seq': ev.seq, 'ts': ev.ts, 'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ev.ts)),
            'level': ev.level, 'source': redact(ev.kind, secrets), 'message': redact(ev.message, secrets),
            'detail': data_text(ev.data, secrets)}


def select(rows: Sequence[Dict[str, Any]], level: str = 'info', source: str = '', q: str = '',
           limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    """Yeni olaylar ÜSTTE. ``level``: en düşük seviye. ``q``: MASKELENMİŞ metinde (``fold`` ile) aranır."""
    floor = LEVELS.index(level)
    needle = fold(q)
    out = [r for r in sorted(rows, key=lambda r: -r['seq'])
           if LEVELS.index(r['level']) >= floor and (not source or r['source'] == source)
           and (not needle or needle in fold(f"{r['source']} {r['message']} {r['detail']}"))]
    return out[:limit]


def _cell(value: Any) -> str:
    text = str(value)
    return "'" + text if text.startswith(FORMULA_LEAD) else text


def to_csv(rows: Sequence[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\r\n')
    writer.writerow(CSV_HEAD)
    for r in rows:
        writer.writerow([_cell(r[k]) for k in ('time', 'seq', 'level', 'source', 'message', 'detail')])
    return '\ufeff' + buf.getvalue()


def install_log_routes(app: FastAPI, hub: Any, *, need_view: Any) -> None:
    def secrets() -> List[str]:
        pin = getattr(getattr(hub, 'access', None), 'pin', None)
        return [pin] if isinstance(pin, str) else []

    def query(request: Request):
        p = request.query_params
        level = p.get('level') or 'info'
        if level not in LEVELS:
            return None, fail(400, 'bad_level', f'level şunlardan biri olmalı: {", ".join(LEVELS)}')
        try:
            limit = max(1, min(MAX_ROWS, int(p.get('limit') or MAX_ROWS)))
        except ValueError:
            return None, fail(400, 'bad_limit', 'limit tam sayı olmalı (1–500)')
        q, source = (p.get('q') or '')[:MAX_Q], (p.get('source') or '')[:64]
        sec = secrets()
        every = [row_of(e, sec) for e in hub.events.since(0, limit=MAX_ROWS)]
        return (every, select(every, level, source, q, limit)), None

    @app.get('/api/log')
    async def log_get(request: Request):
        need_view(request)
        got, bad = query(request)
        if bad is not None:
            return bad
        every, rows = got
        return {'ok': True, 'events': rows, 'sources': sorted({r['source'] for r in every}),
                'levels': list(LEVELS), 'total': len(every), 'shown': len(rows), 'capacity': MAX_ROWS}

    @app.get('/api/log.csv')
    async def log_csv(request: Request):
        need_view(request)
        got, bad = query(request)
        if bad is not None:
            return bad
        name = time.strftime('gunluk-%Y%m%d-%H%M%S.csv')
        return Response(to_csv(got[1]), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})


def install(app, ctx) -> None:
    install_log_routes(app, ctx.hub, need_view=ctx.need_view)
