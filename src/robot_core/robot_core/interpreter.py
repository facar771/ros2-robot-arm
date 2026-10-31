"""Command interpreter for the interactive MoveIt CLI (pure logic, ROS-free)."""
from __future__ import annotations

from typing import Callable, Optional, Set

from robot_core.config import RobotConfig
from robot_core.errors import hint_for
from robot_core.models import BoxObstacle
from robot_core.ports import EventSink, MotionPort, NullEventSink, ScenePort, StatePort
from robot_core.targets import TargetError, TargetParser

HELP = """Komutlar:
  status                        anlık eklem açıları
  move <poz>                    hazır pozisyona planla+git ({poses})
  move j1 .. j{dof}                radyan cinsinden eklem hedefi
  addbox <ad> x y z sx sy sz    {frame} çerçevesinde kutu engel ekle (metre)
  rmbox <ad>                    engeli sil
  clear                         eklenen tüm engelleri sil
  list                          eklenen engeller
  help | exit"""


class CommandInterpreter:
    def __init__(self, config: RobotConfig, motion: MotionPort, scene: ScenePort,
                 state: StatePort, out: Callable[[str], None] = print,
                 events: Optional[EventSink] = None):
        self._config = config
        self._motion = motion
        self._scene = scene
        self._state = state
        self._out = out
        self._events = events or NullEventSink()
        self._parser = TargetParser(config)
        self._obstacles: Set[str] = set()
        self._handlers = {
            'help': self._help, 'status': self._status, 'list': self._list,
            'clear': self._clear, 'move': self._move, 'addbox': self._addbox,
            'rmbox': self._rmbox,
        }

    @property
    def obstacles(self) -> Set[str]:
        return set(self._obstacles)

    @property
    def help_text(self) -> str:
        return HELP.format(poses='/'.join(sorted(self._config.poses)),
                           dof=self._config.dof, frame=self._config.base_frame)

    # ---- entry points ----------------------------------------------------
    def execute(self, line: str) -> bool:
        """Run one command line. Returns False when the user asked to exit."""
        parts = line.split()
        if not parts:
            return True
        cmd, args = parts[0].lower(), parts[1:]
        if cmd == 'exit':
            return False
        handler = self._handlers.get(cmd)
        if handler is None:
            self._out("[-] Geçersiz komut! 'help' yaz.")
            self._events.emit('command_rejected', {'line': line})
            return True
        handler(args)
        return True

    def repl(self, read: Callable[[str], str] = input, prompt: str = 'moveit_cli> ') -> None:
        self._out(self.help_text)
        while True:
            try:
                line = read(prompt)
            except (EOFError, KeyboardInterrupt):
                break
            if not self.execute(line):
                break

    # ---- handlers --------------------------------------------------------
    def _help(self, args) -> None:
        self._out(self.help_text)

    def _status(self, args) -> None:
        positions = self._state.joint_positions()
        if not positions or not all(j in positions for j in self._config.joint_names):
            self._out("[-] Henüz /joint_states verisi yok (controller'lar ayakta mı?)")
            return
        self._out(f"[Anlık Açılar]: {[round(positions[j], 3) for j in self._config.joint_names]}")

    def _list(self, args) -> None:
        self._out(f"Engeller: {sorted(self._obstacles) or 'yok'}")

    def _clear(self, args) -> None:
        for name in sorted(self._obstacles):
            self._remove(name)

    def _move(self, args) -> None:
        try:
            target = self._parser.parse(args)
        except TargetError as exc:
            self._out(f'[-] {exc}')
            self._events.emit('command_rejected', {'line': 'move ' + ' '.join(args),
                                                   'reason': str(exc)})
            return
        self._out(f'[*] Hedef gönderiliyor: {list(target.values)}')
        result = self._motion.move_to(target)
        if result.ok:
            self._out('[+] Hedefe ulaşıldı.')
        else:
            detail = result.code_name or result.message or 'bilinmiyor'
            suffix = f' ({result.code})' if result.code is not None else ''
            self._out(f'[-] Başarısız: {detail}{suffix}')
            hint = hint_for(result.code_name)
            if hint:
                self._out(f'    İpucu: {hint}')
        self._events.emit('motion_result', {
            'target': list(target.values), 'label': target.label, 'ok': result.ok,
            'code': result.code, 'code_name': result.code_name, 'message': result.message})

    def _addbox(self, args) -> None:
        if len(args) != 7:
            self._out('[-] Kullanım: addbox <ad> x y z sx sy sz')
            return
        try:
            x, y, z, sx, sy, sz = (float(v) for v in args[1:])
        except ValueError:
            self._out('[-] Hatalı sayı formatı!')
            return
        if min(sx, sy, sz) <= 0:
            self._out('[-] Kutu boyutları pozitif olmalı!')
            return
        obstacle = BoxObstacle(args[0], x, y, z, sx, sy, sz, self._config.base_frame)
        if self._scene.add_box(obstacle):
            self._obstacles.add(obstacle.name)
            self._out(f'[+] Engel eklendi: {obstacle.name}')
            self._events.emit('obstacle_added', {'name': obstacle.name, 'pose': [x, y, z],
                                                 'size': [sx, sy, sz]})
        else:
            self._out(f'[-] Engel eklenemedi: {obstacle.name}')

    def _rmbox(self, args) -> None:
        if len(args) != 1:
            self._out('[-] Kullanım: rmbox <ad>')
            return
        self._remove(args[0])

    def _remove(self, name: str) -> None:
        if self._scene.remove_box(name):
            self._obstacles.discard(name)
            self._out(f'[-] Engel silindi: {name}')
            self._events.emit('obstacle_removed', {'name': name})
        else:
            self._out(f'[-] Engel silinemedi: {name}')
