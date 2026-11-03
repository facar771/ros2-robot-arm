"""The tools an LLM may call, generated from the robot's own white list (ROS-free).

The tool set is deliberately *smaller* than the Robot API: it is built from the web commands
(:mod:`robot_core.webcmd`: ``objects``, ``home``, ``above``, ``demo_scenario``) and the Skills
docstrings (:class:`robot_core.skills.Skills`), plus the two safety tools. There is **no** tool for
``reset`` (clearing an emergency stop is a human decision), for raw coordinates, for shell commands
or for code. Colours and the height range come from ``robot_core.webcmd`` so they cannot drift.

Risk classes: ``read`` (changes nothing), ``motion`` (moves the arm), ``long`` (moves the arm for
a minute), ``safety`` (stop / cancel; never needs confirmation, never refused for policy reasons),
``plugin`` (``run_plugin``: the real class comes from the plugin's own ``motion`` per call, see
:mod:`robot_agent.plugins`).
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from robot_core.skills import Skills
from robot_core.webcmd import COLORS, HEIGHT_MAX, HEIGHT_MIN

from robot_agent.plugins import PluginBridge
from robot_agent.schema import check_schema

DEFAULT_HEIGHT = 0.35        # inside the reachable window of every object (docs/INTERFACES.md)
RISKS = ('read', 'motion', 'long', 'safety', 'plugin')


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: Dict[str, Any]
    risk: str

    def for_llm(self) -> Dict[str, Any]:
        """The Anthropic tool definition (also what OpenAI-style APIs call a function)."""
        return {'name': self.name, 'description': self.description, 'input_schema': self.schema}


def skill_doc(name: str) -> str:
    """First paragraph of a Skills docstring (before ``Args:``), whitespace collapsed."""
    doc = inspect.getdoc(getattr(Skills, name)) or ''
    return ' '.join(doc.split('Args:')[0].split())


def _object(properties: Dict[str, Any], required: List[str] = ()) -> Dict[str, Any]:
    schema = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        schema['required'] = list(required)
    check_schema(schema)
    return schema


def build_tools(plugins: Optional[PluginBridge] = None) -> Dict[str, ToolSpec]:
    """All tools. ``run_plugin`` is included only when ``plugins`` has at least one allowed,
    installed plugin tool; its description lists exactly those."""
    colors = list(COLORS)
    tools = [
        ToolSpec('list_objects', skill_doc('list_objects') + ' (Read-only.)', _object({}), 'read'),
        ToolSpec('get_robot_state',
                 'Read the robot status: state (idle, busy, estopped, error), whether it is '
                 'moving, the running job, the last error, the emergency-stop latch and the '
                 'safety limits. Read-only.', _object({}), 'read'),
        ToolSpec('go_home', "Move all joints to the named pose 'home' (arm upright). Moves the "
                 'robot.', _object({}), 'motion'),
        ToolSpec('go_above_object',
                 skill_doc('go_above') + f" Only the colours {', '.join(colors)} exist. A height "
                 f'of {DEFAULT_HEIGHT:g} m works for every object; very low or very high '
                 'clearances may be unreachable and are then refused without moving.',
                 _object({
                     'color': {'type': 'string', 'enum': colors,
                               'description': 'colour of the object'},
                     'height_m': {'type': 'number', 'minimum': HEIGHT_MIN, 'maximum': HEIGHT_MAX,
                                  'default': DEFAULT_HEIGHT,
                                  'description': 'clearance above the object top in metres'},
                 }, ['color']), 'motion'),
        ToolSpec('run_demo_scenario',
                 'Run the built-in demo: home, above the green object, above the red object, '
                 'home; every step is verified. Takes about a minute. Moves the robot.',
                 _object({}), 'long'),
        ToolSpec('cancel_current_job',
                 'Cancel the job that is running now; the arm stops and nothing stays locked. '
                 'Does nothing when no job is running.', _object({}), 'safety'),
        ToolSpec('emergency_stop',
                 'EMERGENCY STOP: stop the robot at once and lock it. Use this whenever anything '
                 'looks unsafe or the user asks to stop. Only a human can unlock it afterwards.',
                 _object({'reason': {'type': 'string', 'maxLength': 64,
                                     'description': 'short reason (optional)'}}), 'safety'),
    ]
    plugin_tool = _run_plugin_spec(plugins)
    if plugin_tool is not None:
        tools.insert(-2, plugin_tool)
    return {t.name: t for t in tools}


def _run_plugin_spec(plugins: Optional[PluginBridge]) -> Optional[ToolSpec]:
    entries = list(plugins.tools().values()) if plugins is not None else []
    if not entries:
        return None
    lines = []
    for e in entries:
        what = {'none': 'only looks, moves nothing', 'camera': 'turns the CAMERA',
                'arm': 'MOVES THE ROBOT ARM'}[e.motion]
        props = ', '.join(sorted((e.input_schema.get('properties') or {})))
        text = ' '.join(e.description.split())
        lines.append(f"- plugin='{e.plugin}' tool='{e.name}' ({what}; arguments: "
                     f"{props or 'none'}): {text}")
    plugins_enum = sorted({e.plugin for e in entries})
    tools_enum = sorted({e.name for e in entries})
    return ToolSpec(
        'run_plugin',
        'Run one tool of an installed plugin (vision / scanning / following). Only these exist:\n'
        + '\n'.join(lines) + '\nTools that move the camera or the arm need the user\'s '
        'confirmation and obey the robot\'s safe work box and emergency stop; looking tools '
        'do not.',
        _object({'plugin': {'type': 'string', 'enum': plugins_enum, 'description': 'plugin name'},
                 'tool': {'type': 'string', 'enum': tools_enum, 'description': 'tool name'},
                 'arguments': {'type': 'object', 'description': "the tool's own arguments"}},
                ['plugin', 'tool']), 'plugin')


def command_for(name: str, arguments: Dict[str, Any]) -> str:
    """The white-listed web command a (validated) tool call stands for."""
    if name == 'list_objects':
        return 'objects'
    if name == 'go_home':
        return 'home'
    if name == 'run_demo_scenario':
        return 'demo_scenario'
    if name == 'go_above_object':
        height = float(arguments.get('height_m', DEFAULT_HEIGHT))
        return f"above {arguments['color']} {height:.3f}"
    raise KeyError(name)
