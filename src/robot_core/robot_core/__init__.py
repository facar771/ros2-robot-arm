"""ROS-independent core of the robot arm project.

Nothing in this package imports rclpy or any ROS message type, so everything here is unit-testable
with plain pytest. ROS-specific code lives in ``robot_controller`` and implements the ports defined
in :mod:`robot_core.ports`.
"""
from robot_core.config import ConfigError, RobotConfig
from robot_core.interpreter import CommandInterpreter
from robot_core.models import BoxObstacle, JointLimit, JointTarget, MotionResult
from robot_core.targets import TargetError, TargetParser

__all__ = [
    'BoxObstacle', 'CommandInterpreter', 'ConfigError', 'JointLimit', 'JointTarget',
    'MotionResult', 'RobotConfig', 'TargetError', 'TargetParser',
]
