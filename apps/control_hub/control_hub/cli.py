"""``python -m control_hub [--fake | --robot-url URL]``: Hub'ı başlatır (masaüstü uygulaması aynı çekirdeği
kullanacak)."""
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
    RobotApiBackend,
    load_scenarios,
    load_ui_config,
)
from control_hub.paths import resolve_config_dir

DEFAULT_PORT = 8765
DEFAULT_ROBOT_URL = 'http://127.0.0.1:8080'


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
                   help='sahte robot (ROS/donanım yok). Verilmezse gerçek robot API v1 kullanılır.')
    p.add_argument('--robot-url', default=os.environ.get('HUB_ROBOT_URL', DEFAULT_ROBOT_URL),
                   help=f'robot API adresi (varsayılan {DEFAULT_ROBOT_URL}; robot yalnızca '
                        '127.0.0.1 dinler). --fake ile birlikte yok sayılır.')
    p.add_argument('--host', default='0.0.0.0', help='dinlenen adres (varsayılan: 0.0.0.0, LAN)')
    p.add_argument('--port', type=int, default=int(os.environ.get('HUB_PORT', DEFAULT_PORT)))
    p.add_argument('--access-mode', choices=('open', 'pin', 'locked'),
                   default=os.environ.get('HUB_ACCESS_MODE', 'pin'))
    p.add_argument('--pin', default=os.environ.get('HUB_PIN'),
                   help='4-12 haneli PIN (yoksa her açılışta rastgele 6 hane üretilir)')
    p.add_argument('--config-dir', type=Path, default=os.environ.get('HUB_CONFIG_DIR'),
                   help='scenarios.yaml + ui.config.json klasörü (varsayılan: kullanıcı klasörü varsa '
                        'o, yoksa gömülü varsayılan)')
    p.add_argument('--fake-speed', type=float, default=1.0,
                   help='sahte robot zaman ölçeği (0.1 = 10 kat hızlı; varsayılan 1)')
    p.add_argument('--allow-host', action='append', default=[],
                   help="Host başlığı olarak kabul edilecek ek ad (örn. robot.evim). Tekrarlanabilir.")
    p.add_argument('--no-qr', action='store_true', help='terminalde QR kodu gösterme')
    p.add_argument('--check-config', action='store_true', help='yapılandırmayı doğrula ve çık')
    p.add_argument('--firewall-help', action='store_true',
                   help='telefon erişimi için güvenlik duvarı komutlarını YAZDIR (hiçbir şey eklemez)')
    return p


def print_firewall_help(port: int) -> None:
    from control_hub.desktop import firewall as fw
    print(f'Telefondan bağlanmak için Windows güvenlik duvarında TCP {port} gelen bağlantısına izin ver.')
    print('Bu komut HİÇBİR ŞEY EKLEMEZ; aşağıdakileri kendin, YÖNETİCİ PowerShell\'de çalıştır:\n')
    print('  Ekle (yalnızca Özel ağ profili):\n    ' + fw.powershell_add(port))
    print('  Kontrol:\n    ' + fw.powershell_show())
    print('  Kaldır:\n    ' + fw.powershell_remove())


def build_hub(args: argparse.Namespace) -> Hub:
    config_dir = resolve_config_dir(args.config_dir)
    scenarios = load_scenarios(config_dir / 'scenarios.yaml')
    ui = load_ui_config(config_dir / 'ui.config.json', scenarios)
    access = AccessControl(args.access_mode, args.pin or None)   # boş string = rastgele PIN
    if args.fake:
        backend = FakeBackend(time_scale=args.fake_speed)
    else:
        backend = RobotApiBackend(args.robot_url, autostart=not args.check_config)
    return Hub(backend, scenarios, ui, access)


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
    backend = hub.backend
    if isinstance(backend, RobotApiBackend):
        backend.wait_first_poll(3.0)
        snap = backend.snapshot()
        where = f'{backend.host}:{backend.port}'
        if snap['connected'] and snap['compatible']:
            print(f'  Robot         : {where} bağlı (API v{snap["api_version"]}, mod {snap["mode"]})')
        else:
            print(f'  Robot         : {where} HENÜZ BAĞLI DEĞİL: {snap["problem"]}')
            print('                  (Hub açık kalır ve yeniden dener; robotu başlat: '
                  'docker compose up demo)')
        if not backend.loopback:
            print('  UYARI: robot adresi loopback değil; robot API\'si yalnızca 127.0.0.1 kabul eder.')
    else:
        print('  Robot         : SAHTE (--fake)')
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
    if args.firewall_help:
        print_firewall_help(args.port)
        return 0
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
