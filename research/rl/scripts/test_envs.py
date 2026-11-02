"""Ortam duman testi: gymnasium check_env + belirlenimcilik + DR ve gözlem boyutları."""
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")
from gymnasium.utils.env_checker import check_env  # noqa: E402

from rl_arm.envs import make_env  # noqa: E402

for task in ("reach", "obstacle", "track"):
    for dr in (0.0, 1.0):
        e = make_env(task, dr_level=dr, seed=0)
        check_env(e, skip_render_check=True)
        a = [make_env(task, dr, seed=0) for _ in range(2)]
        trajs = []
        for env in a:
            o, _ = env.reset(seed=5)
            rng = np.random.default_rng(1)
            for _ in range(20):
                o, *_ = env.step(rng.uniform(-1, 1, 6))
            trajs.append(o)
        assert np.allclose(trajs[0], trajs[1]), "aynı tohum aynı yörünge vermeli"
        print("OK", task, "dr", dr, "obs", o.shape)
