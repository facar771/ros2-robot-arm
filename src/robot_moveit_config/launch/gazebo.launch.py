"""MoveIt + ros2_control on Gazebo physics (same controllers/MoveIt config as demo.launch.py).

    ros2 launch robot_moveit_config gazebo.launch.py                  # headless server (CI default)
    ros2 launch robot_moveit_config gazebo.launch.py headless:=false  # with the Gazebo GUI
    ros2 launch robot_moveit_config gazebo.launch.py use_rviz:=true
    ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true \\
        perception_scene:=true use_rviz:=true rviz_config:=robot_demo.rviz   # camera + objects in RViz
    ros2 launch robot_moveit_config gazebo.launch.py world:=workspace # objects + RGB-D camera
    ros2 launch robot_moveit_config gazebo.launch.py world:=workspace camera_points:=true
    ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
    ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true \\
        perception_scene:=true

world:=workspace adds the world from robot_sim, bridges /camera/{image,depth_image,camera_info}
(and /camera/points with camera_points:=true) and publishes the static TF world -> camera_link
-> camera_optical_frame, all derived from worlds/workspace.sdf. Headless rendering is software
(Mesa) when no GPU is present.

perception:=true (with world:=workspace) also starts robot_perception's colour-object detector,
which publishes /perception/objects. perception_scene:=true (needs perception:=true) also starts
robot_controller's perception_scene_sync, which mirrors the detections into MoveIt's planning scene
as obj_<colour>_<n> collision boxes.

rviz_config:=<name or absolute path> picks the RViz layout (default moveit.rviz = the old behaviour;
robot_demo.rviz adds the camera images, /perception/objects and TF, Fixed Frame world). It only
matters with use_rviz:=true.

The robot description is generated with use_gazebo:=true, which swaps mock_components for
gz_ros2_control and anchors base_link to the world.
"""
import math
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
    RegisterEventHandler,
)
from launch.conditions import IfCondition
from launch.event_handlers import OnProcessExit
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, SetParameter
from launch_ros.substitutions import FindPackageShare
from moveit_configs_utils import MoveItConfigsBuilder

from robot_core.rviz_config import resolve_rviz_config
from robot_core.workspace import load_workspace


def generate_launch_description():
    moveit_config = (
        MoveItConfigsBuilder('six_axis_robot', package_name='robot_moveit_config')
        .robot_description(mappings={'use_gazebo': 'true'})
        .to_moveit_configs()
    )
    headless = LaunchConfiguration('headless')
    use_rviz = LaunchConfiguration('use_rviz')
    world = LaunchConfiguration('world')
    camera_points = LaunchConfiguration('camera_points')
    perception = LaunchConfiguration('perception')
    perception_scene = LaunchConfiguration('perception_scene')
    workspace_world = os.path.join(get_package_share_directory('robot_sim'), 'worlds',
                                   'workspace.sdf')
    camera = load_workspace(workspace_world).camera
    with_camera = IfCondition(PythonExpression(["'", world, "' == 'workspace'"]))

    ld = LaunchDescription([
        DeclareLaunchArgument('headless', default_value='true',
                              description='true: gz server only (no GUI, no GPU needed)'),
        DeclareLaunchArgument('use_rviz', default_value='false'),
        DeclareLaunchArgument('rviz_config', default_value='moveit.rviz',
                              description='RViz layout: file name in robot_moveit_config/config '
                                          '(moveit.rviz, robot_demo.rviz) or an absolute path'),
        DeclareLaunchArgument('world', default_value='empty',
                              description="'empty' (physics only) or 'workspace' (objects + camera)"),
        DeclareLaunchArgument('camera_points', default_value='false',
                              description='also bridge /camera/points (large; off by default)'),
        DeclareLaunchArgument('perception', default_value='false',
                              description='start the colour-object detector (needs world:=workspace)'),
        DeclareLaunchArgument('perception_scene', default_value='false',
                              description='mirror detections into the MoveIt planning scene '
                                          '(needs perception:=true)'),
        # Gazebo runs on simulated time; every node started below must follow /clock.
        SetParameter(name='use_sim_time', value=True),
    ])

    gz_args = PythonExpression([
        "('-r -s ' if '", headless, "' == 'true' else '-r ') + ('empty.sdf' if '", world,
        "' == 'empty' else '", workspace_world, "') + (' --headless-rendering' if ('", headless,
        "' == 'true' and '", world, "' != 'empty') else '')"])
    ld.add_action(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([FindPackageShare('ros_gz_sim'), '/launch/gz_sim.launch.py']),
        launch_arguments={'gz_args': gz_args, 'on_exit_shutdown': 'true'}.items()))

    ld.add_action(Node(
        package='ros_gz_bridge', executable='parameter_bridge', output='screen',
        arguments=['/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock']))

    camera_topics = [
        '/camera/image@sensor_msgs/msg/Image[gz.msgs.Image',
        '/camera/depth_image@sensor_msgs/msg/Image[gz.msgs.Image',
        '/camera/camera_info@sensor_msgs/msg/CameraInfo[gz.msgs.CameraInfo',
    ]
    ld.add_action(Node(
        package='ros_gz_bridge', executable='parameter_bridge', name='camera_bridge',
        output='screen', condition=with_camera, arguments=camera_topics))
    ld.add_action(Node(
        package='ros_gz_bridge', executable='parameter_bridge', name='camera_points_bridge',
        output='screen',
        condition=IfCondition(PythonExpression(
            ["'", world, "' == 'workspace' and '", camera_points, "' == 'true'"])),
        arguments=['/camera/points@sensor_msgs/msg/PointCloud2[gz.msgs.PointCloudPacked']))

    ld.add_action(Node(
        package='robot_perception', executable='perception_node', output='screen',
        condition=IfCondition(PythonExpression(
            ["'", world, "' == 'workspace' and '", perception, "' == 'true'"]))))

    ld.add_action(Node(
        package='robot_controller', executable='perception_scene_sync', output='screen',
        condition=IfCondition(PythonExpression(
            ["'", world, "' == 'workspace' and '", perception, "' == 'true' and '",
             perception_scene, "' == 'true'"]))))

    pose = camera.pose

    def static_tf(parent, child, xyz, rpy):
        return Node(
            package='tf2_ros', executable='static_transform_publisher', output='screen',
            condition=with_camera,
            arguments=['--x', str(xyz[0]), '--y', str(xyz[1]), '--z', str(xyz[2]),
                       '--roll', str(rpy[0]), '--pitch', str(rpy[1]), '--yaw', str(rpy[2]),
                       '--frame-id', parent, '--child-frame-id', child])

    # camera_link: x forward, y left, z up. camera_optical_frame: z forward (image convention).
    ld.add_action(static_tf('world', camera.frame_id, pose.position,
                            (pose.roll, pose.pitch, pose.yaw)))
    ld.add_action(static_tf(camera.frame_id, 'camera_optical_frame', (0, 0, 0),
                            (-math.pi / 2, 0.0, -math.pi / 2)))

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

    def rviz_node(context):
        if not IfCondition(use_rviz).evaluate(context):
            return []
        layout = resolve_rviz_config(
            LaunchConfiguration('rviz_config').perform(context),
            os.path.join(get_package_share_directory('robot_moveit_config'), 'config'))
        return [Node(
            package='rviz2', executable='rviz2', output='log', arguments=['-d', str(layout)],
            parameters=[moveit_config.robot_description, moveit_config.robot_description_semantic,
                        moveit_config.planning_pipelines, moveit_config.robot_description_kinematics,
                        moveit_config.joint_limits])]

    ld.add_action(OpaqueFunction(function=rviz_node))
    return ld
