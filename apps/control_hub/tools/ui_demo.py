"""Yeni arayüz iskeletini robot YOKKEN CAD mesh'li görmek için: SİM + karma_robot görünüm modeli
(`src/robot_description/profile/generated/karma_robot/view_model.json`) + profil mesh'leri (depodan).
Robot SİM: kinematik gerçek (robot_core), robot fiziksel değil.
PowerShell'de (apps\\control_hub içinde):  python -m tools.ui_demo
Tarayıcı: http://localhost:8767   (PIN yok, açık erişim)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

import uvicorn  # noqa: E402

from control_hub.cli import build_hub, build_parser  # noqa: E402
from control_hub.server import create_app  # noqa: E402

PORT = 8767


def main() -> None:
    """S1: sahte robot yerine SİM — gerçek kinematik, gerçek eklem sınırları, çalışan hareketler."""
    argv = ['--sim', '--no-agent', '--access-mode', 'open', '--port', str(PORT)]
    hub = build_hub(build_parser().parse_args(argv))
    print(f'Arayüz demosu (SİM, robot_core kinematiği): http://localhost:{PORT}   (Ctrl-C ile çık)')
    uvicorn.run(create_app(hub), host='127.0.0.1', port=PORT, log_level='warning')


if __name__ == '__main__':
    main()
