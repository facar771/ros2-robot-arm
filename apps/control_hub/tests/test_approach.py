"""Dokun-git / Yaklaş (UI_EMIRLER S2 madde 6): hedef hesabı (saf işlev) ve "kontrol -> onay -> hareket" akışı.

Gerçek `web/approach.js` node ile sınanır (DOM/WebGL yok); node yoksa atlanır. Kanıtlar:
  * bilinen düzlemde hedef = nokta + normal·d (d = 0 ve d = 50 mm);
  * ulaşılamaz/güvenli kutu dışı hedefte hareket komutu GİTMEZ; ACİL DUR'da gitmez; bayat planla gitmez;
  * JS'in rpy sözleşmesi robot_core.se3 ile birebir aynı (ileri, geri, gimbal dahil).
"""
import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')


def run_node(script, *args):
    cmd = ['node', str(JS / script), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@needs_node
def test_approach_target_and_move_flow_in_node():
    assert 'APPROACH_JS_OK' in run_node('approach_check.js', WEB)


@needs_node
def test_js_rpy_convention_is_exactly_robot_cores(tmp_path):
    """UI'nin rpy sözleşmesi robot_core.se3 ile birebir aynı mı (ileri/geri, gimbal dahil)? Kanıt: sayılar."""
    se3 = pytest.importorskip('robot_core.se3')
    rng = random.Random(11)
    angles = [[rng.uniform(-180, 180), rng.uniform(-89, 89), rng.uniform(-180, 180)] for _ in range(40)]
    angles += [[0, 0, 0], [180, 0, 0], [0, 0, 90], [90, 0, 0], [0, 90, 0], [30, 90, 40], [30, -90, 40]]
    forward = [{'rpy': a, 'matrix': se3.matrix_from_wpr_display(*a).tolist()} for a in angles]
    inverse = []
    for a in angles:
        m = se3.matrix_from_wpr_display(*a)
        inverse.append({'matrix': m.tolist(), 'rpy': [math.degrees(v) for v in se3.wpr_from_matrix(m)[:3]]})
    ref = tmp_path / 'ref.json'
    ref.write_text(json.dumps({'forward': forward, 'inverse': inverse}), encoding='utf-8')
    assert 'APPROACH_JS_OK' in run_node('approach_check.js', WEB, ref)


def test_view_files_have_no_command_path_the_move_flow_lives_in_approach_js():
    """Görünüm dosyaları yazma fiili/`method:` içermez; hareket akışı approach.js'tedir."""
    for name in ('scene_logic.js', 'scene3d.js'):
        src = (WEB / name).read_text(encoding='utf-8')
        for verb in ("'POST'", "'PUT'", "'DELETE'", "'PATCH'", 'method:', '/api/move'):
            assert verb not in src, f'{name}: {verb}'
    flow = (WEB / 'approach.js').read_text(encoding='utf-8')
    assert "'/api/move/check'" in flow and "'/api/move'" in flow
    assert 'innerHTML' not in flow and 'eval(' not in flow and 'import ' not in flow
