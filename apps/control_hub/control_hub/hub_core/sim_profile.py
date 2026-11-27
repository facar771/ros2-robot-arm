"""SİM profil geçersiz kılma (P4b): kayıtlı TASLAK profil SİM arka ucuna GEÇİCİ yüklenir.

* YALNIZ bellekte. Gerçek robota, Robot API'ye ve depodaki profil dosyasına hiçbir şey yazılmaz;
  Hub yeniden başlayınca gerçek profil döner. ``mode=real`` arka uçlarda bu giriş YOKTUR.
* Değişenler: eklem limitleri, hız/ivme tavanları, güvenli kutu, home. ``SimModel`` ve jog motoru
  YENİDEN kurulur; jog çerçeveleri/küreleri korunur (aynı depo nesneleri).
* Reddedilenler: ACİL DUR (423 ``estop_latched``), çalışan hareket ya da jog (409 ``busy``),
  eklem listesi farklı profil (422). Robot yeni limit/kutunun DIŞINDA kalırsa SİM home'a alınır.
* ``sim_backend.py`` K12 (< 400 satır) sınırında kalsın diye jog kurulumu (``build_jog``) da burada.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.jogapi import JogApiConfig, JogController

from control_hub.hub_core.sim_plan import (
    PCT_MAX,
    SCALE_MAX,
    SimModel,
    check_pct,
    check_scale,
    speed_limits_doc,
)

Reply = Tuple[int, Dict[str, Any]]
JOG_SPEED_SCALE = 0.2            # jog hız ölçeği (robot_viz varsayılanı); profil tavanıyla kıstırılır
LIMIT_EPS = 1e-9
JOG_STEP_RAD = 0.1               # jogda TEK tikte en büyük eklem adımı (sıçramayı önleyen iç sınır)
JOG_DT_REF = 0.04                # bu sınır için varsayılan tik aralığı (nominal 0,02 s'nin iki katı)
BADGE = 'TASLAK PROFİL (SİM)'
OVERRIDE_NOTE = f'{BADGE}: limitler, güvenli kutu ve hızlar taslaktan; gerçek profil değil'


class _Joints:
    """JointPort: ``JogController`` ölçülen eklemleri buradan okur."""

    def __init__(self, sim: Any) -> None:
        self._sim = sim

    def joint_positions(self) -> Optional[Dict[str, float]]:
        return self._sim.joint_dict()


class _Sink:
    """SetpointPort: SİM'de kontrolcü ilk hedef noktaya bir tick'te ulaşır (gecikme/izleme hatası yok)."""

    def __init__(self, sim: Any) -> None:
        self._sim = sim

    def send(self, names, positions, times_s) -> None:
        self._sim.set_joints(positions[0])

    def hold(self, names, position) -> None:
        return None


def build_jog(sim: Any) -> JogController:
    """Jog denetleyicisi; limit/kutu ``sim.sm`` ve ``sim.profile``'dan; profil değişince yeniden kurulur."""
    cfg = JogConfig(speed_scale=min(JOG_SPEED_SCALE, sim.sm.velocity_cap))
    engine = DlsJogEngine(sim.sm.chain, sim.profile.safety, cfg)
    api_cfg = JogApiConfig(hold_enabled=True, hold_remote_enabled=False)
    sim.jog_gain = 1.0                           # yeni denetleyici varsayılan tavanlarla başlar
    return JogController(engine, sim.sm.chain, _Joints(sim), _Sink(sim), sim.estop, sim.frames,
                         spheres=sim.spheres, config=api_cfg, jog_config=cfg, busy=sim._job_running,
                         clock=sim._clock)


def inside(model: SimModel, q: np.ndarray) -> bool:
    """``q`` modelin eklem aralığında VE uç noktası güvenli kutuda mı."""
    if not (np.all(q >= model.lower - LIMIT_EPS) and np.all(q <= model.upper + LIMIT_EPS)):
        return False
    tcp = tuple(float(v) for v in model.tcp(q)[:3, 3])
    return model.profile.safety.zone.check(tcp) is None


class ProfileOverrideMixin:
    """``SimBackend``'in profil geçersiz kılma girişi. Kilit sırası: jog -> sim (``sim_backend`` ile aynı)."""

    override: Optional[Dict[str, Any]] = None        # etkinse {'base': gerçek Profile, 'info': özet}

    def profile_override_public(self) -> Optional[Dict[str, Any]]:
        """Anlık görüntüye girecek KÜÇÜK özet (etkin değilse None)."""
        o = self.override
        return None if o is None else dict(o['info'])

    def profile_override_gate(self) -> Optional[Reply]:
        if self.estop.is_set:
            return self._err(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        busy = self.jog.active                       # sim kilidi dışında: kilit sırası jog -> sim
        with self._lock:
            if busy or self._job_running():
                return self._err(409, 'busy', 'Hareket ya da jog sürerken profil değiştirilemez')
        return None

    def _install_profile(self, profile: Any) -> Tuple[bool, Any]:
        """Kilit ALTINDA çağrılır: ``(home'a alındı mı, eski jog)``. Eski jog kilit dışında kapatılmalı."""
        model = SimModel.from_profile(profile)
        home = np.array([profile.initial_positions[n] for n in profile.joint_names], float)
        rehomed = not inside(model, self._q)
        self.profile, self.sm, self.home_q = profile, model, home
        if rehomed:
            self._q = home.copy()
        old, self.jog = self.jog, build_jog(self)
        return rehomed, old

    def profile_override_apply(self, profile: Any, info: Dict[str, Any]) -> Reply:
        """Doğrulanmış taslak profili SİM'e yükler. ``info``: ``robot, saved_at, changes, fields`` (özet)."""
        if list(profile.joint_names) != list(self.profile.joint_names):
            return self._err(422, 'joint_mismatch', 'Taslağın eklem listesi SİM profiliyle aynı değil')
        blocked = self.profile_override_gate()
        if blocked is not None:
            return blocked
        with self._lock:
            if self._job_running():              # kapıdan sonra hareket başlamış olabilir: yeniden bak
                return self._err(409, 'busy', 'Hareket ya da jog sürerken profil değiştirilemez')
            base = self.override['base'] if self.override else self.profile
            rehomed, old = self._install_profile(profile)
            stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
            self.override = {'base': base, 'info': {**info, 'active': True, 'applied_at': stamp,
                                                    'rehomed': rehomed}}
            pub = dict(self.override['info'])
        old.close()
        return 200, {'ok': True, 'override': pub, 'rehomed': rehomed}

    def profile_override_reset(self) -> Reply:
        """Gerçek profile döner (etkin değilse zararsız: ``noop``)."""
        blocked = self.profile_override_gate()
        if blocked is not None:
            return blocked
        with self._lock:
            if self.override is None:
                return 200, {'ok': True, 'override': None, 'rehomed': False, 'noop': True}
            if self._job_running():
                return self._err(409, 'busy', 'Hareket ya da jog sürerken profil değiştirilemez')
            rehomed, old = self._install_profile(self.override['base'])
            self.override = None
        old.close()
        return 200, {'ok': True, 'override': None, 'rehomed': rehomed, 'noop': False}


def jog_gain(sim: Any, pct: float) -> Tuple[float, float]:
    """``(uygulanan, istenen)`` jog kazancı. İstenen = max(1, %/100) × max(1, tavan ölçeği); uygulanan,
    tek tikte
    en hızlı eklemin ``JOG_STEP_RAD``'ı aşmayacağı değerle kıstırılır (sınırsız sayı kabul edilir, ama jog
    her tikte küçük adımla ilerler: çıldırma/sıçrama olmaz). Kazanç 1 = P1d'ye kadarki davranış."""
    want = max(1.0, float(pct) / 100.0) * max(1.0, sim.sm.velocity_cap)
    eng = sim.jog.engine
    base = float(np.max(sim.sm.vmax)) * min(eng.config.speed_scale, 1.0)
    room = JOG_STEP_RAD / (max(base, 1e-9) * JOG_DT_REF)
    return min(want, max(1.0, room)), want


def apply_jog_gain(sim: Any, gain: float) -> None:
    """Jog hız tavanlarını ``gain`` katına çıkarır (robot_core'a dokunmadan: yapılandırma + denetleyici
    tavanı).
    Kilit: denetleyicinin kendi kilidi. Kazanç 1 = varsayılan."""
    jog, eng = sim.jog, sim.jog.engine
    base = JogConfig()                                   # varsayılan tavanlar (0,10 m/s, 0,5 rad/s)
    with jog._lock:
        eng.config = replace(eng.config, max_linear_speed=base.max_linear_speed * gain,
                             max_angular_speed=base.max_angular_speed * gain)
        eng.chain.velocity[:] = sim.sm.vmax * gain
        v = np.array([eng.config.max_linear_speed] * 3 + [eng.config.max_angular_speed] * 3)
        jog._vmax = v * eng.config.speed_scale
        jog._vmax_sphere = np.array([jog._vmax[3], jog._vmax[3], jog._vmax[0], 0.0, 0.0, 0.0])
        sim.jog_gain = gain


class SpeedMixin:
    """``SimBackend``'in hız/ivme ucu (P1d/P1e): ``set_speed``, ``speed_limits``, ``set_speed_scale``,
    ``jog_speed_hint``. SİM'de hız %, hız ölçeği ve ivme ölçeği SINIRSIZDIR; tek sınır ``check_pct`` /
    ``check_scale`` (sonlu, > 0, % <= PCT_MAX, ölçek <= SCALE_MAX). Hiçbiri diske yazılmaz (Hub yeniden
    başlayınca varsayılan döner); konum limitleri (eklem, güvenli kutu) yine zorlanır."""

    jog_gain: float = 1.0

    def set_speed(self, pct: Any) -> Reply:
        why = check_pct(pct)
        if why is not None:
            return self._err(400, 'bad_request', why, max=PCT_MAX)
        self.speed_pct = float(pct) if float(pct) != int(pct) else int(pct)
        return 200, {'ok': True, 'speed_pct': self.speed_pct}

    def speed_limits(self, pct: Any = None) -> Reply:
        """``pct`` verilmezse robotun genel hızı; kart kendi gösterdiği % ile sorar (hesap yine sunucuda)."""
        why = None if pct is None else check_pct(pct)
        if why is not None:
            return self._err(400, 'bad_request', why, max=PCT_MAX)
        with self._lock:
            sm, pct = self.sm, self.speed_pct if pct is None else pct
        doc = speed_limits_doc(sm, pct, default_scale=self.profile.safety.max_velocity_scale)
        gain, want = jog_gain(self, pct)
        doc['jog'] = {'gain': round(gain, 3), 'requested_gain': round(want, 3), 'capped': gain < want - 1e-9}
        return 200, {'ok': True, 'robot': self.profile.robot, 'mode': 'sim', 'scale_locked': False, **doc}

    def set_speed_scale(self, pct: Any, scale: Any, accel_scale: Any = None,
                        accel_linked: Any = None) -> Reply:
        """Hız ölçeği, ivme ölçeği (``accel_scale``; ``accel_linked`` doğruysa hız ölçeğiyle birlikte) ve
        isteğe bağlı ``pct``; biri geçersizse HİÇBİRİ uygulanmaz. Bir SONRAKİ komutta geçerli."""
        checks = [(pct, check_pct), (scale, check_scale), (accel_scale, check_scale)]
        for value, check in checks:
            why = None if value is None else check(value)
            if why is not None:
                return self._err(400, 'bad_request', why, max=PCT_MAX if check is check_pct else SCALE_MAX)
        if accel_linked is not None and not isinstance(accel_linked, bool):
            return self._err(400, 'bad_request', 'accel_linked doğru/yanlış olmalı')
        if accel_linked and accel_scale is not None:
            return self._err(400, 'bad_request', 'accel_linked açıkken accel_scale verilemez')
        with self._lock:
            vc = self.sm.velocity_cap if scale is None else float(scale)
            linked = accel_linked
            if linked is None:
                linked = accel_scale is None and self.sm.accel_linked
            cap_a = None if linked else float(accel_scale if accel_scale is not None else
                                              (self.sm.accel_cap if self.sm.accel_cap is not None else vc))
            self.sm = replace(self.sm, velocity_cap=vc, accel_cap=cap_a)
            self.jog.engine.safety = replace(self.jog.engine.safety, max_velocity_scale=min(vc, 1.0))
            if pct is not None:
                self.speed_pct = float(pct) if float(pct) != int(pct) else int(pct)
            apply_jog_gain(self, jog_gain(self, self.speed_pct)[0])
        return 200, {'ok': True, 'speed_pct': self.speed_pct, 'scale': vc,
                     'accel_scale': vc if cap_a is None else cap_a, 'accel_linked': cap_a is None}

    def jog_speed_hint(self, pct: Any) -> None:
        """Basılı-tut jog gövdesindeki ``speed_pct`` ipucu: o %'ye uygun kazancı kurar (komut başına)."""
        gain = jog_gain(self, pct)[0]
        if abs(gain - self.jog_gain) > 1e-9 and not self.jog.active:
            apply_jog_gain(self, gain)

    def jog_command(self, body: Any, *, remote: bool = False) -> Reply:
        """``speed_pct`` ipucunu ayıkla (robot_core bilmez), kazancı kur, jog'u iletir."""
        if isinstance(body, dict) and 'speed_pct' in body:
            body = dict(body)
            self.jog_speed_hint(body.pop('speed_pct'))
        return self._jog_command(body, remote=remote)