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
