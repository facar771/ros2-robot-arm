"""Safe-box check ALONG a joint-space path (F4a). ROS-free, pure numpy; wiring is F4b.

``check_path(path, profile)`` walks consecutive path points in joint space (straight joint
interpolation, what a joint trajectory executor does between waypoints), computes the TCP with the
profile's forward kinematics at every sample and tests it against the safe box.

Sampling density (worst case, rigorous): the TCP speed per unit of joint-space "arc" is bounded by
    |dp| <= sum_i |dq_i| * lever_i,     lever_i = sum_{k>i} |origin_k| + |tcp.offset|
(every joint turns the TCP about an axis through joint i's origin; the TCP can never be farther
from it than the sum of the downstream link offsets). A segment is cut into pieces whose bound is
at most ``max_step`` metres, so the TCP path length between two samples is <= max_step.

No leak: for two neighbouring samples inside the box with wall clearances s_a and s_b, the whole
piece is inside whenever s_a + s_b >= bound (every point of the piece is within s_a of sample a
or within s_b of sample b along the path, and the walls are s_a / s_b away). Pieces failing this
(near a wall) are bisected until the bound is <= ``min_step``; what remains is a leak of at most
``min_step / 2`` (reported as ``leak_bound``, default 0.05 mm) which only occurs for paths that
run along a wall within that distance. More than ``max_samples`` FK evaluations: rejected.

Start outside the box (home): only samples where the distance to the box does not grow are
allowed (approach phase); after the first inside sample every later point must be inside.
That approach rule is checked at the samples only (the distance function is not certified
between them). The box limits the TCP only, not the elbow or wrist.

Approach noise (F4b, Robot_1 decision A): a simulated/real ``home`` is never exactly the pose
the plan starts from, and the first plan points can wobble by a fraction of a millimetre. So in
the approach phase the distance to the box may not exceed the SMALLEST distance seen so far by
more than ``APPROACH_EPS`` (2 mm). The reference is the running minimum, not the previous sample,
so the slack does not accumulate: the path can never be farther than ``min + 2 mm`` however many
small steps it takes. ``APPROACH_EPS`` is a constant in this file: no profile, yaml or API
parameter changes it. It is NOT added to the box walls, the inner box or the park envelope: from
the first inside sample on the path must be strictly inside, and ``check_park_move`` uses no
slack at all.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from robot_core.kinematics import Chain
from robot_core.profile import Profile
from robot_core.safezone import AXES, ParkConfig, ParkZone, SafeZone

Vec = Tuple[float, float, float]
DEFAULT_MAX_STEP = 0.005         # m between samples (TCP path length bound): narrow default
DEFAULT_MIN_STEP = 1e-4          # m: bisection floor near a wall; leak bound = min_step / 2
DEFAULT_MAX_SAMPLES = 200_000
APPROACH_EPS = 0.002             # m: approach-phase noise allowance over the running minimum;
#                                  hard-coded on purpose (see the module docstring)


class PathError(ValueError):
    """The path itself is malformed (shape, NaN, parameters)."""


@dataclass(frozen=True)
class Violation:
    segment: int                 # index of the path segment (path[segment] -> path[segment + 1])
    fraction: float              # 0..1 along that segment
    q: Tuple[float, ...]
    point: Vec                   # TCP in the base/world frame, metres
    axis: str                    # 'x' | 'y' | 'z' (the worst violated one, or 'x' for approach)
    side: str                    # 'lower' | 'upper' | 'approach'
    excess: float                # m beyond the wall (approach: how much farther than before)


@dataclass(frozen=True)
class PathCheck:
    ok: bool
    reason: str                  # '' when ok
    violation: Optional[Violation]
    samples: int                 # FK evaluations
    max_chord_bound: float       # worst bound on the TCP path length between two samples (m)
    leak_bound: float            # m: undetected excursion beyond a wall, at most
    starts_outside: bool
    enters_box_at: Optional[Vec] # first TCP inside the box when the path starts outside

    def message(self) -> str:
        if self.ok:
            return 'yol güvenli kutu içinde'
        return self.reason


def _levers(profile: Profile) -> np.ndarray:
    offsets = [float(np.linalg.norm(j.origin)) for j in profile.joints]
    tcp = float(np.linalg.norm(profile.tcp.offset))
    return np.array([sum(offsets[i + 1:]) + tcp for i in range(len(offsets))])


def _outside(point: np.ndarray, lo: np.ndarray, hi: np.ndarray):
    """(distance to the box, worst axis index, side, excess); distance 0 = inside (walls count)."""
    below, above = lo - point, point - hi
    out = np.maximum(np.maximum(below, above), 0.0)
    dist = float(np.linalg.norm(out))
    k = int(np.argmax(out))
    return dist, k, ('lower' if below[k] >= above[k] else 'upper'), float(out[k])


def check_path(path: Sequence[Sequence[float]], profile: Profile, box: Optional[SafeZone] = None,
               max_step: float = DEFAULT_MAX_STEP, min_step: float = DEFAULT_MIN_STEP,
               max_samples: int = DEFAULT_MAX_SAMPLES) -> PathCheck:
    """Check ``path`` (list of joint vectors, chain order, rad) against ``box``
    (default ``profile.safety.zone``). Never raises for a violation: returns ``ok=False``.
    The park envelope (``ParkZone``) is refused here: only ``check_park_move`` may use it."""
    if isinstance(box, ParkZone) or (box is not None and not isinstance(box, SafeZone)):
        raise PathError('check_path yalnız SafeZone kabul eder; park zarfı yalnız '
                        'check_park_move ile kullanılır')
    return _check_path(path, profile, box, max_step, min_step, max_samples,
                       approach_eps=APPROACH_EPS)


def _check_path(path: Sequence[Sequence[float]], profile: Profile, box: Optional[SafeZone],
                max_step: float, min_step: float, max_samples: int,
                approach_eps: Optional[float] = None) -> PathCheck:
    # ``approach_eps`` is internal (not part of check_path's signature): None = APPROACH_EPS
    # at call time; ``check_park_move`` passes 0.0 (no slack for the park envelope).
    eps = APPROACH_EPS if approach_eps is None else float(approach_eps)
    if not (isinstance(max_step, (int, float)) and math.isfinite(max_step) and max_step > 0
            and isinstance(min_step, (int, float)) and 0 < min_step <= max_step
            and isinstance(max_samples, int) and max_samples >= 2):
        raise PathError('max_step > 0, 0 < min_step <= max_step ve max_samples >= 2 olmalı')
    chain = Chain(profile)
    try:
        pts = np.asarray(path, float)
    except (TypeError, ValueError) as exc:
        raise PathError(f'yol sayısal olmalı: {exc}') from exc
    if pts.ndim != 2 or pts.shape[0] < 1 or pts.shape[1] != chain.dof or not np.all(
            np.isfinite(pts)):
        raise PathError(f'yol [N >= 1, {chain.dof}] sonlu sayı olmalı, bulunan şekil: '
                        f'{getattr(pts, "shape", None)}')
    zone = box or profile.safety.zone
    lo, hi = np.array(zone.lower, float), np.array(zone.upper, float)
    lever = _levers(profile)
    state = {'n': 0, 'chord': 0.0}

    def tcp(q: np.ndarray) -> np.ndarray:
        state['n'] += 1
        return chain.fk(q)[:3, 3]

    def violation(seg: int, frac: float, q: np.ndarray, p: np.ndarray, axis: int, side: str,
                  excess: float, reason: str) -> PathCheck:
        v = Violation(seg, float(frac), tuple(float(x) for x in q), tuple(float(x) for x in p),
                      AXES[axis], side, float(excess))
        return PathCheck(False, reason, v, state['n'], state['chord'], min_step / 2,
                         starts_outside, entered)

    p0 = tcp(pts[0])
    d0, k0, side0, ex0 = _outside(p0, lo, hi)
    starts_outside = d0 > 0.0
    entered: Optional[Vec] = None
    inside_phase = not starts_outside
    prev_d, prev_p = d0, p0
    min_d = d0                       # running minimum of the distance (approach phase)

    def where(seg: int, frac: float, p: np.ndarray) -> str:
        return (f'yol {seg + 1}. parçasında %{frac * 100:.0f} noktasında '
                f'TCP ({p[0]:.3f}, {p[1]:.3f}, {p[2]:.3f}) m')

    if len(pts) == 1:
        return PathCheck(not starts_outside, '' if not starts_outside else (
            f'tek nokta güvenli kutunun dışında: TCP ({p0[0]:.3f}, {p0[1]:.3f}, {p0[2]:.3f}) m, '
            f'{AXES[k0]} {side0 == "lower" and "alt" or "üst"} sınırını {ex0:.3f} m aşıyor'),
                         None if not starts_outside else Violation(
                             0, 0.0, tuple(map(float, pts[0])), tuple(map(float, p0)), AXES[k0],
                             side0, ex0), state['n'], 0.0, min_step / 2, starts_outside, None)

    for seg in range(len(pts) - 1):
        qa, qb = pts[seg], pts[seg + 1]
        bound = float(np.abs(qb - qa) @ lever)
        n = max(1, math.ceil(bound / max_step))
        state['chord'] = max(state['chord'], bound / n)
        prev_q, prev_f = qa, 0.0
        for i in range(1, n + 1):
            frac = i / n
            q = qa + (qb - qa) * frac if i < n else qb
            p = tcp(q)
            if state['n'] > max_samples:
                return violation(seg, frac, q, p, 0, 'approach', 0.0,
                                 f'örnek sınırı aşıldı ({max_samples}); yol reddedildi')
            dist, k, side, excess = _outside(p, lo, hi)
            if inside_phase:
                if dist > 0.0:
                    name = 'alt' if side == 'lower' else 'üst'
                    return violation(seg, frac, q, p, k, side, excess, (
                        f'{where(seg, frac, p)} güvenli kutunun dışında: {AXES[k]} {name} '
                        f'sınırını {excess * 1000:.1f} mm aşıyor; hareket başlamaz'))
                # inside-inside piece: certify (or bisect near a wall)
                stack = [(prev_q, q, prev_p, p, prev_f, frac)]
                while stack:
                    a, b, pa, pb, fa, fb = stack.pop()
                    sub = float(np.abs(b - a) @ lever)
                    if sub <= min_step:
                        continue
                    sa = float(min(np.min(pa - lo), np.min(hi - pa)))
                    sb = float(min(np.min(pb - lo), np.min(hi - pb)))
                    if sa + sb >= sub:
                        continue
                    qm, fm = (a + b) / 2, (fa + fb) / 2
                    pm = tcp(qm)
                    if state['n'] > max_samples:
                        return violation(seg, fm, qm, pm, 0, 'approach', 0.0,
                                         f'örnek sınırı aşıldı ({max_samples}); yol reddedildi')
                    dm, km, sidem, exm = _outside(pm, lo, hi)
                    if dm > 0.0:
                        name = 'alt' if sidem == 'lower' else 'üst'
                        return violation(seg, fm, qm, pm, km, sidem, exm, (
                            f'{where(seg, fm, pm)} güvenli kutunun dışında: {AXES[km]} {name} '
                            f'sınırını {exm * 1000:.1f} mm aşıyor; hareket başlamaz'))
                    stack.append((a, qm, pa, pm, fa, fm))
                    stack.append((qm, b, pm, pb, fm, fb))
            else:
                if dist - min_d > eps:
                    return violation(seg, frac, q, p, k, 'approach', dist - min_d, (
                        f'{where(seg, frac, p)}: başlangıç kutu dışında, yalnız kutuya yaklaşırken '
                        f'izin var ama kutuya uzaklık, o ana kadarki en küçük değer '
                        f'{min_d * 1000:.1f} mm iken {dist * 1000:.1f} mm oldu '
                        f'(izinli pay {eps * 1000:.1f} mm); hareket başlamaz'))
                min_d = min(min_d, dist)
                if dist == 0.0:
                    inside_phase = True
                    entered = (float(p[0]), float(p[1]), float(p[2]))
            prev_d, prev_p, prev_q, prev_f = dist, p, q, frac
    if not inside_phase:
        return PathCheck(False, ('başlangıç kutu dışında ve yol kutuya hiç girmiyor (son TCP '
                                 f'({prev_p[0]:.3f}, {prev_p[1]:.3f}, {prev_p[2]:.3f}) m, kutuya '
                                 f'{prev_d * 1000:.1f} mm uzak); hareket başlamaz'),
                         None, state['n'], state['chord'], min_step / 2, True, None)
    return PathCheck(True, '', None, state['n'], state['chord'], min_step / 2, starts_outside,
                     entered)


def transit_extent(path: Sequence[Sequence[float]], profile: Profile,
                   step: float = 0.0005) -> Tuple[Tuple[float, float], ...]:
    """TCP extent per axis (metres, rounded OUTWARD to 0.1 mm) of the straight joint-space path,
    sampled at ``step`` (TCP path length bound). This is how the park file's ``measured`` is
    derived and re-derived by the tests."""
    chain = Chain(profile)
    pts = np.asarray(path, float)
    levers = _levers(profile)
    lo, hi = np.full(3, math.inf), np.full(3, -math.inf)
    for a, b in zip(pts, pts[1:]):
        n = max(1, math.ceil(float(np.abs(b - a) @ levers) / step))
        for i in range(n + 1):
            p = chain.fk(a + (b - a) * (i / n))[:3, 3]
            lo, hi = np.minimum(lo, p), np.maximum(hi, p)
    return tuple((math.floor(a * 1e4) / 1e4, math.ceil(b * 1e4) / 1e4) for a, b in zip(lo, hi))


def _reject(reason: str) -> PathCheck:
    return PathCheck(False, 'park: ' + reason, None, 0, 0.0, 0.0, False, None)


def check_park_move(path: Sequence[Sequence[float]], profile: Profile, park: ParkConfig,
                    pose: str = 'home', max_step: float = DEFAULT_MAX_STEP,
                    min_step: float = DEFAULT_MIN_STEP,
                    max_samples: int = DEFAULT_MAX_SAMPLES) -> PathCheck:
    """The ONE exemption from the inner box: a straight joint move between ``ready`` and the
    parked pose ``home``, checked against the park envelope.

    Accepted only if (1) the profile's ``home`` and ``ready`` joint values equal the verified
    values of the park file (otherwise the pose is not exempt), (2) ``path`` is exactly
    [start, goal] with the goal EXACTLY the profile's pose (``home`` or ``ready``), (3) the other
    end of the move is ``ready`` / ``home`` and the start is within ``ready_tolerance`` of it,
    (4) every sample (and the no-leak bisection of ``check_path``) lies inside the park envelope.
    Returns ``ok=False`` with the reason; the speed cap is ``park.max_velocity_fraction`` of
    the maximum (applied by the caller)."""
    if pose not in park.poses:
        return _reject(f'{pose!r} park listesinde değil (izinli: {sorted(park.poses)})')
    ready_profile = profile.named_poses.get('ready')
    home_profile = profile.named_poses.get(pose)
    if ready_profile is None or home_profile is None:
        return _reject("profilde 'ready' ya da park pozu tanımlı değil")
    for name, got, verified in (('ready', ready_profile, park.ready_joints),
                                (pose, home_profile, park.poses[pose])):
        if len(got) != len(verified) or max(abs(g - v) for g, v in zip(got, verified)) > 1e-9:
            return _reject(f"profildeki {name!r} doğrulanmış eklem değerinden farklı; muaf "
                           'sayılmaz (park dosyası gözden geçirilmeden güncellenmez)')
    try:
        pts = np.asarray(path, float)
    except (TypeError, ValueError) as exc:
        raise PathError(f'yol sayısal olmalı: {exc}') from exc
    if pts.shape != (2, len(park.ready_joints)) or not np.all(np.isfinite(pts)):
        raise PathError(f'park hareketi tam iki nokta ([başlangıç, hedef]) olmalı, şekil: '
                        f'{pts.shape}')
    ready, parked = np.array(park.ready_joints), np.array(park.poses[pose])

    def dist(q: np.ndarray, ref: np.ndarray) -> float:
        return float(np.max(np.abs(q - ref)))

    if dist(pts[1], parked) <= 1e-9 and dist(pts[0], ready) <= park.ready_tolerance:
        pass                                           # ready -> home
    elif dist(pts[1], ready) <= 1e-9 and dist(pts[0], parked) <= park.ready_tolerance:
        pass                                           # home -> ready
    else:
        return _reject(f"hareketin bir ucu 'ready' (başlangıç {park.ready_tolerance} rad içinde) "
                       f"ve hedef tam {pose!r} (ya da tersi) olmalı")
    result = _check_path(pts, profile, park.zone.as_safe_zone(), max_step, min_step, max_samples,
                         approach_eps=0.0)               # no slack for the park envelope
    if not result.ok:
        return PathCheck(False, 'park zarfı: ' + result.reason, result.violation, result.samples,
                         result.max_chord_bound, result.leak_bound, result.starts_outside,
                         result.enters_box_at)
    return result
