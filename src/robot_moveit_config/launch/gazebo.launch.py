"""MoveIt + ros2_control on Gazebo physics (same controllers/MoveIt config as demo.launch.py).

    ros2 launch robot_moveit_config gazebo.launch.py                  # headless server (CI default)
    ros2 launch robot_moveit_config gazebo.launch.py headless:=false  # with the Gazebo GUI
    ros2 launch robot_moveit_config gazebo.launch.py use_rviz:=true

The robot description is generated with use_gazebo:=true, which swaps mock_components for
gz_ros2_control and anchors base_link to the world.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, RegisterEventHandler
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node, SetParameter
from launch_ros.substitutions import FindPackageShare
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    moveit_config = (
        MoveItConfigsBuilder('six_axis_robot', package_name='robot_moveit_config')
        .robot_description(mappings={'use_gazebo': 'true'})
        .to_moveit_configs()
    )
    headless = LaunchConfiguration('headless')
    use_rviz = LaunchConfiguration('use_rviz')

    ld = LaunchDescription([
        DeclareLaunchArgument('headless', default_value='true',
                              description='true: gz server only (no GUI, no GPU needed)'),
        DeclareLaunchArgument('use_rviz', default_value='false'),
        # Gazebo runs on simulated time; every node started below must follow /clock.
        SetParameter(name='use_sim_time', value=True),
    ])

    gz_args = PythonExpression(
        ["'-r -s empty.sdf' if '", headless, "' == 'true' else '-r empty.sdf'"])
    ld.add_action(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([FindPackageShare('ros_gz_sim'), '/launch/gz_sim.launch.py']),
        launch_arguments={'gz_args': gz_args, 'on_exit_shutdown': 'true'}.items()))

    ld.add_action(Node(
        package='ros_gz_bridge', executable='parameter_bridge', output='screen',
        arguments=['/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock']))

    ld.add_action(Node(
        package='robot_state_publisher', executable='robot_state_publisher', output='both',
        parameters=[moveit_config.robot_description]))

    spawn = Node(
        package='ros_gz_sim', executable='create', output='screen',
        arguments=['-topic', 'robot_description', '-name', 'six_axis_robot'])
    ld.add_action(spawn)

    def spawner(controller):
        return Node(
            package='controller_manager', executable='spawner', output='screen',
            arguments=[controller, '--controller-manager', '/controller_manager',
                       '--controller-manager-timeout', '120'])

    # Controllers can only be loaded once the model (and its gz_ros2_control plugin) exists.
    ld.add_action(RegisterEventHandler(OnProcessExit(
        target_action=spawn,
        on_exit=[spawner('joint_state_broadcaster'), spawner('arm_controller')])))

    # Same parameters as moveit_configs_utils.launches.generate_move_group_launch.
    ld.add_action(Node(
        package='moveit_ros_move_group', executable='move_group', output='screen',
        parameters=[moveit_config.to_dict(), {
            'publish_robot_description_semantic': True,
            'allow_trajectory_execution': True,
            'publish_planning_scene': True,
            'publish_geometry_updates': True,
            'publish_state_updates': True,
            'publish_transforms_updates': True,
            'monitor_dynamics': False,
        }]))

    ld.add_action(Node(
        package='rviz2', executable='rviz2', output='log', condition=IfCondition(use_rviz),
        arguments=['-d', PathJoinSubstitution(
            [FindPackageShare('robot_moveit_config'), 'config', 'moveit.rviz'])],
        parameters=[moveit_config.robot_description, moveit_config.robot_description_semantic,
                    moveit_config.planning_pipelines, moveit_config.robot_description_kinematics,
                    moveit_config.joint_limits]))
    return ld
