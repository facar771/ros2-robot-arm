"""``plugin_manager`` / ``python -m robot_plugins``: list, run and serve plugins locally."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from robot_plugins.api import PluginApiServer
from robot_plugins.invoke import catalog, default_plugins_dir, run_plugin  # noqa: E402
from robot_plugins.manager import PluginManager
from robot_plugins.robot_api import FakeRobotApi, HttpRobotApi


def build_manager(args) -> PluginManager:
    api = FakeRobotApi() if args.robot == 'fake' else HttpRobotApi(args.robot)
    return PluginManager(Path(args.plugins_dir), robot_api=api, robot_url=args.robot,
                         frames=args.frames, panner=args.panner,
                         state_file=Path(args.state) if args.state else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--plugins-dir', default=str(default_plugins_dir()))
    ap.add_argument('--robot', default='fake',
                    help="'fake' (benzetim, hiçbir şey hareket etmez) ya da loopback robot API")
    ap.add_argument('--frames', default='synthetic', help='none | synthetic | npz:<yol> | ros')
    ap.add_argument('--panner', default='synthetic', help='none | synthetic')
    ap.add_argument('--state', default='', help='açık eklentileri hatırlayan JSON dosyası')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    sub.add_parser('catalog', help='tüm araçlar (motion: none/camera/arm) — JSON')
    call = sub.add_parser('call', help='bir aracı bir kez çağır; JSON sonuç (invoke.run_plugin)')
    call.add_argument('plugin')
    call.add_argument('tool', nargs='?', default=None)
    call.add_argument('--args', default='{}')
    call.add_argument('--params', default='{}', help='eklenti başlangıç parametreleri (JSON)')
    call.add_argument('--timeout', type=float, default=None,
                      help='saniye (varsayılan: aracın timeout_s)')
    serve = sub.add_parser('serve', help='yönetim API sunucusu (yalnızca 127.0.0.1)')
    serve.add_argument('--port', type=int, default=8770)
    args = ap.parse_args(argv)

    if args.cmd == 'catalog':
        print(json.dumps(catalog(Path(args.plugins_dir)), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == 'call':
        try:
            call_args, call_params = json.loads(args.args), json.loads(args.params)
        except ValueError as exc:
            print(json.dumps({'api_version': 1, 'ok': False, 'code': 'bad_arguments',
                              'error': f'--args/--params geçerli JSON değil: {exc}'}))
            return 2
        result = run_plugin(args.plugin, args.tool, call_args, params=call_params,
                            plugins_dir=Path(args.plugins_dir), robot_url=args.robot,
                            frames=args.frames, panner=args.panner, timeout_s=args.timeout)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result['ok'] else 1
    mgr = build_manager(args)
    if args.cmd == 'list':
        print(json.dumps(mgr.list(), indent=2, ensure_ascii=False))
        return 0
    server = PluginApiServer(mgr, port=args.port)
    mgr.restore()
    if args.robot != 'fake':
        mgr.watch_robot()
    print(f'eklenti yönetim API: http://127.0.0.1:{server.port}/api/plugins '
          '(durdurmak için Ctrl-C)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mgr.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
