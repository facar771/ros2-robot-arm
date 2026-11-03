"""Generator: robot profile -> URDF/xacro, joint limits, SRDF, safe zone, controller files.

    python3 -m robot_core.profile_gen            # (re)write the generated files in the source tree
    python3 -m robot_core.profile_gen --check    # exit 1 if any generated file is out of date

The generated files are committed and start with a "generated, do not edit" header; a pytest
(``test_profile_gen.py``) fails when they no longer match the profile, so the profile and the files
the planner reads cannot drift apart. ROS-free (pure Python + PyYAML).

Not generated (stay hand-written): the xacro wrapper (``world``/mount joint, Gazebo plugin),
``kinematics.yaml``, ``pilz_cartesian_limits.yaml``, launch files, RViz configs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

from robot_core.profile import Joint, Link, Profile, load_profile

SRC = Path(__file__).resolve().parents[2]            # .../src
PROFILE_PATH = SRC / 'robot_description' / 'profile' / 'six_axis_robot.yaml'
PROFILE_REL = 'src/robot_description/profile/six_axis_robot.yaml'
CONTROLLER_UPDATE_RATE = 100                         # Hz, ros2_control manager


def output_paths(robot: str) -> Dict[str, str]:
    """Generated file key -> path relative to ``src``."""
    cfg = 'robot_moveit_config/config'
    return {
        'urdf': 'robot_description/urdf/robot.urdf',
        'ros2_control_xacro': f'{cfg}/{robot}.ros2_control.xacro',
        'joint_limits': f'{cfg}/joint_limits.yaml',
        'srdf': f'{cfg}/{robot}.srdf',
        'safe_zone': f'{cfg}/safe_zone.yaml',
        'initial_positions': f'{cfg}/initial_positions.yaml',
        'ros2_controllers': f'{cfg}/ros2_controllers.yaml',
        'moveit_controllers': f'{cfg}/moveit_controllers.yaml',
    }


def f(value: float) -> str:
    """Shortest text that reads back as the same float (never '-0.0')."""
    value = float(value)
    return '0.0' if value == 0 else repr(value)


def vec(values: Sequence[float]) -> str:
    return ' '.join(f(v) for v in values)


HEADER = ('ÜRETİLDİ: {profile} dosyasından `python3 -m robot_core.profile_gen` ile üretildi. '
          'ELLE DÜZENLEME: profili düzenle, yeniden üret (pytest güncel mi diye denetler).')


def _xml_header() -> str:
    return f'<!-- {HEADER.format(profile=PROFILE_REL)} -->'


def _yaml_header() -> str:
    return f'# {HEADER.format(profile=PROFILE_REL)}'


# ---- URDF -------------------------------------------------------------------------------------
def _geometry(link: Link) -> str:
    if link.shape == 'cylinder':
        return f'<cylinder radius="{f(link.dims[0])}" length="{f(link.dims[1])}"/>'
    return f'<box size="{vec(link.dims)}"/>'


def _link_xml(link: Link) -> str:
    origin = f'<origin xyz="{vec(link.origin)}"/>'
    geometry = f'<geometry>{_geometry(link)}</geometry>'
    ixx, iyy, izz = link.inertia
    return (f'  <link name="{link.name}">\n'
            f'    <visual>{geometry}<material name="{link.material}"/>{origin}</visual>\n'
            f'    <collision>{geometry}{origin}</collision>\n'
            f'    <!-- placeholder mass; inertia = solid primitive of this shape and mass '
            f'(replace with real CAD values) -->\n'
            f'    <inertial>{origin}<mass value="{f(link.mass)}"/><inertia ixx="{f(ixx)}" ixy="0" '
            f'ixz="0" iyy="{f(iyy)}" iyz="0" izz="{f(izz)}"/></inertial>\n'
            f'  </link>\n')


def _joint_xml(joint: Joint) -> str:
    return (f'  <joint name="{joint.name}" type="revolute">\n'
            f'    <parent link="{joint.parent}"/><child link="{joint.child}"/>'
            f'<origin xyz="{vec(joint.origin)}"/><axis xyz="{vec(joint.axis)}"/>\n'
            f'    <limit lower="{f(joint.lower)}" upper="{f(joint.upper)}" '
            f'effort="{f(joint.effort)}" velocity="{f(joint.velocity)}"/>\n'
            f'  </joint>\n')


def gen_urdf(p: Profile) -> str:
    parts = ['<?xml version="1.0"?>\n', _xml_header() + '\n', f'<robot name="{p.robot}">\n']
    for name, rgba in p.materials.items():
        parts.append(f'  <material name="{name}"><color rgba="{vec(rgba)}"/></material>\n')
    parts.append(
        f'\n  <!-- {p.links[0].name} needs an <inertial>: without it Gazebo (sdformat) lumps the '
        f'link into the world and the first joint loses its parent. The xacro wrapper '
        f'(robot_moveit_config) puts a massless `world` link above it, so KDL never sees an '
        f'inertia on the root link. -->\n')
    for i, link in enumerate(p.links):
        parts.append('\n' + _link_xml(link))
        if i < len(p.joints):
            parts.append(_joint_xml(p.joints[i]))
    parts.append('</robot>\n')
    return ''.join(parts)


# ---- ros2_control xacro -----------------------------------------------------------------------
def gen_ros2_control_xacro(p: Profile) -> str:
    joints = ''.join(
        f'            <joint name="{j.name}">\n'
        f'                <command_interface name="position"/>\n'
        f'                <state_interface name="position">\n'
        f'                  <param name="initial_value">'
        f'${{initial_positions[\'{j.name}\']}}</param>\n'
        f'                </state_interface>\n'
        f'                <state_interface name="velocity"/>\n'
        f'            </joint>\n' for j in p.joints)
    return (
        '<?xml version="1.0"?>\n' + _xml_header() + '\n'
        '<robot xmlns:xacro="http://www.ros.org/wiki/xacro">\n'
        f'    <xacro:macro name="{p.robot}_ros2_control" '
        'params="name initial_positions_file use_gazebo:=false">\n'
        '        <xacro:property name="initial_positions" '
        'value="${xacro.load_yaml(initial_positions_file)[\'initial_positions\']}"/>\n\n'
        '        <ros2_control name="${name}" type="system">\n'
        '            <hardware>\n'
        '                <!-- Default: mock hardware (no physics). '
        'use_gazebo:=true: Gazebo physics. '
        'Neither works\n                     on real hardware. -->\n'
        '                <xacro:if value="${use_gazebo}">\n'
        '                    <plugin>gz_ros2_control/GazeboSimSystem</plugin>\n'
        '                </xacro:if>\n'
        '                <xacro:unless value="${use_gazebo}">\n'
        '                    <plugin>mock_components/GenericSystem</plugin>\n'
        '                </xacro:unless>\n'
        '            </hardware>\n'
        f'{joints}\n'
        '        </ros2_control>\n'
        '    </xacro:macro>\n'
        '</robot>\n')


# ---- YAML files -------------------------------------------------------------------------------
def gen_joint_limits(p: Profile) -> str:
    out = [_yaml_header(),
           '# Position limits live in the URDF (same profile values); this file adds velocity and',
           '# acceleration limits. default_*_scaling = fraction of the maximum used when a request',
           '# does not set its own scaling.',
           f'default_velocity_scaling_factor: {f(p.default_velocity_scaling)}',
           f'default_acceleration_scaling_factor: {f(p.default_acceleration_scaling)}',
           '', 'joint_limits:']
    for j in p.joints:
        out += [f'  {j.name}:', '    has_velocity_limits: true',
                f'    max_velocity: {f(j.velocity)}', '    has_acceleration_limits: true',
                f'    max_acceleration: {f(j.acceleration)}']
    return '\n'.join(out) + '\n'


def gen_safe_zone(p: Profile) -> str:
    z = p.safety.zone
    rows = [f'  {a}: [{f(lo)}, {f(hi)}]' for a, lo, hi in zip('xyz', z.lower, z.upper)]
    return '\n'.join([
        _yaml_header(),
        '# Safe working box for tool-tip goals + speed cap (read by robot_core.safezone, used by',
        '# Skills and the MoveIt adapter). A Cartesian goal whose target is outside the box is',
        '# refused with an explicit error and the robot does not move. Only the *target* is',
        "# checked; the path is MoveIt's collision checking. Named joint poses (home) are not",
        '# checked.',
        f'frame: {z.frame}', 'box:               # metres, world frame', *rows,
        f'max_velocity_scale: {f(p.safety.max_velocity_scale)}   # upper bound for MoveIt '
        'velocity/acceleration scaling, 0 < scale <= 1', ''])


def gen_initial_positions(p: Profile) -> str:
    rows = [f'  {n}: {f(v)}' for n, v in p.initial_positions.items()]
    return '\n'.join([_yaml_header(), "# Initial positions of the ros2_control fake/sim system",
                      'initial_positions:', *rows, ''])


def gen_ros2_controllers(p: Profile) -> str:
    joints = '\n'.join(f'      - {n}' for n in p.joint_names)
    return (f'{_yaml_header()}\n# This config file is used by ros2_control\n'
            'controller_manager:\n  ros__parameters:\n'
            f'    update_rate: {CONTROLLER_UPDATE_RATE}  # Hz\n\n'
            '    arm_controller:\n'
            '      type: joint_trajectory_controller/JointTrajectoryController\n\n'
            '    joint_state_broadcaster:\n'
            '      type: joint_state_broadcaster/JointStateBroadcaster\n\n'
            'arm_controller:\n  ros__parameters:\n    joints:\n'
            f'{joints}\n'
            '    command_interfaces:\n      - position\n    state_interfaces:\n      - position\n'
            '      - velocity\n    allow_nonzero_velocity_at_trajectory_end: true\n')


def gen_moveit_controllers(p: Profile) -> str:
    joints = '\n'.join(f'      - {n}' for n in p.joint_names)
    return (f'{_yaml_header()}\n# MoveIt uses this configuration for controller management\n\n'
            'moveit_controller_manager: '
            'moveit_simple_controller_manager/MoveItSimpleControllerManager\n\n'
            'moveit_simple_controller_manager:\n  controller_names:\n    - arm_controller\n\n'
            '  arm_controller:\n    type: FollowJointTrajectory\n    joints:\n'
            f'{joints}\n'
            '    action_ns: follow_joint_trajectory\n    default: true\n')


# ---- SRDF -------------------------------------------------------------------------------------
def gen_srdf(p: Profile) -> str:
    group = p.planning_group
    states = ''.join(
        f'    <group_state name="{name}" group="{group}">\n'
        + ''.join(f'        <joint name="{j}" value="{f(v)}"/>\n'
                  for j, v in zip(p.joint_names, values))
        + '    </group_state>\n' for name, values in p.named_poses.items())
    acm = [(a, b, 'Adjacent') for a, b in p.adjacent_pairs()] + list(p.allow_extra)
    pairs = ''.join(f'    <disable_collisions link1="{a}" link2="{b}" reason="{r}"/>\n'
                    for a, b, r in acm)
    mount, parent, child = p.mount
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n' + _xml_header() + '\n'
        f'<robot name="{p.robot}">\n'
        f'    <group name="{group}">\n'
        + ''.join(f'        <joint name="{n}"/>\n' for n in p.joint_names)
        + '    </group>\n'
        '    <!--GROUP STATES: named joint poses (profile: moveit.named_poses)-->\n'
        f'{states}'
        '    <!--VIRTUAL JOINT: fixes the robot base to the world frame-->\n'
        f'    <virtual_joint name="{mount}" type="fixed" parent_frame="{parent}" '
        f'child_link="{child}"/>\n'
        '    <!--DISABLE COLLISIONS: parent-child links overlap at their joint and are always '
        'disabled;\n         anything else is checked unless the profile lists it under '
        'moveit.collision.allow_extra\n         (with a reason). Without the adjacent entries '
        'every state is reported as self-collision.-->\n'
        f'{pairs}'
        '</robot>\n')


GENERATORS = {
    'urdf': gen_urdf, 'ros2_control_xacro': gen_ros2_control_xacro,
    'joint_limits': gen_joint_limits, 'srdf': gen_srdf, 'safe_zone': gen_safe_zone,
    'initial_positions': gen_initial_positions, 'ros2_controllers': gen_ros2_controllers,
    'moveit_controllers': gen_moveit_controllers,
}


def generate(profile: Profile) -> Dict[str, str]:
    """Generated file key -> text."""
    return {key: gen(profile) for key, gen in GENERATORS.items()}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python3 -m robot_core.profile_gen')
    parser.add_argument('--check', action='store_true',
                        help='write nothing; exit 1 if a generated file is out of date')
    parser.add_argument('--profile', type=Path, default=PROFILE_PATH)
    parser.add_argument('--src', type=Path, default=SRC, help='the repository src/ directory')
    args = parser.parse_args(argv)
    profile = load_profile(args.profile)
    paths = output_paths(profile.robot)
    stale = []
    for key, text in generate(profile).items():
        path = args.src / paths[key]
        current = path.read_text(encoding='utf-8') if path.exists() else None
        if current == text:
            print(f'güncel   {paths[key]}')
            continue
        stale.append(paths[key])
        if args.check:
            print(f'ESKİ     {paths[key]}')
        else:
            path.write_text(text, encoding='utf-8')
            print(f'yazıldı  {paths[key]}')
    if args.check and stale:
        print('\nProfil ile üretilen dosyalar uyuşmuyor; yeniden üret: python3 -m '
              'robot_core.profile_gen')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
