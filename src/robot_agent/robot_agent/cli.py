"""robot_agent: natural-language request -> LLM -> white-listed tools -> Robot API (127.0.0.1).

    robot_agent "yeşil nesnenin üstüne git"             # needs ANTHROPIC_API_KEY; asks first
    robot_agent --offline --yes "yeşil nesnenin üstüne git"   # no key: built-in phrase parser
    robot_agent --dry-run "kırmızıya git"                # shows what would run; sends no motion

Exit code: 0 everything worked, 1 a tool call failed or was refused, 2 setup problem (no key,
bad URL), 3 the run ended in an emergency stop, 130 interrupted (Ctrl-C sends an emergency stop).
"""
from __future__ import annotations

import argparse
import signal
import sys
from typing import List, Optional

from robot_core.ports import NullEventSink

from robot_agent.agent import AgentRunner
from robot_agent.client import DEFAULT_URL, HttpRobot
from robot_agent.executor import ConfirmRequest, Policy, ToolExecutor
from robot_agent.llm import (
    ENV_KEY,
    OPENAI_ENV_KEY,
    OPENAI_ENV_MODEL,
    OPENAI_ENV_URL,
    AnthropicLLM,
    LLMError,
    OpenAILLM,
    RuleLLM,
)
from robot_agent.plugins import DEFAULT_ALLOWED, PluginBridge
from robot_agent.redact import install_log_scrubbing, scrub


def _events():
    try:
        from robot_data import event_sink_from_env
        return event_sink_from_env('robot_agent')
    except Exception:    # noqa: BLE001 - the data layer is optional; the agent works without it
        return NullEventSink()


def _ask(request: ConfirmRequest) -> bool:
    answer = input(f'ONAY: robot hareket edecek: {request.describe()}\nDevam? [e/H] ')
    return answer.strip().lower() in ('e', 'evet', 'y', 'yes')


def main(argv: Optional[List[str]] = None, *, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog='robot_agent', description=__doc__.split('\n')[0])
    parser.add_argument('request', nargs='+', help='ne yapılmasını istediğin (Türkçe)')
    parser.add_argument('--url', default=DEFAULT_URL, help='robot API (yalnızca 127.0.0.1)')
    parser.add_argument('--offline', action='store_true',
                        help='LLM yerine yerleşik kural çözücü (anahtar gerekmez)')
    parser.add_argument('--yes', action='store_true', help='hareket onaylarını otomatik ver')
    parser.add_argument('--dry-run', action='store_true',
                        help='hareket göndermeden ne olacağını göster')
    parser.add_argument('--max-motions', type=int, default=10, help='en çok hareket çağrısı')
    parser.add_argument('--max-steps', type=int, default=8, help='en çok LLM adımı')
    parser.add_argument('--provider', choices=('anthropic', 'openai'), default='anthropic',
                        help='LLM sağlayıcısı (varsayılan: anthropic)')
    parser.add_argument('--model', help='LLM modeli (anthropic: ROBOT_AGENT_MODEL ya da yerleşik; '
                                        f'openai: zorunlu, {OPENAI_ENV_MODEL})')
    parser.add_argument('--base-url', help=f'openai uç adresi (zorunlu, {OPENAI_ENV_URL}); '
                                           'https olmalı, http yalnızca yerel makinede')
    parser.add_argument('--allow-plugin', action='append', default=[], metavar='EKLENTİ.ARAÇ',
                        help='ek eklenti aracına izin ver (ör. benim_eklentim.arac); '
                             'varsayılan: find_object, scan_extent, follow_color')
    parser.add_argument('--no-plugins', action='store_true',
                        help='run_plugin aracını kapat (yalnızca yerleşik araçlar)')
    parser.add_argument('--frames', default='ros',
                        help="eklentilerin kare kaynağı: ros (canlı kamera, varsayılan) | none | "
                             "synthetic")
    args = parser.parse_args(argv)

    install_log_scrubbing()

    def say(text: str) -> None:
        print(scrub(text), file=out, flush=True)

    try:
        robot = HttpRobot(args.url)
    except ValueError as exc:
        say(f'[!] {exc}')
        return 2
    if args.offline:
        llm = RuleLLM()
    elif args.provider == 'openai':
        try:
            llm = OpenAILLM.from_env(model=args.model, base_url=args.base_url)
        except ValueError as exc:
            say(f'[!] OpenAI yapılandırması eksik/geçersiz: {exc}')
            return 2
        if llm is None:
            say(f'[!] {OPENAI_ENV_KEY} ayarlı değil: LLM katmanı kapalı. Anahtarsız denemek için '
                '--offline kullan.')
            return 2
    else:
        llm = AnthropicLLM.from_env(model=args.model)
        if llm is None:
            say(f'[!] {ENV_KEY} ayarlı değil: LLM katmanı kapalı. Anahtarsız denemek için '
                '--offline kullan.')
            return 2
    confirm = (lambda req: (say(f'[onay otomatik] {req.describe()}'), True)[1]) if args.yes \
        else (_ask if sys.stdin.isatty() else None)
    events = _events()
    bridge = PluginBridge(frozenset() if args.no_plugins
                          else DEFAULT_ALLOWED | frozenset(args.allow_plugin))
    executor = ToolExecutor(robot, Policy(max_motion_calls=args.max_motions), events, confirm,
                            dry_run=args.dry_run, plugins=bridge, plugin_frames=args.frames)
    if not args.no_plugins and not bridge.tools():
        say(f'[!] eklenti aracı yok ({bridge.problem}); run_plugin kapalı, diğer araçlar çalışır.')
    runner = AgentRunner(llm, executor, max_steps=args.max_steps)

    def on_interrupt(signum, frame):    # Ctrl-C = emergency stop, then leave
        result = executor.request_stop('Ctrl-C')
        say(f'[!] kesildi: acil dur: {result.message}')
        raise SystemExit(130)
    previous = signal.signal(signal.SIGINT, on_interrupt)
    try:
        run = runner.run(' '.join(args.request))
    except LLMError as exc:
        say(f'[!] LLM hatası: {exc}')
        return 2
    finally:
        signal.signal(signal.SIGINT, previous)
    for name, arguments, result in run.calls:
        mark = '[+]' if result.ok else '[-]'
        say(f'{mark} {name} {arguments or ""} -> {result.status}: {result.message}')
    if run.text:
        say(run.text)
    if run.halted == 'acil dur':
        return 3
    return 0 if run.all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
