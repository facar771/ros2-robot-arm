import pytest

from control_hub.hub_core import ConfigError
from control_hub.hub_core.scenarios import parse_scenarios, parse_ui_config


def test_default_files_are_valid(scenarios, ui):
    assert {'home', 'above_green', 'above_red', 'above_blue', 'demo_tour'} <= set(scenarios)
    assert ui['layout']['panels'] == ['status', 'robot', 'log']
    shown = [i for s in ui['layout']['sections'] for i in s['scenarios']]
    assert set(shown) <= set(scenarios)           # arayüzdeki her buton tanımlı bir senaryo


def test_demo_tour_matches_robot_demo_steps(scenarios):
    cmds = [s.command for s in scenarios['demo_tour'].steps]
    assert cmds == ['home', 'above green 0.25', 'above red 0.3', 'home']


def test_all_problems_reported_at_once():
    doc = {'scenarios': [
        {'id': 'Bad Id', 'label': 'x', 'steps': [{'robot': 'home'}]},
        {'id': 'a', 'label': 'A', 'steps': [{'robot': 'goto 1 2 3'}, {'wait': 999},
                                            {'robot': 'home', 'wait': 1}, {'foo': 1}]},
        {'id': 'b', 'label': 'B', 'color': 'red', 'steps': []},
    ]}
    with pytest.raises(ConfigError) as e:
        parse_scenarios(doc)
    text = str(e.value)
    assert len(e.value.problems) >= 6
    for needle in ("'id'", 'izin verilmeyen', "'wait'", 'tam olarak', "'color'", "'steps'"):
        assert needle in text


def test_duplicate_ids_and_empty():
    step = {'robot': 'home'}
    with pytest.raises(ConfigError, match='tekrar'):
        parse_scenarios({'scenarios': [{'id': 'a', 'label': 'A', 'steps': [step]},
                                       {'id': 'a', 'label': 'B', 'steps': [step]}]})
    for bad in (None, {}, {'scenarios': []}, {'scenarios': 'x'}):
        with pytest.raises(ConfigError):
            parse_scenarios(bad)


def test_ui_config_rejects_unknown_scenario_and_unsafe_css(scenarios):
    with pytest.raises(ConfigError) as e:
        parse_ui_config({'layout': {'sections': [{'scenarios': ['nope']}]}}, scenarios)
    assert 'nope' in str(e.value)
    with pytest.raises(ConfigError) as e:
        parse_ui_config({'theme': {'--bg': 'red; background:url(http://evil)', 'color': '#fff'}},
                        scenarios)
    assert len(e.value.problems) == 2


def test_ui_config_defaults(scenarios):
    ui = parse_ui_config({}, scenarios)
    assert ui['layout']['sections'][0]['scenarios'] == list(scenarios)
    assert ui['layout']['estop']['position'] == 'bottom'
