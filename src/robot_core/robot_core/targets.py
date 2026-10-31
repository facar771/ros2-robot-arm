"""Parsing of user supplied joint targets."""
from __future__ import annotations

from typing import Sequence

from robot_core.config import RobotConfig
from robot_core.models import JointTarget


class TargetError(ValueError):
    """The user input is not a valid target (message is user-facing)."""


class TargetParser:
    def __init__(self, config: RobotConfig):
        self._config = config

    def parse(self, tokens: Sequence[str]) -> JointTarget:
        """Accepts one named pose, or exactly ``dof`` numbers (radians)."""
        if len(tokens) == 1 and tokens[0].lower() in self._config.poses:
            name = tokens[0].lower()
            return JointTarget(self._config.poses[name], label=name)
        if len(tokens) == self._config.dof:
            try:
                values = tuple(float(t) for t in tokens)
            except ValueError as exc:
                raise TargetError('Hatalı sayı formatı!') from exc
            errors = self._config.validate(values)
            if errors:
                raise TargetError('; '.join(errors))
            return JointTarget(values)
        names = '/'.join(sorted(self._config.poses))
        raise TargetError(f'Hedef: {names} ya da {self._config.dof} açı (radyan)')
