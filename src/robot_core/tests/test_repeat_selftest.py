"""docker/repeat_selftest.sh (flaky detection) with a stub `docker`, and the reproducibility rules:
digest-pinned images, hash-locked Python deps, every selftest mode run by CI *and* the nightly."""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
REPEAT = ROOT / 'docker' / 'repeat_selftest.sh'


@pytest.fixture
def stub(tmp_path):
    """A fake `docker`: exit codes come from STUB_CODES (one per call); failures print an
    annotation like run_selftest.sh does."""
    script = tmp_path / 'docker'
    script.write_text('''#!/usr/bin/env bash
codes=($STUB_CODES)
n=$(cat "$STUB_COUNT" 2>/dev/null || echo 0)
code=${codes[$n]:-0}
echo $((n + 1)) >"$STUB_COUNT"
echo "[mode] $5 (çağrı $((n + 1)))"
if [ "$code" -ne 0 ]; then
    echo "::error title=selftest $5%3A başarısız::[FAIL] bir şey düştü"
fi
exit $code
''')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def repeat(stub, codes, tmp_path, runs=3, mode='gazebo-cmd'):
    summary = tmp_path / 'summary.md'
    env = dict(os.environ, DOCKER=str(stub), STUB_CODES=' '.join(map(str, codes)),
               STUB_COUNT=str(tmp_path / 'count'), LOG_DIR=str(tmp_path / 'logs'),
               GITHUB_STEP_SUMMARY=str(summary))
    out = subprocess.run(['bash', str(REPEAT), 'img:ci', mode, str(runs)], capture_output=True,
                         text=True, env=env, timeout=60)
    return out, summary.read_text(encoding='utf-8') if summary.exists() else ''


def test_all_green_is_stable(stub, tmp_path):
    out, summary = repeat(stub, [0, 0, 0], tmp_path)
    assert out.returncode == 0
    assert '::notice title=gecelik gazebo-cmd: STABİL (3/3)::' in out.stdout
    assert out.stdout.count('GEÇTİ') == 3 and '::error' not in out.stdout
    assert '### gazebo-cmd: STABİL (3/3)' in summary and summary.count('GEÇTİ') == 3


def test_a_flaky_mode_is_reported_as_flaky_and_fails_the_job(stub, tmp_path):
    out, summary = repeat(stub, [0, 3, 0], tmp_path)
    assert out.returncode == 1
    assert '::warning title=gecelik gazebo-cmd: KARARSIZ (2/3)::' in out.stdout
    assert 'bazen geçip bazen düşüyor' in out.stdout
    # the failed run's own annotation is repeated, tagged with the run number
    assert '::error title=[koşu 2/3] selftest gazebo-cmd%3A başarısız::[FAIL] bir şey düştü' \
        in out.stdout
    assert 'koşu 2/3: BAŞARISIZ (çıkış 3)' in out.stdout and 'KARARSIZ' in summary


def test_always_failing_is_an_error(stub, tmp_path):
    out, _ = repeat(stub, [1, 1, 1], tmp_path)
    assert out.returncode == 1
    assert '::error title=gecelik gazebo-cmd: BAŞARISIZ (0/3)::3/3 koşu başarısız' in out.stdout


def test_run_count_and_logs(stub, tmp_path):
    out, _ = repeat(stub, [0, 0], tmp_path, runs=2, mode='mock')
    assert 'koşu 2/2: GEÇTİ' in out.stdout and 'koşu 3' not in out.stdout
    logs = sorted(p.name for p in (tmp_path / 'logs').iterdir())
    assert logs == ['mock-1.log', 'mock-2.log']
    assert 'çağrı 2' in (tmp_path / 'logs' / 'mock-2.log').read_text(encoding='utf-8')


def test_usage_error():
    out = subprocess.run(['bash', str(REPEAT)], capture_output=True, text=True)
    assert out.returncode != 0 and 'usage' in out.stderr


# ---- reproducibility rules ----------------------------------------------------------------
DIGEST = re.compile(r'@sha256:[0-9a-f]{64}')


def test_the_dockerfile_base_image_is_pinned_by_digest():
    text = (ROOT / 'docker' / 'Dockerfile').read_text(encoding='utf-8')
    default = re.search(r'^ARG ROS_BASE_IMAGE=(\S+)$', text, re.M)
    assert default and DIGEST.search(default.group(1)), 'ROS_BASE_IMAGE needs a digest'
    assert re.search(r'^FROM \$\{ROS_BASE_IMAGE\} AS base$', text, re.M)
    assert not re.search(r'^FROM ros:', text, re.M)         # no unpinned FROM sneaks back in


def test_other_images_are_pinned_too():
    for path in (ROOT / 'compose.yaml', ROOT / '.github' / 'workflows' / 'ci.yml'):
        for image in re.findall(r'^\s*image:\s*(postgres\S*)$', path.read_text(encoding='utf-8'),
                                re.M):
            assert DIGEST.search(image), f'{path.name}: {image} is not pinned by digest'


def test_python_dependencies_are_hash_locked():
    for name in ('docker', 'ci-core', 'ci-data'):
        wanted = [ln.strip().split('>=')[0].split('[')[0].lower()
                  for ln in (ROOT / 'requirements' / f'{name}.in').read_text(
                      encoding='utf-8').splitlines() if ln.strip() and not ln.startswith('#')]
        locked = (ROOT / 'requirements' / f'{name}.txt').read_text(encoding='utf-8')
        pins = re.findall(r'^([A-Za-z0-9_.\-]+)==\S+ \\$', locked, re.M)
        names = {p.lower().replace('_', '-') for p in pins}
        for package in wanted:
            assert package.replace('_', '-') in names, f'{name}.txt lacks {package}'
        assert locked.count('--hash=sha256:') >= len(pins)      # every pin carries hashes
    ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
    assert 'pip install pytest' not in ci and 'pip install --require-hashes' in ci
    docker = (ROOT / 'docker' / 'Dockerfile').read_text(encoding='utf-8')
    assert '--require-hashes' in docker and 'sqlalchemy>=2.0' not in docker


def selftest_modes():
    text = (ROOT / 'docker' / 'run_selftest.sh').read_text(encoding='utf-8')
    usage = re.search(r'usage: run_selftest \[([^\]]+)\]', text).group(1)
    return usage.split('|')


def test_every_selftest_mode_runs_in_ci_and_in_the_nightly():
    modes = selftest_modes()
    assert 'mock' in modes and len(modes) >= 10
    ci = (ROOT / 'docker' / '..' / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
    nightly = yaml.safe_load((ROOT / '.github' / 'workflows' / 'nightly.yml').read_text(
        encoding='utf-8'))
    matrix = nightly['jobs']['selftests']['strategy']['matrix']['mode']
    for mode in modes:
        assert f'run_selftest {mode}' in ci, f'CI does not run {mode}'
        assert mode in matrix, f'nightly does not run {mode}'
    assert set(matrix) == set(modes)


def test_nightly_is_scheduled_and_repeats():
    nightly = yaml.safe_load((ROOT / '.github' / 'workflows' / 'nightly.yml').read_text(
        encoding='utf-8'))
    triggers = nightly.get('on') or nightly.get(True)       # YAML 1.1 reads `on` as True
    assert triggers['schedule'][0]['cron'] and 'workflow_dispatch' in triggers
    steps = nightly['jobs']['selftests']['steps']
    assert any('repeat_selftest.sh' in s.get('run', '') for s in steps)
    assert nightly['jobs']['selftests']['strategy']['fail-fast'] is False


def test_run_selftest_writes_annotations_on_failure():
    text = (ROOT / 'docker' / 'run_selftest.sh').read_text(encoding='utf-8')
    assert 'annotate_failure "başarısız"' in text and 'tee -a "$CHECK_LOG"' in text
    assert 'PYTHONUNBUFFERED' in text
    # every early `exit 2` path annotates first
    for line in text.splitlines():
        if line.strip().endswith('exit 2') and 'annotate_failure' not in line:
            pytest.fail(f'exit 2 without annotation: {line}')
