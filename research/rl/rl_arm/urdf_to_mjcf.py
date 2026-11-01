"""URDF -> MJCF dönüştürücü (yalnızca bu repodaki robot.urdf yapısı için: zincir, revolute, kutu/silindir).

ROS'suz; sadece standart kütüphane. Eklem limitleri, eksenler, origin'ler, kütle, atalet URDF'ten
AYNEN okunur (tutarlılık testi: scripts/test_consistency.py).

Üretilen model:
  - zemin düzlemi (base_link silindirinin altı, z=-0.04)
  - 'ee' site'ı: link_6 + 0.05 m (link_6 silindirinin ucu) -> uç işlevci
  - her eklem için konum (position) aktüatörü; kp/kv parametreleri `ACT_KP`/`ACT_KV`
  - opsiyonel küresel engel (mocap olmayan, statik gövde 'obstacle'; konumu env'de model.body_pos ile ayarlanır)
  - opsiyonel hedef işaretçisi (çarpışmasız mocap site)
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# Aktüatör sertliği: URDF effort=100 N·m sınırı; küçük atalet (link_6 ~1e-4) için kp makul tutuldu.
ACT_KP = 500.0
ACT_KV = 25.0
JOINT_DAMPING = 0.5
JOINT_ARMATURE = 0.01
GRAVCOMP = 0.95  # gerçek kontrolcülerdeki yerçekimi telafisini taklit eder (%5 artık)
EE_OFFSET = 0.05  # link_6 silindir ucu
GROUND_Z = -0.04

DEFAULT_URDF = Path(__file__).resolve().parents[3] / "src/robot_description/urdf/robot.urdf"


def _f(s: str | None, default=0.0):
    return float(s) if s is not None else default


def _vec(s: str | None, default="0 0 0") -> str:
    return " ".join(str(float(x)) for x in (s or default).split())


def _geom_xml(geom_el, origin_el, extra: str = "") -> str:
    pos = _vec(origin_el.get("xyz") if origin_el is not None else None)
    g = geom_el[0]
    if g.tag == "box":
        sx, sy, sz = (float(x) / 2 for x in g.get("size").split())
        return f'<geom type="box" size="{sx} {sy} {sz}" pos="{pos}" {extra}/>'
    if g.tag == "cylinder":
        r, l = float(g.get("radius")), float(g.get("length"))
        return f'<geom type="cylinder" size="{r} {l / 2}" pos="{pos}" {extra}/>'
    raise ValueError(f"desteklenmeyen geometri: {g.tag}")


def build_mjcf(urdf_path: str | Path = DEFAULT_URDF, with_obstacle: bool = True) -> str:
    root = ET.parse(str(urdf_path)).getroot()
    links = {l.get("name"): l for l in root.findall("link")}
    joints = {j.find("child").get("link"): j for j in root.findall("joint")}
    parent_of = {j.find("child").get("link"): j.find("parent").get("link") for j in root.findall("joint")}
    children: dict[str, list[str]] = {}
    for c, p in parent_of.items():
        children.setdefault(p, []).append(c)
    base = next(n for n in links if n not in parent_of)

    def inertial_xml(link) -> str:
        i = link.find("inertial")
        org = _vec(i.find("origin").get("xyz") if i.find("origin") is not None else None)
        m = float(i.find("mass").get("value"))
        ie = i.find("inertia")
        ixx, iyy, izz = (float(ie.get(k)) for k in ("ixx", "iyy", "izz"))
        assert all(abs(float(ie.get(k))) < 1e-12 for k in ("ixy", "ixz", "iyz")), "köşegen olmayan atalet desteklenmiyor"
        return f'<inertial pos="{org}" mass="{m}" diaginertia="{ixx} {iyy} {izz}"/>'

    def visual_color(link) -> str:
        mat = link.find("visual/material")
        return {"gray": "0.5 0.5 0.5 1", "orange": "1 0.5 0 1", "blue": "0 0.5 1 1"}.get(
            mat.get("name") if mat is not None else "", "0.7 0.7 0.7 1")

    lines: list[str] = []

    def body_xml(name: str, depth: int) -> None:
        ind = "  " * depth
        link = links[name]
        j = joints.get(name)
        if j is None:  # taban: sabit
            lines.append(f'{ind}<body name="{name}" pos="0 0 0" gravcomp="{GRAVCOMP}">')
        else:
            lines.append(f'{ind}<body name="{name}" pos="{_vec(j.find("origin").get("xyz"))}" gravcomp="{GRAVCOMP}">')
            lim = j.find("limit")
            lo, hi = float(lim.get("lower")), float(lim.get("upper"))
            lines.append(
                f'{ind}  <joint name="{j.get("name")}" type="hinge" axis="{_vec(j.find("axis").get("xyz"))}" '
                f'range="{lo} {hi}" limited="true" damping="{JOINT_DAMPING}" armature="{JOINT_ARMATURE}" '
                f'actuatorfrcrange="-{float(lim.get("effort"))} {float(lim.get("effort"))}"/>')
        lines.append(f"{ind}  " + inertial_xml(link))
        col = visual_color(link)
        lines.append(f"{ind}  " + _geom_xml(link.find("collision/geometry"), link.find("collision/origin"),
                                              f'name="{name}_geom" rgba="{col}"'))
        if name == "link_6":
            lines.append(f'{ind}  <site name="ee" pos="0 0 {EE_OFFSET}" size="0.012" rgba="1 0 0 1"/>')
        for c in children.get(name, []):
            body_xml(c, depth + 1)
        lines.append(f"{ind}</body>")

    body_xml(base, 2)
    joint_names = [joints[c].get("name") for c in _chain(children, base)]
    act = "\n".join(
        f'    <position name="act_{jn}" joint="{jn}" kp="{ACT_KP}" kv="{ACT_KV}" '
        f'ctrlrange="-3.5 3.5" forcerange="-100 100"/>' for jn in joint_names)
    obst = ('    <body name="obstacle" pos="0 0 -5"><geom name="obstacle_geom" type="sphere" size="0.07" '
            'rgba="0.9 0.1 0.1 0.8" contype="1" conaffinity="1"/></body>\n') if with_obstacle else ""
    return f"""<mujoco model="six_axis_robot">
  <!-- OTOMATİK ÜRETİLDİ: rl_arm/urdf_to_mjcf.py (kaynak: src/robot_description/urdf/robot.urdf) -->
  <compiler angle="radian" inertiafromgeom="false"/>
  <option timestep="0.005" integrator="implicitfast" gravity="0 0 -9.81"/>
  <visual><global offwidth="640" offheight="480"/></visual>
  <worldbody>
    <light pos="0 0 3" dir="0 0 -1"/>
    <geom name="floor" type="plane" size="3 3 0.1" pos="0 0 {GROUND_Z}" rgba="0.9 0.9 0.9 1"/>
{chr(10).join(lines)}
{obst}    <body name="target" mocap="true" pos="0.5 0 0.5">
      <geom type="sphere" size="0.02" rgba="0 1 0 0.5" contype="0" conaffinity="0"/>
    </body>
  </worldbody>
  <actuator>
{act}
  </actuator>
</mujoco>
"""


def _chain(children, base):
    out, cur = [], base
    while children.get(cur):
        cur = children[cur][0]
        out.append(cur)
    return out


def write_mjcf(path: str | Path, urdf_path: str | Path = DEFAULT_URDF) -> Path:
    p = Path(path)
    p.write_text(build_mjcf(urdf_path))
    return p


if __name__ == "__main__":
    out = Path(__file__).with_name("six_axis_robot.xml")
    write_mjcf(out)
    print("yazıldı:", out)
