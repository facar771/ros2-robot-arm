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
    g = p.add_argument_group('ESP32 donanım köprüsü (USB-seri ↔ TCP)')
    g.add_argument('--serial-bridge', metavar='PORT', default=os.environ.get('HUB_SERIAL'),
                   help="seri port ('COM5', '/dev/ttyUSB0' ya da test için 'socket://127.0.0.1:8771'): "
                        'açılır ve TCP üzerinden tek istemciye sunulur')
    g.add_argument('--serial-baud', type=int, default=921600)
    g.add_argument('--bridge-listen', default='127.0.0.1:8770', metavar='HOST:PORT',
                   help='köprünün TCP adresi (varsayılan yalnızca bu bilgisayar). 0.0.0.0 için '
                        '--bridge-token şart')
    g.add_argument('--bridge-token', default=os.environ.get('HUB_BRIDGE_TOKEN'),
                   help='istemci ilk satırda "HUBBRIDGE-AUTH <token>" göndermeli')
    g.add_argument('--bridge-only', action='store_true',
                   help='yalnızca köprüyü çalıştır (web arayüzü/robot API yok)')
    g.add_argument('--list-serial-ports', action='store_true', help='seri portları listele ve çık')
    a = p.add_argument_group('Yapay zekâ ajanı (sohbet paneli)')
    a.add_argument('--no-agent', action='store_true', help='ajan panelini kapat')
    a.add_argument('--agent-provider', choices=('anthropic', 'openai', 'offline'), default='anthropic',
                   help="LLM sağlayıcısı; 'offline' = anahtarsız yerleşik kural çözücü (LLM değil)")
    a.add_argument('--agent-model', default='', help='model adı (openai için zorunlu; yapılandırma değeri)')
    a.add_argument('--agent-base-url', default='',
                   help='openai uç adresi (zorunlu; https, http yalnızca yerel makinede)')
    a.add_argument('--agent-daily-limit', type=int, default=200, help='günlük LLM isteği sınırı')
    a.add_argument('--agent-max-motions', type=int, default=3, help='tur başına hareket tavanı')
    a.add_argument('--agent-dry-run', action='store_true',
                   help='ajan hareket araçları robota GİTMEZ, ne olacağını söyler (ilk deneme için)')
    a.add_argument('--agent-key-prompt', choices=('anthropic', 'openai'), metavar='SAĞLAYICI',
                   help='açılışta anahtarı terminalden gizli sor (yalnızca bellekte tutulur)')
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


def _make_bridge(args: argparse.Namespace, on_event=None):
    from control_hub.hw import BridgeError, SerialTcpBridge
    from control_hub.hw.bridge import parse_listen
    try:
        host, port = parse_listen(args.bridge_listen)
        return SerialTcpBridge(args.serial_bridge, baud=args.serial_baud, listen=(host, port),
                               token=args.bridge_token or None, on_event=on_event)
    except (BridgeError, ValueError) as exc:
        raise SystemExit(f'Köprü ayarı hatalı: {exc}') from exc


def run_bridge_only(args: argparse.Namespace) -> int:
    import time
    if not args.serial_bridge:
        print('--bridge-only için --serial-bridge PORT gerekli (--list-serial-ports ile bak).',
              file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    bridge = _make_bridge(args)
    try:
        bridge.start()
    except Exception as exc:                                   # noqa: BLE001
        print(f'Köprü başlatılamadı: {exc}', file=sys.stderr)
        return 2
    st = bridge.status()
    print(f'Köprü çalışıyor: {st["serial"]} ↔ tcp://{st["listen"]}   (Ctrl-C ile durdur)')
    print('Docker içinden bağlanmak için: host.docker.internal:' + str(bridge.listen_port))
    try:
        while True:
            time.sleep(5)
            st = bridge.status()
            print(f'  seri={"bağlı" if st["serial_connected"] else "YOK"} '
                  f'istemci={"var" if st["client_connected"] else "yok"} '
                  f'→seri={st["bytes_to_serial"]}B →istemci={st["bytes_to_client"]}B')
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    return 0


def build_hub(args: argparse.Namespace) -> Hub:
    config_dir = resolve_config_dir(args.config_dir)
    scenarios = load_scenarios(config_dir / 'scenarios.yaml')
    ui = load_ui_config(config_dir / 'ui.config.json', scenarios)
    access = AccessControl(args.access_mode, args.pin or None)   # boş string = rastgele PIN
    if args.fake:
        backend = FakeBackend(time_scale=args.fake_speed)
    else:
        backend = RobotApiBackend(args.robot_url, autostart=not args.check_config)
    hub = Hub(backend, scenarios, ui, access)
    if args.serial_bridge and not args.check_config:
        hub.bridge = _make_bridge(
            args, on_event=lambda kind, msg, lvl: hub.events.emit(
                f'hw_{kind}', msg, level=lvl if lvl in ('info', 'warning', 'error') else 'info'))
    if not args.no_agent and not args.check_config:
        hub.agent = make_agent(hub, args)
    return hub


def make_agent(hub: Hub, args: argparse.Namespace):
    from control_hub.agent import AgentConfig, AgentError, AgentService
    config = AgentConfig(provider=args.agent_provider, model=args.agent_model,
                         base_url=args.agent_base_url, daily_limit=args.agent_daily_limit,
                         max_motions=args.agent_max_motions, dry_run=args.agent_dry_run)
    problems = config.problems()
    if problems:
        raise ValueError('ajan ayarı: ' + '; '.join(problems))
    service = AgentService(hub, config)
    if args.agent_key_prompt and service.enabled:
        import getpass
        key = getpass.getpass(f'{args.agent_key_prompt} API anahtarı (görünmez, yalnızca bellekte): ')
        try:
            service.set_key(args.agent_key_prompt, key, remember=False)
        except AgentError as exc:
            raise ValueError(exc.message) from None
    return service


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
    if hub.bridge is not None:
        st = hub.bridge.status()
        print(f'  ESP32 köprüsü : {st["serial"]} ↔ tcp://{st["listen"]} (Docker: host.docker.internal:'
              f'{hub.bridge.listen_port})')
    if hub.agent is not None:
        if hub.agent.enabled:
            cfg = hub.agent.config
            print(f'  AI ajanı      : {cfg.provider}' + (' (KURU ÇALIŞTIRMA)' if cfg.dry_run else '')
                  + f' — tur başına en çok {cfg.max_motions} hareket, hareket onayı Hub arayüzünde')
        else:
            print(f'  AI ajanı      : KAPALI — {hub.agent.unavailable}')
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
    from control_hub.desktop.winconsole import ensure_utf8_output
    ensure_utf8_output()
    args = build_parser().parse_args(argv)
    if args.firewall_help:
        print_firewall_help(args.port)
        return 0
    if args.list_serial_ports:
        from control_hub.hw import list_serial_ports
        ports = list_serial_ports()
        print('\n'.join(ports) if ports else 'Seri port bulunamadı.')
        return 0
    if args.bridge_only:
        return run_bridge_only(args)
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
    if hub.bridge is not None:
        try:
            hub.bridge.start()
        except Exception as exc:                               # noqa: BLE001
            print(f'Köprü başlatılamadı: {exc}', file=sys.stderr)
            hub.close()
            return 2
    _print_banner(hub, args.host, args.port, show_qr=not args.no_qr)
    try:
        uvicorn.run(create_app(hub, extra_allowed_hosts=args.allow_host), host=args.host,
                    port=args.port, log_level='warning')
    finally:
        hub.close()
    return 0
