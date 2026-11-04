"""``RobotPort`` (Robot API v1'in küçük bir alt kümesi) sahte robot arka ucu üzerinde: ``--fake`` ile
ajanı ROS/Docker olmadan denemek ve CI testleri için. Gerçek robotta kullanılmaz (orada
``robot_agent.client.HttpRobot`` ile gerçek Robot API'ye gidilir).
"""
from __future__ import annotations

import itertools
import threading
from typing import Any, Dict, Optional, Tuple

from control_hub.hub_core import Hub

Reply = Tuple[int, Optional[Dict[str, Any]]]


class HubFakeRobotPort:
    base_url = None          # robot_agent: eklenti çalıştırma yok (zaten kapalı)

    def __init__(self, hub: Hub):
        self._hub = hub
        self._ids = itertools.count(1)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._cancel: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def _current(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return next((j for j in self._jobs.values() if j['state'] == 'running'), None)

    # ---- GET --------------------------------------------------------------------------------
    def get(self, path: str) -> Reply:
        if path == '/api/state':
            job = self._current()
            busy = job is not None or self._hub.runner.running
            state = 'estopped' if self._hub.estopped else ('busy' if busy else 'idle')
            return 200, {'robot': {
                'state': state, 'moving': busy,
                'current_job': {'id': job['id'], 'command': job['command']} if job else None,
                'estop': {'tripped': self._hub.estopped}, 'safety': {}}}
        if path.startswith('/api/jobs/'):
            with self._lock:
                job = self._jobs.get(path.rsplit('/', 1)[1])
                return (200, dict(job)) if job else (404, {'error': 'iş yok'})
        return 404, {'error': 'bilinmeyen uç'}

    # ---- POST -------------------------------------------------------------------------------
    def post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Reply:
        payload = payload or {}
        if path == '/api/stop':
            self._hub.estop(source='agent')
            report = self._hub.wait_estop_report(6.0)
            return 200, {'stopped': bool(report and report.stopped),
                         'message': report.message if report else 'doğrulanamadı',
                         **(report.details if report else {})}
        if path == '/api/jobs':
            return self._submit(str(payload.get('command', '')))
        if path.startswith('/api/jobs/') and path.endswith('/cancel'):
            jid = path.split('/')[3]
            flag = self._cancel.get(jid)
            if flag is None:
                return 404, {'error': 'iş yok'}
            flag.set()
            return 200, {'job': {'id': jid, 'state': 'cancelling'}}
        return 404, {'error': 'bilinmeyen uç'}

    def _submit(self, command: str) -> Reply:
        if self._hub.estopped:
            return 423, {'error': 'ACİL DUR kilidi açık'}
        if self._current() is not None or self._hub.runner.running:
            return 409, {'error': 'robot meşgul'}
        jid = f'j{next(self._ids)}'
        cancel = threading.Event()
        job = {'id': jid, 'command': command, 'state': 'running', 'timeout_s': 120}
        with self._lock:
            self._jobs[jid] = job
            self._cancel[jid] = cancel
        threading.Thread(target=self._run, args=(jid, command, cancel), daemon=True,
                         name='agent-fake-job').start()
        return 202, {'id': jid, 'timeout_s': 120}

    def _run(self, jid: str, command: str, cancel: threading.Event) -> None:
        res = self._hub.backend.execute(command, cancel)
        with self._lock:
            job = self._jobs[jid]
            job['result'] = {'ok': res.ok, 'message': res.message, 'data': dict(res.data)}
            if res.ok:
                job['state'] = 'done'
            elif cancel.is_set() or self._hub.estopped:
                job['state'] = 'cancelled'
                job['error'] = res.message
                if self._hub.estopped:
                    job['cancelled_by'] = 'estop'
            else:
                job['state'] = 'failed'
                job['error'] = res.message
