"""Plugin SDK + manager (ROS-free). See ``docs/INTERFACES.md`` -> "Plugin v1"."""
from robot_plugins.context import (
    PermissionDenied,
    PluginBase,
    PluginContext,
    Stopped,
    StopToken,
)
from robot_plugins.manager import ManagerError, PluginManager
from robot_plugins.manifest import Manifest, ManifestError, Tool, load_manifest, parse_manifest
from robot_plugins.robot_api import (
    FakeRobotApi,
    HttpRobotApi,
    RobotApi,
    RobotApiError,
    RobotBusy,
    RobotLocked,
    RobotRefused,
)

__all__ = [
    'PermissionDenied', 'PluginBase', 'PluginContext', 'Stopped', 'StopToken', 'ManagerError',
    'PluginManager', 'Manifest', 'ManifestError', 'Tool', 'load_manifest', 'parse_manifest',
    'FakeRobotApi', 'HttpRobotApi', 'RobotApi', 'RobotApiError', 'RobotBusy', 'RobotLocked',
    'RobotRefused',
]
