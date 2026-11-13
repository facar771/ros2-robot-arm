"""Tezgâh sayfasını DONANIMSIZ görmek için: Hub (sahte robot, PIN kipi) + bellek-içi tezgâh simülatörü.
GERÇEK Uno/motor yok; sayfa ve kapılar denenir.
PowerShell'de (apps\\control_hub içinde):  python -m tools.bench_demo
Tarayıcı: http://localhost:8766   PIN: 123456"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT.parents[1] / 'src' / 'robot_hw_bridge')]

import uvicorn  # noqa: E402
from robot_hw_bridge import HwLink, LoopbackLink, SimRunner  # noqa: E402
from robot_hw_bridge.sim_bench import BenchSim  # noqa: E402

from control_hub.cli import build_hub, build_parser  # noqa: E402
from control_hub.hw import BenchService  # noqa: E402
from control_hub.server import create_app  # noqa: E402

PORT = 8766


def main() -> None:
    lb = LoopbackLink()
    SimRunner(BenchSim(), lb.device).start()
    argv = ['--fake', '--no-agent', '--pin', '123456', '--port', str(PORT)]
    hub = build_hub(build_parser().parse_args(argv))
    hub.attach_bench(BenchService(
        enabled=True, serial_url='SIM', link_factory=lambda: HwLink(lb.host, heartbeat_s=0.02,
                                                                    link_timeout_s=0.3, ack_timeout_s=0.1)))
    print(f'Tezgâh demosu (SİMÜLATÖR): http://localhost:{PORT}   PIN 123456   (Ctrl-C ile çık)')
    uvicorn.run(create_app(hub), host='127.0.0.1', port=PORT, log_level='warning')


if __name__ == '__main__':
    main()
