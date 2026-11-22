"""Show a robot model in RViz with sliders for its joints.

    ros2 launch robot_description display.launch.py                     # six-axis robot (as before)
    ros2 launch robot_description display.launch.py robot:=karma_robot  # KUCUK_ROBOT, CAD meshes

six_axis_robot: urdf/robot.urdf, RViz without a config (unchanged behaviour).
Any other robot: profile/generated/<robot>/robot.urdf (robot_core.profile_gen), sliders start at
its `home` pose (profile/generated/<robot>/initial_positions.yaml; the zero pose may lie outside
its joint limits), RViz with rviz/<robot>.rviz when present.
"""
import os

import yaml
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

DEFAULT_ROBOT = 'six_axis_robot'


def _nodes(context):
    robot = LaunchConfiguration('robot').perform(context)
    pkg_share = FindPackageShare(package='robot_description').find('robot_description')
    if robot == DEFAULT_ROBOT:
        urdf_file = os.path.join(pkg_share, 'urdf', 'robot.urdf')
        zeros, rviz_args = {}, []
    else:
        generated = os.path.join(pkg_share, 'profile', 'generated', robot)
        urdf_file = os.path.join(generated, 'robot.urdf')
        if not os.path.isfile(urdf_file):
            raise RuntimeError(f'{robot}: {urdf_file} yok (profile_gen ile üretilip kurulmalı)')
        with open(os.path.join(generated, 'initial_positions.yaml'), encoding='utf-8') as f:
            zeros = dict(yaml.safe_load(f)['initial_positions'])
        rviz_file = os.path.join(pkg_share, 'rviz', f'{robot}.rviz')
        rviz_args = ['-d', rviz_file] if os.path.isfile(rviz_file) else []

    with open(urdf_file, 'r', encoding='utf-8') as infp:
        robot_desc = infp.read()

    jsp_params = [{'zeros': zeros}] if zeros else []
    return [
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            output='screen',
            parameters=[{'robot_description': robot_desc}]
        ),
        Node(
            package='joint_state_publisher_gui',
            executable='joint_state_publisher_gui',
            name='joint_state_publisher_gui',
            parameters=jsp_params,
        ),
        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            output='screen',
            arguments=rviz_args,
        )
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('robot', default_value=DEFAULT_ROBOT,
                              description='six_axis_robot | karma_robot'),
        OpaqueFunction(function=_nodes),
    ])
