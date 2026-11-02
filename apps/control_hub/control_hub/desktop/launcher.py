"""Giriş noktası: argümansız (çift tık) → pencere; argümanlı → komut satırı (``control_hub.cli``).

    ControlHub.exe                      pencere
    ControlHub.exe --robot-url ...      konsol (Hub'ı doğrudan başlatır)
    ControlHub.exe --check-config       yapılandırmayı doğrula ve çık
    ControlHub.exe --firewall-help      güvenlik duvarı komutlarını yazdır (hiçbir şey eklemez)
    ControlHub.exe --gui-selftest       pencereyi kur, sahte robotla Hub'ı başlat, /health'e bak, çık (CI)
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import traceback
import urllib.request
from typing import List, Optional

from control_hub import cli
from control_hub.desktop.winconsole import hide_console_if_owned


def _setup_file_log() -> None:
    try:
        from control_hub.paths import user_dir
        d = user_dir()
        d.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(d / 'hub.log'), level=logging.INFO, encoding='utf-8',
                            format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    except OSError:
        logging.basicConfig(level=logging.INFO)


def _free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def gui_selftest() -> int:
    """Tk gömülü mü, pencere kuruluyor mu, servis başlayıp /health yanıtlıyor mu: tek komutla."""
    try:
        import tkinter as tk

        from control_hub.desktop.gui import HubWindow
        from control_hub.desktop.settings import Settings
        root = tk.Tk()
        win = HubWindow(root, settings=Settings(fake=True, port=_free_port(), pin='123456'),
                        persist=False, rule_exists=lambda: None)
        win.start()
        assert win.service is not None and win.service.running, 'servis çalışmıyor'
        url = f'http://127.0.0.1:{win.service.port}/health'
        with urllib.request.urlopen(url, timeout=5) as r:                       # noqa: S310
            doc = json.loads(r.read())
        assert doc.get('ok') is True, f'beklenmeyen /health: {doc}'
        root.update()
        qr = win._photo is not None
        win.close()
        print(f'GUI-SELFTEST OK (health={doc}, qr={qr})')
        return 0
    except Exception:                                                           # noqa: BLE001
        traceback.print_exc()
        print('GUI-SELFTEST FAILED')
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--gui-selftest':
        return gui_selftest()
    if not argv or argv == ['--gui']:
        hide_console_if_owned()          # çift tıkta siyah pencere kalmasın; PowerShell'e dokunma
        _setup_file_log()
        try:
            from control_hub.desktop.gui import run_gui
        except ImportError as exc:       # Tk yok (ör. bazı Linux kurulumları)
            print(f'Pencere açılamadı ({exc}); komut satırı kipine geçiliyor.\n'
                  'Ayarları bayraklarla ver: --robot-url, --pin, --port (bkz. --help)')
            return cli.main([])
        return run_gui()
    return cli.main([a for a in argv if a != '--gui'])
