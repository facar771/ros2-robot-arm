"""Locates the installed URDF/SRDF and builds the shared RobotConfig."""
import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from robot_core.safezone import SafetyConfig, load_safety

from robot_core import RobotConfig


def load_robot_config() -> RobotConfig:
    """URDF/SRDF paths can be overridden with ROBOT_URDF / ROBOT_SRDF (useful in tests)."""
    urdf = os.environ.get('ROBOT_URDF') or (
        Path(get_package_share_directory('robot_description')) / 'urdf' / 'robot.urdf')
    srdf = os.environ.get('ROBOT_SRDF') or (
        Path(get_package_share_directory('robot_moveit_config')) / 'config'
        / 'six_axis_robot.srdf')
    return RobotConfig.from_files(urdf, srdf)


def load_safety_config() -> SafetyConfig:
    """Safe working box + speed cap (robot_moveit_config/config/safe_zone.yaml). The path can be
    overridden with ROBOT_SAFETY; a missing or malformed file is an error (never "no limits")."""
    path = os.environ.get('ROBOT_SAFETY') or (
        Path(get_package_share_directory('robot_moveit_config')) / 'config' / 'safe_zone.yaml')
    return load_safety(path)
