"""docker/annotate.py: GitHub annotations from log files (escaping, limits, selection)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'docker' / 'annotate.py'
spec = importlib.util.spec_from_file_location('annotate', SCRIPT)
annotate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(annotate)


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_escaping_follows_the_workflow_command_rules():
    assert annotate.escape_data('a%b\r\nc') == 'a%25b%0D%0Ac'
    assert annotate.escape_property('t: a,b%') == 't%3A a%2Cb%25'
    line = annotate.annotation('error', 'selftest: gazebo-cmd', 'x\ny')
    assert line == '::error title=selftest%3A gazebo-cmd::x%0Ay'
    assert '\n' not in line                       # a real newline would end the command


def test_unknown_level_is_rejected():
    with pytest.raises(ValueError):
        annotate.annotation('fatal', 't', 'm')


def test_long_messages_keep_the_end_and_say_so():
    lines = [f'satir {i}' for i in range(2000)]
    message = annotate.build_message(lines, max_chars=500)
    assert len(message) < 560 and message.startswith('… (başı kesildi)')
    assert message.endswith('satir 1999')


def test_long_lines_are_clipped():
    assert len(annotate.build_message(['x' * 1000], max_line=50)) == 50


def test_cli_last_lines(tmp_path):
    log = tmp_path / 'a.log'
    log.write_text('bir\niki\nüç\ndört\n', encoding='utf-8')
    out = run('--title', 'T', '--last', '2', str(log))
    assert out.returncode == 0 and out.stdout.strip() == '::error title=T::üç%0Adört'


def test_cli_grep_selects_failures_across_files(tmp_path):
    a, b = tmp_path / 'a.log', tmp_path / 'b.log'
    a.write_text('[PASS] x\n[FAIL] kırmızı 100%\n', encoding='utf-8')
    b.write_text('[FAIL] mavi\n[PASS] y\n', encoding='utf-8')
    out = run('--title', 'f', '--grep', r'\[FAIL\]', str(a), str(b))
    assert out.stdout.strip() == '::error title=f::[FAIL] kırmızı 100%25%0A[FAIL] mavi'


def test_cli_levels_and_missing_files(tmp_path):
    out = run('--level', 'warning', '--title', 'w', str(tmp_path / 'yok.log'))
    assert out.stdout.startswith('::warning title=w::') and 'okunamadı' in out.stdout
    empty = tmp_path / 'e.log'
    empty.write_text('', encoding='utf-8')
    assert run('--title', 'e', str(empty)).stdout == ''           # nothing to say: silent


def test_a_real_selftest_failure_shape(tmp_path):
    log = tmp_path / 'check.log'
    log.write_text('\n'.join([
        '[PASS] bir', '[FAIL] demo_scenario -> 200 (durum 422, START_STATE_INVALID)',
        '=== 53/54 test geçti ===']), encoding='utf-8')
    out = run('--title', 'selftest gazebo-cmd: 1 kontrol başarısız', '--grep', r'\[FAIL\]',
              str(log))
    assert out.stdout.count('\n') == 1                           # exactly one workflow command
    assert 'START_STATE_INVALID' in out.stdout
