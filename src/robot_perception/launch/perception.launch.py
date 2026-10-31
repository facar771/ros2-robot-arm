"""Colour-object detection on the simulated camera (needs gazebo.launch.py world:=workspace).

    ros2 launch robot_perception perception.launch.py

Publishes /perception/objects (visualization_msgs/MarkerArray, frame 'world'); see
docs/INTERFACES.md.
"""
from launch import LaunchDescription
from launch_ros.actions import Node, SetParameter


def generate_launch_description():
    return LaunchDescription([
        SetParameter(name='use_sim_time', value=True),
        Node(package='robot_perception', executable='perception_node', output='screen'),
    ])
