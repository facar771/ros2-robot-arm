"""Cartesian jog core: damped-least-squares engine with safe-box, singularity and joint limits.

ROS-free and port-free: the caller (J2) passes the MEASURED joint vector every tick and sends the
returned ``q_next`` through the normal path (Supervisor / emergency stop / velocity ceiling are
unchanged; this module never moves anything by itself). Design and numbers: docs/JOG.md.

Per tick (``JogEngine.step``):

1. The twist ``[vx vy vz wx wy wz]`` is given in the selected frame (World / User / Tool axes);
   linear = TCP velocity (m/s), angular = angular-velocity VECTOR about the TCP (rad/s). No Euler
   rates. Magnitudes are clipped to ``max_* x effective scale``; effective scale =
   ``min(speed_scale, SafetyConfig.max_velocity_scale)`` (the existing velocity ceiling).
2. ``sigma_min`` of the Jacobian: below ``sigma_stop`` the jog stops with a singularity message;
   between ``sigma_stop`` and ``sigma_slow`` speed ramps down smoothly and the damping
   ``lambda`` ramps up, so ``dq`` has no jump.
3. ``dq = V diag(s/(s^2+lambda^2)) U^T (v + K e)``: ``e`` is the error between an internally
   integrated Cartesian TARGET pose and the measured pose (drift does not accumulate).
4. Joint velocity clip (scales the whole step, direction kept), then the safe box: the next TCP
   position is computed with FK BEFORE moving; a velocity component that would push through a wall
   is removed (slide along the wall), and if that is not enough the step is refused.
5. Joint position limits (with margin): a step pushing a joint further out is refused.

Incremental mode (``begin_increment``) runs through the very same ``step`` and stops by itself
when the distance is covered (or aborts with a message when something blocks it).

Safe-box semantics: a TCP INSIDE the box can never leave it. A TCP already outside (the upright
home pose is above the 0.7 m ceiling) may only move toward the box on the violated axes.
Every number in ``JogConfig`` is a SUGGESTION to tune in simulation, not a final value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Sequence, Tuple

import numpy as np

from robot_core.kinematics import Chain
from robot_core.safezone import SafetyConfig, effective_velocity_scale
from robot_core.se3 import Matrix, is_rotation, make_transform, matrix_from_rotvec, pose_error


class JogError(ValueError):
    """Bad input to the jog engine (wrong length, NaN, dt <= 0, ...)."""


class JogStatus(str, Enum):
    IDLE = 'idle'                      # no command: nothing moves
    OK = 'ok'
    LIMITED = 'limited'                # moved, but slower/different than asked (message says why)
    SINGULARITY = 'singularity'        # stopped: too close to a singular configuration
    JOINT_LIMIT = 'joint_limit'        # stopped: a joint would pass its (margin) limit
    BOX_STOP = 'box_stop'              # stopped: would leave the safe box and no slide is possible
    TRACKING = 'tracking'              # stopped: measured pose drifted from the target too far
    DONE = 'done'                      # incremental move finished
    ABORTED = 'aborted'                # incremental move could not finish (message says why)


@dataclass(frozen=True)
class JogConfig:
    """Tunable numbers: SUGGESTIONS to set in simulation; defaults lean to the narrow side."""
    speed_scale: float = 0.2           # requested fraction of the max speeds ("%20")
    max_linear_speed: float = 0.10     # m/s at scale 1.0
    max_angular_speed: float = 0.50    # rad/s at scale 1.0
    sigma_stop: float = 0.02           # jog stops below this sigma_min (mixed m/rad units)
    sigma_slow: float = 0.06           # full speed above, ramp down to 0 at sigma_stop
    damping_max: float = 0.05          # lambda at sigma_stop
    joint_limit_margin: float = 0.02   # rad kept clear of the profile joint limits
    box_margin: float = 0.005          # m kept clear of the safe box walls while sliding
    feedback_gain: float = 5.0         # 1/s, target-pose error feedback
    max_position_error: float = 0.01   # m, tracking lost beyond this
    max_angle_error: float = 0.1       # rad
    increment_tolerance: float = 1e-7  # m or rad left when an increment counts as done
    max_dt: float = 0.5                # s, larger ticks are refused

    def __post_init__(self) -> None:
        ok = (0 < self.speed_scale <= 1 and self.max_linear_speed > 0 and self.max_angular_speed > 0
              and 0 < self.sigma_stop < self.sigma_slow and self.damping_max >= 0
              and self.joint_limit_margin >= 0 and self.box_margin >= 0
              and self.feedback_gain >= 0 and self.max_position_error > 0
              and self.max_angle_error > 0 and self.increment_tolerance > 0 and self.max_dt > 0)
        if not ok:
            raise JogError('JogConfig: değerler pozitif olmalı, 0<speed_scale<=1, '
                           '0<sigma_stop<sigma_slow')


@dataclass(frozen=True, eq=False)
class JogStep:
    dq: np.ndarray                     # joint increment for this tick (rad)
    q_next: np.ndarray                 # q + dq: the command to send
    status: JogStatus
    messages: Tuple[str, ...]
    sigma_min: float
    tcp: Tuple[float, float, float]    # TCP position BEFORE this step (base frame, m)
    remaining: Optional[float] = None  # incremental mode: distance left (m or rad)

    @property
    def moved(self) -> bool:
        return bool(np.any(self.dq != 0.0))


class JogEngine(Protocol):
    """What the Robot API / controller (J2) talks to. Implementations may differ internally."""

    def set_frame(self, rotation: Optional[Matrix]) -> None:
        """Jog-frame axes in the base frame (3x3); ``None`` = Tool (follows the TCP every tick)."""

    def reset(self) -> None:
        """Forget the target pose and any increment (call when a jog session starts or ends)."""

    def begin_increment(self, q: Sequence[float], index: int, amount: float) -> None:
        """Move ``amount`` (m for index 0-2, rad for 3-5) along/about frame axis ``index``."""

    def step(self, q: Sequence[float], twist: Sequence[float], dt: float) -> JogStep:
        """One control tick from the measured ``q``."""


def _smoothstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


class DlsJogEngine:
    """Damped-least-squares implementation of ``JogEngine``."""

    def __init__(self, chain: Chain, safety: SafetyConfig,
                 config: Optional[JogConfig] = None) -> None:
        self.chain, self.safety, self.config = chain, safety, config or JogConfig()
        self._frame: Optional[Matrix] = np.eye(3)
        self._target: Optional[Tuple[np.ndarray, np.ndarray]] = None
        self._inc: Optional[dict] = None

    # ---- session ------------------------------------------------------------------------------
    def set_frame(self, rotation: Optional[Matrix]) -> None:
        if rotation is not None:
            rotation = np.asarray(rotation, float)
            if not is_rotation(rotation, 1e-6):
                raise JogError('çerçeve dönüşü geçerli bir 3x3 dönüş matrisi olmalı')
        self._frame = rotation
        self._target = None

    def reset(self) -> None:
        self._target, self._inc = None, None

    def begin_increment(self, q: Sequence[float], index: int, amount: float) -> None:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= 5:
            raise JogError(f'eksen 0-5 olmalı (X Y Z W P R), bulunan: {index!r}')
        if not math.isfinite(amount) or amount == 0:
            raise JogError(f'artım sıfırdan farklı sonlu bir sayı olmalı, bulunan: {amount!r}')
        pose = self.chain.fk(q)
        rotation = pose[:3, :3] if self._frame is None else self._frame   # Tool: fixed at start
        self._inc = {'index': index, 'remaining': float(amount), 'rotation': rotation.copy()}
        self._target = (pose[:3, 3].copy(), pose[:3, :3].copy())

    # ---- one tick -----------------------------------------------------------------------------
    def step(self, q: Sequence[float], twist: Sequence[float], dt: float) -> JogStep:
        cfg, chain = self.config, self.chain
        try:
            q = chain._check(q)
        except ValueError as exc:
            raise JogError(str(exc)) from exc
        twist = np.asarray(twist, float)
        if twist.shape != (6,) or not np.all(np.isfinite(twist)):
            raise JogError('6 sonlu sayı bekleniyordu (vx vy vz wx wy wz), bulunan: '
                           f'{twist.tolist()}')
        if not (math.isfinite(dt) and 0 < dt <= cfg.max_dt):
            raise JogError(f'dt (0, {cfg.max_dt:g}] s aralığında olmalı, bulunan: {dt!r}')

        pose, jac = chain.fk_and_jacobian(q)
        p, rot = pose[:3, 3], pose[:3, :3]
        u, sing, vt = np.linalg.svd(jac)
        sigma = float(sing[-1])
        tcp = (float(p[0]), float(p[1]), float(p[2]))
        messages = []

        frame_rot = self._inc['rotation'] if self._inc else (rot if self._frame is None
                                                             else self._frame)
        if self._inc:
            twist = self._increment_twist(dt)
        if not np.any(twist):
            self._target = None
            return self._halt(q, JogStatus.IDLE, messages, sigma, tcp)
        if self._target is None:
            self._target = (p.copy(), rot.copy())

        scale = effective_velocity_scale(cfg.speed_scale, self.safety.max_velocity_scale)
        v_lin, w_ang = frame_rot @ twist[:3], frame_rot @ twist[3:]
        for vec, cap, label in ((v_lin, cfg.max_linear_speed * scale, 'doğrusal'),
                                (w_ang, cfg.max_angular_speed * scale, 'açısal')):
            norm = float(np.linalg.norm(vec))
            if norm > cap:
                vec *= cap / norm
                messages.append(f'{label} hız {cap:.4g} ile sınırlandı')

        if sigma < cfg.sigma_stop:
            messages.append(f'tekillik: σ_min={sigma:.4f} < {cfg.sigma_stop:g}; bu yönde jog '
                            'durdu (eklem jog\'u ile tekillikten çıkın)')
            return self._halt(q, JogStatus.SINGULARITY, messages, sigma, tcp)
        slow = _smoothstep((sigma - cfg.sigma_stop) / (cfg.sigma_slow - cfg.sigma_stop))
        if slow < 1.0:
            v_lin, w_ang = v_lin * slow, w_ang * slow
            messages.append(f'tekilliğe yakın (σ_min={sigma:.3f}): hız %{slow * 100:.0f}')
        damping = (cfg.damping_max * (1 - (sigma / cfg.sigma_slow) ** 2)
                   if sigma < cfg.sigma_slow else 0.0)

        err = pose_error(make_transform(self._target[1], self._target[0]), pose)
        if (np.linalg.norm(err[:3]) > cfg.max_position_error
                or np.linalg.norm(err[3:]) > cfg.max_angle_error):
            messages.append(f'izleme hatası: {np.linalg.norm(err[:3]) * 1000:.1f} mm / '
                            f'{math.degrees(np.linalg.norm(err[3:])):.1f}°; jog durdu')
            return self._halt(q, JogStatus.TRACKING, messages, sigma, tcp)

        vlim = chain.velocity * scale

        def solve(lin: np.ndarray) -> Tuple[np.ndarray, float]:
            cmd = np.concatenate([lin, w_ang]) + cfg.feedback_gain * err
            dq = vt.T @ ((sing / (sing ** 2 + damping ** 2)) * (u.T @ cmd)) * dt
            ratio = float(np.max(np.abs(dq) / (vlim * dt)))
            return (dq / ratio, 1.0 / ratio) if ratio > 1.0 else (dq, 1.0)

        lo, hi = self.safety.zone.lower, self.safety.zone.upper
        v_try, dq, shrink, slid = v_lin.copy(), None, 1.0, False
        for attempt in range(4):
            cand, cand_shrink = solve(v_try)
            bad = self._outward(p, chain.fk(q + cand)[:3, 3], lo, hi, cfg.box_margin)
            if not any(bad):
                dq, shrink = cand, cand_shrink
                break
            if attempt < 3:                      # slide: drop the velocity component into the wall
                slid = True
                for i, flag in enumerate(bad):
                    if flag:
                        v_try[i] = 0.0
        if dq is None:
            messages.append('güvenli kutudan çıkacağı için jog durdu (hareket etmedi)')
            return self._halt(q, JogStatus.BOX_STOP, messages, sigma, tcp)
        if slid:
            messages.append('güvenli kutu duvarı: hız duvar boyunca kaydırıldı')

        q_next = q + dq
        margin = cfg.joint_limit_margin
        for i, name in enumerate(chain.joint_names):
            out_hi = q_next[i] > chain.upper[i] - margin and dq[i] > 0
            out_lo = q_next[i] < chain.lower[i] + margin and dq[i] < 0
            if out_hi or out_lo:
                messages.append(f'eklem limiti: {name} (q={q[i]:.3f} rad) sınıra '
                                f'{margin:g} rad yaklaşıyor; jog durdu')
                return self._halt(q, JogStatus.JOINT_LIMIT, messages, sigma, tcp)

        # accepted: integrate the TARGET with the twist actually granted (after every reduction)
        v_eff, w_eff = v_try * shrink, w_ang * shrink
        t_pos, t_rot = self._target
        self._target = (t_pos + v_eff * dt, matrix_from_rotvec(w_eff * dt) @ t_rot)
        if shrink < 1.0:
            messages.append(f'eklem hız sınırı: adım %{shrink * 100:.0f}')
        status = JogStatus.LIMITED if messages else JogStatus.OK
        remaining = None
        if self._inc:
            done, remaining, abort = self._advance_increment(v_eff, w_eff, dt)
            if abort:
                messages.append('artım ilerleyemedi; iptal edildi')
                self.reset()
                status = JogStatus.ABORTED
            elif done:
                status = JogStatus.DONE
                self.reset()
        return JogStep(dq=dq, q_next=q_next, status=status, messages=tuple(messages),
                       sigma_min=sigma, tcp=tcp, remaining=remaining)

    # ---- helpers ------------------------------------------------------------------------------
    @staticmethod
    def _outward(p: np.ndarray, nxt: np.ndarray, lo: Sequence[float], hi: Sequence[float],
                 margin: float) -> Tuple[int, int, int]:
        """+1/-1 per axis where the next point is past a (margin-shrunk) wall AND moving out."""
        flags = []
        for i in range(3):
            if nxt[i] > hi[i] - margin and nxt[i] > p[i]:
                flags.append(1)
            elif nxt[i] < lo[i] + margin and nxt[i] < p[i]:
                flags.append(-1)
            else:
                flags.append(0)
        return flags[0], flags[1], flags[2]

    def _halt(self, q: np.ndarray, status: JogStatus, messages, sigma: float,
              tcp: Tuple[float, float, float]) -> JogStep:
        remaining = self._inc['remaining'] if self._inc else None
        if self._inc is not None and status is not JogStatus.IDLE:
            messages = list(messages) + ['artım iptal edildi']
        self._target, self._inc = None, None
        zero = np.zeros(self.chain.dof)
        return JogStep(dq=zero, q_next=q.copy(), status=status, messages=tuple(messages),
                       sigma_min=sigma, tcp=tcp, remaining=remaining)

    def _increment_twist(self, dt: float) -> np.ndarray:
        inc, cfg = self._inc, self.config
        linear = inc['index'] < 3
        top = cfg.max_linear_speed if linear else cfg.max_angular_speed
        cap = top * effective_velocity_scale(cfg.speed_scale, self.safety.max_velocity_scale)
        speed = min(cap, abs(inc['remaining']) / dt)         # the last tick lands exactly
        twist = np.zeros(6)
        twist[inc['index']] = math.copysign(speed, inc['remaining'])
        return twist

    def _advance_increment(self, v_eff: np.ndarray, w_eff: np.ndarray,
                           dt: float) -> Tuple[bool, float, bool]:
        inc = self._inc
        axis = inc['rotation'][:, inc['index'] % 3]
        progress = float((v_eff if inc['index'] < 3 else w_eff) @ axis) * dt
        if abs(progress) < 1e-12:
            return False, inc['remaining'], True
        inc['remaining'] -= progress
        if abs(inc['remaining']) <= self.config.increment_tolerance:
            return True, 0.0, False
        return False, inc['remaining'], False
