"""Gerçek robot yığını (Gazebo/ros2_control YOK): robot_state_publisher + move_group + hw_bridge_node.

    ros2 launch robot_hw_bridge hw_stack.launch.py      # Hub köprüsü: host.docker.internal:8770
    ros2 launch robot_hw_bridge hw_stack.launch.py bridge_host:=127.0.0.1 bridge_port:=8771   # selftest-hw

Robot API için ayrıca: ros2 launch robot_viz web_view.launch.py commands:=true mode:=real use_sim_time:=false
Donanım yoksa bu yığını AÇMAYIN; simülasyon için robot_moveit_config launch'ları kullanılır."""
from ament_index_python.packages import get_package_share_directory
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from launch import LaunchDescription


def generate_launch_description():
    moveit = get_package_share_directory('robot_moveit_config')

    def include(name):
        return IncludeLaunchDescription(PythonLaunchDescriptionSource(f'{moveit}/launch/{name}'))

    def arg(name, default):
        return DeclareLaunchArgument(name, default_value=default)

    def param(name, kind):
        return ParameterValue(LaunchConfiguration(name), value_type=kind)

    return LaunchDescription([
        arg('bridge_host', 'host.docker.internal'), arg('bridge_port', '8770'),
        arg('robot_api_url', 'http://127.0.0.1:8080'), arg('watchdog_ms', '300'),
        arg('loop_stall_ms', '250'),
        include('rsp.launch.py'),
        include('move_group.launch.py'),
        Node(package='robot_hw_bridge', executable='hw_bridge_node', output='screen',
             parameters=[{'bridge_host': param('bridge_host', str),
                          'bridge_port': param('bridge_port', int),
                          'robot_api_url': param('robot_api_url', str),
                          'watchdog_ms': param('watchdog_ms', int),
                          'loop_stall_ms': param('loop_stall_ms', int)}]),
    ])
