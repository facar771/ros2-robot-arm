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

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.jogapi import JogApiConfig, JogController

from control_hub.hub_core.sim_plan import SimModel

Reply = Tuple[int, Dict[str, Any]]
JOG_SPEED_SCALE = 0.2            # jog hız ölçeği (robot_viz varsayılanı); profil tavanıyla kıstırılır
LIMIT_EPS = 1e-9
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
