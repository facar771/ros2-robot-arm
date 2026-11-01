import pytest

from control_hub.hub_core import CommandError, parse_robot_command


@pytest.mark.parametrize('text,norm', [
    ('home', 'home'), ('  HOME ', 'home'), ('objects', 'objects'),
    ('above green', 'above green'), ('above green 0.25', 'above green 0.25'),
    ('above kırmızı 0,3', 'above red 0.3'), ('Above Mavi 0.30', 'above blue 0.3'),
    ('above red 0', 'above red 0'), ('above red 0.5', 'above red 0.5'),
])
def test_valid(text, norm):
    assert parse_robot_command(text).text() == norm


@pytest.mark.parametrize('text', [
    '', '   ', 'goto 1 2 3', 'move home', 'home now', 'objects x', 'above', 'above green 1 2',
    'above yellow', 'above red 0.51', 'above red -0.1', 'above red abc', 'above red nan',
    'above red inf', 'home; rm -rf /', 'home && ls', 'above red $(x)', 'a' * 65,
    'demo_scenario',      # robotta var ama Hub senaryo adımı olarak izinli değil
])
def test_rejected(text):
    with pytest.raises(CommandError):
        parse_robot_command(text)


def test_non_string():
    with pytest.raises(CommandError):
        parse_robot_command(None)   # type: ignore[arg-type]
