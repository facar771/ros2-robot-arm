#!/usr/bin/env python3
"""Print GitHub Actions workflow annotations from log files (stdlib only).

Why: people who review a failed CI run often cannot open the raw step log, but they can always
read *annotations* (the red/yellow boxes on the run page and the PR checks). run_selftest.sh calls
this when a selftest fails so the failing checks and the last log lines show up there.

    annotate.py --level error --title "selftest gazebo-cmd" --grep '\\[FAIL\\]' check.log
    annotate.py --level warning --title "launch log" --last 25 launch.log

Output is one ``::error title=...::message`` line per call. Escaping follows the workflow-command
rules (message: % \\r \\n; properties additionally : and ,). The message is cut to --max-chars from
the *start*, keeping the end (the interesting part of a log), and says so.
"""
import argparse
import re
import sys

MAX_CHARS = 3500            # well below GitHub's per-annotation limit
MAX_LINE = 300
LEVELS = ('error', 'warning', 'notice')


def escape_data(text: str) -> str:
    return text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')


def escape_property(text: str) -> str:
    return escape_data(text).replace(':', '%3A').replace(',', '%2C')


def tail_lines(text: str, count: int):
    return text.splitlines()[-count:] if count > 0 else text.splitlines()


def build_message(lines, max_chars: int = MAX_CHARS, max_line: int = MAX_LINE) -> str:
    clipped = [ln if len(ln) <= max_line else ln[:max_line - 1] + '…' for ln in lines]
    message = '\n'.join(clipped)
    if len(message) > max_chars:
        message = '… (başı kesildi)\n' + message[-max_chars:]
    return message


def annotation(level: str, title: str, message: str) -> str:
    if level not in LEVELS:
        raise ValueError(f'level {level!r} geçersiz')
    return f'::{level} title={escape_property(title)}::{escape_data(message)}'


def read(path: str) -> str:
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError as exc:
        return f'(günlük okunamadı: {path}: {exc.strerror or exc})'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('files', nargs='+')
    parser.add_argument('--level', default='error', choices=LEVELS)
    parser.add_argument('--title', required=True)
    parser.add_argument('--last', type=int, default=25, help='last N lines (0 = all)')
    parser.add_argument('--grep', help='keep only lines matching this regex (then --last applies)')
    parser.add_argument('--max-chars', type=int, default=MAX_CHARS)
    args = parser.parse_args(argv)
    lines = []
    for path in args.files:
        text = read(path)
        if args.grep:
            pattern = re.compile(args.grep)
            lines += [ln for ln in text.splitlines() if pattern.search(ln)]
        else:
            lines += tail_lines(text, args.last)
    if args.grep:
        lines = lines[-args.last:] if args.last > 0 else lines
    if not lines:
        return 0                      # nothing to say is not an error
    print(annotation(args.level, args.title, build_message(lines, args.max_chars)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
