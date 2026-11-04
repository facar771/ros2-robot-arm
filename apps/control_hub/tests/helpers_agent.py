"""Ajan testleri için ortak parçalar: bellek içi keyring, sahte sağlayıcı sunucusu, hızlı bekleme."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CANARY = 'sk-CANARY-7f3a9c2e5b1d4068aa'


class MemoryKeyring:
    """keyring arayüzü (get/set/delete_password) bellekte; ne yazıldığını da kaydeder."""

    def __init__(self, fail_set=False):
        self.data, self.calls, self.fail_set = {}, [], fail_set

    def get_password(self, service, user):
        self.calls.append(('get', service, user))
        return self.data.get((service, user))

    def set_password(self, service, user, value):
        self.calls.append(('set', service, user))
        if self.fail_set:
            raise RuntimeError('depo kilitli')
        self.data[(service, user)] = value

    def delete_password(self, service, user):
        self.calls.append(('delete', service, user))
        self.data.pop((service, user), None)


def wait_for(cond, timeout=8.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


class Provider:
    """Sahte OpenAI uyumlu sunucu (127.0.0.1). ``script``: sırayla yanıtlar; ``fail``: auth başlığını
    yankılayan hata (gerçek sağlayıcıların kötü anahtara yaptığı gibi)."""

    def __init__(self, script=(), fail=None):
        self.script, self.fail, self.requests = list(script), fail, []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):    # noqa: N802
                raw = self.rfile.read(int(self.headers['Content-Length']))
                auth = self.headers.get('Authorization', '')
                provider.requests.append((self.path, auth, raw.decode()))
                if provider.fail:
                    status, text = provider.fail(auth)
                else:
                    status, text = 200, json.dumps(provider.script.pop(0))
                data = text.encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def tool_reply(name, arguments, call_id='call_1'):
    return {'choices': [{'message': {'content': None, 'tool_calls': [{
        'id': call_id, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}]}


def text_reply(text):
    return {'choices': [{'message': {'content': text}}]}
