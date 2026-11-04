"""HubService: Hub çekirdeğini + web sunucusunu arka plan iş parçacığında başlatır/durdurur.

GUI'siz ve Linux'ta test edilir. ``start()`` hata olursa ``ServiceError`` (Türkçe, kullanıcıya
gösterilebilir) fırlatır; Hub açılırken robot kapalıysa sorun değil (yeniden dener, arayüz söyler).
"""
from __future__ import annotations

import logging
import secrets
import socket
import sys
import threading
import time
from typing import Any, Dict, Optional

import uvicorn

from control_hub.cli import lan_ip
from control_hub.desktop.settings import Settings
from control_hub.hub_core import (
    AccessControl,
    FakeBackend,
    Hub,
    RobotApiBackend,
    load_scenarios,
    load_ui_config,
)
from control_hub.hub_core.scenarios import ConfigError
from control_hub.hw import BridgeError, SerialTcpBridge
from control_hub.paths import resolve_config_dir
from control_hub.server import create_app

log = logging.getLogger('control_hub.desktop')


class ServiceError(Exception):
    """Kullanıcıya gösterilebilir başlatma hatası."""


def port_free(port: int, host: str = '0.0.0.0') -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != 'win32':      # TIME_WAIT "dolu" sayılmasın (Windows'ta seçenek tersine işler)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class HubService:
    def __init__(self, settings: Settings, host: str = '0.0.0.0', config_dir=None):
        self.settings, self.host, self._config_dir = settings, host, config_dir
        self.hub: Optional[Hub] = None
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None
        self.port = settings.port

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and bool(
            self._server and self._server.started)

    @property
    def pin(self) -> Optional[str]:
        return self.hub.access.pin if self.hub and self.hub.access.mode == 'pin' else None

    def urls(self) -> Dict[str, str]:
        return {'local': f'http://localhost:{self.port}', 'lan': f'http://{lan_ip()}:{self.port}'}

    def start(self, timeout: float = 15.0) -> None:
        if self._thread is not None:
            raise ServiceError('Hub zaten çalışıyor')
        problems = self.settings.problems()
        if problems:
            raise ServiceError('; '.join(problems))
        if self.settings.port and not port_free(self.settings.port, self.host):
            raise ServiceError(f'{self.settings.port} numaralı port dolu: Hub zaten açık olabilir '
                               '(başka bir pencere?) ya da başka bir program kullanıyor. '
                               'Başka bir port seç.')
        try:
            cfg = resolve_config_dir(self._config_dir)
            scenarios = load_scenarios(cfg / 'scenarios.yaml')
            ui = load_ui_config(cfg / 'ui.config.json', scenarios)
            backend = FakeBackend() if self.settings.fake else RobotApiBackend(self.settings.robot_url)
            access = AccessControl(self.settings.access_mode, self.settings.pin or None)
        except ConfigError as exc:
            raise ServiceError(str(exc)) from exc
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        self.hub = Hub(backend, scenarios, ui, access)
        if self.settings.serial_port:
            try:
                self._start_bridge()
            except ServiceError:
                self._cleanup()
                raise
        config = uvicorn.Config(create_app(self.hub), host=self.host, port=self.settings.port,
                                log_level='warning', log_config=None)
        self._server = uvicorn.Server(config)
        self._error = None
        self._thread = threading.Thread(target=self._run, name='hub-server', daemon=True)
        self._thread.start()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._server.started:
                self.port = self._actual_port()
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.05)
        err = self._error
        self._cleanup()
        raise ServiceError(f'Sunucu başlamadı: {err}' if err else 'Sunucu zamanında başlamadı')

    @property
    def bridge_token(self) -> Optional[str]:
        return self.hub.bridge.token if self.hub and self.hub.bridge else None

    def _start_bridge(self) -> None:
        s = self.settings
        host = '0.0.0.0' if s.bridge_open else '127.0.0.1'
        token = secrets.token_urlsafe(9) if s.bridge_open else None     # açıkken token şart
        try:
            bridge = SerialTcpBridge(
                s.serial_port, baud=s.serial_baud, listen=(host, s.bridge_port), token=token,
                on_event=lambda kind, msg, lvl: self.hub.events.emit(  # type: ignore[union-attr]
                    f'hw_{kind}', msg, level=lvl if lvl in ('info', 'warning', 'error') else 'info'))
            bridge.start()
        except BridgeError as exc:
            raise ServiceError(str(exc)) from exc
        self.hub.bridge = bridge                                          # type: ignore[union-attr]

    def _run(self) -> None:
        try:
            self._server.run()          # type: ignore[union-attr]
        except BaseException as exc:    # noqa: BLE001 - GUI'ye taşınır
            self._error = exc
            log.exception('sunucu hata verdi')

    def _actual_port(self) -> int:
        try:
            return self._server.servers[0].sockets[0].getsockname()[1]    # type: ignore[union-attr]
        except Exception:
            return self.settings.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(8)
        self._cleanup()

    def _cleanup(self) -> None:
        if self.hub is not None:
            self.hub.close()
        self.hub = self._server = self._thread = None

    def robot_snapshot(self) -> Dict[str, Any]:
        return self.hub.backend.snapshot() if self.hub else {}
