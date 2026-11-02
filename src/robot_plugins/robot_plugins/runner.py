"""Plugin process: ``python -m robot_plugins.runner --dir plugins/<name> ...``.

Protocol (one JSON object per line):
  manager -> plugin (stdin):  {"op": "call", "id": 7, "tool": "...", "args": {...}} | {"op": "stop"}
  plugin -> manager (stdout): {"type": "ready"} | {"type": "result", "id": 7, "ok": bool, ...}
                              | {"type": "log", "level": "info", "msg": "..."}
stdout belongs to the protocol: the plugin's own ``print`` is redirected to stderr.
Tools run in their own threads, so a ``stop`` can arrive while one is still running; the stop token
is then set and the tool is expected to return promptly. Closing stdin also means stop.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from typing import Any, Dict

from robot_plugins.context import PermissionDenied, PluginContext, Stopped, StopToken
from robot_plugins.frames import make_frame_source
from robot_plugins.loader import load_plugin_class
from robot_plugins.manifest import load_manifest
from robot_plugins.panning import make_panner
from robot_plugins.robot_api import FakeRobotApi, HttpRobotApi, RobotApiError
from robot_plugins.schema import ValidationError, apply_defaults, validate

EXIT_GRACE = 5.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--params', default='{}')
    ap.add_argument('--frames', default='none')
    ap.add_argument('--robot', default='fake', help="'fake' ya da robot API adresi")
    ap.add_argument('--panner', default='none', help='none | synthetic')
    ap.add_argument('--frame-wait', type=float, default=5.0,
                    help='ilk kamera karesi için en çok bekleme (s)')
    args = ap.parse_args(argv)

    proto = sys.stdout
    sys.stdout = sys.stderr              # plugin prints must not corrupt the protocol
    send_lock = threading.Lock()

    def send(obj: Dict[str, Any]) -> None:
        with send_lock:
            proto.write(json.dumps(obj, default=str) + '\n')
            proto.flush()

    manifest = load_manifest(args.dir)
    params = manifest.resolve_params(json.loads(args.params))
    api = FakeRobotApi() if args.robot == 'fake' else HttpRobotApi(args.robot)
    stop = StopToken()
    ctx = PluginContext(manifest.name, params, frozenset(manifest.granted),
                        make_frame_source(args.frames), api,
                        lambda level, msg: send({'type': 'log', 'level': level, 'msg': msg}), stop,
                        make_panner(args.panner), frame_wait_s=args.frame_wait)
    plugin = load_plugin_class(manifest)(ctx)
    plugin.start()
    send({'type': 'ready', 'name': manifest.name, 'version': manifest.version})

    workers = []

    def run_call(req: Dict[str, Any]) -> None:
        rid, tool = req.get('id'), req.get('tool')
        try:
            schema = manifest.tool(tool).input_schema
            call_args = apply_defaults(req.get('args') or {}, schema)
            validate(call_args, schema)
            result = plugin.call(tool, call_args)
            if not isinstance(result, dict):
                raise TypeError('araç sonucu dict olmalı')
            send({'type': 'result', 'id': rid, 'ok': True, 'data': result})
        except KeyError:
            send({'type': 'result', 'id': rid, 'ok': False, 'code': 'unknown_tool',
                  'error': f'bilinmeyen araç {tool!r}'})
        except ValidationError as exc:
            send({'type': 'result', 'id': rid, 'ok': False, 'code': 'bad_arguments',
                  'error': str(exc)})
        except PermissionDenied as exc:
            send({'type': 'result', 'id': rid, 'ok': False, 'code': 'permission_denied',
                  'error': str(exc)})
        except Stopped:
            send({'type': 'result', 'id': rid, 'ok': False, 'code': 'stopped',
                  'error': 'eklenti durduruldu'})
        except RobotApiError as exc:
            send({'type': 'result', 'id': rid, 'ok': False, 'code': f'robot_{exc.status}',
                  'error': exc.message})
        except Exception as exc:                            # noqa: BLE001 - report, never crash
            traceback.print_exc()
            send({'type': 'result', 'id': rid, 'ok': False, 'code': 'exception',
                  'error': f'{type(exc).__name__}: {exc}'})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get('op') == 'stop':
            stop.set()
            break
        if msg.get('op') == 'call':
            t = threading.Thread(target=run_call, args=(msg,), daemon=True)
            t.start()
            workers.append(t)
    stop.set()                                              # EOF or explicit stop
    for t in workers:
        t.join(timeout=EXIT_GRACE)
    try:
        plugin.shutdown()
    except Exception:                                       # noqa: BLE001
        traceback.print_exc()
    return 0


if __name__ == '__main__':
    sys.exit(main())
