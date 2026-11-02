"""The tools an LLM may call, generated from the robot's own white list (ROS-free).

The tool set is deliberately *smaller* than the Robot API: it is built from the web commands
(:mod:`robot_core.webcmd`: ``objects``, ``home``, ``above``, ``demo_scenario``) and the Skills
docstrings (:class:`robot_core.skills.Skills`), plus the two safety tools. There is **no** tool for
``reset`` (clearing an emergency stop is a human decision), for raw coordinates, for shell commands
or for code. Colours and the height range come from ``robot_core.webcmd`` so they cannot drift.

Risk classes: ``read`` (changes nothing), ``motion`` (moves the arm), ``long`` (moves the arm for
a minute), ``safety`` (stop / cancel; never needs confirmation, never refused for policy reasons).
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Dict, List

from robot_core.skills import Skills
from robot_core.webcmd import COLORS, HEIGHT_MAX, HEIGHT_MIN

from robot_agent.schema import check_schema

DEFAULT_HEIGHT = 0.35        # inside the reachable window of every object (docs/INTERFACES.md)
RISKS = ('read', 'motion', 'long', 'safety')


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


def build_tools() -> Dict[str, ToolSpec]:
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
    return {t.name: t for t in tools}


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
