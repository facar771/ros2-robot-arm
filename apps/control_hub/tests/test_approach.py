"""Dokun-git / Yaklaş (UI_EMIRLER S2 madde 6): hedef hesabı (saf işlev, scene_logic.js) ve
"kontrol -> onay -> hareket" akışı (approach.js). Gerçek JS dosyaları node ile sınanır (DOM/WebGL yok);
node yoksa atlanır. Kanıtlar:
  * bilinen düzlemde hedef = nokta + normal·d (d = 0 ve d = 50 mm): önce saf hesapta, sonra GERÇEK SİM
    kinematiğinde
    (JS'in hedefi /api/move/check'ten geçer, /api/move ile TCP düzlemin d mm üstüne varır);
  * ulaşılamaz/güvenli kutu dışı hedefte hareket komutu GİTMEZ; ACİL DUR'da gitmez; bayat planla gitmez;
  * JS'in rpy sözleşmesi robot_core.se3 ile birebir aynı (ileri, geri, gimbal dahil);
  * görünüm dosyalarında komut yolu yoktur.
"""
import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app
from tests.conftest import CONFIG

WEB = Path(__file__).resolve().parents[1] / 'control_hub' / 'web'
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')
PLANE_Z_M = 0.09            # SİM ev TCP'sinin (z≈0.18) 9 cm altı: güvenli kutunun (z 0.02–0.22) içinde


def run_node(script, *args):
    cmd = ['node', str(JS / script), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def js_targets(cases):
    return json.loads(run_node('approach_targets.js', WEB, json.dumps(cases)))


@needs_node
def test_move_flow_check_then_confirm_then_move_in_node():
    assert 'APPROACH_JS_OK' in run_node('approach_check.js', WEB)


@needs_node
def test_scene_logic_se3_target_schema_in_node():
    assert 'SCENE_S2_JS_OK' in run_node('scene_s2_check.js', WEB)


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
    assert 'SCENE_S2_JS_OK' in run_node('scene_s2_check.js', WEB, ref)


def test_view_files_have_no_command_path_the_move_flow_lives_in_approach_js():
    """Görünüm dosyaları yazma fiili/`method:` içermez; hareket akışı approach.js'tedir."""
    for name in ('scene_logic.js', 'scene_hub.js', 'scene3d.js'):
        src = (WEB / name).read_text(encoding='utf-8')
        for verb in ("'POST'", "'PUT'", "'DELETE'", "'PATCH'", 'method:', '/api/move'):
            assert verb not in src, f'{name}: {verb}'
    flow = (WEB / 'approach.js').read_text(encoding='utf-8')
    assert "'/api/move/check'" in flow and "'/api/move'" in flow
    assert 'innerHTML' not in flow and 'eval(' not in flow and 'import ' not in flow


# --- GERÇEK SİM'e karşı: JS'in hedefi gerçek kinematikte doğru mu? -------------------------------------
@pytest.fixture
def sim_api():
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    sim = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(sim, scen, ui, AccessControl('open', '123456'))
    yield TestClient(create_app(hub), base_url='http://localhost'), sim
    hub.close()


def body_of(target, speed=100):
    return {'type': 'linear', 'pos_m': target['pos_m'], 'rpy_deg': target['rpy_deg'], 'speed_pct': speed}


def run_job(sim, job_id, limit_s=120):
    for _ in range(int(limit_s / sim.tick_s)):
        state = sim.move_get(job_id)[1]['job']['state']
        if state not in ('running', 'paused'):
            return state
        sim.advance(sim.tick_s)
    raise AssertionError('iş bitmedi')


@needs_node
@pytest.mark.parametrize('d_mm', [0, 50])
def test_touch_and_go_on_known_plane_reaches_d_above_it_in_sim(sim_api, d_mm):
    """KABUL: bilinen düzlemde dokun-git: d=0 düzleme, d=50 düzlemin 50 mm üstüne gider (gerçek kinematik)."""
    c, sim = sim_api
    tcp = sim.snapshot()['tcp']
    x, y = tcp['pos_m'][:2]
    (target,) = js_targets([{'point': [x, y, PLANE_Z_M], 'normal': [0, 0, 1], 'd': d_mm,
                             'opts': {'orientation': 'keep', 'currentRpy': tcp['rpy_deg']}}])
    assert target['pos_m'] == pytest.approx([x, y, PLANE_Z_M + d_mm / 1000], abs=1e-12)
    body = body_of(target)
    check = c.post('/api/move/check', json=body)
    assert check.status_code == 200 and check.json() == {'ok': True, 'violations': []}
    r = c.post('/api/move', json=body)
    assert r.status_code == 202
    assert run_job(sim, r.json()['job_id']) == 'done'
    assert sim.snapshot()['tcp']['pos_m'] == pytest.approx([x, y, PLANE_Z_M + d_mm / 1000], abs=1.5e-3)


@needs_node
def test_unreachable_touch_target_is_rejected_by_check_and_never_moves(sim_api):
    """KABUL: ulaşılamaz hedefte kontrol ihlal bildirir (istemci göndermez); sunucu yine de reddeder."""
    c, sim = sim_api
    tcp = sim.snapshot()['tcp']
    x, y = tcp['pos_m'][:2]
    before = sim.snapshot()['tcp']['pos_m']
    (below,) = js_targets([{'point': [x, y, 0.005], 'normal': [0, 0, 1], 'd': 0,
                            'opts': {'orientation': 'keep', 'currentRpy': tcp['rpy_deg']}}])    # kutunun altı
    (far,) = js_targets([{'point': [2.0, 0.0, 0.1], 'normal': [0, 0, 1], 'd': 50,
                          'opts': {'orientation': 'down'}}])                                   # menzil dışı
    for target in (below, far):
        body = body_of(target)
        check = c.post('/api/move/check', json=body).json()
        assert check['ok'] is False and check['violations'], check
        kinds = ('outside_safe_zone', 'joint_limit', 'path_blocked', 'unreachable')
        assert check['violations'][0]['kind'] in kinds
        assert check['violations'][0]['message']
        r = c.post('/api/move', json=body)
        assert r.status_code == 409 and r.json()['ok'] is False
    assert sim.snapshot()['tcp']['pos_m'] == before and not sim.snapshot()['moving']


@needs_node
def test_touch_and_go_returns_423_during_estop(sim_api):
    c, sim = sim_api
    tcp = sim.snapshot()['tcp']
    x, y = tcp['pos_m'][:2]
    (target,) = js_targets([{'point': [x, y, PLANE_Z_M], 'normal': [0, 0, 1], 'd': 50,
                             'opts': {'orientation': 'keep', 'currentRpy': tcp['rpy_deg']}}])
    assert c.post('/api/estop').status_code == 200
    r = c.post('/api/move', json=body_of(target))
    assert r.status_code == 423 and r.json()['error'] == 'estop_latched'
    assert not sim.snapshot()['moving']
