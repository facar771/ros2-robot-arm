"""``plugin_manager`` / ``python -m robot_plugins``: list, run and serve plugins locally."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from robot_plugins.api import PluginApiServer
from robot_plugins.manager import ManagerError, PluginManager
from robot_plugins.robot_api import FakeRobotApi, HttpRobotApi


def default_plugins_dir() -> Path:
    return Path(__file__).resolve().parents[3] / 'plugins'


def build_manager(args) -> PluginManager:
    api = FakeRobotApi() if args.robot == 'fake' else HttpRobotApi(args.robot)
    return PluginManager(Path(args.plugins_dir), robot_api=api, robot_url=args.robot,
                         frames=args.frames, state_file=Path(args.state) if args.state else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--plugins-dir', default=str(default_plugins_dir()))
    ap.add_argument('--robot', default='fake', help="'fake' ya da robot API adresi (http://127.0.0.1:8080)")
    ap.add_argument('--frames', default='synthetic', help='none | synthetic | npz:<yol>')
    ap.add_argument('--state', default='', help='açık eklentileri hatırlayan JSON dosyası')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    call = sub.add_parser('call', help='bir aracı çağır (geçici olarak etkinleştirir)')
    call.add_argument('plugin')
    call.add_argument('tool')
    call.add_argument('--args', default='{}')
    serve = sub.add_parser('serve', help='yönetim API sunucusu (yalnızca 127.0.0.1)')
    serve.add_argument('--port', type=int, default=8770)
    args = ap.parse_args(argv)

    mgr = build_manager(args)
    if args.cmd == 'list':
        print(json.dumps(mgr.list(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == 'call':
        try:
            mgr.enable(args.plugin)
            print(json.dumps(mgr.call(args.plugin, args.tool, json.loads(args.args)), indent=2,
                             ensure_ascii=False))
        except ManagerError as exc:
            print(f'hata: {exc}')
            return 1
        finally:
            mgr.shutdown()
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
