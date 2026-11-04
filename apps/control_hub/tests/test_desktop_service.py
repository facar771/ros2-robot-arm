import json
import socket
import urllib.error
import urllib.request

import pytest

from control_hub.desktop.service import HubService, ServiceError, port_free
from control_hub.desktop.settings import Settings


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:        # noqa: S310
        return r.status, json.loads(r.read())


def test_start_serves_health_and_stops_cleanly():
    svc = HubService(Settings(fake=True, port=free_port(), pin='123456'), host='127.0.0.1')
    svc.start()
    try:
        assert svc.running and svc.pin == '123456'
        status, doc = get(f'http://127.0.0.1:{svc.port}/health')
        assert status == 200 and doc['ok'] and doc['backend'] == 'fake'
        with pytest.raises(urllib.error.HTTPError) as e:         # PIN korumalı
            get(f'http://127.0.0.1:{svc.port}/api/state')
        assert e.value.code == 401
        assert set(svc.urls()) == {'local', 'lan'}
        assert svc.robot_snapshot()['name'] == 'fake'
    finally:
        svc.stop()
    assert not svc.running and svc.hub is None
    assert port_free(svc.settings.port, '127.0.0.1')              # port serbest kaldı


def test_busy_port_gives_a_clear_error():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        with pytest.raises(ServiceError, match='port dolu'):
            HubService(Settings(fake=True, port=port), host='127.0.0.1').start()
    finally:
        s.close()


def test_invalid_settings_and_double_start():
    with pytest.raises(ServiceError, match='PIN'):
        HubService(Settings(fake=True, pin='1')).start()
    svc = HubService(Settings(fake=True, port=free_port()), host='127.0.0.1')
    svc.start()
    try:
        with pytest.raises(ServiceError, match='zaten'):
            svc.start()
    finally:
        svc.stop()


def test_bad_config_dir_is_reported_not_raised_raw(tmp_path):
    (tmp_path / 'scenarios.yaml').write_text('scenarios: []\n', encoding='utf-8')
    (tmp_path / 'ui.config.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ServiceError, match='scenarios'):
        HubService(Settings(fake=True, port=free_port()), host='127.0.0.1', config_dir=tmp_path).start()


def test_real_robot_url_does_not_need_the_robot_to_be_up():
    svc = HubService(Settings(robot_url='http://127.0.0.1:9', port=free_port(), pin='123456'),
                     host='127.0.0.1')
    svc.start()                                                   # robot kapalı: Hub yine açılır
    try:
        snap = svc.robot_snapshot()
        assert snap['name'] == 'robot-api' and snap['connected'] is False
    finally:
        svc.stop()


def test_service_runs_the_esp32_bridge_next_to_the_hub():
    from robot_hw_bridge import Esp32Sim, HwLink, SimServer, SocketTransport
    sim = Esp32Sim()
    srv = SimServer(sim).start()
    bport = free_port()
    svc = HubService(Settings(fake=True, port=free_port(), pin='123456', bridge_port=bport,
                              serial_port=f'socket://127.0.0.1:{srv.port}'), host='127.0.0.1')
    svc.start()
    try:
        import time
        end = time.monotonic() + 3
        while time.monotonic() < end and not svc.hub.bridge.status()['serial_connected']:
            time.sleep(0.02)
        snap = svc.hub.snapshot()
        assert snap['bridge']['serial_connected'] and snap['bridge']['listen'] == f'127.0.0.1:{bport}'
        assert svc.bridge_token is None                       # kapalıyken (yalnızca loopback) token yok
        hw = HwLink(SocketTransport('127.0.0.1', bport), heartbeat_s=0.02).start()
        assert hw.wait_link(3)
        hw.stop()
        assert any(e.kind == 'hw_bridge_started' for e in svc.hub.events.since(0))
    finally:
        svc.stop()
        srv.stop()
    assert port_free(bport, '127.0.0.1')                      # köprü portu da serbest kaldı


def test_open_bridge_gets_a_token_and_listens_on_all_interfaces():
    svc = HubService(Settings(fake=True, port=free_port(), bridge_port=free_port(), bridge_open=True,
                              serial_port='socket://127.0.0.1:9'), host='127.0.0.1')
    svc.start()
    try:
        assert svc.bridge_token and len(svc.bridge_token) >= 8
        assert svc.hub.bridge.status()['listen'].startswith('0.0.0.0:')
        assert svc.hub.bridge.status()['authenticated'] is True
    finally:
        svc.stop()


def test_busy_bridge_port_fails_cleanly_and_releases_the_hub_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    s.listen()
    hub_port = free_port()
    try:
        svc = HubService(Settings(fake=True, port=hub_port, bridge_port=s.getsockname()[1],
                                  serial_port='COM3'), host='127.0.0.1')
        with pytest.raises(ServiceError, match='köprü portu'):
            svc.start()
        assert svc.hub is None and port_free(hub_port, '127.0.0.1')
    finally:
        s.close()
