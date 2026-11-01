"""Browser view of the running simulation (needs gazebo.launch.py world:=workspace
perception:=true).

    ros2 launch robot_viz web_view.launch.py                    # read-only, http://127.0.0.1:8080
    ros2 launch robot_viz web_view.launch.py commands:=true     # + white-listed POST /api/command
    ros2 launch robot_viz web_view.launch.py host:=0.0.0.0      # inside a container, see compose

Read-only unless commands:=true; see docs/INTERFACES.md for the endpoints.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node, SetParameter
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('host', default_value='127.0.0.1',
                              description='address to bind; 0.0.0.0 only inside a container whose '
                                          'published port is restricted to 127.0.0.1'),
        DeclareLaunchArgument('port', default_value='8080'),
        DeclareLaunchArgument('commands', default_value='false',
                              description='true: enable POST /api/command (white-listed robot '
                                          'commands, one at a time)'),
        SetParameter(name='use_sim_time', value=True),
        Node(package='robot_viz', executable='web_view', output='screen',
             parameters=[{'host': ParameterValue(LaunchConfiguration('host'), value_type=str),
                          'port': ParameterValue(LaunchConfiguration('port'), value_type=int),
                          'commands': ParameterValue(LaunchConfiguration('commands'),
                                                     value_type=bool)}]),
    ])
