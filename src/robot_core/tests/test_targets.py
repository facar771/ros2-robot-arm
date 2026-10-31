import pytest

from robot_core import TargetError, TargetParser


@pytest.fixture
def parser(config):
    return TargetParser(config)


def test_named_pose_case_insensitive(parser, config):
    target = parser.parse(['CNC'])
    assert target.label == 'cnc'
    assert target.values == config.poses['cnc']


def test_six_numbers(parser):
    target = parser.parse('0.1 0 0 0 0 0'.split())
    assert target.values == (0.1, 0, 0, 0, 0, 0)
    assert target.label is None


@pytest.mark.parametrize('tokens, message', [
    (['nope'], 'Hedef:'),
    ([], 'Hedef:'),
    ('0 0 0'.split(), 'Hedef:'),
    ('0 x 0 0 0 0'.split(), 'Hatalı sayı'),
    ('0 2 0 0 0 0'.split(), 'joint_2'),
])
def test_invalid_input(parser, tokens, message):
    with pytest.raises(TargetError, match=message):
        parser.parse(tokens)
