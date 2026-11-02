"""Asynchronous jobs on top of the white-listed commands (ROS-free).

``JobManager.submit`` starts a command in a worker thread and returns at once; the client polls
the job document. One job at a time (a second ``submit`` is :class:`JobBusy`). A job ends in
exactly one of:

    done       the command succeeded
    failed     the robot could not do it (message in ``error``), an internal error, or the
               per-job timeout (``timed_out: true``)
    cancelled  ``cancel`` (``cancelled_by: 'user'``) or an emergency stop (``'estop'``)

Cancelling or timing out sets the shared :class:`~robot_core.supervisor.CancelFlag`: the motion
adapter cancels the running MoveIt goal and the guarded motion refuses new ones, so a scenario ends
at its next step. Unlike the e-stop nothing stays locked; the flag is cleared when the job ended.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from robot_core.supervisor import ESTOP_MESSAGE, CancelFlag, EStop, EStopped
from robot_core.webcmd import Busy, Command, CommandOutcome, CommandRunner

log = logging.getLogger('robot_core.jobs')

DEFAULT_TIMEOUTS = {'home': 90.0, 'objects': 30.0, 'above': 120.0, 'demo_scenario': 300.0}
TIMEOUT_MIN, TIMEOUT_MAX = 1.0, 600.0
CANCEL_WAIT_S = 3.0
HISTORY = 50
_STEP = re.compile(r'^\[(\d+)/(\d+)\]\s*(.*)$')


class JobBusy(Busy):
    """A job is already running (``job_id``)."""

    def __init__(self, message: str, job_id: str):
        super().__init__(message)
        self.job_id = job_id


class JobNotFound(KeyError):
    pass


class BadTimeout(ValueError):
    pass


def check_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BadTimeout(f'timeout_s sayı olmalı, verilen: {value!r}')
    if not TIMEOUT_MIN <= value <= TIMEOUT_MAX:
        raise BadTimeout(f'timeout_s {TIMEOUT_MIN:g} ile {TIMEOUT_MAX:g} s arasında olmalı, '
                         f'verilen: {value:g}')
    return float(value)


@dataclass
class Job:
    id: str
    command: Command
    timeout_s: float
    created_at: float
    started_mono: float
    state: str = 'running'
    cancel_requested: bool = False
    cancelled_by: Optional[str] = None
    timed_out: bool = False
    finished_at: Optional[float] = None
    finished_mono: Optional[float] = None
    outcome: Optional[CommandOutcome] = None
    error: Optional[str] = None
    log: List[str] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)


def parse_progress(lines: List[str]) -> Dict[str, Any]:
    """step / steps / say from the narration (the scenario prints ``[k/n] text``)."""
    step = steps = None
    say = None
    for line in lines:
        match = _STEP.match(line)
        if match:
            step, steps, say = int(match.group(1)), int(match.group(2)), match.group(3)
    if say is None:
        say = next((ln for ln in reversed(lines) if ln.strip() and not ln.startswith('> ')), None)
    return {'step': step, 'steps': steps, 'say': say}


class JobManager:
    def __init__(self, runner: CommandRunner, cancel: Optional[CancelFlag] = None,
                 estop: Optional[EStop] = None, *, history: int = HISTORY,
                 clock=time.monotonic, wall=time.time):
        self._runner = runner
        self.cancel_flag = cancel or CancelFlag()
        self._estop = estop
        self._history = history
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self._jobs: 'OrderedDict[str, Job]' = OrderedDict()
        self._active: Optional[Job] = None

    # ---- submit / cancel ----------------------------------------------------------------------
    def submit(self, command: Command, timeout_s: Optional[float] = None) -> Job:
        timeout = timeout_s if timeout_s is not None else DEFAULT_TIMEOUTS.get(command.verb, 120.0)
        with self._lock:
            if self._estop is not None and self._estop.is_set:
                raise EStopped(ESTOP_MESSAGE)
            if self._active is not None:
                raise JobBusy(f'başka bir iş çalışıyor: {self._active.id} '
                              f'({self._active.command.line})', self._active.id)
            job = Job(self._runner.new_job_id(), command, timeout, self._wall(), self._clock())
            self._jobs[job.id] = job
            self._active = job
            self.cancel_flag.clear()
            timer = threading.Timer(timeout, self._expire, (job,))
            timer.daemon = True
            timer.start()
        threading.Thread(target=self._work, args=(job, timer), daemon=True,
                         name=f'job-{job.id}').start()
        return job

    def _expire(self, job: Job) -> None:
        with self._lock:
            if self._active is job and job.state == 'running':
                job.timed_out = True
                self.cancel_flag.set()

    def cancel(self, job_id: str, wait_s: float = CANCEL_WAIT_S):
        """Ask a running job to stop. Returns ``(job, was_running)``; waits up to ``wait_s`` for
        it to end. Raises :class:`JobNotFound`."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if job.state != 'running':
                return job, False
            job.cancel_requested = True
            self.cancel_flag.set()
        job.done.wait(wait_s)
        return job, True

    # ---- the worker ---------------------------------------------------------------------------
    def _work(self, job: Job, timer: threading.Timer) -> None:
        outcome: Optional[CommandOutcome] = None
        problem: Optional[str] = None
        try:
            outcome = self._runner.run(job.command, job_id=job.id)
        except EStopped as exc:
            problem = str(exc)
        except Busy as exc:
            problem = str(exc)
        except Exception as exc:    # noqa: BLE001 - never let a worker die silently
            log.exception('iş işlenemedi: %s', job.command.line)
            problem = f'iç hata ({type(exc).__name__})'
        finally:
            timer.cancel()
        lines = list(self._runner.status()['log']) if outcome is not None else []
        with self._lock:
            job.outcome, job.log = outcome, lines
            if outcome is not None and outcome.ok:
                job.state = 'done'
            elif job.timed_out:
                job.state = 'failed'
                job.error = f'zaman aşımı ({job.timeout_s:g} s); iş iptal edildi'
            elif self._estop is not None and self._estop.is_set:
                job.state, job.cancelled_by = 'cancelled', 'estop'
                job.error = ESTOP_MESSAGE
            elif job.cancel_requested:
                job.state, job.cancelled_by = 'cancelled', 'user'
                job.error = 'iş kullanıcı tarafından iptal edildi'
            else:
                job.state = 'failed'
                job.error = problem or (outcome.message if outcome else 'bilinmeyen hata')
            job.finished_at, job.finished_mono = self._wall(), self._clock()
            self._active = None
            self.cancel_flag.clear()
            self._trim()
            job.done.set()

    def _trim(self) -> None:
        while len(self._jobs) > self._history:
            oldest = next(iter(self._jobs))
            if self._jobs[oldest] is self._active:
                break
            del self._jobs[oldest]

    # ---- reading ------------------------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
        return None if job is None else self._document(job)

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())[-limit:]
        return [self._document(j) for j in reversed(jobs)]

    def _document(self, job: Job) -> Dict[str, Any]:
        status = self._runner.status()
        running = job.state == 'running'
        lines = list(status['log']) if running and (status['job'] or {}).get('id') == job.id \
            else list(job.log)
        end = job.finished_mono if job.finished_mono is not None else self._clock()
        outcome = job.outcome
        return {
            'id': job.id, 'command': job.command.line, 'state': job.state,
            'created_at': round(job.created_at, 3),
            'finished_at': None if job.finished_at is None else round(job.finished_at, 3),
            'elapsed_s': round(end - job.started_mono, 2), 'timeout_s': job.timeout_s,
            'cancel_requested': job.cancel_requested, 'cancelled_by': job.cancelled_by,
            'timed_out': job.timed_out,
            'progress': {**parse_progress(lines), 'log': lines},
            'result': None if outcome is None else {
                'ok': outcome.ok, 'message': outcome.message, 'data': outcome.data,
                'duration_s': outcome.duration_s},
            'error': job.error,
        }

    # ---- the old blocking door ----------------------------------------------------------------
    def run_sync(self, command: Command, timeout_s: Optional[float] = None) -> CommandOutcome:
        """``POST /api/command`` compatibility: submit, wait for the end, return the outcome."""
        job = self.submit(command, timeout_s)
        if not job.done.wait(job.timeout_s + 60.0):
            return CommandOutcome(False, 'iş sonlanmadı (zaman aşımı korumasına rağmen)', {},
                                  round(self._clock() - job.started_mono, 2), True)
        if job.outcome is not None:
            return job.outcome
        return CommandOutcome(False, job.error or 'iş çalıştırılamadı', {}, 0.0, False)
