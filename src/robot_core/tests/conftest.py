from pathlib import Path

import pytest

from robot_core import RobotConfig

SRC = Path(__file__).resolve().parents[2]  # .../src
URDF = SRC / 'robot_description' / 'urdf' / 'robot.urdf'
SRDF = SRC / 'robot_moveit_config' / 'config' / 'six_axis_robot.srdf'
CONFIG_DIR = SRC / 'robot_moveit_config' / 'config'


@pytest.fixture(scope='session')
def config():
    return RobotConfig.from_files(URDF, SRDF)
