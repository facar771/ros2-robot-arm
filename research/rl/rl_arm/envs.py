"""Gymnasium ortamları: ArmReach-v0, ArmReachObstacle-v0, ArmTrack-v0 (MuJoCo, ROS'suz).

Eylem: [-1,1]^6 -> eklem konum komutu artışı = a * V_MAX * dt (V_MAX = URDF hız limiti 1 rad/s).
Komut eklem limitlerine kırpılır; konum aktüatörü komutu izler (yerçekimi telafisi %95).
Kontrol 20 Hz (dt=0.05 s), simülasyon adımı 5 ms.

Alan rastgeleleştirmesi (dr_level in [0,1]): kütle, kp, sönümleme ölçeği, eylem gecikmesi (0-2 adım),
gözlem gürültüsü. dr_level=0 -> nominal model.
"""
from __future__ import annotations

from pathlib import Path

import gymnasium as gym
import mujoco
import numpy as np
from gymnasium import spaces

from .urdf_to_mjcf import ACT_KP, ACT_KV, build_mjcf

V_MAX = 1.0
CTRL_DT = 0.05
N_SUB = 10
HOME_Q = np.array([0.0, 0.6, -1.2, 0.0, 0.6, 0.0])
SUCCESS_TOL = 0.03

_MJCF_CACHE: str | None = None


def load_model() -> mujoco.MjModel:
    global _MJCF_CACHE
    if _MJCF_CACHE is None:
        _MJCF_CACHE = build_mjcf()
    return mujoco.MjModel.from_xml_string(_MJCF_CACHE)


class ArmEnv(gym.Env):
    metadata = {"render_modes": []}
    task = "reach"
    max_steps = 100

    def __init__(self, dr_level: float = 0.0, seed: int | None = None):
        self.m = load_model()
        self.d = mujoco.MjData(self.m)
        self.mk = load_model()  # kinematics (FK/Jacobian) için ayrı örnek
        self.dk = mujoco.MjData(self.mk)
        self.ee_id = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_SITE, "ee")
        self.jlo = self.m.jnt_range[:, 0].copy()
        self.jhi = self.m.jnt_range[:, 1].copy()
        self.obst_body = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_BODY, "obstacle")
        self.obst_geom = mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_GEOM, "obstacle_geom")
        self.obst_r = float(self.m.geom_size[self.obst_geom][0])
        self._nom = dict(mass=self.m.body_mass.copy(), inertia=self.m.body_inertia.copy(),
                         kp=self.m.actuator_gainprm[:, 0].copy(), damping=self.m.dof_damping.copy())
        self.dr_level = dr_level
        self.rng = np.random.default_rng(seed)
        self.action_space = spaces.Box(-1.0, 1.0, (6,), np.float32)
        n = self._obs_dim()
        self.observation_space = spaces.Box(-np.inf, np.inf, (n,), np.float32)
        self.delay = 0
        self.obs_noise = 0.0
        self._buf: list[np.ndarray] = []
        self.q_cmd = HOME_Q.copy()
        self.target = np.zeros(3)
        self.tgt_vel = np.zeros(3)
        self.t = 0
        self.collided = False
        self.n_coll = 0

    # ---- yardımcılar -------------------------------------------------
    def fk(self, q: np.ndarray) -> np.ndarray:
        self.dk.qpos[:] = q
        mujoco.mj_kinematics(self.mk, self.dk)
        return self.dk.site_xpos[self.ee_id].copy()

    def ee(self) -> np.ndarray:
        return self.d.site_xpos[self.ee_id].copy()

    def _obs_dim(self) -> int:
        return 6 + 6 + 6 + 3 + 3 + 3 + (6 if self.task == "obstacle" else 0) + (3 if self.task == "track" else 0)

    def _apply_dr(self):
        lv = self.dr_level
        m, nom, r = self.m, self._nom, self.rng
        ms = 1 + lv * r.uniform(-0.3, 0.3, size=m.nbody)
        m.body_mass[:] = nom["mass"] * ms
        m.body_inertia[:] = nom["inertia"] * ms[:, None]
        kps = 1 + lv * r.uniform(-0.4, 0.4, size=m.nu)
        m.actuator_gainprm[:, 0] = nom["kp"] * kps
        m.actuator_biasprm[:, 1] = -nom["kp"] * kps
        m.dof_damping[:] = nom["damping"] * (1 + lv * r.uniform(-0.5, 1.0, size=m.nv))
        self.delay = int(np.round(lv * r.integers(0, 3)))
        self.obs_noise = lv

    def _sample_q(self, margin: float = 0.85) -> np.ndarray:
        for _ in range(100):
            q = self.rng.uniform(self.jlo * margin, self.jhi * margin)
            p = self.fk(q)
            if p[2] > 0.12 and np.linalg.norm(p[:2]) > 0.12:
                return q
        return HOME_Q.copy()

    def _set_target(self, p: np.ndarray):
        self.target = p.copy()
        self.d.mocap_pos[0] = p

    def _in_obstacle_contact(self) -> bool:
        d, g = self.d, self.obst_geom
        for i in range(d.ncon):
            c = d.contact[i]
            if c.geom1 == g or c.geom2 == g:
                return True
        return False

    def _obs(self) -> np.ndarray:
        q, qd = self.d.qpos.copy(), self.d.qvel.copy()
        ee, tgt = self.ee(), self.target
        if self.obs_noise > 0:
            q = q + self.rng.normal(0, 0.005 * self.obs_noise, 6)
            qd = qd + self.rng.normal(0, 0.03 * self.obs_noise, 6)
            ee = self.fk(q)
        parts = [q / 3.14, np.clip(qd, -5, 5) * 0.5, (self.q_cmd - q) * 2.0, ee, tgt, (tgt - ee) * 3.0]
        if self.task == "obstacle":
            ob = self.m.body_pos[self.obst_body]
            parts += [ob, (ob - ee) * 3.0]
        if self.task == "track":
            parts += [self.tgt_vel]
        return np.concatenate(parts).astype(np.float32)

    # ---- gym API -----------------------------------------------------
    def _reset_task(self):
        self._set_target(self.fk(self._sample_q()))

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        m = self.m
        m.body_mass[:] = self._nom["mass"]
        m.body_inertia[:] = self._nom["inertia"]
        m.actuator_gainprm[:, 0] = self._nom["kp"]
        m.actuator_biasprm[:, 1] = -self._nom["kp"]
        m.dof_damping[:] = self._nom["damping"]
        self.delay, self.obs_noise = 0, 0.0
        if self.dr_level > 0:
            self._apply_dr()
        mujoco.mj_resetData(m, self.d)
        q0 = np.clip(HOME_Q + self.rng.normal(0, 0.1, 6), self.jlo * 0.95, self.jhi * 0.95)
        self.d.qpos[:] = q0
        self.q_cmd = q0.copy()
        self.d.ctrl[:] = q0
        m.body_pos[self.obst_body] = [0, 0, -5]
        self.t = 0
        self.n_coll = 0
        self.tgt_vel = np.zeros(3)
        self._buf = [np.zeros(6)] * self.delay
        self._reset_task()
        mujoco.mj_forward(m, self.d)
        return self._obs(), {}

    def _task_update(self):
        pass

    def step(self, action):
        a = np.clip(np.asarray(action, dtype=np.float64), -1, 1)
        self._buf.append(a)
        a_eff = self._buf.pop(0)
        self.q_cmd = np.clip(self.q_cmd + a_eff * V_MAX * CTRL_DT, self.jlo, self.jhi)
        self.d.ctrl[:] = self.q_cmd
        coll = False
        for _ in range(N_SUB):
            mujoco.mj_step(self.m, self.d)
            if self.task == "obstacle" and not coll:
                coll = self._in_obstacle_contact()
        self.t += 1
        self._task_update()
        mujoco.mj_forward(self.m, self.d)
        dist = float(np.linalg.norm(self.ee() - self.target))
        if coll:
            self.n_coll += 1
        success = dist < SUCCESS_TOL
        reward = -dist - 0.01 * float(a @ a)
        terminated = False
        if self.task == "reach":
            if success:
                reward += 2.0
                terminated = True
        elif self.task == "obstacle":
            reward -= 1.0 if coll else 0.0
            if success:
                reward += 2.0
                terminated = True
        truncated = self.t >= self.max_steps
        info = dict(dist=dist, success=bool(success), collided=bool(coll), n_coll=self.n_coll,
                    safe_success=bool(success and self.n_coll == 0))
        return self._obs(), reward, terminated, truncated, info


class ArmReachEnv(ArmEnv):
    task = "reach"
    max_steps = 100


class ArmReachObstacleEnv(ArmEnv):
    """Başlangıç uç noktası ile hedef arasındaki doğrunun yakınına küresel (r=7 cm) engel."""
    task = "obstacle"
    max_steps = 120

    def _reset_task(self):
        ee0 = self.fk(self.d.qpos)
        for _ in range(200):
            tgt = self.fk(self._sample_q())
            v = tgt - ee0
            L = np.linalg.norm(v)
            if L < 0.35:
                continue
            t = self.rng.uniform(0.4, 0.6)
            off = self.rng.normal(0, 0.02, 3)
            ob = ee0 + t * v + off
            if (np.linalg.norm(ob - tgt) > self.obst_r + 0.10 and np.linalg.norm(ob - ee0) > self.obst_r + 0.15
                    and ob[2] > self.obst_r + GROUND_CLEAR):
                break
        self._set_target(tgt)
        self.m.body_pos[self.obst_body] = ob


GROUND_CLEAR = 0.0


class ArmTrackEnv(ArmEnv):
    """Hareketli hedef: eklem uzayında sinüs (erişilebilir olması garanti), 10 s."""
    task = "track"
    max_steps = 200

    def _reset_task(self):
        self.qc = self._sample_q(0.6)
        self.amp = self.rng.uniform(0.15, 0.45, 6) * np.array([1, 1, 1, 0.5, 0.5, 0.5])
        self.freq = self.rng.uniform(0.08, 0.25, 6) * 2 * np.pi  # rad/s
        self.ph = self.rng.uniform(0, 2 * np.pi, 6)
        self._tgt_at(0.0)
        self._set_target(self._p_now)
        self.tgt_vel = self._v_now.copy()

    def _tgt_at(self, t):
        q = self.qc + self.amp * np.sin(self.freq * t + self.ph)
        qn = self.qc + self.amp * np.sin(self.freq * (t + 0.01) + self.ph)
        self._p_now = self.fk(q)
        self._v_now = (self.fk(qn) - self._p_now) / 0.01

    def _task_update(self):
        self._tgt_at(self.t * CTRL_DT)
        self._set_target(self._p_now)
        self.tgt_vel = self._v_now.copy()


ENVS = {"reach": ArmReachEnv, "obstacle": ArmReachObstacleEnv, "track": ArmTrackEnv}


def make_env(task: str, dr_level: float = 0.0, seed: int | None = None) -> ArmEnv:
    return ENVS[task](dr_level=dr_level, seed=seed)


for _name, _cls in (("ArmReach-v0", ArmReachEnv), ("ArmReachObstacle-v0", ArmReachObstacleEnv),
                    ("ArmTrack-v0", ArmTrackEnv)):
    try:
        gym.register(id=_name, entry_point=_cls)
    except gym.error.Error:
        pass
