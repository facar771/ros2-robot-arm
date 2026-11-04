"""``ControlHub.exe --agent-selftest``: ajan .exe içinde gerçekten çalışıyor mu? (CI ve elle)

Gerçek Hub (sahte robot) + yerel sahte OpenAI-uyumlu LLM sunucusu; her şey gerçek HTTP ile:

1. ``robot_agent`` pakete girmiş mi (ajan ``enabled``), arayüz dosyaları (ajan kartı) gömülü mü;
2. anahtar girilir, **kuru çalıştırma** turu: LLM bir hareket aracı çağırır, robota HİÇBİR komut gitmez;
3. kötü anahtarı yankılayan 401 yolu: hata kullanıcıya gelir, anahtar sızmaz;
4. **canary**: sahte anahtar tüm HTTP yanıtlarında, olaylarda, anlık görüntüde ve günlük dosyasında aranır
   (anahtar yalnızca sağlayıcıya, yalnızca ``Authorization`` başlığında gitmiş olmalı);
5. anahtar deposu: bellek içi ile "beni hatırla" yaz-oku-sil; ardından **gerçek işletim sistemi deposu**
   (Windows'ta Credential Manager) ayrı bir hizmet adıyla yaz-oku-sil. Depo yoksa/çalışmıyorsa çıktı
   ``KEYRING-REAL: SKIPPED <neden>`` der (sessiz atlama yok); veri uyuşmazlığı ise hatadır.

Çıkış 0: hepsi geçti (gerçek depo SKIPPED olabilir, açıkça yazılır); 1: hata.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import os
import secrets
import socket
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

REAL_SERVICE = 'ControlHub-Agent-selftest'      # kullanıcının gerçek kaydına (ControlHub-Agent) dokunulmaz


class SelfTestError(AssertionError):
    pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Provider:
    """Sahte OpenAI-uyumlu sunucu. ``echo401``: her istekte kötü anahtarı yankılayan 401."""

    def __init__(self, mode: str = 'ok'):
        self.mode, self.requests = mode, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):    # noqa: N802
                raw = self.rfile.read(int(self.headers['Content-Length']))
                auth = self.headers.get('Authorization', '')
                outer.requests.append((self.path, auth, raw.decode('utf-8', 'replace')))
                if outer.mode == 'echo401':
                    status = 401
                    body = {'error': {'message': f'Incorrect API key provided: {auth}'}}
                else:
                    status = 200
                    sent_tool_result = any(m.get('role') == 'tool' for m in json.loads(raw)['messages'])
                    if sent_tool_result:
                        message: Dict[str, Any] = {'content': 'eve gittim (kuru çalıştırma)'}
                    else:
                        message = {'content': None, 'tool_calls': [{
                            'id': 'call_1', 'type': 'function',
                            'function': {'name': 'go_home', 'arguments': '{}'}}]}
                    body = {'choices': [{'message': message}]}
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class _Http:
    def __init__(self, base: str):
        self.base = base
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.seen: List[str] = []             # her yanıt (gövde + başlıklar): canary taraması için
        self.csrf = ''

    def call(self, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers = {'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf}
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            resp = self.opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            resp = exc
        raw = resp.read().decode('utf-8', 'replace')
        self.seen.append(raw + str(dict(resp.headers)))
        try:
            return resp.status, json.loads(raw)
        except ValueError:
            return resp.status, {'_raw': raw}


def _wait(cond, what: str, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise SelfTestError(f'zaman aşımı: {what}')


def _check(cond: Any, message: str) -> None:
    if not cond:
        raise SelfTestError(message)


class _MemoryBackend:
    """keyring arayüzü, bellekte."""

    def __init__(self) -> None:
        self.data: Dict[Tuple[str, str], str] = {}

    def get_password(self, service, user):
        return self.data.get((service, user))

    def set_password(self, service, user, value):
        self.data[(service, user)] = value

    def delete_password(self, service, user):
        self.data.pop((service, user), None)


class _ScopedReal:
    """Gerçek ``keyring``, ama ayrı hizmet adıyla (kullanıcının gerçek kaydına dokunmaz)."""

    def __init__(self, keyring_module: Any):
        self._k = keyring_module

    def get_password(self, service, user):
        return self._k.get_password(REAL_SERVICE, user)

    def set_password(self, service, user, value):
        self._k.set_password(REAL_SERVICE, user, value)

    def delete_password(self, service, user):
        self._k.delete_password(REAL_SERVICE, user)


def keyring_real_check(canary: str) -> str:
    """'OK <arka uç>' ya da 'SKIPPED <neden>'. Veri uyuşmazlığı SelfTestError."""
    try:
        import keyring
        backend = keyring.get_keyring()
    except Exception as exc:    # noqa: BLE001
        return f'SKIPPED keyring içe aktarılamadı ({type(exc).__name__}: {exc})'
    name = f'{type(backend).__module__}.{type(backend).__name__}'
    if any(part in name for part in ('.fail.', '.null.')):
        return f'SKIPPED kullanılabilir gerçek depo yok (arka uç: {name})'
    from control_hub.agent.keystore import KeyStore
    scoped = _ScopedReal(keyring)
    try:
        try:
            keyring.delete_password(REAL_SERVICE, 'openai')
        except Exception:    # noqa: BLE001 - yoktu
            pass
        ks = KeyStore(scoped)
        remembered, warning = ks.set('openai', canary, remember=True)
        if not remembered:
            return f'SKIPPED depo yazmayı reddetti ({name}): {warning}'
        got = keyring.get_password(REAL_SERVICE, 'openai')
        _check(got == canary, f'gerçek depo geri okuma uyuşmadı ({name})')
        again = KeyStore(scoped)                                         # "yeniden başlatma"
        _check(again.get('openai') == canary and again.status()['openai']['remembered'],
               'yeni KeyStore kayıtlı anahtarı yüklemedi')
        again.clear('openai')
        _check(keyring.get_password(REAL_SERVICE, 'openai') is None, 'silme sonrası kayıt hâlâ var')
        return f'OK {name}'
    except SelfTestError:
        raise
    except Exception as exc:    # noqa: BLE001 - depo çalışmıyor: nedeniyle birlikte açıkça atla
        return f'SKIPPED gerçek depo hata verdi ({name}): {type(exc).__name__}'
    finally:
        try:
            keyring.delete_password(REAL_SERVICE, 'openai')
        except Exception:    # noqa: BLE001
            pass


def agent_selftest() -> int:
    from control_hub.agent import KeyStore
    from control_hub.desktop.service import HubService
    from control_hub.desktop.settings import Settings

    canary = os.environ.get('HUB_SELFTEST_CANARY') or f'sk-CANARY-selftest-{secrets.token_hex(8)}'
    log_fd, log_path = tempfile.mkstemp(prefix='agent_selftest.', suffix='.log')
    os.close(log_fd)
    handler = logging.FileHandler(log_path, encoding='utf-8')
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    provider_ok, provider_bad = _Provider('ok'), _Provider('echo401')
    svc: Optional[HubService] = None
    try:
        memory = _MemoryBackend()
        keys = KeyStore(memory)
        settings = Settings(fake=True, port=_free_port(), pin='123456', agent_provider='openai',
                            agent_model='selftest', agent_base_url=provider_ok.base_url,
                            agent_dry_run=True)
        svc = HubService(settings, host='127.0.0.1', keystore=keys)
        svc.start()
        hub = svc.hub
        _check(hub is not None and hub.agent is not None, 'Hub ajanı yok')
        agent = hub.agent
        _check(agent.enabled, f'ajan .exe içinde KAPALI: {agent.unavailable}')
        print('1. ajan etkin (robot_agent paketlenmiş)')

        http = _Http(f'http://127.0.0.1:{svc.port}')
        st, _ = http.call('/api/login', {'pin': '123456'})
        _check(st == 200, f'giriş başarısız ({st})')
        st, doc = http.call('/api/agent/status')
        _check(st == 200 and doc.get('enabled') and doc.get('can_configure') and doc.get('csrf'),
               f'ajan durumu beklenmedik: {st} {str(doc)[:200]}')
        http.csrf = doc['csrf']
        st, page = http.call('/')
        _check('/agent.js' in page.get('_raw', ''), 'index.html agent.js yüklemiyor')
        st, js = http.call('/agent.js')
        _check(st == 200 and 'p-agent' in js.get('_raw', ''), 'agent.js (ajan kartı) gömülü değil')
        print('2. ajan kartı arayüz dosyaları gömülü; CSRF + yerel yetki tamam')

        st, doc = http.call('/api/agent/key', {'provider': 'openai', 'key': canary, 'remember': False})
        _check(st == 200 and doc['remembered'] is False, f'anahtar ayarlanamadı: {st}')
        st, _ = http.call('/api/agent/message', {'text': 'eve git'})
        _check(st == 202, f'mesaj kabul edilmedi ({st})')
        _wait(lambda: any(m['role'] == 'assistant' for m in agent.chat()), 'kuru çalıştırma turu')
        _wait(lambda: not agent.active, 'tur bitişi')
        tools = [m['text'] for m in agent.chat() if m['role'] == 'tool']
        _check(any('go_home' in t and 'dry_run' in t for t in tools), f'kuru çalıştırma satırı yok: {tools}')
        _check(hub.backend.history == [], f'KURU ÇALIŞTIRMADA robota komut gitti: {hub.backend.history}')
        _check(provider_ok.requests and provider_ok.requests[0][1] == f'Bearer {canary}',
               'anahtar sağlayıcıya Authorization ile gitmedi')
        _check(all(canary not in body for _, _, body in provider_ok.requests),
               'anahtar sağlayıcı istek GÖVDESİNE sızdı')
        print('3. kuru çalıştırma: sahte yerel LLM + hareket aracı, robota komut GİTMEDİ')

        st, _ = http.call('/api/agent/config', {'base_url': provider_bad.base_url})
        _check(st == 200, f'ayar kaydedilemedi ({st})')
        st, _ = http.call('/api/agent/message', {'text': f'selam {canary}'})
        _check(st == 202, f'2. mesaj kabul edilmedi ({st})')
        _wait(lambda: any('HTTP 401' in m['text'] for m in agent.chat()), '401 hata mesajı')
        _wait(lambda: not agent.active, '2. tur bitişi')
        print('4. kötü anahtar yankılayan 401: hata kullanıcıya geldi')

        for path in ('/api/state', '/api/events', '/api/session', '/api/ui', '/api/agent/status',
                     '/api/agent/chat'):
            http.call(path)
        handler.flush()
        everything = '\n'.join(http.seen + [
            json.dumps([e.as_dict() for e in hub.events.since(0, 1000)]),
            json.dumps(hub.snapshot(), default=str), open(log_path, encoding='utf-8').read(),
            json.dumps(agent.chat())])
        _check(canary not in everything, 'CANARY SIZDI (yanıt/olay/anlık görüntü/günlük)')
        print(f'5. canary: {len(http.seen)} yanıt + olaylar + anlık görüntü + günlük taranıdı, sızıntı yok')

        st, doc = http.call('/api/agent/key', {'provider': 'anthropic', 'key': canary, 'remember': True})
        _check(st == 200 and doc['remembered'] is True, 'bellek-içi depoya "hatırla" yazılamadı')
        _check(memory.data.get(('ControlHub-Agent', 'anthropic')) == canary, 'depoda anahtar yok')
        st, _ = http.call('/api/agent/key/clear', {'provider': 'anthropic', 'forget': True})
        _check(st == 200 and not memory.data, 'bellek-içi depodan silinmedi')
        print('6. KEYRING-MEMORY: OK (yaz-oku-sil, bellek içi)')

        real = keyring_real_check(canary)
        print(f'7. KEYRING-REAL: {real}')
        print('AGENT-SELFTEST OK')
        return 0
    except Exception:    # noqa: BLE001
        traceback.print_exc()
        print('AGENT-SELFTEST FAILED')
        return 1
    finally:
        if svc is not None:
            svc.stop()
        provider_ok.close()
        provider_bad.close()
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()
        try:
            os.unlink(log_path)
        except OSError:
            pass
