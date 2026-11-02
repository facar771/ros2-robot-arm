"""Tracking world: a 3-DoF camera platform and a red target (robot_sim/worlds/tracking.sdf).

    ros2 launch robot_vision_ros tracking.launch.py            # headless, tracking node idle
    ros2 launch robot_vision_ros tracking.launch.py headless:=false

Bridges: /clock, /camera/{image,depth_image,camera_info}, /platform/joint_states, the three
/platform/<joint>/cmd_vel rate commands and the world's set_pose service
(/world/tracking/set_pose, ros_gz_interfaces/srv/SetEntityPose). ``tracking_node`` starts in mode
'idle' (rates zero); `ros2 topic pub --once /tracking/mode std_msgs/msg/String "{data: track}"`
makes it follow the red target. Everything runs on simulated time.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, SetParameter
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    headless = LaunchConfiguration('headless')
    world = os.path.join(get_package_share_directory('robot_sim'), 'worlds', 'tracking.sdf')
    gz_args = PythonExpression([
        "('-r -s ' if '", headless, "' == 'true' else '-r ') + '", world, "'",
        " + (' --headless-rendering' if '", headless, "' == 'true' else '')"])
    joints = ('yaw', 'slide', 'pitch')
    bridge_args = (
        ['/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock',
         '/camera/image@sensor_msgs/msg/Image[gz.msgs.Image',
         '/camera/depth_image@sensor_msgs/msg/Image[gz.msgs.Image',
         '/camera/camera_info@sensor_msgs/msg/CameraInfo[gz.msgs.CameraInfo',
         '/platform/joint_states@sensor_msgs/msg/JointState[gz.msgs.Model',
         '/world/tracking/set_pose@ros_gz_interfaces/srv/SetEntityPose']
        + [f'/platform/{j}_joint/cmd_vel@std_msgs/msg/Float64]gz.msgs.Double' for j in joints])
    return LaunchDescription([
        DeclareLaunchArgument('headless', default_value='true',
                              description='true: no GUI, software rendering friendly'),
        DeclareLaunchArgument('tracker', default_value='true',
                              description='start tracking_node (mode idle until told otherwise)'),
        SetParameter(name='use_sim_time', value=True),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                [FindPackageShare('ros_gz_sim'), '/launch/gz_sim.launch.py']),
            launch_arguments={'gz_args': gz_args, 'on_exit_shutdown': 'true'}.items()),
        Node(package='ros_gz_bridge', executable='parameter_bridge', output='screen',
             arguments=bridge_args),
        Node(package='robot_vision_ros', executable='tracking_node', output='screen',
             condition=IfCondition(LaunchConfiguration('tracker'))),
    ])
