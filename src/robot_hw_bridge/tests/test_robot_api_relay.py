"""Robot API ile acil dur bağı (sahte API sunucusu, 127.0.0.1): ESP32 -> API (/api/stop, yeniden deneme),
API -> ESP32 (estop kilidi görülünce donanım acil durdurması), API susarsa kontrollü durdurma."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from robot_hw_bridge.robot_api import EstopRelay, RobotApiClient


class FakeApi:
    def __init__(self, port=0):
        self.tripped, self.stops, self.fail_stop = False, [], 0
        api = self

        class H(BaseHTTPRequestHandler):
            def _send(self, status, doc):
                data = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):    # noqa: N802
                self._send(200, {'robot': {'estop': {'tripped': api.tripped}}})

            def do_POST(self):   # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if api.fail_stop > 0:
                    api.fail_stop -= 1
                    return self._send(503, {})
                api.stops.append(body['reason'])
                api.tripped = True
                self._send(200, {'ok': True})

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', port), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def make(api, **kw):
    calls = {'estop': [], 'stop': []}
    relay = EstopRelay(RobotApiClient(f'http://127.0.0.1:{api.port}', timeout=0.3),
                       hw_estop=calls['estop'].append, hw_stop=calls['stop'].append,
                       moving=kw.pop('moving', lambda: False), poll_s=0.02, retry_s=0.02, **kw).start()
    return relay, calls


@pytest.mark.parametrize('url', ['http://example.com:8080', 'http://192.168.1.5:8080', 'https://127.0.0.1:8080',
                                 'http://127.0.0.1', 'http://host.docker.internal:8080'])
def test_client_is_loopback_only(url):
    with pytest.raises(ValueError):
        RobotApiClient(url)


def test_esp32_problem_stops_the_robot_api():
    api = FakeApi()
    relay, calls = make(api)
    try:
        relay.report('ESP32 telemetrisi kesildi')
        assert wait(lambda: api.stops) and api.stops[0].startswith('hw_bridge: ESP32 telemetrisi')
        assert relay.delivered == ['ESP32 telemetrisi kesildi']
    finally:
        relay.close()
        api.close()


def test_report_is_retried_until_the_api_answers():
    api = FakeApi()
    api.fail_stop = 3
    relay, _ = make(api)
    try:
        relay.report('MCU ACİL DUR')
        assert wait(lambda: api.stops) and relay.unreached == []
    finally:
        relay.close()
        api.close()


def test_unreachable_api_is_visible_not_silent():
    api = FakeApi()
    port = api.port
    api.close()
    relay = EstopRelay(RobotApiClient(f'http://127.0.0.1:{port}', timeout=0.1), hw_estop=lambda r: None,
                       hw_stop=lambda r: None, moving=lambda: False, poll_s=0.02, retry_s=0.02,
                       give_up_s=0.3).start()
    try:
        relay.report('FAULT')
        assert wait(lambda: relay.unreached == ['FAULT'])
    finally:
        relay.close()


def test_api_estop_reaches_the_hardware_once():
    api = FakeApi()
    relay, calls = make(api)
    try:
        time.sleep(0.1)
        assert calls['estop'] == []
        api.tripped = True
        assert wait(lambda: calls['estop'])
        time.sleep(0.2)
        assert calls['estop'] == ['robot API acil dur']
        api.tripped = False                                   # reset
        time.sleep(0.1)
        api.tripped = True                                    # yeni acil dur -> yine iletilir
        assert wait(lambda: len(calls['estop']) == 2)
    finally:
        relay.close()
        api.close()


def test_a_latch_that_is_already_set_at_startup_stops_the_hardware():
    api = FakeApi()
    api.tripped = True
    relay, calls = make(api)
    try:
        assert wait(lambda: calls['estop'])
    finally:
        relay.close()
        api.close()


def test_silent_api_while_moving_stops_the_axes_controlled():
    api = FakeApi()
    relay, calls = make(api, moving=lambda: True, api_lost_s=0.2)
    try:
        time.sleep(0.1)
        api.close()
        assert wait(lambda: calls['stop'], 2.0) and calls['estop'] == []
        assert len(calls['stop']) == 1
    finally:
        relay.close()
