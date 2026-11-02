"""Tool definitions: generated from the robot's white list, schemas valid, no dangerous tools."""
import math

import pytest
from robot_agent.schema import check_schema, validate
from robot_agent.tools import DEFAULT_HEIGHT, RISKS, build_tools, command_for, skill_doc
from robot_core.skills import Skills
from robot_core.webcmd import COLORS, HEIGHT_MAX, HEIGHT_MIN, CommandError, parse_command


def test_the_tool_set_is_small_and_has_no_dangerous_tool():
    tools = build_tools()
    assert set(tools) == {'list_objects', 'get_robot_state', 'go_home', 'go_above_object',
                          'run_demo_scenario', 'cancel_current_job', 'emergency_stop'}
    for banned in ('reset', 'reset_emergency_stop', 'goto', 'move', 'exec', 'shell', 'run_code',
                   'addbox', 'set_joint', 'go_to_position'):
        assert banned not in tools
    assert {t.risk for t in tools.values()} <= set(RISKS)
    assert tools['emergency_stop'].risk == 'safety' and tools['go_home'].risk == 'motion'
    assert tools['run_demo_scenario'].risk == 'long'


def test_llm_definitions_have_the_anthropic_shape_and_valid_schemas():
    for tool in build_tools().values():
        spec = tool.for_llm()
        assert set(spec) == {'name', 'description', 'input_schema'}
        assert spec['description'] and spec['input_schema']['type'] == 'object'
        assert spec['input_schema']['additionalProperties'] is False
        check_schema(spec['input_schema'])


def test_schema_constants_come_from_the_web_white_list():
    props = build_tools()['go_above_object'].schema['properties']
    assert props['color']['enum'] == list(COLORS)
    assert props['height_m']['minimum'] == HEIGHT_MIN and props['height_m']['maximum'] == HEIGHT_MAX
    assert build_tools()['go_above_object'].schema['required'] == ['color']


def test_descriptions_are_built_from_the_skills_docstrings():
    assert 'tool tip' in skill_doc('go_above') and 'Args' not in skill_doc('go_above')
    tools = build_tools()
    assert skill_doc('go_above') in tools['go_above_object'].description
    assert skill_doc('list_objects') in tools['list_objects'].description
    assert Skills.go_above.__doc__


@pytest.mark.parametrize('height', [0, 1e-9, 0.001, 0.0449, 0.35, 0.4999, 0.5])
@pytest.mark.parametrize('color', COLORS)
def test_every_valid_tool_call_becomes_a_command_the_white_list_accepts(color, height):
    line = command_for('go_above_object', {'color': color, 'height_m': height})
    parsed = parse_command(line)
    assert parsed.verb == 'above' and parsed.target == color
    assert math.isclose(parsed.height, height, abs_tol=5e-4)


def test_fixed_commands_and_the_default_height():
    assert command_for('go_home', {}) == 'home' and command_for('list_objects', {}) == 'objects'
    assert command_for('run_demo_scenario', {}) == 'demo_scenario'
    assert parse_command(command_for('go_above_object', {'color': 'red'})).height == DEFAULT_HEIGHT
    with pytest.raises(KeyError):
        command_for('emergency_stop', {})


@pytest.mark.parametrize('arguments', [
    {}, {'color': 'purple'}, {'color': 'RED'}, {'color': 5}, {'color': 'red', 'height_m': -0.1},
    {'color': 'red', 'height_m': 0.51}, {'color': 'red', 'height_m': 9},
    {'color': 'red', 'height_m': math.nan}, {'color': 'red', 'height_m': math.inf},
    {'color': 'red', 'height_m': True}, {'color': 'red', 'height_m': '0.3'},
    {'color': 'red; reboot'}, {'color': 'red', 'extra': 1}, {'color': 'x' * 500}, 'red', None, [],
])
def test_invalid_arguments_are_found(arguments):
    problems = validate(build_tools()['go_above_object'].schema, arguments)
    assert problems, arguments


def test_valid_arguments_pass():
    schema = build_tools()['go_above_object'].schema
    assert validate(schema, {'color': 'green'}) == []
    assert validate(schema, {'color': 'blue', 'height_m': 0.2}) == []
    assert validate(schema, {'color': 'blue', 'height_m': 0}) == []
    assert validate(build_tools()['go_home'].schema, {}) == []
    assert validate(build_tools()['go_home'].schema, {'x': 1}) != []


def test_a_command_the_white_list_would_refuse_is_never_produced():
    with pytest.raises(CommandError):
        parse_command('goto 1 2 3')
    for tool in build_tools().values():
        if tool.risk in ('motion', 'long', 'read') and tool.name != 'get_robot_state':
            args = {'color': 'red'} if tool.name == 'go_above_object' else {}
            parse_command(command_for(tool.name, args))


def test_schema_checker_rejects_unsupported_constructs():
    with pytest.raises(ValueError):
        check_schema({'type': 'object', 'properties': {'a': {'type': 'array'}},
                      'additionalProperties': False})
    with pytest.raises(ValueError):
        check_schema({'type': 'object', 'properties': {}})
    with pytest.raises(ValueError):
        check_schema({'type': 'object', 'properties': {'a': {'type': 'string', 'pattern': '.*'}},
                      'additionalProperties': False})
