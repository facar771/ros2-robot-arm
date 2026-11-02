"""runs/*/result.json + results/baselines.json -> results/summary.md, results/*.png, results/summary.json
Birincil metrik: reach=başarı, obstacle=çarpışmasız başarı, track=kuyruk başarısı (son yarıda <5 cm oranı).
Anlamlılık: Welch t-testi + Mann-Whitney (5 tohum => düşük güç; p>=0.05 ise 'fark anlamlı değil')."""
import glob
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
from scipy import stats

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
RES.mkdir(exist_ok=True)
PRIM = {"reach": "success", "obstacle": "safe_success", "track": "tail_success"}
PRIM_TR = {"reach": "başarı (3 cm)", "obstacle": "çarpışmasız başarı", "track": "kuyruk başarısı (<5 cm)"}
COL = {"sac": "#2a78d6", "td3": "#eb6834", "ppo": "#1baf7a"}
NAME = {"sac": "SAC", "td3": "TD3", "ppo": "PPO"}
CLASSIC = {"reach": "klasik-IK+RRTConnect", "obstacle": "klasik-IK+RRTConnect", "track": "klasik-DLS-hız"}
INK, INK2 = "#0b0b0b", "#52514e"

runs = {}
for f in sorted(glob.glob(str(ROOT / "runs/*/result.json"))):
    r = json.load(open(f))
    a = r["args"]
    if a["dr"] != 0.0 or a.get("tag"):
        continue
    runs.setdefault((a["task"], a["algo"]), []).append(r)
base = json.load(open(RES / "baselines.json"))

def bl(task, dr, key, policy=None):
    return base[f"{task}|{policy or CLASSIC[task]}|dr{dr}"][key]

lines = ["# Sonuç özeti (otomatik üretildi: scripts/aggregate.py)\n"]
summ = {}
for task in ("reach", "obstacle", "track"):
    k = PRIM[task]
    lines.append(f"\n## {task} — birincil metrik: {PRIM_TR[task]}\n")
    lines.append("| algoritma | n tohum | adım | DR=0 | DR=0.5 | DR=1 | ort. mesafe DR=0 (cm) | eğitim (dk) | ilk ≥%50 (adım) | eşit adımda eğri* |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    vals = {}
    for algo in ("sac", "td3", "ppo"):
        rs = runs.get((task, algo), [])
        if not rs:
            continue
        v = {dr: np.array([r["final"][f"dr{dr}"][k] for r in rs]) for dr in (0.0, 0.5, 1.0)}
        vals[algo] = v
        md = np.array([r["final"]["dr0.0"]["mean_dist"] for r in rs]) * 100
        tt = np.mean([r["train_time_s"] for r in rs]) / 60
        first = []
        for r in rs:
            hit = [c["timesteps"] for c in r["curve"] if c[k] >= 0.5]
            first.append(hit[0] if hit else np.nan)
        fs = f"{np.nanmedian(first):.0f} ({int(np.sum(~np.isnan(first)))}/{len(rs)})" if np.any(~np.isnan(first)) else f"ulaşılamadı (0/{len(rs)})"
        OFF = {"reach": 60000, "obstacle": 80000, "track": 80000}[task]
        eq = np.array([[c[k] for c in r["curve"] if c["timesteps"] <= OFF][-1] for r in rs])
        f = lambda x: f"{x.mean():.2f} ± {x.std(ddof=1) if len(x) > 1 else 0:.2f}"  # noqa: E731
        lines.append(f"| {NAME[algo]} | {len(rs)} | {rs[0]['args']['steps']} | {f(v[0.0])} | {f(v[0.5])} | {f(v[1.0])} | "
                     f"{md.mean():.1f} ± {md.std(ddof=1) if len(md) > 1 else 0:.1f} | {tt:.1f} | {fs} | {eq.mean():.2f} ± {eq.std(ddof=1) if len(eq)>1 else 0:.2f} |")
        summ[f"{task}|{algo}"] = {str(d): v[d].tolist() for d in v}
    cl = CLASSIC[task]
    lines.append(f"| **{cl}** (öğrenme yok) | – | – | {bl(task,0.0,k):.2f} | {bl(task,0.5,k):.2f} | {bl(task,1.0,k):.2f} | "
                 f"{bl(task,0.0,'mean_dist')*100:.1f} | ~0 | – | – |")
    if task != "track":
        o = "klasik-DLS-hız"
        lines.append(f"| {o} | – | – | {bl(task,0.0,k,o):.2f} | {bl(task,0.5,k,o):.2f} | {bl(task,1.0,k,o):.2f} | {bl(task,0.0,'mean_dist',o)*100:.1f} | ~0 | – | – |")
    lines.append("\n*eşit adımda eğri: aynı ortam-adımı bütçesinde (off-policy bütçesi) 20 bölümlük değerlendirme noktası; PPO'nun 5x fazla adımla kazandığı yanılgısını önlemek için.\n")
    # ikili anlamlılık (DR=0)
    algos = list(vals)
    lines.append("\nİkili karşılaştırma (DR=0 birincil metrik; Welch p / Mann-Whitney p):\n")
    for i in range(len(algos)):
        for j in range(i + 1, len(algos)):
            a, b = vals[algos[i]][0.0], vals[algos[j]][0.0]
            if len(a) > 1 and len(b) > 1:
                pt = stats.ttest_ind(a, b, equal_var=False).pvalue if (a.std() + b.std()) > 0 else float("nan")
                pm = stats.mannwhitneyu(a, b).pvalue
                verdict = "anlamlı" if (pt < 0.05 and pm < 0.05) else "ANLAMLI DEĞİL"
                lines.append(f"- {NAME[algos[i]]} vs {NAME[algos[j]]}: {a.mean():.2f} vs {b.mean():.2f}, p={pt:.3f} / {pm:.3f} → {verdict}")
    for algo in algos:
        a = vals[algo][0.0]
        lines.append(f"- {NAME[algo]} vs klasik ({cl}): RL {a.mean():.2f} ± {a.std(ddof=1) if len(a)>1 else 0:.2f} vs klasik {bl(task,0.0,k):.2f}")

    # --- öğrenme eğrisi ---
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=140)
    for algo in algos:
        rs = runs[(task, algo)]
        T = [c["timesteps"] for c in rs[0]["curve"]]
        Y = np.array([[c[k] for c in r["curve"]] for r in rs])
        m, s = Y.mean(0), Y.std(0)
        ax.plot(T, m, color=COL[algo], lw=2, label=f"{NAME[algo]} (n={len(rs)})")
        ax.fill_between(T, m - s, m + s, color=COL[algo], alpha=0.15, lw=0)
    ax.axhline(bl(task, 0.0, k), color=INK, ls="--", lw=1.5, label=f"{cl}")
    ax.set_xlabel("ortam adımı", color=INK2); ax.set_ylabel(PRIM_TR[task], color=INK2)
    ax.set_ylim(-0.02, 1.05); ax.set_title(f"{task}: öğrenme eğrisi (ort. ± std, 20 değerlendirme bölümü/nokta)", color=INK, fontsize=10)
    ax.grid(alpha=0.2); [ax.spines[s_].set_visible(False) for s_ in ("top", "right")]
    ax.legend(frameon=False, fontsize=8, loc="center right")
    fig.tight_layout(); fig.savefig(RES / f"curve_{task}.png"); plt.close(fig)

    # --- DR dayanıklılığı ---
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=140)
    w = 0.2
    for i, algo in enumerate(algos + ["classic"]):
        if algo == "classic":
            y = [bl(task, d, k) for d in (0.0, 0.5, 1.0)]; e = [0] * 3; c = INK2; lab = cl
        else:
            y = [vals[algo][d].mean() for d in (0.0, 0.5, 1.0)]; e = [vals[algo][d].std() for d in (0.0, 0.5, 1.0)]
            c = COL[algo]; lab = NAME[algo]
        ax.bar(np.arange(3) + (i - 1.5) * w, y, w * 0.92, yerr=e, color=c, label=lab, capsize=2, error_kw=dict(lw=1, ecolor=INK2))
    ax.set_xticks(range(3)); ax.set_xticklabels(["DR=0 (nominal)", "DR=0.5", "DR=1 (kütle±30%, kp±40%, gecikme≤2, gürültü)"], fontsize=8)
    ax.set_ylabel(PRIM_TR[task], color=INK2); ax.set_ylim(0, 1.05)
    ax.set_title(f"{task}: nominalde eğitilmiş politikaların DR altında dayanıklılığı", color=INK, fontsize=10)
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    [ax.spines[s_].set_visible(False) for s_ in ("top", "right")]
    fig.tight_layout(); fig.savefig(RES / f"robust_{task}.png"); plt.close(fig)

json.dump(summ, open(RES / "summary.json", "w"), indent=1)
(RES / "summary.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
