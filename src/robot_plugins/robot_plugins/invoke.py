"""The ONE entry point for calling a plugin from outside (the agent layer, scripts, the CLI).

    from robot_plugins.invoke import run_plugin, catalog
    result = run_plugin('find_object', 'find_object', {'color': 'red'},
                        robot_url='http://127.0.0.1:8080', frames='ros')

``run_plugin`` starts the plugin in its own process, makes ONE tool call, and always shuts the
process down again (also on timeout, Ctrl-C and errors). It **never raises** and always returns the
same JSON-able shape (``api_version`` 1)::

    {"api_version": 1, "ok": bool, "plugin": str, "tool": str|None,
     "motion": "none"|"camera"|"arm"|None,      # what the TOOL may physically move (manifest)
     "data": {...}|None,                        # the plugin's own result, whatever it returned
     "error": str|None, "code": str|None,       # set iff ok is false (codes below)
     "warning": str|None,                       # e.g. simulated robot
     "robot": "fake"|"<url>", "frames": str, "duration_s": float}

``ok`` is true only if the call went through AND the plugin did not report ``ok: false`` itself.
Error ``code`` values: ``unknown_plugin``, ``unknown_tool``, ``bad_parameters``, ``bad_arguments``,
``bad_robot_url``, ``start_failed``, ``not_running``, ``permission_denied``, ``timeout``,
``stopped`` (``stop_event`` set or plugin stopped), ``interrupted`` (Ctrl-C), ``estop`` (the robot's
emergency stop is latched: do not retry, a human must reset), ``robot_<http status>``,
``plugin_error`` (the plugin returned ``ok: false``; ``data`` has details such as ``reason``),
``exception``.

Time-out / stop behaviour: on ``timeout_s`` (default: the tool's ``timeout_s`` from the manifest)
or on ``stop_event``, the plugin gets its stop token first (``follow_color`` then cancels its
running robot job), and is killed after ``grace_s``. This module never sends the robot's emergency
stop by itself (that latches the robot; it is the caller's decision), but a result with
``motion == 'arm'`` and code ``timeout`` / ``stopped`` / ``interrupted`` carries a warning to check
``/api/state``.

Cost: one process start per call (about 1-2 s of imports). For many calls keep a ``PluginManager``.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from robot_plugins.manager import ManagerError, PluginManager
from robot_plugins.manifest import Manifest
from robot_plugins.robot_api import FakeRobotApi, HttpRobotApi

API_VERSION = 1
LOOPBACK = {'127.0.0.1', 'localhost', '::1'}
DEFAULT_TIMEOUT_S = 60.0


def default_plugins_dir() -> Path:
    return Path(__file__).resolve().parents[3] / 'plugins'


def robot_url_ok(url: str) -> bool:
    """``'fake'`` or an http(s) URL whose host is loopback (the Robot API is unauthenticated)."""
    if url == 'fake':
        return True
    parts = urlsplit(url)
    return parts.scheme in ('http', 'https') and (parts.hostname or '') in LOOPBACK


def _result(plugin: str, tool: Optional[str], motion: Optional[str], started: float, *,
            robot: str, frames: str, ok: bool = False, data: Optional[dict] = None,
            error: Optional[str] = None, code: Optional[str] = None,
            warning: Optional[str] = None) -> Dict[str, Any]:
    return {'api_version': API_VERSION, 'ok': ok, 'plugin': plugin, 'tool': tool, 'motion': motion,
            'data': data, 'error': error, 'code': code, 'warning': warning, 'robot': robot,
            'frames': frames, 'duration_s': round(time.monotonic() - started, 3)}


def _normalise(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fold the manager's two-level result (transport ok / plugin ok) into one ``ok``."""
    if not raw.get('ok'):
        code = str(raw.get('code') or 'exception')
        return {'ok': False, 'code': 'estop' if code == 'robot_423' else code,
                'error': str(raw.get('error') or code), 'data': None}
    data = raw.get('data') if isinstance(raw.get('data'), dict) else {'value': raw.get('data')}
    if data.get('ok') is False:
        code = 'estop' if data.get('reason') == 'estop' else 'plugin_error'
        return {'ok': False, 'code': code, 'error': str(data.get('error') or code), 'data': data}
    return {'ok': True, 'code': None, 'error': None, 'data': data}


def run_plugin(plugin: str, tool: Optional[str] = None, args: Optional[Dict[str, Any]] = None, *,
               params: Optional[Dict[str, Any]] = None, plugins_dir: Optional[Path] = None,
               robot_url: str = 'fake', frames: str = 'none', panner: str = 'none',
               timeout_s: Optional[float] = None, frame_wait_s: float = 5.0, grace_s: float = 3.0,
               stop_event: Optional[threading.Event] = None) -> Dict[str, Any]:
    """Run ``plugin``'s ``tool`` once and return the result dict described in the module doc.

    ``tool`` may be omitted if the plugin has exactly one. ``params`` are the plugin's start-up
    parameters (``package.yaml`` ``parameters``); ``args`` the tool arguments. ``robot_url`` is
    ``'fake'`` (in-memory robot, nothing moves: the result says so) or a loopback Robot API URL.
    ``frames`` / ``panner``: see ``frames.make_frame_source`` / ``panning.make_panner``.
    ``stop_event``: set it (e.g. from an emergency-stop handler) to stop the plugin at once.
    """
    started = time.monotonic()
    ctx = dict(robot=robot_url, frames=frames)
    if not robot_url_ok(robot_url):
        return _result(plugin, tool, None, started, **ctx, code='bad_robot_url',
                       error="robot_url 'fake' ya da loopback bir adres (127.0.0.1) olmalı")
    robot_api = FakeRobotApi() if robot_url == 'fake' else HttpRobotApi(robot_url)
    try:
        mgr = PluginManager(Path(plugins_dir or default_plugins_dir()), robot_api=robot_api,
                            robot_url=robot_url, frames=frames, panner=panner, grace_s=grace_s,
                            frame_wait_s=frame_wait_s)
        manifest: Manifest = mgr.manifest(plugin)
    except ManagerError as exc:
        return _result(plugin, tool, None, started, **ctx, code=exc.code, error=str(exc))
    except Exception as exc:                                   # noqa: BLE001 - never raise
        return _result(plugin, tool, None, started, **ctx, code='exception',
                       error=f'{type(exc).__name__}: {exc}')
    if tool is None and len(manifest.tools) == 1:
        tool = manifest.tools[0].name
    try:
        spec = manifest.tool(tool)
    except KeyError:
        names = ', '.join(t.name for t in manifest.tools)
        return _result(plugin, tool, None, started, **ctx, code='unknown_tool',
                       error=f'bilinmeyen araç {tool!r}; {plugin} araçları: {names}')
    timeout = float(timeout_s) if timeout_s is not None else spec.timeout_s
    warning = None
    if robot_url == 'fake' and spec.motion == 'arm':
        warning = ("robot_url='fake': robot BENZETİM, hiçbir şey hareket etmedi; gerçek robot için "
                   'Robot API adresini ver')
    box: Dict[str, Any] = {}

    def work() -> None:
        try:
            mgr.enable(plugin, params)
            box['raw'] = mgr.call(plugin, tool, args or {}, timeout=timeout)
        except ManagerError as exc:
            box['raw'] = {'ok': False, 'code': exc.code, 'error': str(exc)}
        except Exception as exc:                               # noqa: BLE001
            box['raw'] = {'ok': False, 'code': 'exception',
                          'error': f'{type(exc).__name__}: {exc}'}

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    interrupted = False
    try:
        while worker.is_alive():
            worker.join(0.1)
            if stop_event is not None and stop_event.is_set() and 'stopping' not in box:
                box['stopping'] = True
                mgr.stop_all()                                 # token first, kill after grace_s
    except KeyboardInterrupt:
        interrupted = True
        mgr.stop_all()
        worker.join(grace_s + 2.0)
    finally:
        try:
            mgr.shutdown()
        except Exception:                                      # noqa: BLE001
            pass
    raw = box.get('raw') or {'ok': False, 'code': 'exception', 'error': 'çağrı sonuç vermedi'}
    norm = _normalise(raw)
    if interrupted:
        norm.update(ok=False, code='interrupted', error='Ctrl-C ile kesildi')
    elif box.get('stopping') and not norm['ok'] and norm['code'] in ('stopped', 'exception',
                                                                      'not_running'):
        norm['code'] = 'stopped'
        norm['error'] = norm['error'] or 'durduruldu (stop_event)'
    if not norm['ok'] and spec.motion == 'arm' and norm['code'] in ('timeout', 'stopped',
                                                                    'interrupted', 'exception'):
        extra = ('robot işi iptal edilmiş olmayabilir: /api/state ile kontrol et, gerekirse '
                 'acil dur gönder')
        warning = f'{warning}; {extra}' if warning else extra
    return _result(plugin, tool, spec.motion, started, **ctx, ok=norm['ok'], data=norm['data'],
                   error=norm['error'], code=norm['code'], warning=warning)


def catalog(plugins_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every valid plugin's tools, ready to hand to an LLM / the agent's tool registry.

    One entry per tool: ``plugin``, ``name``, ``description``, ``input_schema`` (JSON-Schema
    subset),
    ``motion`` (``none`` looks only, ``camera`` may turn the camera, ``arm`` may move the robot arm
    through the Robot API), ``timeout_s``, ``permissions``. Invalid plugins are left out here (see
    ``PluginManager.list()`` for them)."""
    mgr = PluginManager(Path(plugins_dir or default_plugins_dir()))
    out: List[Dict[str, Any]] = []
    for name in mgr.names():
        try:
            m = mgr.manifest(name)
        except ManagerError:
            continue
        for t in m.describe()['tools']:
            out.append({'plugin': m.name, 'version': m.version, **t,
                        'permissions': list(m.granted)})
    return out
