import threading

import pytest
from robot_core.webcmd import (
    HEIGHT_DEFAULT,
    USAGE,
    Busy,
    Command,
    CommandError,
    CommandRunner,
    parse_command,
)

from tests.fakes import FakeSkills, FakeTool, RecordingSink


# ---- the white list ----------------------------------------------------------------------------
@pytest.mark.parametrize('text, expected', [
    ('home', Command('home')),
    ('  HOME  ', Command('home')),
    ('objects', Command('objects')),
    ('demo_scenario', Command('demo_scenario')),
    ('above green', Command('above', 'green', HEIGHT_DEFAULT)),
    ('above red 0.3', Command('above', 'red', 0.3)),
    ('Above  BLUE   0,25', Command('above', 'blue', 0.25)),
    ('above yeşil 0.25', Command('above', 'green', 0.25)),
    ('above kırmızı 0', Command('above', 'red', 0.0)),
    ('above kirmizi 0.5', Command('above', 'red', 0.5)),
    ('above mavi', Command('above', 'blue', HEIGHT_DEFAULT)),
])
def test_valid_commands(text, expected):
    assert parse_command(text) == expected


def test_normalised_line():
    assert parse_command('Above  KIRMIZI 0,250').line == 'above red 0.25'
    assert parse_command('home').line == 'home'
    assert parse_command('above green').line == 'above green 0.1'


@pytest.mark.parametrize('text', [
    '', '   ', 'dance', 'goto 1 2 3', 'move home', 'addbox a 0 0 0 1 1 1', 'exit', 'clear',
    'home now', 'objects all', 'demo_scenario 3',                   # no arguments allowed
    'above', 'above green 0.2 extra', 'above purple', 'above obj_red_1', 'above 0.2 green',
    'above green abc', 'above green -0.1', 'above green 0.51', 'above green 9', 'above green 1e9',
    'above green nan', 'above green inf', 'above green 0.2.3', 'above green .5', 'above green 0.',
    'above green; reboot', 'home && ls', 'home | cat', 'home\nobjects', 'home\tobjects',
    'above green 0.2\n', '$(reboot)', '`id`', '../../etc/passwd', 'home;', "home'", 'home"',
    'ho me', 'ｈome', 'above green ０.２',                           # full-width characters
    'x' * 65, 'above ' + 'g' * 80,
])
def test_everything_else_is_rejected(text):
    with pytest.raises(CommandError):
        parse_command(text)


@pytest.mark.parametrize('value', [None, 5, 1.5, ['home'], {'command': 'home'}, b'home', True])
def test_non_string_is_rejected(value):
    with pytest.raises(CommandError):
        parse_command(value)


def test_errors_say_what_is_allowed():
    with pytest.raises(CommandError) as exc:
        parse_command('dance')
    assert all(u in str(exc.value) for u in USAGE)
    with pytest.raises(CommandError) as exc:
        parse_command('above purple')
    assert 'red' in str(exc.value) and 'green' in str(exc.value) and 'blue' in str(exc.value)
    with pytest.raises(CommandError) as exc:
        parse_command('above green 0.9')
    assert '0.5' in str(exc.value)


# ---- the runner --------------------------------------------------------------------------------
def make_runner(**kw):
    skills = kw.pop('skills', None) or FakeSkills()
    sink = RecordingSink()
    return CommandRunner(skills, FakeTool((0.0, 0.0, 1.0)), sink, **kw), skills, sink


def test_home_objects_and_above_use_the_skills():
    runner, skills, _ = make_runner()
    assert runner.run(parse_command('home')).ok
    objects = runner.run(parse_command('objects'))
    assert objects.ok and [o['name'] for o in objects.data['objects']] == [
        'obj_red_1', 'obj_green_1']
    above = runner.run(parse_command('above green 0.25'))
    assert above.ok and above.data['target'] == pytest.approx([0.40, -0.10, 0.05 + 0.05 + 0.25])
    assert skills.calls == [('go_to_named_pose', 'home'), ('list_objects',),
                            ('go_above', 'green', 0.25)]


def test_a_refused_command_is_not_ok_and_says_why():
    runner, _, _ = make_runner(skills=FakeSkills(fail='erişim alanı dışında, robot hareket etmedi'))
    outcome = runner.run(parse_command('above red 0'))
    assert not outcome.ok and 'robot hareket etmedi' in outcome.message
    assert not outcome.internal_error


def test_objects_with_nothing_known_says_so_and_is_ok():
    runner, _, _ = make_runner(skills=FakeSkills(objects=[]))
    outcome = runner.run(parse_command('objects'))
    assert outcome.ok and outcome.data == {'objects': []} and 'nesne yok' in outcome.message


def test_unexpected_exception_is_an_internal_error_without_details():
    runner, _, _ = make_runner(skills=FakeSkills(raises=RuntimeError('gizli ayrıntı /etc/x')))
    outcome = runner.run(parse_command('home'))
    assert not outcome.ok and outcome.internal_error
    assert 'RuntimeError' in outcome.message and 'gizli' not in outcome.message
    assert runner.status()['running'] is False          # and the runner is usable again
    runner._skills.raises = None
    assert runner.run(parse_command('home')).ok


def test_status_log_and_last():
    runner, _, _ = make_runner()
    assert runner.status() == {'running': False, 'name': None, 'log': [], 'last': None}
    runner.run(parse_command('above green 0.25'))
    status = runner.status()
    assert status['running'] is False and status['name'] is None
    assert status['log'][0] == '> above green 0.25' and status['log'][-1].startswith('[+] ')
    assert status['last']['command'] == 'above green 0.25' and status['last']['ok'] is True
    runner.run(parse_command('home'))
    assert runner.status()['log'][0] == '> home'           # the log is per command


def test_only_one_command_at_a_time():
    block, entered = threading.Event(), threading.Event()
    skills = FakeSkills(block=block)
    skills.entered = entered
    runner, _, _ = make_runner(skills=skills)
    results = []
    worker = threading.Thread(target=lambda: results.append(runner.run(parse_command('above red'))))
    worker.start()
    assert entered.wait(5)
    status = runner.status()
    assert status['running'] is True and status['name'] == 'above red 0.1'
    with pytest.raises(Busy) as exc:
        runner.run(parse_command('home'))
    assert 'above red' in str(exc.value)
    assert ('go_to_named_pose', 'home') not in skills.calls      # the second command never ran
    block.set()
    worker.join(5)
    assert results[0].ok and runner.status()['running'] is False
    assert runner.run(parse_command('home')).ok                  # free again


def test_reject_emits_an_event_and_never_raises():
    runner, _, sink = make_runner()
    runner.reject('dance', 'bilinmeyen komut')
    assert sink.events == [('command_rejected', {'line': 'dance', 'reason': 'bilinmeyen komut'})]

    class Broken:
        def emit(self, kind, payload):
            raise RuntimeError('db down')
    CommandRunner(FakeSkills(), FakeTool((0, 0, 0)), Broken()).reject('x', 'y')   # must not raise


def test_demo_scenario_runs_through_the_runner():
    calls = []

    def fake_scenario(skills, tool, say, events=None):
        calls.append((skills, tool, events))
        say('adım 1')
        from robot_core.scenario import ScenarioResult
        return ScenarioResult(True, 'tamam', ())
    runner, skills, sink = make_runner(scenario=fake_scenario)
    outcome = runner.run(parse_command('demo_scenario'))
    assert outcome.ok and outcome.message == 'tamam' and outcome.data == {'steps': []}
    assert calls == [(skills, runner._tool, sink)]
    assert 'adım 1' in runner.status()['log']


def test_the_verdict_line_is_not_logged_twice():
    from robot_core.scenario import ScenarioResult

    def narrating(skills, tool, say, events=None):
        say('[1/1] adım')
        say('[+] bitti')                      # run_demo_scenario ends with its own verdict line
        return ScenarioResult(True, 'bitti', ())
    runner, _, _ = make_runner(scenario=narrating)
    runner.run(parse_command('demo_scenario'))
    assert runner.status()['log'] == ['> demo_scenario', '[1/1] adım', '[+] bitti']
    runner.run(parse_command('home'))                      # other commands still get a verdict
    assert runner.status()['log'][-1].startswith('[+] ')
