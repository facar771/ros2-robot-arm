"""Yerel (bu bilgisayardan gelen) istek denetimi: tek yerde. Basılı-tut jog ve ajan anahtarı gibi uçlar yalnız
aynı makineden izinlidir (docs/MIMARI.md §7 R2). Vekil başlığı varsa yerel SAYILMAZ: ters vekil arkasındaki
uzak istemci loopback'ten gelmiş gibi görünebilir."""
from __future__ import annotations

from fastapi import Request

LOOPBACK_CLIENTS = ('127.0.0.1', '::1', '::ffff:127.0.0.1')
LOOPBACK_HOSTS = ('localhost', '127.0.0.1', '::1')
PROXY_HEADERS = ('x-forwarded-for', 'x-forwarded-host', 'forwarded', 'x-real-ip')


def is_local_request(request: Request) -> bool:
    """İstemci loopback, Host başlığı loopback adı ve vekil başlığı yok: üçü birlikte."""
    client = request.client.host if request.client else ''
    host = (request.headers.get('host') or '').strip().lower()
    if host.startswith('['):
        name = host[1:].split(']', 1)[0]
    else:
        name = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
    return (client in LOOPBACK_CLIENTS and name in LOOPBACK_HOSTS
            and not any(h in request.headers for h in PROXY_HEADERS))
