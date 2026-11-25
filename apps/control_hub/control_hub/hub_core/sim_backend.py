"""SİM arka ucu: profildeki GERÇEK kinematik zincir, eklem limitleri, güvenli kutu ve çalışan hareketler.

``--fake`` yalnız uç noktayı oynatır; burada ``robot_core.kinematics.Chain`` ileri kinematiği,
``DlsJogEngine`` + ``JogController`` jog'u, ``sim_plan`` hareket doğrulamasını (limit, kutu, yol) yapar.
SİMÜLASYONDUR: ölçüm değil, gerçek robotu oynatmaz (``maturity`` ``tahmini``).

Zaman deterministiktir: iş ve jog her ``tick_s``'te ilerler; testlerde ``autostart=False`` + ``SimClock`` ile
``advance(saniye)`` çağrılır (gerçek saat/iş parçacığı yok). ``time_scale`` süreyi çarpar (0.1 = 10× hızlı).
"""
from __future__ import annotations

import logging
import math
import secrets
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from robot_core.frames import FrameError, FrameStore
from robot_core.jog import DlsJogEngine, JogConfig
from robot_core.jogapi import (
    JogApiConfig,
    JogController,
    JogDenied,
    frames_document,
    replace_frames,
    sphere_reach,
)
from robot_core.modelapi import ModelUnavailable, load_model
from robot_core.profile import load_profile
from robot_core.se3 import display_wpr
from robot_core.spheres import SphereStore
from robot_core.supervisor import EStop

from control_hub.hub_core.backend import BackendResult, ResetResult, StopReport
from control_hub.hub_core.commands import CommandError, parse_robot_command
from control_hub.hub_core.sim_assets import SimAssetsError, resolve
from control_hub.hub_core.sim_jobs import Job, SimClock, parse_move
from control_hub.hub_core.sim_plan import PlanError, SimModel, plan_joint, plan_linear, violations_for

log = logging.getLogger('control_hub.sim')
Reply = Tuple[int, Dict[str, Any]]
DEFAULT_SPEED_PCT = 50
GRIPPER_PCT_PER_S = 50.0         # alet açma/kapama hızı (%/s); SİM'e özgü sahte sayı
JOG_SPEED_SCALE = 0.2            # jog hız ölçeği (robot_viz varsayılanı); profil tavanıyla kıstırılır
MAX_JOBS_KEPT = 32
__all__ = ('SimBackend', 'SimClock')
SIM_NOTE = 'SİM: gerçek robot değil; değerler ölçüm değildir (tahmini)'


class _Joints:
    """JointPort: ``JogController`` ölçülen eklemleri buradan okur."""

    def __init__(self, sim: 'SimBackend') -> None:
        self._sim = sim

    def joint_positions(self) -> Optional[Dict[str, float]]:
        return self._sim.joint_dict()


class _Sink:
    """SetpointPort: SİM'de kontrolcü ilk hedef noktaya bir tick'te ulaşır (gecikme/izleme hatası yok)."""

    def __init__(self, sim: 'SimBackend') -> None:
        self._sim = sim

    def send(self, names, positions, times_s) -> None:
        self._sim.set_joints(positions[0])

    def hold(self, names, position) -> None:
        return None


class SimBackend:
    name = 'sim'
    can_interrupt = True
    caps = ('sim',)

    def __init__(self, robot: Optional[str] = None, *, tick_s: float = 0.02, time_scale: float = 1.0,
                 clock: Optional[Callable[[], float]] = None, autostart: bool = True) -> None:
        if not (0 < tick_s <= 0.05 and time_scale > 0):
            raise ValueError('tick_s (0, 0.05] ve time_scale > 0 olmalı')
        try:
            self.robot, self._profile_path, self._model_path = resolve(robot)
            self.profile = load_profile(self._profile_path)
        except SimAssetsError:
            raise
        except Exception as exc:                  # noqa: BLE001 - profil hatası: nedeni adlandır, dosya yolu yazma
            raise SimAssetsError(f'robot profili okunamadı ({type(exc).__name__}): {exc}') from None
        self.sm = SimModel.from_profile(self.profile)
        self.tick_s, self.time_scale = tick_s, time_scale
        self._clock = self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self.estop = EStop()
        self.home_q = np.array([self.profile.initial_positions[n] for n in self.profile.joint_names], float)
        self._q = self.home_q.copy()
        self._gripper = 0.0
        self.speed_pct = DEFAULT_SPEED_PCT
        self._job: Optional[Job] = None
        self._jobs: 'OrderedDict[str, Job]' = OrderedDict()
        self.history: List[str] = []
        self._tmp = Path(tempfile.mkdtemp(prefix='hub_sim_'))     # çerçeveler: kapanınca silinir
        self.frames = FrameStore(self._tmp / 'frames.yaml')
        self.spheres = SphereStore(self._tmp / 'spheres.yaml')
        jog_cfg = JogConfig(speed_scale=min(JOG_SPEED_SCALE, self.sm.velocity_cap))
        engine = DlsJogEngine(self.sm.chain, self.profile.safety, jog_cfg)
        self.jog = JogController(engine, self.sm.chain, _Joints(self), _Sink(self), self.estop, self.frames,
                                 spheres=self.spheres,
                                 config=JogApiConfig(hold_enabled=True, hold_remote_enabled=False),
                                 jog_config=jog_cfg, busy=self._job_running, clock=self._clock)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if autostart:
            self._thread = threading.Thread(target=self._run, name='sim-tick', daemon=True)
            self._thread.start()

    # --- eklem durumu (iş parçacığı güvenli) ---------------------------------------------------------
    def joint_dict(self) -> Dict[str, float]:
        with self._lock:
            return {n: float(v) for n, v in zip(self.profile.joint_names, self._q)}

    def set_joints(self, q) -> None:
        with self._lock:
            self._q = np.clip(np.asarray(q, float), self.sm.lower, self.sm.upper)

    def _job_running(self) -> bool:
        with self._lock:
            return self._job is not None and self._job.state in ('running', 'paused')

    # --- zaman ---------------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.wait(self.tick_s):
            try:
                self.step()
            except Exception:                    # noqa: BLE001 - bir hata simülatörü değil yalnız o tick'i bozar
                log.exception('SİM tick hatası')

    def step(self, jobs_dt: Optional[float] = None) -> None:
        """Bir tick: jog (gerçek saat), sonra iş (``jobs_dt`` sim. saniyesi; varsayılan tick/time_scale)."""
        self.jog.tick()
        self._tick_job(self.tick_s / self.time_scale if jobs_dt is None else jobs_dt)

    def advance(self, seconds: float) -> None:
        """Testler: ``SimClock`` ile saati ``seconds`` ilerletip her tick'i çalıştırır."""
        for _ in range(max(1, math.ceil(seconds / self.tick_s - 1e-9))):
            if isinstance(self._clock, SimClock):
                self._clock.t += self.tick_s
            self.step()

    def _tick_job(self, dt: float) -> None:
        with self._lock:
            job = self._job
            if job is None or job.state != 'running':
                return
            if self.estop.is_set:
                self._finish(job, 'cancelled', 'estop_latched', 'ACİL DUR: hareket kesildi')
                return
            job.t = min(job.duration_s, job.t + dt)
            if job.plan is not None:
                self._q = np.clip(job.plan.q_at(job.t), self.sm.lower, self.sm.upper)
            elif job.target_pct is not None:
                f = job.t / job.duration_s
                self._gripper = job.from_pct + (job.target_pct - job.from_pct) * f
            if job.t >= job.duration_s:
                self._finish(job, 'done', None, 'hedefe varıldı')

    def _finish(self, job: Job, state: str, error: Optional[str], message: str) -> None:
        job.state, job.error, job.message = state, error, message
        if self._job is job:
            self._job = None

    # --- hareket (UI_CONTRACT §9-B) ----------------------------------------------------------------
    def _plan(self, body: Any):
        kind, args, speed = parse_move(body, self.speed_pct)
        with self._lock:
            q = self._q.copy()
        if kind == 'joint':
            return kind, plan_joint(self.sm, q, args[0], speed), None
        if kind == 'linear':
            return kind, plan_linear(self.sm, q, args[0], args[1], args[2], speed), None
        return kind, None, args[0]

    @staticmethod
    def _err(status: int, kind: str, message: str, **extra: Any) -> Reply:
        return status, {'ok': False, 'error': kind, 'message': message, **extra}

    def move_submit(self, body: Any) -> Reply:
        if self.estop.is_set:
            return self._err(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        try:
            kind, plan, tool_pct = self._plan(body)
        except PlanError as exc:
            return self._err(400 if exc.kind == 'bad_request' else 409, exc.kind, exc.message,
                             at_pct=round(exc.at_pct, 1))
        busy = self.jog.active                    # sim kilidi dışında: kilit sırası jog -> sim
        with self._lock:
            if busy or self._job_running():
                return self._err(409, 'busy', 'Başka bir hareket ya da jog sürüyor')
            if plan is not None:
                job = Job(secrets.token_hex(6), kind, plan, plan.duration_s)
            else:
                span = abs(tool_pct - self._gripper)
                job = Job(secrets.token_hex(6), 'tool', None, max(0.2, span / GRIPPER_PCT_PER_S),
                          target_pct=tool_pct, from_pct=self._gripper)
            self._job = job
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS_KEPT:
                self._jobs.popitem(last=False)
        return 202, {'ok': True, 'job_id': job.id}

    def move_check(self, body: Any) -> Reply:
        """Kuru çalıştırma: aynı doğrulama, robot OYNAMAZ."""
        try:
            kind, args, speed = parse_move(body, self.speed_pct)
        except PlanError as exc:
            bad = {'kind': exc.kind, 'at_pct': 0.0, 'message': exc.message}
            return 200, {'ok': False, 'violations': [bad]}
        if kind == 'tool':
            return 200, {'ok': True, 'violations': []}
        with self._lock:
            q = self._q.copy()
        planner = (plan_joint if kind == 'joint' else plan_linear)
        viol, _ = violations_for(planner, self.sm, q, *args, speed)
        return 200, {'ok': not viol, 'violations': viol}

    def _change(self, want: Tuple[str, ...], to: str, message: str) -> Reply:
        with self._lock:
            job = self._job
            if job is None or job.state not in want:
                return self._err(409, 'not_running', 'Çalışan bir hareket yok')
            if to == 'cancelled':
                self._finish(job, 'cancelled', None, message)
            else:
                job.state = to
        return 200, {'ok': True}

    def move_stop(self) -> Reply:
        return self._change(('running', 'paused'), 'cancelled', 'kullanıcı durdurdu')

    def move_pause(self) -> Reply:
        return self._change(('running',), 'paused', '')

    def move_resume(self) -> Reply:
        if self.estop.is_set:
            return self._err(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        return self._change(('paused',), 'running', '')

    def move_get(self, job_id: str) -> Reply:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return self._err(404, 'unknown_job', 'iş bulunamadı')
            return 200, {'ok': True, 'job': job.doc()}

    # --- SİM'e özgü uçlar ----------------------------------------------------------------------------
    def sim_reset(self) -> Reply:
        if self.estop.is_set:
            return self._err(423, 'estop_latched', 'ACİL DUR aktif: önce kilidi aç', state='estopped')
        busy = self.jog.active
        with self._lock:
            if busy or self._job_running():
                return self._err(409, 'busy', 'Hareket ya da jog sürerken sıfırlanamaz')
            self._q = self.home_q.copy()
            self._gripper = 0.0
        return 200, {'ok': True}

    def set_speed(self, pct: Any) -> Reply:
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 1 <= pct <= 100:
            return self._err(400, 'bad_request', 'speed_pct 1-100 arasında bir sayı olmalı')
        self.speed_pct = int(pct)
        return 200, {'ok': True, 'speed_pct': self.speed_pct}

    # --- RobotBackend --------------------------------------------------------------------------------
    def execute(self, command: str, cancel: threading.Event) -> BackendResult:
        self.history.append(command)
        try:
            cmd = parse_robot_command(command)
        except CommandError as exc:
            return BackendResult(False, str(exc), {'rejected': True})
        if cmd.name == 'objects':
            return BackendResult(True, '0 nesne (SİM\'de sahne nesnesi yok)', {'objects': []})
        pose = self.profile.named_poses.get(cmd.name)
        if pose is None:
            return BackendResult(False, f'SİM bu komutu bilmiyor: {cmd.name} (nesne/ortam sahnesi yok)')
        status, doc = self.move_submit({'type': 'joint', 'joints_deg': [math.degrees(v) for v in pose]})
        if status != 202:
            return BackendResult(False, doc.get('message', 'reddedildi'),
                                 {'rejected': True, 'error': doc.get('error')})
        job = self._jobs[doc['job_id']]
        while job.state in ('running', 'paused'):
            if cancel.wait(0.02):
                self.move_stop()
                return BackendResult(False, 'hareket kesildi', {'interrupted': True})
        ok = job.state == 'done'
        text = f'{cmd.name} pozuna gidildi' if ok else (job.message or 'hareket tamamlanamadı')
        return BackendResult(ok, text, {} if ok else {'interrupted': True})

    def emergency_stop(self) -> StopReport:
        was = self._job_running()
        self.estop.trip('Hub ACİL DUR')
        with self._lock:
            if self._job is not None:
                self._finish(self._job, 'cancelled', 'estop_latched', 'ACİL DUR: hareket kesildi')
        return StopReport(True, 'SİM durdu', {'stop_time_s': 0.0, 'was_running': was})

    def reset_emergency_stop(self) -> ResetResult:
        self.estop.reset()                        # home'a OTOMATİK dönmez, iş kendiliğinden devam etmez
        return ResetResult(True, 'sıfırlandı')

    def snapshot(self) -> Dict[str, Any]:
        jog_active = self.jog.active
        jog = self.jog.status() if jog_active else None
        tcp_doc = self.jog.tcp_document() or {}
        with self._lock:
            q, gripper = self._q.copy(), self._gripper
            job = self._job.doc() if self._job else None
            moving = self._job is not None and self._job.state == 'running'
        near = self.sm.near_limit(q)
        zone = self.profile.safety.zone
        pose = self.sm.tcp(q)
        wpr = display_wpr(pose[:3, :3])
        rpy = [round(wpr['w'], 2), round(wpr['p'], 2), round(wpr['r'], 2)]
        pos = [round(float(v), 4) for v in pose[:3, 3]]
        return {
            'name': self.name, 'connected': True, 'can_interrupt': True, 'mode': 'sim',
            'moving': moving or bool((jog or {}).get('moving')),
            'estop': {'tripped': self.estop.is_set, 'reason': None, 'since': None},
            'speed_pct': self.speed_pct, 'maturity': 'tahmini', 'stale': False, 'note': SIM_NOTE,
            'tool': pos, 'gripper': {'open_pct': round(gripper, 1)},
            'tcp': {'frame': 'World', 'position': pos, 'wpr_deg': rpy, 'warning': tcp_doc.get('warning'),
                    'pos_m': pos, 'rpy_deg': rpy},
            'joints': [{'name': n, 'rad': round(float(v), 4), 'deg': round(math.degrees(float(v)), 1),
                        'min_deg': round(math.degrees(float(lo)), 1),
                        'max_deg': round(math.degrees(float(hi)), 1), 'near_limit': nl}
                       for n, v, lo, hi, nl in zip(self.profile.joint_names, q, self.sm.lower,
                                                   self.sm.upper, near)],
            'safe_zone': {'min': list(zone.lower), 'max': list(zone.upper)}, 'jog': jog, 'job': job,
            'objects': [], 'home_deg': [round(math.degrees(float(v)), 1) for v in self.home_q],
        }

    def self_check(self) -> str:
        """``--check-config``: profil yüklü, FK ``robot_core`` ile BİREBİR aynı, doğrulama çalışıyor."""
        from robot_core.kinematics import Chain
        fresh = Chain(load_profile(self._profile_path))
        zero = np.zeros(self.sm.dof)
        if not np.allclose(self.sm.tcp(zero), fresh.fk(zero), atol=1e-12):
            raise ValueError('SİM: sıfır poz FK robot_core ile uyuşmuyor')
        snap = self.snapshot()
        if not np.allclose(snap['tcp']['pos_m'], fresh.fk(self.home_q)[:3, 3], atol=1e-4):
            raise ValueError('SİM: anlık TCP, home FK ile uyuşmuyor')
        status, doc = self.move_check({'type': 'joint', 'joints_deg': [math.degrees(v) for v in self.home_q]})
        if status != 200 or not doc['ok']:
            raise ValueError(f'SİM: home hedefi doğrulamadan geçmedi: {doc}')
        return f'SİM {self.robot}: {self.sm.dof} eklem, FK robot_core ile aynı, home güvenli kutu içinde'

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.jog.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- jog / çerçeveler / model (Hub bunları backend'den ister; jog_routes.py) ------------------
    def jog_command(self, body: Any, *, remote: bool = False) -> Reply:
        try:
            return 200, self.jog.command(body, remote=remote)
        except JogDenied as exc:
            return exc.status, {'ok': False, 'error': exc.message, **exc.extra}

    def jog_status(self) -> Reply:
        return 200, {**self.jog.status(), 'tcp': self.jog.tcp_document()}

    def model(self) -> Reply:
        try:
            return 200, load_model(self._model_path, self.profile.joint_names)
        except ModelUnavailable as exc:
            return 503, {'ok': False, 'error': 'model_unavailable', 'message': str(exc)}

    def frames_get(self) -> Reply:
        return 200, frames_document(self.frames, self.spheres, None)

    def frames_put(self, body: Any) -> Reply:
        if self.jog.active:
            return 409, {'ok': False, 'error': 'jog etkinken çerçeve listesi değiştirilemez'}
        try:
            return 200, replace_frames(self.frames, body, None, self.spheres, None)
        except FrameError as exc:
            return 400, {'ok': False, 'error': str(exc)}

    def frames_reach(self, body: Any) -> Reply:
        try:
            return 200, sphere_reach(self.jog, body)
        except JogDenied as exc:
            return exc.status, {'ok': False, 'error': exc.message, **exc.extra}
