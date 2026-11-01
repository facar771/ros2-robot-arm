"""Emergency stop and robot state for the Robot API v1 (ROS-free).

``EStop`` is a latch: once tripped, nothing new may move the robot until ``Supervisor.reset``.
``GuardedMotion`` wraps the motion ports so a tripped latch refuses every new goal (a running
scenario therefore ends at its next step instead of carrying on). ``Supervisor.stop`` trips the
latch, asks the adapter to cancel the running MoveIt goal and stop trajectory execution, and then
*verifies from the joint velocities* that the arm has really stopped. ``Supervisor.reset`` only
clears the latch while the arm stands still (and refuses when it cannot tell).

State shown to clients (``robot.state``), highest priority first: ``estopped`` (latched),
``busy`` (a command runs), ``error`` (the last command ended in an internal error), ``idle``.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol, Sequence

from robot_core.models import JointTarget, MotionResult, PoseTarget
from robot_core.ports import EventSink, NullEventSink

log = logging.getLogger('robot_core.supervisor')

API_VERSION = 1
MOVING_SPEED = 0.05        # rad/s: above this a joint counts as moving (the idle arm sways ~0.03)
QUIET_S = 0.25             # the arm must stay below MOVING_SPEED this long to count as stopped
STOP_TIMEOUT_S = 5.0       # give up verifying the stop after this long (reported, not hidden)
RESEND_S = 0.5             # the stop request is repeated this often until the arm is quiet
RESET_WAIT_S = 3.0         # reset waits this long for a cancelled command to unwind
VELOCITY_MAX_AGE_S = 1.0   # older joint velocities are treated as "unknown"
MODES = ('mock', 'gazebo', 'real')
ESTOP_MESSAGE = 'acil dur etkin: hareket reddedildi (önce POST /api/reset)'


class StopPort(Protocol):
    def stop(self) -> None:
        """Cancel the running goal and stop trajectory execution. Must be safe to call from any
        thread and repeatedly."""


class VelocityPort(Protocol):
    def joint_velocities(self) -> Optional[Dict[str, float]]:
        """Latest joint velocities (rad/s) by name; None when unknown or older than ~1 s."""


class EStopped(RuntimeError):
    """The emergency stop is latched; the request was refused."""


class EStop:
    """Thread-safe latch."""

    def __init__(self, wall: Callable[[], float] = time.time):
        self._lock = threading.Lock()
        self._wall = wall
        self._tripped = False
        self._reason = ''
        self._since: Optional[float] = None

    def trip(self, reason: str = '') -> bool:
        """Latch. True when this call tripped it, False when it already was."""
        with self._lock:
            if self._tripped:
                return False
            self._tripped, self._reason, self._since = True, reason, self._wall()
            return True

    def reset(self) -> bool:
        with self._lock:
            was = self._tripped
            self._tripped, self._reason, self._since = False, '', None
            return was

    @property
    def is_set(self) -> bool:
        with self._lock:
            return self._tripped

    def info(self) -> Dict[str, Any]:
        with self._lock:
            return {'tripped': self._tripped, 'reason': self._reason or None,
                    'since': None if self._since is None else round(self._since, 3)}


class GuardedMotion:
    """MotionPort + PosePort that refuse new goals while the e-stop is latched."""

    def __init__(self, motion: Any, estop: EStop):
        self._motion, self._estop = motion, estop

    def move_to(self, target: JointTarget) -> MotionResult:
        if self._estop.is_set:
            return MotionResult(False, message=ESTOP_MESSAGE)
        return self._motion.move_to(target)

    def move_to_pose(self, target: PoseTarget) -> MotionResult:
        if self._estop.is_set:
            return MotionResult(False, message=ESTOP_MESSAGE)
        return self._motion.move_to_pose(target)


def max_speed(velocities: Optional[Dict[str, float]], names: Sequence[str]) -> Optional[float]:
    """Largest |velocity| over ``names``; None when any of them is unknown."""
    if not velocities or any(n not in velocities for n in names):
        return None
    return max(abs(float(velocities[n])) for n in names)


@dataclass(frozen=True)
class StopResult:
    latched: bool
    stopped: bool                      # verified from joint velocities
    stop_time_s: float
    max_joint_speed: Optional[float]   # rad/s at the end of the wait (None: unknown)
    was_running: bool
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {'latched': self.latched, 'stopped': self.stopped,
                'stop_time_s': self.stop_time_s, 'max_joint_speed': self.max_joint_speed,
                'was_running': self.was_running, 'message': self.message}


@dataclass(frozen=True)
class ResetResult:
    ok: bool
    changed: bool
    message: str


class Supervisor:
    """Owns the e-stop semantics and the ``robot`` document of ``/api/state``.

    ``runner`` is a :class:`robot_core.webcmd.CommandRunner` (or None in the read-only view);
    it is only used through ``status()``, ``clear_error()`` and ``error_latched``.
    """

    def __init__(self, estop: EStop, velocities: VelocityPort, joint_names: Sequence[str],
                 stop_port: Optional[StopPort] = None, runner: Any = None, mode: str = 'gazebo',
                 events: Optional[EventSink] = None, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, moving_speed: float = MOVING_SPEED,
                 quiet_s: float = QUIET_S, stop_timeout_s: float = STOP_TIMEOUT_S):
        if mode not in MODES:
            raise ValueError(f'mode {mode!r} geçersiz; geçerli: {", ".join(MODES)}')
        self.estop = estop
        self.mode = mode
        self._velocities, self._names = velocities, tuple(joint_names)
        self._stop_port, self._runner = stop_port, runner
        self._events = events or NullEventSink()
        self._clock, self._sleep = clock, sleep
        self._moving_speed, self._quiet_s = moving_speed, quiet_s
        self._stop_timeout_s = stop_timeout_s

    # ---- state ------------------------------------------------------------------------------
    def _speed(self) -> Optional[float]:
        return max_speed(self._velocities.joint_velocities(), self._names)

    def moving(self) -> Optional[bool]:
        speed = self._speed()
        return None if speed is None else speed > self._moving_speed

    def state(self) -> str:
        status = self._runner.status() if self._runner is not None else {}
        if self.estop.is_set:
            return 'estopped'
        if status.get('running'):
            return 'busy'
        if self._runner is not None and self._runner.error_latched:
            return 'error'
        return 'idle'

    def document(self) -> Dict[str, Any]:
        """The ``robot`` object of ``/api/state`` (always the same keys)."""
        status = self._runner.status() if self._runner is not None else {}
        return {'api_version': API_VERSION, 'mode': self.mode, 'state': self.state(),
                'current_job': status.get('job'), 'last_error': status.get('last_error'),
                'moving': self.moving(), 'estop': self.estop.info()}

    # ---- stop / reset -----------------------------------------------------------------------
    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        try:
            self._events.emit(kind, payload)
        except Exception:    # noqa: BLE001 - events must never break a stop
            log.exception('olay yazılamadı: %s', kind)

    def _send_stop(self) -> Optional[str]:
        if self._stop_port is None:
            return 'durdurma bağlantısı yok'
        try:
            self._stop_port.stop()
        except Exception as exc:    # noqa: BLE001 - the latch is already set; report, go on
            log.exception('stop isteği gönderilemedi')
            return f'stop isteği gönderilemedi ({type(exc).__name__})'
        return None

    def stop(self, reason: str = 'api') -> StopResult:
        """Latch the e-stop, cancel the running goal, and wait until the joints are quiet."""
        started = self._clock()
        was_running = bool(self._runner is not None and self._runner.status().get('running'))
        self.estop.trip(reason)                       # first: nothing new may start from here on
        problem = self._send_stop()
        last_sent = self._clock()
        quiet_since: Optional[float] = None
        speed: Optional[float] = None
        stopped = False
        while True:
            now = self._clock()
            speed = self._speed()
            if speed is not None and speed <= self._moving_speed:
                quiet_since = quiet_since if quiet_since is not None else now
                if now - quiet_since >= self._quiet_s:
                    stopped = True
                    break
            else:
                quiet_since = None
                if now - last_sent >= RESEND_S:       # a lost request must not leave it moving
                    problem = self._send_stop() or problem
                    last_sent = now
            if now - started >= self._stop_timeout_s:
                break
            self._sleep(0.02)
        elapsed = round(self._clock() - started, 3)
        if stopped:
            message = 'robot durdu (eklem hızları doğrulandı); komutlar kilitli, devam: /api/reset'
        elif speed is None:
            message = 'acil dur kilitlendi ama durma doğrulanamadı: eklem hızı okunamıyor'
        else:
            message = (f'acil dur kilitlendi ama robot {self._stop_timeout_s:g} s içinde durmadı '
                       f'(en yüksek eklem hızı {speed:.3f} rad/s)')
        if problem:
            message += f' [{problem}]'
        result = StopResult(True, stopped, elapsed, None if speed is None else round(speed, 4),
                            was_running, message)
        self._emit('estop', {'reason': reason, **result.as_dict()})
        return result

    def reset(self) -> ResetResult:
        """Clear the latch (and a latched error) when the arm stands still."""
        runner = self._runner
        if runner is not None and runner.status().get('running'):
            # only a cancelled command is given time to unwind; a healthy one is just refused
            deadline = self._clock() + (RESET_WAIT_S if self.estop.is_set else 0.0)
            while runner.status().get('running') and self._clock() < deadline:
                self._sleep(0.05)
            if runner.status().get('running'):
                return ResetResult(False, False, 'bir komut hâlâ çalışıyor (sonlanması bekleniyor)')
        speed = self._speed()
        if speed is None:
            return ResetResult(False, False, 'eklem hızı okunamıyor: durma doğrulanamadığı için '
                                             'sıfırlanmadı')
        if speed > self._moving_speed:
            return ResetResult(False, False, f'robot hâlâ hareket ediyor ({speed:.3f} rad/s)')
        had_error = bool(runner is not None and runner.error_latched)
        if runner is not None:
            runner.clear_error()
        changed = self.estop.reset() or had_error
        if changed:
            self._emit('estop_reset', {'had_error': had_error})
        return ResetResult(True, changed,
                           'sıfırlandı; komutlar açık' if changed else 'zaten hazır (idle)')
