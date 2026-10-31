"""Robot model configuration, read from the URDF and SRDF (single source of truth).

Joint names/order, joint limits and named poses are never hard-coded: they are parsed from
``robot.urdf`` and ``six_axis_robot.srdf``, so changing the model cannot silently desynchronise
the CLI tools.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple, Union

from robot_core.models import JointLimit

PathLike = Union[str, Path]
_ACTUATED = ('revolute', 'prismatic')


class ConfigError(ValueError):
    """The URDF/SRDF is missing something the tools rely on."""


@dataclass(frozen=True)
class RobotConfig:
    joint_names: Tuple[str, ...]
    limits: Mapping[str, JointLimit]
    poses: Mapping[str, Tuple[float, ...]]
    planning_group: str = 'robot_arm'
    base_frame: str = 'base_link'

    # ---- construction ----------------------------------------------------
    @classmethod
    def from_files(cls, urdf_path: PathLike, srdf_path: PathLike,
                   planning_group: str = 'robot_arm') -> 'RobotConfig':
        urdf = ET.parse(str(urdf_path)).getroot()
        srdf = ET.parse(str(srdf_path)).getroot()
        base_frame, joint_names, limits = cls._read_urdf(urdf)
        cls._check_group(srdf, planning_group, joint_names)
        poses = cls._read_poses(srdf, planning_group, joint_names)
        config = cls(tuple(joint_names), limits, poses, planning_group, base_frame)
        for pose_name, values in poses.items():
            errors = config.validate(values)
            if errors:
                raise ConfigError(f"SRDF pozu '{pose_name}' limit dışı: {'; '.join(errors)}")
        return config

    @staticmethod
    def _read_urdf(urdf: ET.Element) -> Tuple[str, List[str], Dict[str, JointLimit]]:
        links = {link.get('name') for link in urdf.findall('link')}
        joints = urdf.findall('joint')
        children = {j.find('child').get('link') for j in joints}
        roots = sorted(links - children)
        if len(roots) != 1:
            raise ConfigError(f'URDF tek köklü olmalı, bulunan kökler: {roots}')
        by_parent: Dict[str, List[ET.Element]] = {}
        for j in joints:
            by_parent.setdefault(j.find('parent').get('link'), []).append(j)

        names: List[str] = []
        limits: Dict[str, JointLimit] = {}
        link = roots[0]
        while link in by_parent:
            if len(by_parent[link]) != 1:
                raise ConfigError(f"'{link}' dallanıyor; yalnızca seri zincir destekleniyor")
            joint = by_parent[link][0]
            if joint.get('type') in _ACTUATED:
                lim = joint.find('limit')
                if lim is None or lim.get('lower') is None or lim.get('upper') is None:
                    raise ConfigError(f"{joint.get('name')}: <limit lower/upper> eksik")
                vel = lim.get('velocity')
                limits[joint.get('name')] = JointLimit(
                    joint.get('name'), float(lim.get('lower')), float(lim.get('upper')),
                    float(vel) if vel is not None else None)
                names.append(joint.get('name'))
            link = joint.find('child').get('link')
        if not names:
            raise ConfigError('URDF içinde tahrikli eklem yok')
        return roots[0], names, limits

    @staticmethod
    def _check_group(srdf: ET.Element, group: str, joint_names: Sequence[str]) -> None:
        elem = srdf.find(f"group[@name='{group}']")
        if elem is None:
            raise ConfigError(f"SRDF içinde '{group}' grubu yok")
        listed = [j.get('name') for j in elem.findall('joint')]
        if listed and set(listed) != set(joint_names):
            raise ConfigError(
                f"SRDF grubu '{group}' ile URDF eklemleri uyuşmuyor: {sorted(listed)} != "
                f'{sorted(joint_names)}')

    @staticmethod
    def _read_poses(srdf: ET.Element, group: str,
                    joint_names: Sequence[str]) -> Dict[str, Tuple[float, ...]]:
        poses: Dict[str, Tuple[float, ...]] = {}
        for state in srdf.findall('group_state'):
            if state.get('group') != group:
                continue
            values = {j.get('name'): float(j.get('value')) for j in state.findall('joint')}
            missing = [n for n in joint_names if n not in values]
            if missing:
                raise ConfigError(f"Poz '{state.get('name')}' için eklem değeri eksik: {missing}")
            poses[state.get('name').lower()] = tuple(values[n] for n in joint_names)
        return poses

    # ---- queries ---------------------------------------------------------
    @property
    def dof(self) -> int:
        return len(self.joint_names)

    def validate(self, values: Sequence[float]) -> List[str]:
        """Human readable limit violations (empty list means OK)."""
        if len(values) != self.dof:
            return [f'{self.dof} eklem değeri gerekli, {len(values)} verildi']
        errors = []
        for name, value in zip(self.joint_names, values):
            lim = self.limits[name]
            if not lim.lower <= value <= lim.upper:
                errors.append(f'{name}={value} limit dışı [{lim.lower}, {lim.upper}]')
        return errors
