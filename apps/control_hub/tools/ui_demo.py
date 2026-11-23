"""Yeni arayüz iskeletini robot YOKKEN CAD mesh'li görmek için: sahte robot + karma_robot görünüm modeli
(`src/robot_description/profile/generated/karma_robot/view_model.json`) + profil mesh'leri (depodan).
Çizilen duruş sahte (gerçek kinematik değil).
PowerShell'de (apps\\control_hub içinde):  python -m tools.ui_demo
Tarayıcı: http://localhost:8767   (PIN yok, açık erişim)"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

import uvicorn  # noqa: E402

from control_hub.cli import build_hub, build_parser  # noqa: E402
from control_hub.server import create_app  # noqa: E402

PORT = 8767
GENERATED = ROOT.parents[1] / 'src' / 'robot_description' / 'profile' / 'generated'
MODEL = GENERATED / 'karma_robot' / 'view_model.json'


def main() -> None:
    doc = {k: v for k, v in json.loads(MODEL.read_text(encoding='utf-8')).items() if not k.startswith('_')}
    argv = ['--fake', '--no-agent', '--access-mode', 'open', '--port', str(PORT)]
    hub = build_hub(build_parser().parse_args(argv))
    hub.backend.model = lambda: (200, copy.deepcopy(doc))
    print(f'Arayüz demosu (SAHTE robot, karma_robot + mesh): http://localhost:{PORT}   (Ctrl-C ile çık)')
    uvicorn.run(create_app(hub), host='127.0.0.1', port=PORT, log_level='warning')


if __name__ == '__main__':
    main()
