"""Klasik taban çizgileri (öğrenme yok):

* dls_ik            : damped-least-squares IK (yalnız konum, 3 görev boyutu; boşluk uzayında duruş çekimi)
* ResolvedRatePolicy: kapalı çevrim DLS hız kontrolü (+ hareketli hedef için ileri besleme)
* PlannedPolicy     : MoveIt benzeri: DLS-IK hedef konfigürasyon + RRT-Connect (çarpışma kontrollü) +
                      kısayol yumuşatma + hız limitli izleme
Hepsi env ile AYNI eylem uzayında çalışır (eklem konum artışı, V_MAX sınırı).
"""
from __future__ import annotations

import time

import mujoco
import numpy as np

from .envs import CTRL_DT, HOME_Q, SUCCESS_TOL, V_MAX, ArmEnv, load_model


def _jac(env: ArmEnv, q: np.ndarray):
    env.dk.qpos[:] = q
    mujoco.mj_kinematics(env.mk, env.dk)
    mujoco.mj_comPos(env.mk, env.dk)
    jacp = np.zeros((3, env.mk.nv))
    mujoco.mj_jacSite(env.mk, env.dk, jacp, None, env.ee_id)
    return env.dk.site_xpos[env.ee_id].copy(), jacp


def dls_step(env: ArmEnv, q, target_vel_cmd, lam=0.05, q_ref=HOME_Q, k_null=0.5):
    """Görev uzayı hız komutu -> eklem hızı (DLS + boşluk uzayı duruş çekimi)."""
    _, J = _jac(env, q)
    JJt = J @ J.T + lam**2 * np.eye(3)
    Jp = J.T @ np.linalg.inv(JJt)
    dq = Jp @ target_vel_cmd
    null = (np.eye(6) - Jp @ J) @ (k_null * (q_ref - q))
    return dq + null


def dls_ik(env: ArmEnv, target, q0, iters=200, tol=2e-3, lam=0.05, q_ref=HOME_Q):
    q = q0.copy()
    for _ in range(iters):
        p, J = _jac(env, q)
        e = target - p
        if np.linalg.norm(e) < tol:
            break
        dq = dls_step(env, q, 1.0 * e / 1.0, lam=lam, q_ref=q_ref, k_null=0.05)
        q = np.clip(q + 0.7 * dq, env.jlo, env.jhi)
    return q, float(np.linalg.norm(target - env.fk(q)))


class ResolvedRatePolicy:
    """a = clip(dq / (V_MAX*dt)); dq = DLS(K*e + v_hedef)."""
    name = "klasik-DLS-hız"

    def __init__(self, env: ArmEnv, gain=4.0, lam=0.06):
        self.env, self.gain, self.lam = env, gain, lam

    def reset(self):
        pass

    def act(self, obs):
        env = self.env
        q = env.q_cmd
        e = env.target - env.fk(q)  # komut konfigürasyonundan (ölçülen q'dan değil): kararlı
        v_ff = env.tgt_vel if env.task == "track" else 0.0
        dq = dls_step(env, q, self.gain * e + v_ff, lam=self.lam, k_null=0.3)
        return np.clip(dq / V_MAX, -1, 1)


class PlannedPolicy:
    """IK -> RRT-Connect -> yumuşatma -> hız limitli yürütme (açık çevrim, MoveIt/OMPL benzeri)."""
    name = "klasik-IK+RRTConnect"

    def __init__(self, env: ArmEnv, margin=0.025, max_samples=4000, seed=0):
        self.env, self.margin = env, margin
        self.max_samples = max_samples
        self.rng = np.random.default_rng(seed)
        self.mc = load_model()
        self.dc = mujoco.MjData(self.mc)
        self.path: list[np.ndarray] = []
        self.plan_time = 0.0
        self.plan_ok = False
        self.arm_geoms = set(range(self.mc.ngeom))

    # -- çarpışma -------------------------------------------------------
    def _sync_obstacle(self):
        m, e = self.mc, self.env
        m.body_pos[e.obst_body] = e.m.body_pos[e.obst_body]
        m.geom_size[e.obst_geom][0] = e.obst_r + self.margin

    def in_collision(self, q) -> bool:
        m, d = self.mc, self.dc
        d.qpos[:] = q
        mujoco.mj_forward(m, d)
        for i in range(d.ncon):
            c = d.contact[i]
            if d.contact[i].dist < 0:
                return True
        return False

    def edge_free(self, a, b, step=0.08) -> bool:
        n = max(2, int(np.ceil(np.linalg.norm(b - a) / step)))
        for s in np.linspace(0, 1, n + 1)[1:]:
            if self.in_collision(a + s * (b - a)):
                return False
        return True

    # -- RRT-Connect ----------------------------------------------------
    def _rrt_connect(self, qa, qb, tmax=3.0):
        env = self.env
        Ta, Tb = [qa], [qb]
        Pa, Pb = [-1], [-1]
        t0 = time.time()
        swapped = False

        def nearest(T, q):
            return int(np.argmin(np.linalg.norm(np.asarray(T) - q, axis=1)))

        def extend(T, P, q, eta=0.3):
            i = nearest(T, q)
            v = q - T[i]
            L = np.linalg.norm(v)
            qn = q if L <= eta else T[i] + v / L * eta
            if self.edge_free(T[i], qn):
                T.append(qn)
                P.append(i)
                return len(T) - 1, np.allclose(qn, q)
            return None, False

        for _ in range(self.max_samples):
            if time.time() - t0 > tmax:
                break
            qr = self.rng.uniform(env.jlo, env.jhi) if self.rng.random() > 0.1 else (Tb if not swapped else Ta)[-1]
            ia, _ = extend(Ta, Pa, qr)
            if ia is not None:
                while True:
                    ib, reached = extend(Tb, Pb, Ta[ia])
                    if ib is None:
                        break
                    if reached:
                        pa, i = [], ia
                        while i != -1:
                            pa.append(Ta[i]); i = Pa[i]
                        pb, i = [], ib
                        while i != -1:
                            pb.append(Tb[i]); i = Pb[i]
                        path = pa[::-1] + pb
                        return path[::-1] if swapped else path
            Ta, Tb, Pa, Pb = Tb, Ta, Pb, Pa
            swapped = not swapped
        return None

    def _shortcut(self, path, iters=60):
        path = [p.copy() for p in path]
        for _ in range(iters):
            if len(path) < 3:
                break
            i, j = sorted(self.rng.integers(0, len(path), 2))
            if j - i < 2:
                continue
            if self.edge_free(path[i], path[j]):
                path = path[: i + 1] + path[j:]
        return path

    # -- politika -------------------------------------------------------
    def reset(self):
        env = self.env
        self._sync_obstacle()
        t0 = time.time()
        q0 = env.q_cmd.copy()
        best = None
        for k in range(8):  # çoklu başlangıçlı IK, çarpışmasız çözüm ara
            qs = q0 if k == 0 else np.clip(self.rng.uniform(env.jlo * 0.8, env.jhi * 0.8), env.jlo, env.jhi)
            q, err = dls_ik(env, env.target, qs, iters=300)
            if err < 3e-3 and not self.in_collision(q):
                best = q
                break
        self.path, self.plan_ok = [q0], False
        if best is not None:
            path = (self._rrt_connect(q0, best) if not self.edge_free(q0, best) else [q0, best])
            if path is not None:
                self.path = self._shortcut(path)
                self.plan_ok = True
        self.plan_time = time.time() - t0
        self.idx = 1

    def act(self, obs):
        env = self.env
        if not self.plan_ok or self.idx >= len(self.path):
            return np.zeros(6)
        goal = self.path[self.idx]
        dq = goal - env.q_cmd
        # eklem başına hız limiti (V_MAX*dt) altında en büyük ölçekle ilerle
        mx = np.max(np.abs(dq)) / (V_MAX * CTRL_DT)
        if mx < 1.0:
            self.idx += 1
            return np.clip(dq / (V_MAX * CTRL_DT), -1, 1)
        return dq / (V_MAX * CTRL_DT) / mx


def run_episode(env: ArmEnv, policy, seed: int):
    obs, _ = env.reset(seed=seed)
    policy.reset()
    done, ret, info = False, 0.0, {}
    dists = []
    while not done:
        obs, r, te, tr, info = env.step(policy.act(obs))
        ret += r
        dists.append(info["dist"])
        done = te or tr
    return dict(success=info["success"], safe_success=info["safe_success"], n_coll=info["n_coll"],
                final_dist=info["dist"], mean_dist=float(np.mean(dists)), steps=env.t, ret=ret,
                tail_success=float(np.mean(np.array(dists[len(dists) // 2:]) < 0.05)))
