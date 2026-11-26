"""R8: ortak mesaj satırı HubPages.say — node sarmalayıcı + 6 kopyanın gerçekten ona geçtiği ve DOM
yazan yerel kopyanın kalmadığı (dosya taraması). scene_hub.js bilerek kapsam dışıdır (PR §6)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
WEB = HERE.parent / 'control_hub' / 'web'

# dosya -> (HubPages.say çağrısının beklenen biçimi)
MIGRATED = {
    'frames.js': r"HubPages\.say\(q\('fw-msg'\), text, bad\)",
    'sphere.js': r"HubPages\.say\(q\('sw-msg'\), text, bad\)",
    'jog.js': r"HubPages\.say\(q\('jp-msg'\), text, bad\)",
    'bench.js': r"HubPages\.say\(q\('bn-msg'\), lines, bad, \{ list: true \}\)",
    'pages/jog.js': r"HubPages\.say\(q\('jp-gmsg'\), text, bad\)",
    'pages/algo.js': r"HubPages\.say\(Q\.msg, text, bad\)",
}
# kopyanın imzası: mesaj düğümüne doğrudan yazan kalıplar (ortak yazıcıdan başka yerde olmamalı)
LOCAL_WRITE = (
    re.compile(r"textContent = text \|\| ''; \w+\.classList\.toggle\('bad'"),
    re.compile(r"\.className = `say \$\{"),
    re.compile(r"replaceChildren\(\.\.\.\(lines \|\| \[\]\)"),
)


def read(name):
    return (WEB / name).read_text(encoding='utf-8')


def test_say_in_node():
    if shutil.which('node') is None:
        pytest.skip('node yok')
    script = HERE / 'js' / 'hubpages_say_check.js'
    r = subprocess.run(['node', str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and 'HUBPAGES_SAY_OK' in r.stdout, r.stdout + r.stderr


def test_six_files_use_the_shared_say_and_keep_no_local_writer():
    for name, call in MIGRATED.items():
        src = read(name)
        assert re.search(call, src), f'{name}: {call} çağrısı yok'
        for pat in LOCAL_WRITE:
            assert not pat.search(src), f'{name}: yerel mesaj yazıcı kalmış ({pat.pattern})'
    # pages/jog.js'in 3B hedef mesajı da aynı yazıcıdan geçer (eskiden korumasız kopya)
    tmsg = r"tmsg = \(text, bad\) => HubPages\.say\(q\('jp-tmsg'\), text, bad\)"
    assert re.search(tmsg, read('pages/jog.js'))


def test_single_definition_lives_in_pages_js():
    page = read('pages.js')
    assert page.count('function say(node, text, bad, opts)') == 1
    assert re.search(r"return \{[^}]*\bsay\b[^}]*\}", page)
    # pages.js dışında "say(node, ..." tanımı yok (parts.js: kendi say(n, text, cls) imzası, Robot_11'in işi)
    for f in WEB.rglob('*.js'):
        rel = f.relative_to(WEB).as_posix()
        if rel in ('pages.js', 'pages/parts.js'):
            continue
        assert 'function say(node' not in f.read_text(encoding='utf-8'), rel


def test_files_stay_small_and_unsafe_dom_free():
    unsafe = re.compile(r'\.innerHTML\b|\binsertAdjacentHTML\b|\beval\(|document\.write')
    for name in list(MIGRATED) + ['pages.js']:
        src = read(name)
        assert len(src.splitlines()) < 400, name
        assert not unsafe.search(src), name
