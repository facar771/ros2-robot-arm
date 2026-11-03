"""The agent's door to plugins: a white-listed, fail-closed view of ``robot_plugins``.

Only ``plugin.tool`` pairs on the allow-list can ever be called by an LLM, and only if the plugin
is really installed and its manifest is sane (known motion class, supported schema). A plugin
uploaded later does NOT become callable by the model until someone adds it to the list
(``--allow-plugin`` / ``PluginBridge(allowed=...)``).

The robot-side reality is taken from the plugin catalogue (``robot_plugins.invoke.catalog``), not
assumed here: its ``motion`` class decides how the executor treats a call::

    motion  none   -> only looks      -> risk ``read``   (no confirmation)
    motion  camera -> turns the camera-> risk ``motion`` (confirmation, quota, state checks)
    motion  arm    -> moves the arm   -> risk ``long``   (the same + safe-box preflight, e-stop)

Nothing here sends an emergency stop or a reset; ``run_plugin`` itself never does either.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, List, Optional

log = logging.getLogger('robot_agent.plugins')

DEFAULT_ALLOWED: FrozenSet[str] = frozenset({
    'find_object.find_object', 'scan_extent.scan_extent', 'follow_color.follow_color'})
MOTION_RISK = {'none': 'read', 'camera': 'motion', 'arm': 'long'}


@dataclass(frozen=True)
class PluginTool:
    plugin: str
    name: str
    description: str
    input_schema: Dict[str, Any]
    motion: str
    timeout_s: float

    @property
    def key(self) -> str:
        return f'{self.plugin}.{self.name}'

    @property
    def risk(self) -> str:
        return MOTION_RISK[self.motion]


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True


class PluginBridge:
    """``catalog_fn()`` -> list of tool dicts, ``run_fn(plugin, tool, args, **kw)`` -> result dict;
    both default to ``robot_plugins.invoke`` (imported lazily, so a missing package just means
    "no plugin tool", never a crash)."""

    def __init__(self, allowed: Optional[FrozenSet[str]] = None, *,
                 catalog_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
                 run_fn: Optional[Callable[..., Dict[str, Any]]] = None):
        self.allowed = frozenset(DEFAULT_ALLOWED if allowed is None else allowed)
        self._catalog_fn, self._run_fn = catalog_fn, run_fn
        self._tools: Optional[Dict[str, PluginTool]] = None
        self.problem: Optional[str] = None      # why the list is empty (shown by the CLI)

    # ---- the white-listed tools -------------------------------------------------------------
    def tools(self) -> Dict[str, PluginTool]:
        if self._tools is None:
            self._tools = self._load()
        return self._tools

    def _load(self) -> Dict[str, PluginTool]:
        try:
            catalog_fn = self._catalog_fn or _default_catalog()
            entries = catalog_fn()
        except Exception as exc:    # noqa: BLE001 - fail closed: no catalogue, no plugin tool
            self.problem = f'eklenti kataloğu okunamadı ({type(exc).__name__}: {exc})'
            return {}
        found: Dict[str, PluginTool] = {}
        for entry in entries:
            try:
                tool = PluginTool(str(entry['plugin']), str(entry['name']),
                                  str(entry.get('description', '')), dict(entry['input_schema']),
                                  str(entry['motion']), float(entry.get('timeout_s') or 60.0))
            except (KeyError, TypeError, ValueError):
                continue
            if tool.key not in self.allowed:
                continue
            if tool.motion not in MOTION_RISK or not self._schema_ok(tool.input_schema):
                log.warning('eklenti aracı atlandı (motion/şema desteklenmiyor): %s', tool.key)
                continue
            found[tool.key] = tool
        if not found and not self.problem:
            self.problem = 'izinli eklenti aracı bulunamadı'
        return found

    @staticmethod
    def _schema_ok(schema: Dict[str, Any]) -> bool:
        try:
            from robot_plugins.schema import check_schema
            check_schema(schema)
        except Exception:    # noqa: BLE001
            return False
        return schema.get('type') == 'object'

    # ---- arguments ----------------------------------------------------------------------------
    def check_args(self, tool: PluginTool, args: Any) -> List[str]:
        """Problems with ``args`` against the tool's own schema (empty: fine). Fails closed."""
        if not isinstance(args, dict):
            return ['arguments bir nesne (sözlük) olmalı']
        if not _finite(args):
            return ['arguments içinde sonlu olmayan sayı (NaN/inf) var']
        props = tool.input_schema.get('properties') or {}
        problems = [f"bilinmeyen alan '{k}'" for k in args if k not in props]   # even if the
        if problems:                                                          # schema forgot
            return problems
        try:
            from robot_plugins.schema import ValidationError, validate
            validate(args, tool.input_schema)
        except ValidationError as exc:
            return [str(exc)]
        except Exception as exc:    # noqa: BLE001
            return [f'şema denetlenemedi ({type(exc).__name__})']
        return []

    @staticmethod
    def with_defaults(tool: PluginTool, args: Dict[str, Any]) -> Dict[str, Any]:
        from robot_plugins.schema import apply_defaults
        return apply_defaults(args, tool.input_schema)

    # ---- running ------------------------------------------------------------------------------
    def run(self, plugin: str, tool: str, args: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        run_fn = self._run_fn or _default_run()
        return run_fn(plugin, tool, args, **kwargs)


def _default_catalog():
    from robot_plugins.invoke import catalog
    return catalog


def _default_run():
    from robot_plugins.invoke import run_plugin
    return run_plugin
