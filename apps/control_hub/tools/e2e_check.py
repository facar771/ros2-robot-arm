"""Hub ↔ GERÇEK robot yığını uçtan uca denemesi (Docker demo ya da herhangi bir Robot API v1).

    python -m tools.e2e_check --robot-url http://127.0.0.1:8080

Hub çekirdeğini süreç içinde kurar (HTTP sunucusu/PIN yok) ve robota gerçekten komut gönderir:
bağlantı + sürüm, `home`, `demo_tour` senaryosu, hareket ortasında İPTAL (kilitlemez), hareket
ortasında ACİL DUR (robotun durduğu doğrulanır), 423 kilidi, `reset`, yeniden `home`. Robot HAREKET EDER.
Çıkış kodu 0 = hepsi geçti.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable

from control_hub.hub_core import (
    AccessControl,
    EstopLatchedError,
    Hub,
    RobotApiBackend,
    load_scenarios,
    load_ui_config,
)

CONFIG = Path(__file__).resolve().parents[1] / 'config'
FAILED = []


def check(name: str, ok: bool, detail: str = '') -> bool:
    print(f"  [{'GEÇTİ' if ok else 'KALDI'}] {name}" + (f': {detail}' if detail else ''))
    if not ok:
        FAILED.append(name)
    return ok


def wait_for(pred: Callable[[], bool], timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--robot-url', default='http://127.0.0.1:8080')
    ap.add_argument('--timeout', type=float, default=240.0, help='tek senaryo için üst süre (sn)')
    a = ap.parse_args()
    scenarios = load_scenarios(CONFIG / 'scenarios.yaml')
    hub = Hub(RobotApiBackend(a.robot_url), scenarios, load_ui_config(CONFIG / 'ui.config.json',
                                                                     scenarios), AccessControl('open'))
    backend = hub.backend
    try:
        print(f'Robot: {a.robot_url}')
        backend.wait_first_poll(5)
        snap = backend.snapshot()
        if not check('bağlı ve API v1', snap['connected'] and snap['compatible'], str(snap['problem'])):
            return 1
        check('nesneler görünüyor', {o['label'] for o in snap['objects']} >= {'red', 'green', 'blue'})
        check('robot hazır', snap['state'] == 'idle', str(snap['state']))

        hub.run_scenario('home')
        hub.runner.wait_idle(a.timeout)
        check('home', hub.runner.status()['last']['ok'], hub.runner.status()['last']['message'])

        hub.run_scenario('demo_tour')
        hub.runner.wait_idle(a.timeout)
        last = hub.runner.status()['last']
        check('demo_tour (4 adım)', last['ok'], last['message'])

        hub.run_scenario('demo_tour')
        check('hareket başladı', wait_for(lambda: backend.snapshot()['moving'] is True, 120))
        time.sleep(0.5)
        hub.cancel_scenario()
        hub.runner.wait_idle(30)
        last = hub.runner.status()['last']
        check('iptal: senaryo iptal edildi', last['cancelled'] and not last['ok'], last['message'])
        check('iptal: robot kilitlenmedi', wait_for(lambda: backend.snapshot()['state'] == 'idle', 20)
              and not hub.estopped)

        hub.run_scenario('demo_tour')
        check('hareket başladı (2)', wait_for(lambda: backend.snapshot()['moving'] is True, 120))
        time.sleep(0.5)
        t0 = time.monotonic()
        hub.estop('e2e_check')
        report = hub.wait_estop_report(15)
        check('ACİL DUR: robotun durduğu doğrulandı', bool(report and report.stopped),
              f'{time.monotonic() - t0:.2f} sn; {report.details if report else None}')
        hub.runner.wait_idle(30)
        check('ACİL DUR: senaryo iptal', hub.runner.status()['last']['cancelled'])
        snap = backend.snapshot()
        check('ACİL DUR: robot kilitli ve duruyor', snap['state'] == 'estopped'
              and snap['moving'] is False, f"{snap['state']} moving={snap['moving']}")
        try:
            hub.run_scenario('home')
            check('kilitliyken senaryo reddedilir', False)
        except EstopLatchedError:
            check('kilitliyken senaryo reddedilir', True)
        hub.reset_estop('e2e_check')
        check('reset', not hub.estopped and backend.snapshot()['state'] == 'idle')
        hub.run_scenario('home')
        hub.runner.wait_idle(a.timeout)
        check('reset sonrası home', hub.runner.status()['last']['ok'])
    finally:
        hub.close()
    print('\nSONUÇ:', 'HEPSİ GEÇTİ' if not FAILED else f'KALDI: {FAILED}')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
