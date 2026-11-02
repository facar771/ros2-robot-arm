"""PluginManager: discovers trusted plugins, runs each in its own process, links them to the e-stop.

Trust model (deliberately simple, stated honestly): only directories directly under ``plugins_dir``
that contain a valid ``package.yaml`` are known; nothing is downloaded or unpacked at run time.
Permissions are enforced by the API objects a plugin is given (camera / robot), not by the operating
system: a plugin is *trusted code* from this repository. Untrusted packages need the sandbox
described in ``docs/plugin-upload-design.md`` and are not supported.
"""
from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import robot_core
import robot_plugins  # noqa: I001  (kept next to its siblings for the PYTHONPATH below)
import robot_vision
from robot_plugins.manifest import Manifest, ManifestError, load_manifest
from robot_plugins.robot_api import RobotApi, RobotApiError
from robot_plugins.schema import ValidationError, apply_defaults, validate

READY_TIMEOUT = 20.0


class ManagerError(Exception):
    """A request the manager cannot honour (unknown plugin, not running...); message in Turkish."""

    def __init__(self, message: str, code: str = 'error'):
        super().__init__(message)
        self.code = code


class _Running:
    """One plugin process and everything the manager tracks about it."""

    def __init__(self, manifest: Manifest, params: Dict[str, Any], popen: subprocess.Popen):
        self.manifest, self.params, self.popen = manifest, params, popen
        self.started_at = time.time()
        self.ready = threading.Event()
        self.logs: Deque[str] = collections.deque(maxlen=300)
        self.pending: Dict[int, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.next_id = 1
        self.write_lock = threading.Lock()
        self.stopping = False

    def send(self, obj: Dict[str, Any]) -> bool:
        try:
            with self.write_lock:
                self.popen.stdin.write(json.dumps(obj) + '\n')
                self.popen.stdin.flush()
            return True
        except (OSError, ValueError):
            return False


def _python_path() -> str:
    dirs = [str(Path(m.__file__).resolve().parents[1]) for m in (robot_plugins, robot_vision,
                                                                  robot_core)]
    extra = os.environ.get('PYTHONPATH')
    return os.pathsep.join(dirs + ([extra] if extra else []))


class PluginManager:
    def __init__(self, plugins_dir: Path, *, robot_api: Optional[RobotApi] = None,
                 robot_url: str = 'fake', frames: str = 'none', panner: str = 'none',
                 grace_s: float = 3.0,
                 state_file: Optional[Path] = None, python: str = sys.executable):
        self.plugins_dir = Path(plugins_dir)
        self.robot_api = robot_api
        self.robot_url = robot_url
        self.frames = frames
        self.panner = panner
        self.grace_s = grace_s
        self.state_file = state_file
        self.python = python
        self._lock = threading.RLock()
        self._procs: Dict[str, _Running] = {}
        self._crashed: Dict[str, str] = {}
        self._manifests: Dict[str, Manifest] = {}
        self._errors: Dict[str, str] = {}
        self._estop_seen = False
        self._watch: Optional[threading.Thread] = None
        self._watch_stop = threading.Event()
        self.discover()

    # ---- discovery -----------------------------------------------------------------------------
    def discover(self) -> None:
        """Re-read ``plugins_dir``; a broken plugin shows up as ``invalid`` instead of vanishing."""
        manifests: Dict[str, Manifest] = {}
        errors: Dict[str, str] = {}
        if self.plugins_dir.is_dir():
            for d in sorted(self.plugins_dir.iterdir()):
                if not d.is_dir() or d.name.startswith(('.', '_')) or d.is_symlink():
                    continue
                try:
                    manifests[d.name] = load_manifest(d)
                except ManifestError as exc:
                    errors[d.name] = str(exc)
        with self._lock:
            self._manifests, self._errors = manifests, errors

    def names(self) -> List[str]:
        with self._lock:
            return sorted(set(self._manifests) | set(self._errors))

    def manifest(self, name: str) -> Manifest:
        with self._lock:
            if name in self._manifests:
                return self._manifests[name]
        raise ManagerError(f'eklenti yok ya da geçersiz: {name!r}', 'unknown_plugin')

    # ---- status --------------------------------------------------------------------------------
    def status(self, name: str) -> Dict[str, Any]:
        with self._lock:
            if name in self._errors:
                return {'name': name, 'status': 'invalid', 'enabled': False,
                        'error': self._errors[name]}
            m = self.manifest(name)
            proc = self._procs.get(name)
            state = 'stopped'
            if proc is not None:
                code = proc.popen.poll()
                if code is not None:
                    state = 'crashed' if not proc.stopping else 'stopped'
                    if state == 'crashed':
                        self._crashed[name] = f'süreç {code} koduyla çıktı'
                else:
                    state = 'running' if proc.ready.is_set() else 'starting'
            elif name in self._crashed:
                state = 'crashed'
            doc = {'name': m.name, 'version': m.version, 'description': m.description,
                   'status': state, 'enabled': state in ('running', 'starting'),
                   'permissions': list(m.granted), 'tools': [t.name for t in m.tools],
                   'pid': proc.popen.pid if proc is not None and state in ('running', 'starting')
                   else None}
            if proc is not None and state in ('running', 'starting'):
                doc.update(started_at=proc.started_at,
                           uptime_s=round(time.time() - proc.started_at, 1), params=proc.params)
            if state == 'crashed':
                doc['error'] = self._crashed.get(name, 'süreç beklenmedik biçimde bitti')
            return doc

    def list(self) -> List[Dict[str, Any]]:
        return [self.status(n) for n in self.names()]

    def logs(self, name: str, n: int = 100) -> List[str]:
        self.manifest(name)
        with self._lock:
            proc = self._procs.get(name)
            return list(proc.logs)[-n:] if proc is not None else []

    # ---- lifecycle -----------------------------------------------------------------------------
    def enable(self, name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        m = self.manifest(name)
        try:
            resolved = m.resolve_params(params or {})
        except ValidationError as exc:
            raise ManagerError(f'parametre geçersiz: {exc}', 'bad_parameters') from exc
        with self._lock:
            if self._estop_seen:
                raise ManagerError('robot acil dur durumunda; eklentiler etkinleştirilemez',
                                   'estop')
            existing = self._procs.get(name)
            if existing is not None and existing.popen.poll() is None and not existing.stopping:
                return self.status(name)
            self._crashed.pop(name, None)
            env = dict(os.environ, PYTHONPATH=_python_path(), PYTHONUNBUFFERED='1')
            popen = subprocess.Popen(
                [self.python, '-m', 'robot_plugins.runner', '--dir', str(m.directory),
                 '--params', json.dumps(resolved), '--frames', self.frames,
                 '--robot', self.robot_url, '--panner', self.panner],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                bufsize=1, env=env, start_new_session=True)
            proc = _Running(m, resolved, popen)
            self._procs[name] = proc
        threading.Thread(target=self._read_stdout, args=(name, proc), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
        if not proc.ready.wait(READY_TIMEOUT):
            tail = ' | '.join(list(proc.logs)[-3:])
            self._kill(proc)
            with self._lock:
                self._crashed[name] = f'başlamadı: {tail}'.strip()
            raise ManagerError(f'eklenti {READY_TIMEOUT:g} s içinde hazır olmadı: {tail}',
                               'start_failed')
        self._persist()
        return self.status(name)

    def disable(self, name: str) -> Dict[str, Any]:
        self.manifest(name)
        with self._lock:
            proc = self._procs.get(name)
        if proc is not None:
            self._stop_process(proc)
            with self._lock:
                self._procs.pop(name, None)
        self._crashed.pop(name, None)
        self._persist()
        return self.status(name)

    def _stop_process(self, proc: _Running) -> None:
        proc.stopping = True
        proc.send({'op': 'stop'})
        try:
            proc.popen.wait(timeout=self.grace_s)
        except subprocess.TimeoutExpired:
            self._kill(proc)
        self._fail_pending(proc, 'eklenti durduruldu')

    def _kill(self, proc: _Running) -> None:
        proc.stopping = True
        for sig in ('terminate', 'kill'):
            if proc.popen.poll() is not None:
                break
            try:
                getattr(proc.popen, sig)()
                proc.popen.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                continue

    def _fail_pending(self, proc: _Running, message: str) -> None:
        with proc.lock:
            for slot in proc.pending.values():
                if 'result' not in slot:
                    slot['result'] = {'ok': False, 'code': 'stopped', 'error': message}
                    slot['event'].set()

    def shutdown(self) -> None:
        """Stop the watchdog and every plugin (call on exit)."""
        self._watch_stop.set()
        for name in list(self._procs):
            self.disable(name)

    # ---- reading the plugin's output -----------------------------------------------------------
    def _read_stdout(self, name: str, proc: _Running) -> None:
        for line in proc.popen.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                proc.logs.append(f'[stdout] {line.rstrip()}')
                continue
            kind = msg.get('type')
            if kind == 'ready':
                proc.ready.set()
            elif kind == 'log':
                proc.logs.append(f'[{msg.get("level", "info")}] {msg.get("msg", "")}')
            elif kind == 'result':
                with proc.lock:
                    slot = proc.pending.get(msg.get('id'))
                if slot is not None:
                    slot['result'] = msg
                    slot['event'].set()
        self._fail_pending(proc, 'eklenti süreci bitti')

    @staticmethod
    def _read_stderr(proc: _Running) -> None:
        for line in proc.popen.stderr:
            proc.logs.append(f'[stderr] {line.rstrip()}')

    # ---- tools ---------------------------------------------------------------------------------
    def call(self, name: str, tool: str, args: Optional[Dict[str, Any]] = None,
             timeout: float = 60.0) -> Dict[str, Any]:
        """Run ``tool`` of a running plugin. Always returns a dict: ``{"ok": True, "data": ...}`` or
        ``{"ok": False, "code": ..., "error": ...}`` (bad arguments, not running, timeout...)."""
        m = self.manifest(name)
        try:
            schema = m.tool(tool).input_schema
        except KeyError:
            return {'ok': False, 'code': 'unknown_tool', 'error': f'bilinmeyen araç {tool!r}'}
        full = apply_defaults(args or {}, schema)
        try:
            validate(full, schema)
        except ValidationError as exc:
            return {'ok': False, 'code': 'bad_arguments', 'error': str(exc)}
        with self._lock:
            proc = self._procs.get(name)
        if proc is None or proc.popen.poll() is not None or not proc.ready.is_set():
            return {'ok': False, 'code': 'not_running',
                    'error': f'{name} çalışmıyor (önce etkinleştir)'}
        with proc.lock:
            rid = proc.next_id
            proc.next_id += 1
            slot = {'event': threading.Event()}
            proc.pending[rid] = slot
        try:
            if not proc.send({'op': 'call', 'id': rid, 'tool': tool, 'args': full}):
                return {'ok': False, 'code': 'not_running', 'error': 'eklentiye yazılamadı'}
            if not slot['event'].wait(timeout):
                return {'ok': False, 'code': 'timeout', 'error': f'{timeout:g} s içinde yanıt yok'}
            res = dict(slot['result'])
            res.pop('type', None)
            res.pop('id', None)
            return res
        finally:
            with proc.lock:
                proc.pending.pop(rid, None)

    # ---- emergency stop ------------------------------------------------------------------------
    def stop_all(self) -> List[str]:
        """Stop every running plugin without touching the robot (their stop tokens are set first,
        everything still alive after ``grace_s`` is killed)."""
        with self._lock:
            procs = dict(self._procs)
        for proc in procs.values():
            proc.stopping = True
            proc.send({'op': 'stop'})
        deadline = time.monotonic() + self.grace_s
        for proc in procs.values():
            try:
                proc.popen.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._kill(proc)
            self._fail_pending(proc, 'acil dur: eklenti durduruldu')
        with self._lock:
            self._procs.clear()
        return sorted(procs)

    def emergency_stop(self, reason: str = 'eklenti yöneticisi') -> Dict[str, Any]:
        """The e-stop button for plugins: tell the plugins to stop, latch the robot's own e-stop
        (``POST /api/stop``, which always works), then reap the plugins. The robot call is made even
        if no plugin runs, and a failing robot call is reported, never hidden."""
        self._estop_seen = True
        with self._lock:
            procs = dict(self._procs)
        for proc in procs.values():
            proc.stopping = True
            proc.send({'op': 'stop'})                      # non-blocking: tokens are set at once
        robot: Dict[str, Any]
        if self.robot_api is None:
            robot = {'ok': False, 'error': 'robot API tanımlı değil'}
        else:
            try:
                robot = self.robot_api.stop(reason)
            except RobotApiError as exc:
                robot = {'ok': False, 'error': str(exc)}
        stopped = self.stop_all()
        return {'plugins_stopped': stopped, 'robot': robot}

    def clear_estop(self) -> None:
        """Allow plugins again (call after a human reset the robot)."""
        self._estop_seen = False

    def watch_robot(self, interval: float = 0.5) -> None:
        """Background thread: when the robot reports ``estopped`` (button, Hub, anyone), stop all
        plugins. An unreachable API is NOT treated as an e-stop (plugins keep running)."""
        if self.robot_api is None or (self._watch is not None and self._watch.is_alive()):
            return

        def loop() -> None:
            while not self._watch_stop.wait(interval):
                try:
                    state = self.robot_api.state().get('state')
                except RobotApiError:
                    continue
                if state == 'estopped' and not self._estop_seen:
                    self._estop_seen = True
                    self.stop_all()
                elif state != 'estopped' and self._estop_seen and not self._procs:
                    self._estop_seen = False                 # a human reset the robot

        self._watch = threading.Thread(target=loop, daemon=True)
        self._watch.start()

    # ---- persistence ---------------------------------------------------------------------------
    def _persist(self) -> None:
        if self.state_file is None:
            return
        with self._lock:
            data = {n: {'params': p.params} for n, p in self._procs.items()
                    if p.popen.poll() is None}
        try:
            self.state_file.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError:
            pass

    def restore(self) -> List[str]:
        """Re-enable the plugins that were enabled when the state file was last written."""
        if self.state_file is None or not self.state_file.is_file():
            return []
        try:
            data = json.loads(self.state_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        restored = []
        for name, item in data.items():
            try:
                self.enable(name, item.get('params'))
                restored.append(name)
            except ManagerError:
                continue
        return restored
