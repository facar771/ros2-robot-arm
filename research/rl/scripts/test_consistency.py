"""URDF <-> MJCF tutarlılık testi (pytest ya da doğrudan: python scripts/test_consistency.py)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rl_arm.envs import load_model  # noqa: E402
from rl_arm.urdf_to_mjcf import DEFAULT_URDF, EE_OFFSET  # noqa: E402

ROOT = ET.parse(DEFAULT_URDF).getroot()
M = load_model()


def _xyz(el, key="xyz"):
    return np.array([float(x) for x in (el.get(key, "0 0 0")).split()]) if el is not None else np.zeros(3)


def test_joint_limits_axes_origins():
    for j in ROOT.findall("joint"):
        name = j.get("name")
        jid = mujoco.mj_name2id(M, mujoco.mjtObj.mjOBJ_JOINT, name)
        assert jid >= 0, name
        lim = j.find("limit")
        np.testing.assert_allclose(M.jnt_range[jid], [float(lim.get("lower")), float(lim.get("upper"))])
        np.testing.assert_allclose(M.jnt_axis[jid], _xyz(j.find("axis")))
        bid = M.jnt_bodyid[jid]
        np.testing.assert_allclose(M.body_pos[bid], _xyz(j.find("origin")))
        np.testing.assert_allclose(M.actuator_forcerange[jid], [-float(lim.get("effort")), float(lim.get("effort"))])


def test_mass_inertia_com():
    tot = 0.0
    for l in ROOT.findall("link"):
        bid = mujoco.mj_name2id(M, mujoco.mjtObj.mjOBJ_BODY, l.get("name"))
        i = l.find("inertial")
        m = float(i.find("mass").get("value"))
        tot += m
        assert abs(M.body_mass[bid] - m) < 1e-12, l.get("name")
        np.testing.assert_allclose(M.body_ipos[bid], _xyz(i.find("origin")))
        ie = i.find("inertia")
        np.testing.assert_allclose(M.body_inertia[bid], [float(ie.get(k)) for k in ("ixx", "iyy", "izz")], rtol=1e-9)
    assert abs(M.body_mass.sum() - tot) < 1e-9  # (engel/hedef gövdeleri kütlesiz)


def _rot(axis, th):
    a = axis / np.linalg.norm(axis)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K


def urdf_fk(q):
    """URDF'ten bağımsız (MuJoCo'suz) numpy FK; uç = link_6 + EE_OFFSET."""
    R, p = np.eye(3), np.zeros(3)
    for k, j in enumerate(ROOT.findall("joint")):
        p = p + R @ _xyz(j.find("origin"))
        R = R @ _rot(_xyz(j.find("axis")), q[k])
    return p + R @ np.array([0, 0, EE_OFFSET])


def test_fk_matches_independent_numpy():
    d = mujoco.MjData(M)
    sid = mujoco.mj_name2id(M, mujoco.mjtObj.mjOBJ_SITE, "ee")
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(200):
        q = rng.uniform(M.jnt_range[:, 0], M.jnt_range[:, 1])
        d.qpos[:] = q
        mujoco.mj_kinematics(M, d)
        worst = max(worst, np.linalg.norm(d.site_xpos[sid] - urdf_fk(q)))
    assert worst < 1e-9, worst
    print(f"  maks FK farkı: {worst:.2e} m")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("OK", n)
