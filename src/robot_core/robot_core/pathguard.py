"""Safe-box check ALONG the executed path: plan -> verify -> execute (F4b). ROS-free.

``PathGuard`` is the ONE place where a motion goal becomes a motion. ``goto_waypoint`` (joint goal),
``go_to_position`` and ``go_above`` (pose goals) and the named poses (``home``, ``ready``) all go
through it, so the rule is written once:

1. plan WITHOUT executing (``PlanningPort.plan_*``);
2. ``pathcheck.check_path`` walks the planned joint path and tests the TCP against the safe box;
3. only a path that passes is executed. A rejected plan is replanned at most ``max_plans`` times
   in total (the planner is randomised); then the error names the violation point and its
   coordinates. The box is never widened and a path is never "repaired".

Park transit (the ONE exemption, ``safe_zone.park.yaml``): only for a NAMED pose request
(``move_to_named_pose``), only ready <-> ``home``, never MoveIt: a straight joint line at
``min(max_velocity_fraction, velocity_scale)`` of the maximum speed (the global velocity cap
``max_velocity_scale`` and the requested ``velocity_scale`` apply to the park transit too, the
transit is never faster than any other motion), checked by ``check_park_move`` against the park
envelope and against the planning scene (collision), starting within ``ready_tolerance`` of
``ready``. A waypoint called "home" or a pose goal never reaches this code path: they use
``move_to`` / ``move_to_pose``, which only know the inner box.

``return_home`` ("Eve dön") = (when not at ready) a normal, box-checked move to ``ready`` + the
ready -> home park transit.

The e-stop and the job cancel flag are checked before planning and again right before execution;
the executor itself keeps cancelling a running goal while the e-stop is latched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from robot_core.models import JointTarget, MotionResult, PlanResult, PoseTarget
from robot_core.pathcheck import PathCheck, PathError, check_park_move, check_path
from robot_core.ports import EventSink, NullEventSink, PlanningPort, StatePort
from robot_core.profile import Profile
from robot_core.safezone import ParkConfig, SafeZone

DEFAULT_MAX_PLANS = 3             # in total, the first plan included
START_TOLERANCE = 0.05            # rad: plan start vs measured state (a stale plan is not run)
VALIDITY_STEP = 0.05              # rad: largest joint step between collision-checked states
TRAJECTORY_DT = 0.05              # s between points of the park trajectory
CODE_PATH = 'PATH_ZONE'           # MotionResult.code_name of a path-box rejection
CODE_PARK = 'PARK_REJECTED'
Q = Tuple[float, ...]


def straight_trajectory(q0: Sequence[float], q1: Sequence[float], vmax: Sequence[float],
                        amax: Sequence[float], fraction: float, dt: float = TRAJECTORY_DT):
    """Time-parameterised STRAIGHT joint line q0 -> q1: ``q(t) = q0 + (q1 - q0) s(t)``,
    ``s`` a quintic smoothstep (zero speed and acceleration at both ends).

    Every joint follows the same scalar profile, so positions and velocities are all parallel to
    ``q1 - q0``: any interpolation between the points (the controller's splines included) stays
    on the line, which is what ``check_park_move`` verified. The duration is the shortest one
    where each joint stays within ``fraction`` of its maximum speed AND acceleration
    (peak ``s' = 1.875 / T``, peak ``s'' = 5.7735 / T^2``). Returns (times, positions, velocities).
    """
    a, b = np.asarray(q0, float), np.asarray(q1, float)
    if not (0.0 < fraction <= 1.0) or not math.isfinite(fraction):
        raise ValueError(f'hız oranı (0, 1] olmalı: {fraction!r}')
    delta = b - a
    v = np.asarray(vmax, float) * fraction
    acc = np.asarray(amax, float) * fraction
    if np.any(v <= 0.0) or np.any(acc <= 0.0):
        raise ValueError('eklem hız/ivme sınırları pozitif olmalı')
    t_min = 0.5
    duration = max(t_min, float(np.max(1.875 * np.abs(delta) / v)),
                   float(np.max(np.sqrt(5.7735 * np.abs(delta) / acc))))
    n = max(2, math.ceil(duration / dt))
    times, positions, velocities = [], [], []
    for i in range(n + 1):
        tau = i / n
        s = tau ** 3 * (10 - 15 * tau + 6 * tau ** 2)
        ds = 30 * tau ** 2 * (1 - tau) ** 2 / duration
        times.append(tau * duration)
        positions.append(tuple(float(x) for x in (b if i == n else a + delta * s)))
        velocities.append(tuple(float(x) for x in (np.zeros_like(delta) if i in (0, n)
                                                    else delta * ds)))
    return times, positions, velocities


@dataclass(frozen=True)
class GuardConfig:
    max_plans: int = DEFAULT_MAX_PLANS
    max_step: float = 0.005           # check_path sampling bound (m)


class PathGuard:
    def __init__(self, planner: PlanningPort, state: StatePort, joint_names: Sequence[str],
                 profile: Profile, zone: SafeZone, park: Optional[ParkConfig] = None,
                 poses: Optional[Dict[str, Q]] = None, events: Optional[EventSink] = None,
                 abort: Optional[Callable[[], Optional[MotionResult]]] = None,
                 config: Optional[GuardConfig] = None, *,
                 velocity_scale: Callable[[], float]):
        """``abort``: returns a refusal (e-stop latched, job cancelling) or None; asked before
        planning and again before every execution. ``park`` None: no park moves at all.
        ``velocity_scale``: returns the EFFECTIVE global scale (requested, already limited to
        ``max_velocity_scale``), read at every park move; required on purpose, so a caller cannot
        forget the global cap. The park transit runs at ``min(park fraction, that scale)``."""
        if config is not None and config.max_plans < 1:
            raise ValueError('max_plans en az 1 olmalı')
        self._planner, self._state = planner, state
        self._names = tuple(joint_names)
        self._profile, self._zone, self._park = profile, zone, park
        self._events = events or NullEventSink()
        self._abort = abort or (lambda: None)
        self._cfg = config or GuardConfig()
        self._velocity_scale = velocity_scale
        self._poses = {k.lower(): tuple(v) for k, v in (poses or {}).items()}
        self._vmax = [j.velocity for j in profile.joints]
        self._amax = [j.acceleration for j in profile.joints]

    def park_fraction(self, scale: float) -> float:
        """Speed fraction of the park transit: the park file's cap, never above the global
        (effective) velocity scale."""
        assert self._park is not None
        return min(self._park.max_velocity_fraction, min(float(scale), 1.0))

    def set_abort(self, abort: Callable[[], Optional[MotionResult]]) -> None:
        self._abort = abort

    # ---- goals that obey the inner box ------------------------------------------------------
    def move_to(self, target: JointTarget) -> MotionResult:
        """Joint goal (``goto_waypoint``, named poses other than the park moves)."""
        return self._guarded(lambda: self._planner.plan_joint(target),
                             target.label or 'eklem hedefi')

    def move_to_pose(self, target: PoseTarget) -> MotionResult:
        """Cartesian goal (``go_to_position``, ``go_above``)."""
        return self._guarded(lambda: self._planner.plan_pose(target),
                             target.label or 'konum hedefi')

    def _guarded(self, plan: Callable[[], PlanResult], what: str) -> MotionResult:
        refusal = self._abort()
        if refusal:
            return refusal
        reasons: List[str] = []
        for attempt in range(1, self._cfg.max_plans + 1):
            result = plan()
            if not result.ok:                       # MoveIt itself said no: nothing to replan
                return MotionResult(False, None, result.code_name, result.message)
            problem = self._reject_reason(result)
            if problem is None:
                refusal = self._abort()
                if refusal:
                    return refusal
                return self._planner.execute_plan(result)
            reasons.append(problem)
            self._events.emit('path_rejected', {'goal': what, 'attempt': attempt,
                                                'reason': problem})
        # every attempt is named (a flaky Gazebo run must show all of them, not only the last one)
        history = ' | '.join(f'plan {i}: {r}' for i, r in enumerate(reasons, 1))
        return MotionResult(False, None, CODE_PATH, (
            f'{self._cfg.max_plans} planın hepsi güvenli kutudan çıkıyor, hareket başlamadı '
            f'(kutu genişletilmedi). Son plan: {reasons[-1]}. Tüm planlar: {history}'))

    def _reject_reason(self, plan: PlanResult) -> Optional[str]:
        """None when the planned path is acceptable, otherwise why not."""
        path = [tuple(p) for p in plan.path]
        if len(path) < 2:
            return 'plan en az iki nokta içermeli'
        now = self._current()
        if now is None:
            return 'eklem durumu okunamadı; yol doğrulanamaz'
        if max(abs(a - b) for a, b in zip(path[0], now)) > START_TOLERANCE:
            return (f'planın başlangıcı robotun şu anki durumundan farklı '
                    f'(> {START_TOLERANCE} rad); plan eski')
        try:
            checked: PathCheck = check_path(path, self._profile, self._zone,
                                            max_step=self._cfg.max_step)
        except PathError as exc:
            return f'yol doğrulanamadı: {exc}'
        return None if checked.ok else checked.message()

    # ---- named poses and the park exemption --------------------------------------------------
    def move_to_named_pose(self, name: str, values: Optional[Sequence[float]] = None
                           ) -> MotionResult:
        """``go_to_named_pose``: the ONLY entry that can use the park transit.

        ``home``: park move, accepted only when the arm stands at ``ready`` (otherwise rejected,
        "Eve dön" is ``return_home``). ``ready`` from ``home`` is the park move back. Every other
        name (or ``ready`` from anywhere else) is a normal, box-checked joint goal."""
        key = str(name).lower()
        goal = tuple(values) if values is not None else self._poses.get(key)
        if goal is None:
            return MotionResult(False, None, 'UNKNOWN_POSE', f'bilinmeyen poz {name!r}')
        if self._park is not None and key in self._park.poses:
            if self._near(self._park.ready_joints):
                return self._park_move(key, to_home=True)
            return MotionResult(False, None, CODE_PARK, (
                f"{key!r} pozuna doğrudan gidilemez: robot 'ready' konumunda değil "
                f"(en büyük eklem farkı {self._gap(self._park.ready_joints):.3f} rad, izin "
                f'{self._park.ready_tolerance} rad). Önce ready\'ye gidin ya da "Eve dön" '
                'kullanın. Robot hareket etmedi.'))
        if self._park is not None and key == 'ready':
            for parked in self._park.poses:
                if self._near(self._park.poses[parked]):
                    return self._park_move(parked, to_home=False)
        return self.move_to(JointTarget(goal, key))

    def return_home(self) -> MotionResult:
        """"Eve dön": (not at ready) normal box-checked move to ready, then ready -> home."""
        if self._park is None or 'home' not in self._park.poses:
            return MotionResult(False, None, CODE_PARK,
                                'park dosyası yüklenmedi: eve dönülemez. Robot hareket etmedi.')
        if self._near(self._park.poses['home']):
            return MotionResult(True, None, 'SUCCESS', 'robot zaten home konumunda')
        if not self._near(self._park.ready_joints):
            first = self.move_to(JointTarget(tuple(self._park.ready_joints), 'ready'))
            if not first.ok:
                return MotionResult(False, first.code, first.code_name, (
                    "ready'ye gidilemedi, home'a geçilmedi: " + (first.message or
                                                                 first.code_name)))
        return self._park_move('home', to_home=True)

    def _park_move(self, pose: str, to_home: bool) -> MotionResult:
        assert self._park is not None
        refusal = self._abort()
        if refusal:
            return refusal
        now = self._current()
        if now is None:
            return MotionResult(False, None, CODE_PARK,
                                'eklem durumu okunamadı; park geçişi doğrulanamaz.')
        parked = tuple(self._park.poses[pose])
        goal = parked if to_home else tuple(self._park.ready_joints)
        verdict = check_park_move([now, goal], self._profile, self._park, pose,
                                  max_step=self._cfg.max_step)
        if not verdict.ok:
            self._events.emit('path_rejected', {'goal': f'park {pose}', 'attempt': 1,
                                                'reason': verdict.message()})
            return MotionResult(False, None, CODE_PARK, verdict.message() +
                                '. Robot hareket etmedi.')
        n = max(2, math.ceil(max(abs(a - b) for a, b in zip(now, goal)) / VALIDITY_STEP) + 1)
        line = [tuple(float(x) for x in np.asarray(now) + (np.asarray(goal) - np.asarray(now)) *
                      (i / (n - 1))) for i in range(n)]
        bad = self._planner.states_valid(line)
        if bad is not None:
            return MotionResult(False, None, CODE_PARK, (
                f'park geçişi çarpışma denetiminden geçmedi: {bad}. Robot hareket etmedi.'))
        try:
            scale = float(self._velocity_scale())
        except Exception as exc:    # noqa: BLE001 - unknown scale: no park move
            return MotionResult(False, None, CODE_PARK, (
                f'global hız ölçeği okunamadı ({exc}); park geçişi başlamadı.'))
        if not (math.isfinite(scale) and scale > 0.0):
            return MotionResult(False, None, CODE_PARK, (
                f'global hız ölçeği geçersiz ({scale!r}); park geçişi başlamadı.'))
        times, pos, vel = straight_trajectory(now, goal, self._vmax, self._amax,
                                              self.park_fraction(scale))
        refusal = self._abort()
        if refusal:
            return refusal
        return self._planner.execute_timed(times, pos, vel)

    # ---- state ------------------------------------------------------------------------------
    def _current(self) -> Optional[Q]:
        pos = self._state.joint_positions()
        if not pos or any(n not in pos for n in self._names):
            return None
        return tuple(float(pos[n]) for n in self._names)

    def _gap(self, ref: Sequence[float]) -> float:
        now = self._current()
        return math.inf if now is None else max(abs(a - b) for a, b in zip(now, ref))

    def _near(self, ref: Sequence[float]) -> bool:
        assert self._park is not None
        return self._gap(ref) <= self._park.ready_tolerance
