"""Commands that may be sent from the browser: a strict white list, one at a time (ROS-free).

``parse_command`` is the only door: anything that is not exactly one of the commands below, with
valid arguments, is a :class:`CommandError` and never reaches the robot. ``CommandRunner`` runs one
parsed command at a time on the :class:`~robot_core.skills.Skills` layer (a second request while one
is running gets :class:`Busy`) and keeps a short log that ``/api/state`` shows live.

    home                          robot to the named pose 'home'
    objects                       list the objects the robot knows about
    above <red|green|blue> [m]    tool above that object, clearance in metres (default 0.10, 0-0.5)
    demo_scenario                 home -> above green -> above red -> home, every step measured

Turkish colour names (kırmızı, yeşil, mavi) are accepted and normalised.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional

from robot_core.ports import EventSink, NullEventSink, ToolPositionPort
from robot_core.scenario import run_demo_scenario
from robot_core.skills import Skills

log = logging.getLogger('robot_core.webcmd')

COLORS = ('red', 'green', 'blue')
ALIASES = {'kırmızı': 'red', 'kirmizi': 'red', 'yeşil': 'green', 'yesil': 'green', 'mavi': 'blue'}
MAX_LINE = 64
HEIGHT_MIN, HEIGHT_MAX, HEIGHT_DEFAULT = 0.0, 0.5, 0.10
USAGE = ('home', 'objects', 'above <red|green|blue> [height_m]', 'demo_scenario')
_ALLOWED_CHARS = re.compile(r'[0-9A-Za-zçğıöşüÇĞİÖŞÜ.,_ ]+')
_NUMBER = re.compile(r'[0-9]+(?:[.,][0-9]+)?')


class CommandError(ValueError):
    """The text is not an allowed command (message: why, in plain Turkish)."""


class Busy(RuntimeError):
    """A command is already running."""


@dataclass(frozen=True)
class Command:
    verb: str
    target: Optional[str] = None
    height: Optional[float] = None

    @property
    def line(self) -> str:
        parts = [self.verb]
        if self.target is not None:
            parts.append(self.target)
        if self.height is not None:
            parts.append(f'{self.height:g}')
        return ' '.join(parts)


def parse_command(text: Any) -> Command:
    if not isinstance(text, str):
        raise CommandError("'command' bir metin olmalı")
    if not text.strip():
        raise CommandError('komut boş')
    if len(text) > MAX_LINE:
        raise CommandError(f'komut çok uzun (en fazla {MAX_LINE} karakter)')
    if not _ALLOWED_CHARS.fullmatch(text):
        raise CommandError('komutta izin verilmeyen karakter var (harf, rakam, boşluk, nokta ve '
                           'virgül dışında bir şey kullanılamaz)')
    tokens = text.split()
    verb, args = tokens[0].lower(), tokens[1:]
    if verb in ('home', 'objects', 'demo_scenario'):
        if args:
            raise CommandError(f"'{verb}' argüman almaz, verilen: {' '.join(args)}")
        return Command(verb)
    if verb == 'above':
        if not 1 <= len(args) <= 2:
            raise CommandError('kullanım: above <red|green|blue> [yükseklik_m]')
        name = args[0].lower()
        color = ALIASES.get(name, name)
        if color not in COLORS:
            raise CommandError(f"bilinmeyen renk '{args[0]}'; geçerli renkler: "
                               f"{', '.join(COLORS)}")
        height = HEIGHT_DEFAULT
        if len(args) == 2:
            if not _NUMBER.fullmatch(args[1]):
                raise CommandError(
                    f"yükseklik sayı olmalı (metre, örn. 0.25), verilen: '{args[1]}'")
            height = float(args[1].replace(',', '.'))
        if not (math.isfinite(height) and HEIGHT_MIN <= height <= HEIGHT_MAX):
            raise CommandError(f'yükseklik {HEIGHT_MIN:g} ile {HEIGHT_MAX:g} m arasında olmalı, '
                               f'verilen: {args[1]}')
        return Command('above', color, height)
    raise CommandError(f"bilinmeyen komut '{tokens[0][:20]}'; izin verilenler: {', '.join(USAGE)}")


@dataclass(frozen=True)
class CommandOutcome:
    ok: bool
    message: str
    data: Dict[str, Any]
    duration_s: float
    internal_error: bool = False


class CommandRunner:
    """Runs white-listed commands, one at a time. Thread-safe; ``run`` blocks until it is done."""

    def __init__(self, skills: Skills, tool: ToolPositionPort, events: Optional[EventSink] = None,
                 *, log_lines: int = 60, clock=time.monotonic, wall=time.time,
                 scenario=run_demo_scenario):
        self._skills, self._tool = skills, tool
        self._events = events or NullEventSink()
        self._clock, self._wall, self._scenario = clock, wall, scenario
        self._gate = threading.Lock()          # held while a command runs: single flight
        self._lock = threading.Lock()          # guards the status fields below
        self._running: Optional[str] = None
        self._log: Deque[str] = deque(maxlen=log_lines)
        self._last: Optional[Dict[str, Any]] = None

    @property
    def allowed(self) -> List[str]:
        return list(USAGE)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {'running': self._running is not None, 'name': self._running,
                    'log': list(self._log),
                    'last': None if self._last is None else dict(self._last)}

    def reject(self, line: Any, reason: str) -> None:
        """Record a refused request (event for the run history); never raises."""
        try:
            self._events.emit('command_rejected', {'line': str(line)[:MAX_LINE], 'reason': reason})
        except Exception:    # noqa: BLE001
            pass

    def _say(self, text: str) -> None:
        with self._lock:
            self._log.append(text)

    def run(self, command: Command) -> CommandOutcome:
        if not self._gate.acquire(blocking=False):
            raise Busy(f"başka bir komut çalışıyor: {self.status()['name']}")
        started = self._clock()
        with self._lock:
            self._running = command.line
            self._log.clear()
        self._say(f'> {command.line}')
        try:
            ok, message, data = self._dispatch(command)
            internal = False
        except Exception as exc:    # noqa: BLE001 - details go to the log, not to the client
            log.exception('komut işlenemedi: %s', command.line)
            ok, message, data, internal = False, f'iç hata ({type(exc).__name__})', {}, True
        outcome = CommandOutcome(ok, message, data, round(self._clock() - started, 2), internal)
        verdict = ('[+] ' if ok else '[-] ') + message
        if self.status()['log'][-1:] != [verdict]:      # the scenario already narrates its verdict
            self._say(verdict)
        with self._lock:
            self._last = {'command': command.line, 'ok': ok, 'message': message,
                          'finished_at': round(self._wall(), 3)}
            self._running = None
        self._gate.release()
        return outcome

    def _dispatch(self, command: Command):
        if command.verb == 'home':
            r = self._skills.go_to_named_pose('home')
            return r.ok, r.message, dict(r.data)
        if command.verb == 'objects':
            objs = self._skills.list_objects()
            listing = [{'name': o.name, 'label': o.label,
                        'position': [round(v, 4) for v in o.position],
                        'size': [round(v, 4) for v in o.size]} for o in objs]
            message = (f'{len(objs)} nesne: ' + ', '.join(o.name for o in objs)) if objs else (
                'bilinen nesne yok (algı çalışıyor mu? perception_scene:=true)')
            return True, message, {'objects': listing}
        if command.verb == 'above':
            r = self._skills.go_above(command.target, command.height)
            return r.ok, r.message, dict(r.data)
        if command.verb == 'demo_scenario':
            result = self._scenario(self._skills, self._tool, self._say, events=self._events)
            return result.ok, result.message, {'steps': [s.as_dict() for s in result.steps]}
        raise AssertionError(f'parse_command let {command.verb!r} through')
