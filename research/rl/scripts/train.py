"""Tek koşu: python scripts/train.py --algo ppo|sac|td3 --task reach|obstacle|track --seed 0 --steps 150000 [--dr 0.0]
Çıktı: runs/<task>_<algo>_dr<dr>_s<seed>/{result.json,model.zip}
"""
import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch  # noqa: E402
from stable_baselines3 import PPO, SAC, TD3  # noqa: E402
from stable_baselines3.common.env_util import make_vec_env  # noqa: E402
from stable_baselines3.common.noise import NormalActionNoise  # noqa: E402
from stable_baselines3.common.callbacks import BaseCallback  # noqa: E402

from rl_arm.envs import make_env  # noqa: E402
from rl_arm.ik import run_episode  # noqa: E402

EVAL_SEED0 = 10_000  # eğitim tohumlarından ayrı sabit değerlendirme seti
NET = dict(net_arch=[128, 128])


class SB3Policy:
    def __init__(self, model, env):
        self.model, self.env = model, env

    def reset(self):
        pass

    def act(self, obs):
        return self.model.predict(obs, deterministic=True)[0]


def evaluate(model, task, dr, n, seed0=EVAL_SEED0):
    env = make_env(task, dr_level=dr)
    pol = SB3Policy(model, env)
    rs = [run_episode(env, pol, seed0 + i) for i in range(n)]
    return {k: float(np.mean([r[k] for r in rs])) for k in rs[0]}


class CurveCB(BaseCallback):
    def __init__(self, task, every, n_eval=20):
        super().__init__()
        self.task, self.every, self.n_eval, self.next, self.curve = task, every, n_eval, every, []
        self.t0 = time.time()

    def _on_step(self):
        if self.num_timesteps >= self.next:
            self.next += self.every
            r = evaluate(self.model, self.task, 0.0, self.n_eval)
            self.curve.append(dict(timesteps=self.num_timesteps, wall=time.time() - self.t0, **r))
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--algo", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--steps", type=int, default=150_000)
    ap.add_argument("--dr", type=float, default=0.0, help="EĞİTİMDE alan rastgeleleştirme seviyesi")
    ap.add_argument("--lr", type=float, default=None)
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "runs"))
    a = ap.parse_args()
    torch.set_num_threads(1)
    name = f"{a.task}_{a.algo}_dr{a.dr}_s{a.seed}"
    out = Path(a.out) / name
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    mk = lambda: make_env(a.task, dr_level=a.dr)  # noqa: E731
    if a.algo == "ppo":
        venv = make_vec_env(mk, n_envs=8, seed=a.seed)
        model = PPO("MlpPolicy", venv, n_steps=128, batch_size=256, n_epochs=a.epochs, learning_rate=a.lr or 3e-4, gamma=0.98,
                    gae_lambda=0.95, ent_coef=0.0, policy_kwargs=NET, seed=a.seed, device="cpu", verbose=0)
    else:
        venv = make_vec_env(mk, n_envs=1, seed=a.seed)
        kw = dict(learning_rate=a.lr or 1e-3, buffer_size=200_000, learning_starts=2000, batch_size=256, gamma=0.98,
                  tau=0.02, train_freq=1, gradient_steps=1, policy_kwargs=NET, seed=a.seed, device="cpu", verbose=0)
        if a.algo == "sac":
            model = SAC("MlpPolicy", venv, **kw)
        else:
            model = TD3("MlpPolicy", venv, action_noise=NormalActionNoise(np.zeros(6), 0.1 * np.ones(6)), **kw)
    cb = CurveCB(a.task, every=max(a.steps // 10, 1000))
    model.learn(a.steps, callback=cb)
    train_time = time.time() - t0
    res = dict(args=vars(a), train_time_s=train_time, curve=cb.curve, final={})
    for dr in (0.0, 0.5, 1.0):
        res["final"][f"dr{dr}"] = evaluate(model, a.task, dr, 100)
    json.dump(res, open(out / "result.json", "w"), indent=1)
    model.save(out / "model.zip")
    f = res["final"]["dr0.0"]
    print(f"{name}: {train_time:.0f}s  başarı={f['success']:.2f} güvenli={f['safe_success']:.2f} ort_mesafe={f['mean_dist']:.3f}")


if __name__ == "__main__":
    main()
