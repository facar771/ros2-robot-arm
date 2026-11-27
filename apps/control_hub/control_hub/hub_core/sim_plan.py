"""SİM hareket planlama: SAF işlevler (saat/iş parçacığı yok), testlenebilir.

* Eklem hareketi: ``q0 -> q1`` doğrusu, **senkron trapez** zaman profili (her eklem aynı skaler ``s(t)``).
* Doğrusal hareket: TCP konumu doğru, yönelim slerp; her örnek için sayısal ters kinematik (DLS).
* Doğrulama ``robot_core`` kurallarıyla: eklem limiti, güvenli kutu (``safezone``), yol (``pathcheck``).
  Hız tavanı (``max_velocity_scale``) burada zorlanır (UI_CONTRACT §9-B); gerçek modda Hub onu gevşetmez.
SİM'de (P1e) hız % ve hız/ivme ölçeği SINIRSIZDIR; tek sınır ``check_pct`` / ``check_scale``: sayı sonlu, > 0,
hız % <= ``PCT_MAX``, ölçek <= ``SCALE_MAX``. Konum limitleri (eklem, güvenli kutu) yine zorlanır.

Hata türleri (``Violation.kind`` = ``PlanError.kind``): ``outside_safe_zone | joint_limit | path_blocked |
unreachable | bad_request``. ``at_pct``: hareketin kaçta kaçında (0-100) sorun görüldü.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np
from robot_core.kinematics import Chain
from robot_core.pathcheck import PathError, check_path
from robot_core.profile import Profile
from robot_core.se3 import (
    make_transform,
    matrix_from_rotvec,
    matrix_from_wpr_display,
    pose_error,
    rotvec_from_matrix,
)

from control_hub.hub_core.backend import PCT_MAX, SCALE_MAX, check_pct, check_scale  # noqa: F401  (yeniden dışa)

EXAMPLE_MOVES_DEG = (6.0, 15.0)   # hız tablosundaki örnekler (ilki kartta yazılı; 15° kabul ölçümü)
EXAMPLE_MOVE_DEG = 6.0           # hız tablosundaki örnek: en yavaş eklem bu kadar döner, süre tahmini yazılır
RAMP = 0.25                      # trapezin ivmelenme/yavaşlama payı (toplam sürenin her ucunda)
MIN_DURATION_S = 0.2
LINEAR_SPEED_M_S = 0.10          # %100 hızda TCP çizgisel hızı (JogConfig.max_linear_speed ile aynı)
ANGULAR_SPEED_RAD_S = 0.50       # %100 hızda TCP açısal hızı (JogConfig.max_angular_speed)
SAMPLE_STEP_M = 0.004            # doğrusal hareket örnek aralığı
SAMPLE_STEP_RAD = 0.04
MAX_SAMPLES = 400
IK_ITERATIONS = 80
IK_TOL_POS_M = 1e-4
IK_TOL_ANG_RAD = 1e-3
IK_DAMPING = 0.03
IK_MAX_STEP_RAD = 0.25
IK_EXTRA_SEEDS = 12                # çok tohumlu çözüm: ek tohum sayısı (sabit rastgelelik: tekrarlanabilir)
IK_SEED_RNG = 20240601
MAX_JOINT_JUMP_RAD = 0.35        # iki örnek arası eklem sıçraması: üstü "konfigürasyon atlaması"
NEAR_LIMIT_FRACTION = 0.05       # EK-1/§9-A: limit aralığının %5'i içinde = near_limit
ORIENTATIONS = ('fixed', 'free')     # 'free': yalnız konum hedefi, yönelimi çözücü belirler
KINDS = ('outside_safe_zone', 'joint_limit', 'path_blocked', 'unreachable', 'bad_request')


class PlanError(Exception):
    """Plan kurulamadı: ``kind`` (§9-B ret kodu), ``at_pct`` (0-100), ``message`` (Türkçe)."""

    def __init__(self, kind: str, message: str, at_pct: float = 100.0):
        super().__init__(message)
        assert kind in KINDS, kind
        self.kind, self.message, self.at_pct = kind, message, float(max(0.0, min(100.0, at_pct)))


@dataclass(frozen=True)
class Plan:
    """Doğrulanmış hareket: eklem örnekleri (rad, ``[N, dof]``) ve süre."""
    kind: str                    # 'joint' | 'linear'
    path: np.ndarray
    duration_s: float
    speed_scale: float           # fiilen kullanılan hız ölçeği (tavan uygulanmış)

    def q_at(self, t_s: float) -> np.ndarray:
        """``t_s`` anındaki eklemler (trapez profil; süre dolunca son örnek)."""
        s = trapezoid(t_s / self.duration_s)
        pos = s * (len(self.path) - 1)
        i = min(int(pos), len(self.path) - 2)
        f = pos - i
        return self.path[i] * (1.0 - f) + self.path[i + 1] * f


# ---- zaman profili -----------------------------------------------------------------------------
def trapezoid(tau: float, ramp: float = RAMP) -> float:
    """Normalize trapez: ``s(0)=0, s(1)=1``, uçlarda sıfır hız; ``tau`` [0,1] dışı kıstırılır."""
    tau = min(1.0, max(0.0, tau))
    v = 1.0 / (1.0 - ramp)
    if tau < ramp:
        return 0.5 * v / ramp * tau * tau
    if tau <= 1.0 - ramp:
        return 0.5 * v * ramp + v * (tau - ramp)
    return 1.0 - 0.5 * v / ramp * (1.0 - tau) ** 2


def duration_for(delta: np.ndarray, vmax: np.ndarray, amax: np.ndarray, scale: float,
                 ramp: float = RAMP, accel_scale: Optional[float] = None) -> float:
    """Senkron trapez için en kısa süre: her eklem ``scale``·vmax hızını ve ``accel_scale``·amax ivmesini
    aşmaz (``accel_scale`` yoksa ``scale``). Tepe hız ``|d| / (T (1-ramp))``, tepe ivme
    ``|d| / (T² ramp (1-ramp))``."""
    d = np.abs(np.asarray(delta, float))
    ka = scale if accel_scale is None else accel_scale
    t_v = float(np.max(d / (np.asarray(vmax) * scale))) / (1.0 - ramp)
    t_a = math.sqrt(float(np.max(d / (np.asarray(amax) * ka))) / (ramp * (1.0 - ramp)))
    return max(MIN_DURATION_S, t_v, t_a)


# ---- model -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SimModel:
    """Profilden türeyen, hareket planı için gereken her şey."""
    profile: Profile
    chain: Chain
    lower: np.ndarray
    upper: np.ndarray
    vmax: np.ndarray
    amax: np.ndarray
    velocity_cap: float          # profil `max_velocity_scale`: %100 hız = bu oran (SİM'de ayarlanabilir, P1d)
    accel_cap: Optional[float] = None   # ivme tavanı ölçeği (SİM, P1e); None = hız ölçeğiyle birlikte

    @classmethod
    def from_profile(cls, profile: Profile) -> 'SimModel':
        chain = Chain(profile)
        return cls(profile, chain, chain.lower.copy(), chain.upper.copy(), chain.velocity.copy(),
                   np.array([j.acceleration for j in profile.joints], float),
                   float(profile.safety.max_velocity_scale))

    @property
    def dof(self) -> int:
        return self.chain.dof

    def scale_for(self, speed_pct: float) -> float:
        """Hız % -> fiili ölçek = % / 100 × ``velocity_cap`` (%100 = profil limiti × tavan ölçeği).
        Kıstırma YOK: sınır denetimi ``check_pct`` işidir (sunucu girişte 400 verir)."""
        return float(speed_pct) / 100.0 * self.velocity_cap

    @property
    def accel_linked(self) -> bool:
        return self.accel_cap is None

    def accel_scale_for(self, speed_pct: float) -> float:
        """Hız % -> ivme ölçeği = % / 100 × ivme tavanı (bağlıysa hız tavanı)."""
        cap = self.velocity_cap if self.accel_cap is None else self.accel_cap
        return float(speed_pct) / 100.0 * cap

    def tcp(self, q: Sequence[float]) -> np.ndarray:
        return self.chain.fk(q)

    def near_limit(self, q: Sequence[float]) -> List[bool]:
        span = self.upper - self.lower
        q = np.asarray(q, float)
        margin = NEAR_LIMIT_FRACTION * span
        return [bool(a) for a in ((q - self.lower) <= margin) | ((self.upper - q) <= margin)]


def speed_limits_doc(model: SimModel, speed_pct: float, *, default_scale: float) -> dict:
    """GET /api/speed/limits gövdesinin hesaplanan kısmı (arayüz hesap yapmaz, yalnız gösterir).

    Etkin üst hız = profil limiti × tavan ölçeği × hız %; etkin ivme = profil ivmesi × ivme ölçeği × hız %.
    Eklem hareketinde (senkron profil) tüm eklemler AYNI anda varır; süreyi en yavaş eklem
    belirler. ``example_move``: en yavaş eklem 6° dönerken ``duration_for`` süresi (ivme dahil).
    ``over_speed`` / ``over_accel``: etkin değer profil limitini AŞIYOR (varsayımsal; yalnız SİM);
    ``above_profile``: ikisinden biri."""
    k, ka = model.scale_for(speed_pct), model.accel_scale_for(speed_pct)
    names = list(model.profile.joint_names)
    eff, eff_a = model.vmax * k, model.amax * ka
    over_v, over_a = k > 1.0 + 1e-9, ka > 1.0 + 1e-9
    rows = [{'name': n, 'limit_rad_s': round(float(v), 4), 'limit_deg_s': round(math.degrees(v), 2),
             'effective_rad_s': round(float(e), 4), 'effective_deg_s': round(math.degrees(e), 2),
             'accel_limit_rad_s2': round(float(a), 4), 'accel_effective_rad_s2': round(float(ea), 4),
             'over_speed': over_v, 'over_accel': over_a}
            for n, v, e, a, ea in zip(names, model.vmax, eff, model.amax, eff_a)]
    slow = int(np.argmin(eff))
    examples = []
    for deg in EXAMPLE_MOVES_DEG:                    # en yavaş eklem ``deg`` döner: süre tahmini (ivme dahil)
        delta = np.zeros(model.dof)
        delta[slow] = math.radians(deg)
        t = duration_for(delta, model.vmax, model.amax, k, accel_scale=ka)
        examples.append({'joint': names[slow], 'deg': deg, 'duration_s': round(t, 2)})
    cap_a = model.velocity_cap if model.accel_cap is None else model.accel_cap
    return {'scale': model.velocity_cap, 'scale_default': float(default_scale), 'scale_min': 0.0,
            'scale_max': SCALE_MAX, 'pct_max': PCT_MAX, 'accel_scale': cap_a,
            'accel_linked': model.accel_linked, 'speed_pct': speed_pct,
            'over_speed': over_v, 'over_accel': over_a, 'above_profile': over_v or over_a,
            'joints': rows, 'limiting_joint': names[slow],
            'example_move': examples[0], 'example_moves': examples}


def _finite(vec: Sequence[float], n: int, what: str) -> np.ndarray:
    try:
        arr = np.asarray(vec, float)
    except (TypeError, ValueError):
        raise PlanError('bad_request', f'{what} sayı listesi olmalı') from None
    if arr.shape != (n,) or not np.all(np.isfinite(arr)):
        raise PlanError('bad_request', f'{what}: {n} sonlu sayı bekleniyordu')
    return arr


def _check_zone(model: SimModel, point: Sequence[float], at_pct: float) -> None:
    why = model.profile.safety.zone.check(tuple(float(v) for v in point))
    if why:
        raise PlanError('outside_safe_zone', why[0].upper() + why[1:], at_pct)


def _check_path(model: SimModel, samples: np.ndarray) -> None:
    """Örnekler arası yolu güvenli kutuya karşı denetler (``robot_core.pathcheck``)."""
    try:
        res = check_path(samples.tolist(), model.profile)
    except PathError as exc:
        raise PlanError('bad_request', str(exc)) from None
    if res.ok:
        return
    v = res.violation
    segs = max(1, len(samples) - 1)
    at = 100.0 * ((v.segment + v.fraction) / segs) if v is not None else 100.0
    raise PlanError('path_blocked', res.reason, at)


# ---- eklem hareketi ----------------------------------------------------------------------------
def plan_joint(model: SimModel, q0: Sequence[float], joints_deg: Sequence[float], speed_pct: float) -> Plan:
    q1 = np.radians(_finite(joints_deg, model.dof, 'joints_deg'))
    for i, (v, lo, hi) in enumerate(zip(q1, model.lower, model.upper)):
        if not lo - 1e-9 <= v <= hi + 1e-9:
            raise PlanError('joint_limit', f'{model.profile.joint_names[i]} hedefi {math.degrees(v):.1f}°, '
                            f'izin verilen aralık [{math.degrees(lo):.1f}°, {math.degrees(hi):.1f}°]')
    q0 = np.asarray(q0, float)
    _check_zone(model, model.tcp(q1)[:3, 3], 100.0)
    path = np.vstack([q0, q1])
    _check_path(model, path)
    scale = model.scale_for(speed_pct)
    ka = model.accel_scale_for(speed_pct)
    return Plan('joint', path, duration_for(q1 - q0, model.vmax, model.amax, scale, accel_scale=ka), scale)


# ---- ters kinematik ----------------------------------------------------------------------------
def solve_ik(model: SimModel, target: np.ndarray, seed: Sequence[float],
             position_only: bool = False) -> Optional[np.ndarray]:
    """DLS ile ``target`` (4x4, taban çerçevesi) için ``seed``'e en yakın çözüm; limitlere kıstırılır.
    Yakınsamazsa ``None`` (ulaşılamaz)."""
    q = np.asarray(seed, float).copy()
    rows = slice(0, 3) if position_only else slice(0, 6)
    for _ in range(IK_ITERATIONS):
        pose, jac = model.chain.fk_and_jacobian(q)
        err = pose_error(target, pose)
        ang_ok = position_only or np.linalg.norm(err[3:]) < IK_TOL_ANG_RAD
        if np.linalg.norm(err[:3]) < IK_TOL_POS_M and ang_ok:
            return q
        j, e = jac[rows], err[rows]
        dq = j.T @ np.linalg.solve(j @ j.T + IK_DAMPING ** 2 * np.eye(j.shape[0]), e)
        biggest = float(np.max(np.abs(dq)))
        if biggest > IK_MAX_STEP_RAD:
            dq *= IK_MAX_STEP_RAD / biggest
        q = np.clip(q + dq, model.lower, model.upper)
    return None


def _seeds(model: SimModel, seed: np.ndarray) -> List[np.ndarray]:
    """Tohumlar: verilen + limit ortası + tohumun etrafında sabit-rastgele sapmalar (tekillikten çıkış)."""
    rng = np.random.default_rng(IK_SEED_RNG)
    mid = 0.5 * (model.lower + model.upper)
    span = model.upper - model.lower
    out = [mid]
    for _ in range(IK_EXTRA_SEEDS):
        out.append(np.clip(seed + rng.uniform(-0.25, 0.25, size=seed.shape) * span, model.lower, model.upper))
    return out


def solve_ik_multi(model: SimModel, target: np.ndarray, seed: Sequence[float],
                   position_only: bool = False) -> Optional[np.ndarray]:
    """Önce ``seed`` (süreklilik); yakınsamazsa ya da çok uzaksa ek tohumlarla dener, ``seed``'e EN YAKIN
    çözümü döndürür. Hâlâ yoksa ``None``. Eklem-uzayına düşme YOK: sıçrama denetimi çağıranda."""
    seed = np.asarray(seed, float)
    first = solve_ik(model, target, seed, position_only)
    if first is not None and float(np.max(np.abs(first - seed))) <= MAX_JOINT_JUMP_RAD:
        return first
    best = first
    for s0 in _seeds(model, seed):
        q = solve_ik(model, target, s0, position_only)
        if q is not None and (best is None or np.linalg.norm(q - seed) < np.linalg.norm(best - seed)):
            best = q
    return best


# ---- doğrusal hareket --------------------------------------------------------------------------
def _pose_samples(start: np.ndarray, end: np.ndarray) -> List[np.ndarray]:
    """Konum doğrusu + yönelim slerp ile 4x4 örnekler (başlangıç dahil, bitiş dahil)."""
    dist = float(np.linalg.norm(end[:3, 3] - start[:3, 3]))
    rel = rotvec_from_matrix(start[:3, :3].T @ end[:3, :3])
    angle = float(np.linalg.norm(rel))
    n = int(min(MAX_SAMPLES, max(2, math.ceil(max(dist / SAMPLE_STEP_M, angle / SAMPLE_STEP_RAD)) + 1)))
    out = []
    for k in range(n):
        s = k / (n - 1)
        rot = start[:3, :3] @ matrix_from_rotvec(rel * s)
        out.append(make_transform(rot, start[:3, 3] + (end[:3, 3] - start[:3, 3]) * s))
    return out


def plan_linear(model: SimModel, q0: Sequence[float], pos_m: Sequence[float],
                rpy_deg: Optional[Sequence[float]], orientation: str, speed_pct: float) -> Plan:
    """TCP'yi doğrusal götürür. ``rpy_deg`` (W-P-R, derece) yoksa mevcut yönelim korunur; verilirse ve
    sağlanamıyorsa hedef ``unreachable`` sayılır (yönelim sessizce bırakılmaz). ``orientation='free'``: yalnız
    konum hedeflenir, yönelimi çözücü belirler (``rpy_deg`` ile birlikte verilemez).

    Yolda eklem sıçraması varsa ``path_blocked`` döner; eklem-uzayı hareketine OTOMATİK düşülmez (güvenlik:
    TCP'nin doğrusal yolu kullanıcının onayladığı yoldur)."""
    if orientation not in ORIENTATIONS:
        raise PlanError('bad_request', "orientation 'fixed' ya da 'free' olmalı")
    free = orientation == 'free'
    if free and rpy_deg is not None:
        raise PlanError('bad_request', "orientation:'free' ile rpy_deg birlikte verilemez")
    q0 = np.asarray(q0, float)
    pos = _finite(pos_m, 3, 'pos_m')
    _check_zone(model, pos, 100.0)
    start = model.tcp(q0)
    rot = start[:3, :3] if rpy_deg is None else matrix_from_wpr_display(*_finite(rpy_deg, 3, 'rpy_deg'))
    samples = _pose_samples(start, make_transform(rot, pos))
    qs = [q0]
    for k, target in enumerate(samples[1:], start=1):
        at = 100.0 * k / (len(samples) - 1)
        q = solve_ik_multi(model, target, qs[-1], position_only=free)
        if q is None:
            raise PlanError('unreachable', 'Hedefe (ya da yolundaki bir noktaya) eklem sınırları içinde '
                            'ulaşılamıyor', at)
        if float(np.max(np.abs(q - qs[-1]))) > MAX_JOINT_JUMP_RAD:
            raise PlanError('path_blocked', 'Doğrusal yolda eklem sıçraması (tekilliğe yakın): hareket '
                            'reddedildi; eklem-uzayı hareketine otomatik geçilmez', at)
        qs.append(q)
    path = np.vstack(qs)
    _check_path(model, path)
    scale = model.scale_for(speed_pct)
    lin = float(np.linalg.norm(pos - start[:3, 3]))
    ang = 0.0 if free else float(np.linalg.norm(rotvec_from_matrix(start[:3, :3].T @ rot)))
    variation = np.sum(np.abs(np.diff(path, axis=0)), axis=0)
    t_joint = duration_for(variation, model.vmax, model.amax, scale,
                           accel_scale=model.accel_scale_for(speed_pct))
    t_tcp = max(lin / (LINEAR_SPEED_M_S * scale), ang / (ANGULAR_SPEED_RAD_S * scale)) / (1.0 - RAMP)
    return Plan('linear', path, max(t_joint, t_tcp, MIN_DURATION_S), scale)


def violations_for(planner, *args) -> Tuple[List[dict], Optional[Plan]]:
    """``/api/move/check`` gövdesi: ihlal listesi (boşsa geçerli) ve plan."""
    try:
        plan = planner(*args)
    except PlanError as exc:
        return [{'kind': exc.kind, 'at_pct': round(exc.at_pct, 1), 'message': exc.message}], None
    return [], plan
