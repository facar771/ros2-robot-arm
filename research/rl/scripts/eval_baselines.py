"""Klasik tabanları (DLS hız kontrolü, IK+RRT-Connect) 3 görev x 3 DR seviyesi x N bölüm değerlendirir.
Kullanım: python scripts/eval_baselines.py [N=100]   -> results/baselines.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rl_arm.envs import make_env  # noqa: E402
from rl_arm.ik import PlannedPolicy, ResolvedRatePolicy, run_episode  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
EVAL_SEED0 = 10_000
out = {}
for task in ("reach", "obstacle", "track"):
    for P in (ResolvedRatePolicy, PlannedPolicy):
        if task == "track" and P is PlannedPolicy:
            continue  # açık çevrim planlayıcı hareketli hedef için anlamsız
        for dr in (0.0, 0.5, 1.0):
            env = make_env(task, dr_level=dr)
            pol = P(env)
            t0 = time.time()
            rs = [run_episode(env, pol, EVAL_SEED0 + i) for i in range(N)]
            agg = {k: float(np.mean([r[k] for r in rs])) for k in rs[0]}
            agg["wall_s_per_ep"] = (time.time() - t0) / N
            if hasattr(pol, "plan_time"):
                agg["last_plan_time"] = pol.plan_time
            out[f"{task}|{P.name}|dr{dr}"] = agg
            print(f"{task:9s} {P.name:22s} dr={dr}: başarı={agg['success']:.2f} güvenli={agg['safe_success']:.2f} "
                  f"son_mesafe={agg['final_dist']:.3f} ort_mesafe={agg['mean_dist']:.3f}")
Path(__file__).resolve().parents[1].joinpath("results").mkdir(exist_ok=True)
json.dump(out, open(Path(__file__).resolve().parents[1] / "results/baselines.json", "w"), indent=1)
