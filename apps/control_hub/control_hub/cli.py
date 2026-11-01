"""``python -m control_hub --fake``: Hub'ı başlatır (PR-H3 masaüstü uygulaması aynı çekirdeği kullanır)."""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path
from typing import List, Optional

from control_hub.hub_core import (
    AccessControl,
    ConfigError,
    FakeBackend,
    Hub,
    load_scenarios,
    load_ui_config,
)

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / 'config'
DEFAULT_PORT = 8765


def lan_ip() -> str:
    """Yerel ağdaki IP (paket göndermez: UDP soketini bağlayıp yerel adrese bakar)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='control_hub', description='Robot Kontrol Merkezi (Hub)')
    p.add_argument('--fake', action='store_true',
                   help='sahte robot (ROS/donanım yok). Gerçek robot bağlantısı PR-H2 ile gelir.')
    p.add_argument('--host', default='0.0.0.0', help='dinlenen adres (varsayılan: 0.0.0.0, LAN)')
    p.add_argument('--port', type=int, default=int(os.environ.get('HUB_PORT', DEFAULT_PORT)))
    p.add_argument('--access-mode', choices=('open', 'pin', 'locked'),
                   default=os.environ.get('HUB_ACCESS_MODE', 'pin'))
    p.add_argument('--pin', default=os.environ.get('HUB_PIN'),
                   help='4-12 haneli PIN (yoksa her açılışta rastgele 6 hane üretilir)')
    p.add_argument('--config-dir', type=Path, default=Path(os.environ.get('HUB_CONFIG_DIR',
                                                                          DEFAULT_CONFIG_DIR)))
    p.add_argument('--fake-speed', type=float, default=1.0,
                   help='sahte robot zaman ölçeği (0.1 = 10 kat hızlı; varsayılan 1)')
    p.add_argument('--allow-host', action='append', default=[],
                   help="Host başlığı olarak kabul edilecek ek ad (örn. robot.evim). Tekrarlanabilir.")
    p.add_argument('--no-qr', action='store_true', help='terminalde QR kodu gösterme')
    p.add_argument('--check-config', action='store_true', help='yapılandırmayı doğrula ve çık')
    return p


def build_hub(args: argparse.Namespace) -> Hub:
    scenarios = load_scenarios(args.config_dir / 'scenarios.yaml')
    ui = load_ui_config(args.config_dir / 'ui.config.json', scenarios)
    if not args.fake:
        raise SystemExit('Gerçek robot bağlantısı PR-H2 ile gelecek; şimdilik --fake ile başlat.')
    access = AccessControl(args.access_mode, args.pin or None)   # boş string = rastgele PIN
    return Hub(FakeBackend(time_scale=args.fake_speed), scenarios, ui, access)


def _print_banner(hub: Hub, host: str, port: int, show_qr: bool) -> None:
    ip = lan_ip() if host in ('0.0.0.0', '::') else host
    url = f'http://{ip}:{port}'
    print('\n=== Robot Kontrol Merkezi ===')
    print(f'  Bilgisayardan : http://localhost:{port}')
    if os.path.exists('/.dockerenv'):
        print(f'  Telefondan    : http://<bilgisayarın Wi-Fi IP\'si>:{port}   (Windows\'ta: ipconfig)')
        url = ''
    else:
        print(f'  Telefondan    : {url}   (aynı Wi-Fi)')
    print(f'  Erişim kipi   : {hub.access.mode}')
    if hub.access.mode == 'pin':
        print(f'  PIN           : {hub.access.pin}')
    if hub.access.mode == 'open' and host not in ('127.0.0.1', 'localhost', '::1'):
        print('  UYARI: kip "open" ve ağa açık: aynı ağdaki herkes robotu kontrol edebilir.')
    print('  İnternete açma / port yönlendirme YAPMA.  Durdurmak için Ctrl-C.\n')
    if show_qr and url:
        try:
            import segno
            segno.make(url).terminal(compact=True)
        except Exception:                                   # QR yalnızca kolaylık
            pass


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        hub = build_hub(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f'Hata: {exc}', file=sys.stderr)
        return 2
    if args.check_config:
        print(f'Yapılandırma geçerli: {len(hub.scenarios)} senaryo.')
        return 0
    import uvicorn

    from control_hub.server import create_app
    _print_banner(hub, args.host, args.port, show_qr=not args.no_qr)
    try:
        uvicorn.run(create_app(hub, extra_allowed_hosts=args.allow_host), host=args.host,
                    port=args.port, log_level='warning')
    finally:
        hub.close()
    return 0
