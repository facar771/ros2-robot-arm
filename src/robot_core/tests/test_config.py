import pytest

from robot_core import ConfigError, RobotConfig
from tests.conftest import SRDF, URDF


def test_joint_order_follows_kinematic_chain(config):
    assert config.joint_names == tuple(f'joint_{i}' for i in range(1, 7))
    assert config.dof == 6
    assert config.base_frame == 'base_link'


def test_limits_come_from_urdf(config):
    assert (config.limits['joint_2'].lower, config.limits['joint_2'].upper) == (-1.57, 1.57)
    assert (config.limits['joint_3'].lower, config.limits['joint_3'].upper) == (-2.0, 2.0)
    assert config.limits['joint_1'].velocity == 1.0


def test_named_poses_come_from_srdf(config):
    assert set(config.poses) == {'home', 'cnc', 'pc', 'dik'}
    assert config.poses['cnc'] == (0.5, -0.4, 0.4, 0.0, 0.5, 0.0)


def test_all_named_poses_inside_limits(config):
    for name, values in config.poses.items():
        assert config.validate(values) == [], name


def test_validate_reports_each_violation(config):
    errors = config.validate([0, 2.0, 0, 0, 0, 9])
    assert len(errors) == 2
    assert 'joint_2' in errors[0] and 'joint_6' in errors[1]


def test_validate_wrong_length(config):
    assert 'gerekli' in config.validate([0, 0])[0]


def test_srdf_pose_outside_limits_is_rejected(tmp_path):
    bad = SRDF.read_text(encoding='utf-8').replace('<joint name="joint_2" value="-0.4"/>',
                                                  '<joint name="joint_2" value="-3.0"/>')
    srdf = tmp_path / 'bad.srdf'
    srdf.write_text(bad, encoding='utf-8')
    with pytest.raises(ConfigError, match='limit dışı'):
        RobotConfig.from_files(URDF, srdf)


def test_srdf_pose_missing_joint_is_rejected(tmp_path):
    bad = SRDF.read_text(encoding='utf-8').replace('        <joint name="joint_6" value="0.0"/>\n',
                                                  '', 1)
    srdf = tmp_path / 'bad.srdf'
    srdf.write_text(bad, encoding='utf-8')
    with pytest.raises(ConfigError, match='eksik'):
        RobotConfig.from_files(URDF, srdf)


def test_unknown_group_is_rejected():
    with pytest.raises(ConfigError, match='grubu yok'):
        RobotConfig.from_files(URDF, SRDF, planning_group='nope')
