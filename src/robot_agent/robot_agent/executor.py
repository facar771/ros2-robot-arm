"""ToolExecutor: the only way a tool call reaches the robot.

Every call goes through, in this order (a ``safety`` tool skips the middle: no delay for a stop):

1. the tool must be white-listed and enabled (``unknown`` -> rejected, never forwarded);
2. the arguments must satisfy the tool's JSON schema (``rejected``);
3. ``safety`` tools (``emergency_stop``, ``cancel_current_job``) go straight to the API;
4. the robot state is read: ``estopped`` -> refused (``estopped``), ``busy`` -> refused, API down ->
   refused (a motion is never sent blind);
5. motion quota (``max_motion_calls`` per executor);
6. for ``go_above_object``: the target is computed from the real object position and checked
   against the safe working box the robot reports (``refused`` outside it, nothing sent);
7. ``dry_run`` stops here and reports what *would* run;
8. confirmation (``Policy.confirm_risks``; no confirmer means refused);
9. the command is sent as an asynchronous job and followed to its end; a concurrent
   ``request_stop`` (another thread, Ctrl-C) sends the emergency stop at once.

Every call, including every refusal, is written to the event log as ``agent_tool_call``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from robot_core.ports import EventSink, NullEventSink
from robot_core.safezone import SafeZone
from robot_core.webcmd import COLORS

from robot_agent.client import RobotPort, RobotUnreachable
from robot_agent.plugins import PluginBridge, PluginTool
from robot_agent.redact import ScrubbingSink, install_log_scrubbing, scrub
from robot_agent.schema import validate
from robot_agent.tools import ToolSpec, build_tools, command_for

log = logging.getLogger('robot_agent.executor')

STATUSES = ('ok', 'dry_run', 'rejected', 'refused', 'failed', 'cancelled', 'estopped')


@dataclass(frozen=True)
class Policy:
    confirm_risks: FrozenSet[str] = frozenset({'motion', 'long'})
    max_motion_calls: int = 10
    poll_s: float = 0.2
    job_margin_s: float = 15.0          # wait this much longer than the job's own timeout
    allowed_tools: Optional[FrozenSet[str]] = None      # None: every tool


@dataclass(frozen=True)
class ConfirmRequest:
    tool: str
    arguments: Dict[str, Any]
    command: str
    target: Optional[List[float]] = None

    def describe(self) -> str:
        where = f' (hedef {[round(v, 3) for v in self.target]})' if self.target else ''
        args = json.dumps(self.arguments, ensure_ascii=False)
        return f"{self.tool} {args} -> '{self.command}'{where}"


@dataclass
class ToolResult:
    tool: str
    status: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    job_id: Optional[str] = None
    dry_run: bool = False
    ok: Optional[bool] = None      # default: status is ok / dry_run (a verified e-stop is also ok)
    risk: Optional[str] = None     # set for run_plugin: the class of the plugin tool actually run

    def __post_init__(self) -> None:
        if self.ok is None:
            self.ok = self.status in ('ok', 'dry_run')

    @property
    def halts(self) -> bool:
        """An emergency stop ends the whole run; the LLM gets no chance to retry."""
        return self.status == 'estopped'

    def to_llm(self) -> str:
        return scrub(json.dumps({'ok': self.ok, 'status': self.status, 'message': self.message,
                                 'data': self.data, 'job_id': self.job_id,
                                 'dry_run': self.dry_run}, ensure_ascii=False))


def _result(tool, status, message, data=None, job_id=None, **kw) -> ToolResult:
    return ToolResult(tool, status, message, {} if data is None else data, job_id, **kw)


class ToolExecutor:
    def __init__(self, robot: RobotPort, policy: Optional[Policy] = None,
                 events: Optional[EventSink] = None,
                 confirm: Optional[Callable[[ConfirmRequest], bool]] = None, *,
                 dry_run: bool = False, plugins: Optional[PluginBridge] = None,
                 plugin_frames: str = 'ros', clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._robot = robot
        self.policy = policy or Policy()
        self._events = ScrubbingSink(events or NullEventSink())
        install_log_scrubbing()
        self._confirm = confirm
        self.dry_run = dry_run
        self._clock, self._sleep = clock, sleep
        self._plugins = plugins if plugins is not None else PluginBridge()
        self._plugin_frames = plugin_frames
        self._tools = build_tools(self._plugins)
        self._motions = 0
        self._stop_event = threading.Event()
        self._stop_sent = False

    # ---- what the LLM sees --------------------------------------------------------------------
    def specs(self) -> List[Dict[str, Any]]:
        allowed = self.policy.allowed_tools
        return [t.for_llm() for t in self._tools.values() if allowed is None or t.name in allowed]

    # ---- the single entry ---------------------------------------------------------------------
    def execute(self, name: Any, arguments: Any, *, dry_run: Optional[bool] = None) -> ToolResult:
        dry = self.dry_run if dry_run is None else dry_run
        started = self._clock()
        arguments = scrub(arguments)        # a key echoed into a tool argument goes no further
        try:
            result = self._execute(name, arguments, dry)
        except RobotUnreachable as exc:
            result = _result(str(name), 'refused', f'{exc}; robot API çalışıyor mu?')
        except Exception as exc:    # noqa: BLE001 - a tool call never raises into the agent loop
            log.exception('araç çağrısı işlenemedi: %s', name)
            result = _result(str(name), 'failed', f'iç hata ({type(exc).__name__})')
        self._log(name, arguments, result, dry, self._clock() - started)
        return result

    def request_stop(self, reason: str = 'ajan: dış istek') -> ToolResult:
        """Emergency stop from outside the tool loop (another thread, Ctrl-C). Idempotent."""
        self._stop_event.set()
        return self._emergency_stop(reason)

    # ---- the pipeline -------------------------------------------------------------------------
    def _execute(self, name: Any, arguments: Any, dry: bool) -> ToolResult:
        spec = self._tools.get(name) if isinstance(name, str) else None
        allowed = self.policy.allowed_tools
        if spec is None or (allowed is not None and spec.name not in allowed):
            known = ', '.join(sorted(t for t in self._tools if allowed is None or t in allowed))
            return _result(str(name)[:40], 'rejected',
                           f'bilinmeyen araç {str(name)[:40]!r}; kullanılabilir araçlar: {known}')
        if spec.risk == 'safety':
            # a stop is never refused over its arguments (a verbose model writes a long reason)
            clean = arguments if isinstance(arguments, dict) else {}
            return self._safety_tool(spec, {k: v for k, v in clean.items() if k == 'reason'
                                            and isinstance(v, str)}, dry)
        problems = validate(spec.schema, {} if arguments is None else arguments)
        if problems:
            return _result(spec.name, 'rejected', 'geçersiz argüman: ' + '; '.join(problems))
        arguments = {} if arguments is None else arguments
        if spec.name == 'get_robot_state':
            return self._state_tool(spec)
        if spec.name == 'run_plugin':
            return self._plugin_tool(spec, arguments, dry)
        return self._robot_tool(spec, arguments, dry)

    def _safety_tool(self, spec: ToolSpec, arguments: Dict[str, Any], dry: bool) -> ToolResult:
        if dry:
            return _result(spec.name, 'dry_run', 'kuru çalıştırma: bu araç robotu durdururdu; '
                           'gönderilmedi', dry_run=True)
        if spec.name == 'emergency_stop':
            reason = 'ajan: ' + str(arguments.get('reason') or 'LLM isteği')
            self._stop_event.set()
            return self._emergency_stop(reason[:64])
        return self._cancel_job(spec.name)

    def _cancel_job(self, tool: str) -> ToolResult:
        job = (self._state().get('robot') or {}).get('current_job')
        if not job:
            return _result(tool, 'ok', 'iptal edilecek çalışan iş yok')
        status, body = self._robot.post(f"/api/jobs/{job['id']}/cancel", {})
        doc = (body or {}).get('job') or {}
        if status == 200:
            return _result(tool, 'cancelled', 'çalışan iş iptal edildi',
                           data={'job_state': doc.get('state')}, job_id=job['id'])
        return _result(tool, 'failed', (body or {}).get('error') or f'HTTP {status}',
                       job_id=job['id'])

    def _emergency_stop(self, reason: str) -> ToolResult:
        last: Optional[Exception] = None
        for _ in range(3):                      # nothing may be lost to one dropped request
            try:
                status, body = self._robot.post('/api/stop', {'reason': reason[:64]})
            except RobotUnreachable as exc:
                last = exc
                self._sleep(0.2)
                continue
            self._stop_sent = True
            body = body or {}
            if status == 200 and body.get('stopped'):
                return _result('emergency_stop', 'estopped',
                               'acil dur verildi; robot durdu (hız doğrulandı); kilidi yalnızca '
                               'bir insan açabilir', data={'stop_time_s': body.get('stop_time_s')},
                               ok=True)
            return _result('emergency_stop', 'estopped',
                           'acil dur kilitlendi ama robotun durduğu DOĞRULANAMADI: '
                           + str(body.get('message') or f'HTTP {status}'), data=body, ok=False)
        return _result('emergency_stop', 'failed',
                       f'ACİL DUR GÖNDERİLEMEDİ: robot API yanıt vermiyor ({last}); robotu elle '
                       'durdur')

    def _state(self) -> Dict[str, Any]:
        status, body = self._robot.get('/api/state')
        if status != 200 or not body:
            raise RobotUnreachable(f'/api/state HTTP {status}')
        return body

    def _state_tool(self, spec: ToolSpec) -> ToolResult:
        robot = self._state().get('robot') or {}
        return _result(spec.name, 'ok', f"robot durumu: {robot.get('state')}", data=robot)

    def _gate(self, tool: str, risk: str, robot: Dict[str, Any]) -> Optional[ToolResult]:
        """The state / busy / quota checks every non-safety tool passes (None: go on)."""
        if robot.get('state') == 'estopped' or self._stop_event.is_set():
            return _result(tool, 'estopped', 'acil dur etkin: hiçbir hareket verilmez; '
                           'kilidi yalnızca bir insan açabilir (POST /api/reset)')
        if risk != 'read' and robot.get('state') == 'busy':
            job = robot.get('current_job') or {}
            return _result(tool, 'refused',
                           f"robot meşgul: {job.get('id')} ({job.get('command')}) çalışıyor")
        if risk in ('motion', 'long') and self._motions >= self.policy.max_motion_calls:
            return _result(tool, 'refused', 'hareket çağrısı sınırı doldu '
                           f'({self.policy.max_motion_calls}); insan onayıyla yeniden başlat')
        return None

    def _needs_ok(self, tool: str, risk: str, arguments: Dict[str, Any], command: str,
                  target) -> Optional[ToolResult]:
        """Confirmation (None: given or not needed)."""
        if risk not in ('motion', 'long') or risk not in self.policy.confirm_risks:
            return None
        if self._confirm is None:
            return _result(tool, 'refused', 'onay gerekli ve onaylayan yok '
                           '(CLI: --yes ya da etkileşimli terminal)')
        if not self._confirm(ConfirmRequest(tool, dict(arguments), command, target)):
            return _result(tool, 'refused', 'kullanıcı onaylamadı; hareket verilmedi')
        return None

    def _robot_tool(self, spec: ToolSpec, arguments: Dict[str, Any], dry: bool) -> ToolResult:
        robot = self._state().get('robot') or {}
        refused = self._gate(spec.name, spec.risk, robot)
        if refused:
            return refused
        moves = spec.risk in ('motion', 'long')
        command = command_for(spec.name, arguments)
        target = None
        extra: Dict[str, Any] = {}
        if spec.name == 'go_above_object':
            checked = self._preflight_above(arguments, robot)
            if isinstance(checked, ToolResult):
                return checked
            target, extra = checked
        if dry and moves:
            return _result(spec.name, 'dry_run', f"kuru çalıştırma: '{command}' çalıştırılırdı; "
                           'hareket GÖNDERİLMEDİ', data={'would_run': command, 'target': target,
                                                         **extra}, dry_run=True)
        refused = self._needs_ok(spec.name, spec.risk, arguments, command, target)
        if refused:
            return refused
        if moves:
            self._motions += 1
        return self._run(spec, command, target, extra)

    # ---- run_plugin -----------------------------------------------------------------------------
    def _plugin_tool(self, spec: ToolSpec, arguments: Dict[str, Any], dry: bool) -> ToolResult:
        plugin, tool = arguments['plugin'], arguments['tool']
        label = f'{plugin}.{tool}'
        entry = self._plugins.tools().get(label)
        if entry is None:
            known = ', '.join(sorted(self._plugins.tools())) or 'yok'
            return _result(spec.name, 'rejected', f'{label!r} beyaz listede değil ya da eklenti '
                           f'kurulu değil; kullanılabilir eklenti araçları: {known}')
        raw = arguments.get('arguments')
        raw = {} if raw is None else raw
        problems = self._plugins.check_args(entry, raw)
        if problems:
            return _result(spec.name, 'rejected', f'geçersiz eklenti argümanı ({label}): '
                           + '; '.join(problems), risk=entry.risk)
        args = self._plugins.with_defaults(entry, raw)
        robot = self._state().get('robot') or {}
        refused = self._gate(label, entry.risk, robot)
        if refused:
            refused.risk = entry.risk
            return refused
        target = None
        extra: Dict[str, Any] = {'plugin': plugin, 'tool': tool, 'motion': entry.motion}
        if entry.motion == 'arm':
            if args.get('color') in COLORS:     # follow_color-style tools: the first target
                checked = self._preflight_above(
                    {'color': args['color'], 'height_m': args.get('height_m', 0.35)}, robot,
                    tool=label)
                if isinstance(checked, ToolResult):
                    checked.risk = entry.risk
                    return checked
                target, zone = checked
                extra.update(zone)
            else:
                extra['zone'] = ('hedef eklentiye bağlı: istemci tarafı denetim yok, yalnızca '
                                 'robotun kendi güvenli kutusu her işte geçerli')
        command = f'eklenti {label}'
        moves = entry.risk != 'read'
        if dry and moves:
            return _result(spec.name, 'dry_run', f'kuru çalıştırma: {command} {args} '
                           'çalıştırılırdı; hareket GÖNDERİLMEDİ',
                           data={'would_run': command, 'arguments': args, 'target': target,
                                 **extra}, dry_run=True, risk=entry.risk)
        refused = self._needs_ok(spec.name, entry.risk, {'plugin': plugin, 'tool': tool,
                                                         'arguments': args}, command, target)
        if refused:
            refused.risk = entry.risk
            return refused
        robot_url = getattr(self._robot, 'base_url', None)
        if robot_url is None and moves:
            return _result(spec.name, 'refused', 'robot adresi bilinmiyor: hareket eden eklenti '
                           'güvenli yoldan (Robot API) çalıştırılamaz', risk=entry.risk)
        if moves:
            self._motions += 1
        result = self._plugins.run(plugin, tool, args, robot_url=robot_url or 'fake',
                                   frames=self._plugin_frames, stop_event=self._stop_event)
        return self._from_plugin(spec.name, entry, result, extra, target)

    def _from_plugin(self, tool: str, entry: PluginTool, result: Dict[str, Any],
                     extra: Dict[str, Any], target) -> ToolResult:
        code = result.get('code')
        data: Dict[str, Any] = dict(extra)
        data.update(plugin_data=_shrink(result.get('data')), duration_s=result.get('duration_s'))
        if target:
            data['target'] = target
        if result.get('warning'):
            data['warning'] = result['warning']
        error = str(result.get('error') or code or 'eklenti hatası')
        out = ToolResult(tool, 'failed', error, data, risk=entry.risk)
        if result.get('ok'):
            out.status, out.ok, out.message = 'ok', True, f'{entry.key} tamam'
        elif code == 'estop' or self._stop_event.is_set():
            out.status = 'estopped'
            out.message = 'acil dur: eklenti durduruldu; robot kilitli (kilidi yalnızca insan açar)'
        elif code in ('unknown_plugin', 'unknown_tool', 'bad_parameters', 'bad_arguments',
                      'bad_robot_url', 'permission_denied'):
            out.status = 'rejected'
        elif code in ('stopped', 'interrupted'):
            out.status = 'cancelled'
        if entry.motion == 'arm':
            self._after_arm_plugin(out, code)
        return out

    def _after_arm_plugin(self, out: ToolResult, code: Optional[str]) -> None:
        """An arm tool may leave a robot job running when it times out / is killed: cancel it, and
        report an emergency stop that happened meanwhile."""
        try:
            if not out.ok and code in ('timeout', 'stopped', 'interrupted', 'exception',
                                       'not_running'):
                cancelled = self._cancel_job(out.tool)
                out.data['job_cleanup'] = cancelled.message
            if (self._state().get('robot') or {}).get('state') == 'estopped' \
                    and out.status != 'estopped':
                out.status, out.ok = 'estopped', False
                out.message = 'acil dur etkin: robot kilitli (kilidi yalnızca insan açar)'
        except RobotUnreachable as exc:
            out.data['job_cleanup'] = f'robot durumu okunamadı: {exc}'

    def _preflight_above(self, arguments: Dict[str, Any], robot: Dict[str, Any],
                         tool: str = 'go_above_object'):
        """Real object position -> target -> safe-box check: (target, extra) or a refusal."""
        found = self._run_read('objects')
        if isinstance(found, ToolResult):
            return found
        wanted = [o for o in found if o.get('label') == arguments['color']]
        if not wanted:
            return _result(tool, 'refused', f"{arguments['color']} nesne şu an "
                           'görünmüyor (algı çalışıyor mu?); hareket verilmedi')
        if len(wanted) > 1:
            return _result(tool, 'refused', f"birden fazla {arguments['color']} "
                           'nesne var; belirsiz hedef, hareket verilmedi')
        obj = wanted[0]
        height = float(arguments.get('height_m', 0.35))
        target = [obj['position'][0], obj['position'][1],
                  obj['position'][2] + obj['size'][2] / 2 + height]
        zone = ((robot.get('safety') or {}).get('zone') or {}).get('box')
        if not zone:
            return target, {'zone': 'robot güvenli kutu bildirmedi: istemci tarafı denetim yok'}
        box = SafeZone(tuple(zone[a][0] for a in 'xyz'), tuple(zone[a][1] for a in 'xyz'))
        outside = box.check(tuple(target))
        if outside:
            return _result(tool, 'refused', outside, data={'target': target})
        return target, {'zone': 'içeride'}

    def _run_read(self, command: str):
        """Run a read-only command as a job; the objects list, or a ToolResult on failure."""
        status, body = self._robot.post('/api/jobs', {'command': command, 'timeout_s': 30})
        if status != 202:
            return self._submit_failure('list_objects', status, body)
        doc = self._wait(body['id'], 30)
        if doc.get('state') != 'done':
            return _result('list_objects', 'failed', doc.get('error') or 'nesneler okunamadı')
        return list(((doc.get('result') or {}).get('data') or {}).get('objects') or [])

    def _submit_failure(self, tool: str, status: int, body: Optional[Dict[str, Any]]):
        message = (body or {}).get('error') or f'HTTP {status}'
        if status == 423:
            return _result(tool, 'estopped', message)
        if status == 409:
            return _result(tool, 'refused', message)
        if status == 400:
            return _result(tool, 'rejected', message)
        return _result(tool, 'failed', message)

    def _run(self, spec: ToolSpec, command: str, target, extra) -> ToolResult:
        status, body = self._robot.post('/api/jobs', {'command': command})
        if status != 202:
            return self._submit_failure(spec.name, status, body)
        doc = self._wait(body['id'], float(body.get('timeout_s') or 120))
        return self._from_job(spec, doc, target, extra)

    def _wait(self, job_id: str, timeout_s: float) -> Dict[str, Any]:
        deadline = self._clock() + timeout_s + self.policy.job_margin_s
        doc: Dict[str, Any] = {'id': job_id, 'state': 'running'}
        while self._clock() < deadline:
            if self._stop_event.is_set() and not self._stop_sent:
                self._emergency_stop('ajan: dış durdurma isteği')
            status, body = self._robot.get(f'/api/jobs/{job_id}')
            if status == 200 and body:
                doc = body
                if doc.get('state') != 'running':
                    return doc
            self._sleep(self.policy.poll_s)
        doc['error'] = 'iş beklenen sürede bitmedi (istemci zaman aşımı)'
        doc['state'] = 'timeout'
        return doc

    def _from_job(self, spec: ToolSpec, doc: Dict[str, Any], target, extra) -> ToolResult:
        state = doc.get('state')
        result = doc.get('result') or {}
        data = dict(result.get('data') or {})
        if target:
            data.setdefault('target', target)
        data.update(extra)
        job_id = doc.get('id')
        if state == 'done':
            return _result(spec.name, 'ok', result.get('message') or 'tamam', data, job_id)
        if state == 'cancelled':
            if doc.get('cancelled_by') == 'estop':
                return _result(spec.name, 'estopped', 'acil dur: iş iptal edildi; robot kilitli',
                               data, job_id)
            return _result(spec.name, 'cancelled', doc.get('error') or 'iş iptal edildi', data,
                           job_id)
        return _result(spec.name, 'failed', doc.get('error') or result.get('message')
                       or f'iş bitmedi ({state})', data, job_id)

    # ---- the event log ------------------------------------------------------------------------
    def _log(self, name: Any, arguments: Any, result: ToolResult, dry: bool, took: float) -> None:
        try:
            spec = self._tools.get(name) if isinstance(name, str) else None
            self._events.emit('agent_tool_call', {
                'tool': str(name)[:64], 'arguments': arguments if isinstance(arguments, dict)
                else str(arguments)[:80], 'risk': result.risk or (spec.risk if spec else None),
                'status': result.status, 'ok': result.ok, 'message': result.message[:300],
                'dry_run': dry, 'job_id': result.job_id, 'duration_s': round(took, 2)})
        except Exception:    # noqa: BLE001 - the log must never break a call
            log.exception('olay yazılamadı')


MAX_DATA_CHARS = 6000


def _shrink(data: Any) -> Any:
    """Plugin results can be large (scan_extent); keep what fits the model's context."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= MAX_DATA_CHARS:
        return data
    return {'truncated': True, 'chars': len(text), 'preview': text[:MAX_DATA_CHARS]}
