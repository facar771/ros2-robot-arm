"""A1 Algoritmalar uçları: UI_CONTRACT §5 şablonu, kuru doğrulama (SAHTE ve GERÇEK SİM), ihlalin satıra
yazılması, robotun OYNAMAMASI ve üretilen programın SİM'de gerçekten çalışması."""
import time

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import AccessControl, Hub, load_scenarios, load_ui_config
from control_hub.hub_core.sim_backend import SimBackend, SimClock
from control_hub.server import create_app, registry
from tests.conftest import CONFIG

# 3. hücre y = 120 mm: güvenli kutunun dışında (|y| ≤ 100 mm)
GRID_1X3 = {'rows': 1, 'cols': 3, 'place_y_mm': 60, 'pitch_mm': 30}
# SAHTE arka ucun uydurma engeli (x 0.15–0.25) dışında kalan noktalar
MOCK_FREE = {'rows': 2, 'cols': 3, 'pick_x_mm': 400, 'pick_y_mm': 300, 'pick_z_mm': 100,
             'place_x_mm': 450, 'place_y_mm': 200, 'place_z_mm': 100}


def gen(c, kind, params=None, **extra):
    return c.post('/api/algo/generate', json={'kind': kind, 'params': params or {}, **extra})


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('HUB_DATA_DIR', str(tmp_path / 'data'))


@pytest.fixture
def api():
    """GERÇEK SİM arka ucu (sahte saat: robot ancak ``advance`` ile oynar)."""
    scen = load_scenarios(CONFIG / 'scenarios.yaml')
    ui = load_ui_config(CONFIG / 'ui.config.json', scen)
    backend = SimBackend(clock=SimClock(), autostart=False)
    hub = Hub(backend, scen, ui, AccessControl('open', '123456'))
    client = TestClient(create_app(hub), base_url='http://localhost')
    yield client, hub, backend
    client.app.state.program_runner.cancel('test sonu')
    hub.close()


def joints(b):
    return [j['deg'] for j in b.snapshot()['joints']]


# --- UI_CONTRACT §5 şablonu ------------------------------------------------------------------------
def test_routes_are_auto_installed_and_hidden_without_login(make_client):
    assert 'algo_routes' in registry.route_module_names()
    pin, _ = make_client('pin')
    assert pin.get('/api/algo/kinds').status_code == 401
    assert pin.post('/api/algo/generate', json={}).status_code == 401


def test_locked_mode_may_read_the_definitions_but_not_generate(make_client):
    c, _ = make_client('locked')
    assert c.get('/api/algo/kinds').status_code == 200
    assert gen(c, 'grid').status_code == 403         # kuru doğrulama da kontrol ister (move/check gibi)


def test_post_needs_json_content_type_like_every_api_post(make_client):
    c, _ = make_client('open')
    assert c.post('/api/algo/generate', content='{}').status_code == 415


def test_page_is_listed_and_files_are_served(make_client):
    c, _ = make_client('open')
    assert 'algo' in [p['id'] for p in c.get('/api/pages').json()['pages']]
    assert c.get('/pages/algo.js').status_code == 200 and c.get('/pages/algo.css').status_code == 200


def test_kinds_lists_three_generators_with_fields_and_limits(make_client):
    c, _ = make_client('open')
    body = c.get('/api/algo/kinds').json()
    assert body['ok'] and body['limits'] == {'max_steps': 500}
    assert [k['id'] for k in body['kinds']] == ['grid', 'arc', 'zigzag']
    grid = body['kinds'][0]['fields']
    assert [f['key'] for f in grid][:4] == ['rows', 'cols', 'pitch_mm', 'approach_mm']
    assert all('default' in f and f['label'] for f in grid)


# --- parametre ve gövde hataları -------------------------------------------------------------------
@pytest.mark.parametrize('body, status, error', [
    ({'kind': 'torus'}, 422, 'invalid_params'), ({'kind': 5}, 422, 'invalid_params'),
    ({}, 422, 'invalid_params'),
    ({'kind': 'grid', 'params': {'rows': 0}}, 422, 'invalid_params'),
    ({'kind': 'grid', 'params': [1]}, 422, 'invalid_params'),
    ({'kind': 'grid', 'params': {'rows': 20, 'cols': 20}}, 422, 'invalid_params'),      # 500 adım sınırı
    ({'kind': 'grid', 'speed_pct': 0}, 400, 'bad_request'),
    ({'kind': 'grid', 'speed_pct': 101}, 400, 'bad_request'),
    ({'kind': 'grid', 'speed_pct': True}, 400, 'bad_request'), ([1], 400, 'bad_request')])
def test_bad_requests_are_refused_with_an_error_body(make_client, body, status, error):
    c, _ = make_client('open')
    r = c.post('/api/algo/generate', json=body)
    assert r.status_code == status and r.json()['ok'] is False and r.json()['error'] == error
    assert r.json()['message']


def test_parameter_errors_name_every_bad_field(make_client):
    c, _ = make_client('open')
    r = gen(c, 'arc', {'radius_mm': 0, 'sweep_deg': 0, 'plane': 'ab'})
    assert {e['field'] for e in r.json()['errors']} == {'radius_mm', 'sweep_deg', 'plane'}


def test_nan_infinity_and_junk_bodies_are_rejected(make_client):
    c, _ = make_client('open')
    h = {'content-type': 'application/json'}
    assert c.post('/api/algo/generate', content='{"kind":"grid","params":{"pitch_mm":NaN}}', headers=h
                  ).status_code == 422
    assert c.post('/api/algo/generate', content='{"kind":"grid","params":{"pitch_mm":Infinity}}', headers=h
                  ).status_code == 422
    assert c.post('/api/algo/generate', content='{kötü', headers=h).status_code == 400
    assert c.post('/api/algo/generate', content='{"kind":"grid","x":"' + 'a' * 9000 + '"}', headers=h
                  ).status_code == 413


# --- SAHTE (mock) arka uç ----------------------------------------------------------------------------
def test_generate_on_the_mock_backend_returns_steps_preview_and_a_check(make_client):
    c, _ = make_client('open')
    d = gen(c, 'grid', MOCK_FREE).json()
    assert d['ok'] and d['kind'] == 'grid' and d['count'] == len(d['steps']) == 55
    assert d['backend'] == {'label': 'SAHTE (mock)', 'measured': False, 'mock': True}
    assert d['check']['ok'] and d['check']['violations'] == 0
    moves_and_tools = [i for i, s in enumerate(d['steps']) if s['kind'] in ('move_l', 'tool')]
    assert [r['line'] for r in d['check']['rows']] == moves_and_tools      # not/bekleme satırı denetlenmez
    assert len(d['points']) == 36 and d['marks'][0]['role'] == 'pick'
    assert all(s['orientation'] == 'free' and 'rpy_deg' not in s for s in d['steps'] if s['kind'] == 'move_l')


def test_mock_flags_a_point_outside_its_box(make_client):
    c, _ = make_client('open')
    d = gen(c, 'arc', {'center_x_mm': 700, 'radius_mm': 40}).json()          # mock kutusu x ≤ 0.6 m
    assert d['check']['ok'] is False
    assert {v['kind'] for r in d['check']['rows'] for v in r['violations']} == {'outside_safe_zone'}


# --- GERÇEK SİM --------------------------------------------------------------------------------------
@pytest.mark.parametrize('kind', ['grid', 'arc', 'zigzag'])
def test_default_parameters_pass_the_real_sim_dry_run(api, kind):
    c, _, _ = api
    d = gen(c, kind).json()
    assert d['backend'] == {'label': 'SİM', 'measured': False, 'mock': False}
    assert d['check']['ok'] is True and d['check']['violations'] == 0, d['check']
    assert d['check']['rows'] and all(r['ok'] for r in d['check']['rows'])


def test_a_step_outside_the_safe_box_is_red_and_only_that_cell(api):
    c, _, _ = api
    d = gen(c, 'grid', GRID_1X3).json()
    assert d['check']['ok'] is False and d['check']['violations'] == 3
    bad = [r for r in d['check']['rows'] if not r['ok']]
    assert all(v['kind'] == 'outside_safe_zone' for r in bad for v in r['violations'])
    for row in d['check']['rows']:                      # kırmızı olan = |y| > 100 mm olan hareket adımı
        step = d['steps'][row['line']]
        outside = step['kind'] == 'move_l' and abs(step['pos_m'][1]) > 0.1
        assert (not row['ok']) == outside, (row['line'], step)
    assert [d['steps'][r['line']]['pos_m'] for r in bad] == [
        [0.1, 0.12, 0.08], [0.1, 0.12, 0.05], [0.1, 0.12, 0.08]]
    assert all(v['message'] for r in bad for v in r['violations'])


def test_violating_steps_are_still_returned_so_the_user_can_fix_parameters(api):
    c, _, _ = api
    d = gen(c, 'arc', {'radius_mm': 150}).json()
    assert d['count'] == 13 and d['check']['violations'] == 13       # hepsi kutu dışı, yine de 200 + adımlar


def test_dry_run_does_not_move_the_robot_nor_start_a_job(api):
    c, _, sim = api
    before = joints(sim)
    assert gen(c, 'zigzag').status_code == 200
    assert gen(c, 'grid', GRID_1X3).status_code == 200
    sim.advance(5)
    assert joints(sim) == before and sim.snapshot()['moving'] is False
    assert c.get('/api/program/state').json()['runner']['state'] == 'idle'


def test_the_check_starts_from_the_robots_current_pose_and_chains_the_steps(api):
    c, _, sim = api
    ok = gen(c, 'arc', {'segments': 40}).json()['check']           # 41 nokta zincirleme planlanır
    assert ok['ok'] and len(ok['rows']) == 41


def test_dry_run_still_works_while_estopped_and_never_moves(api):
    c, hub, sim = api
    assert c.post('/api/estop').status_code == 200
    before = joints(sim)
    assert gen(c, 'arc').status_code == 200
    sim.advance(2)
    assert joints(sim) == before


def test_largest_legal_grid_is_checked_in_one_request(api):
    c, _, _ = api
    d = gen(c, 'grid', {'rows': 7, 'cols': 7, 'pitch_mm': 10}).json()
    assert d['count'] == 442 and d['check']['ok'] and len(d['check']['rows']) == 442 - 49


# --- Program ile bütünleşme --------------------------------------------------------------------------
def drive(c, sim, pred, limit_s=90.0):
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        st = c.get('/api/program/state').json()['runner']
        if pred(st):
            return st
        c.post('/api/program/heartbeat', json={})
        sim.advance(0.25)
        time.sleep(0.02)
    raise AssertionError(f'zaman aşımı: {c.get("/api/program/state").json()["runner"]}')


def test_generated_steps_are_a_valid_program_that_runs_in_the_sim_and_ends_on_the_last_point(api):
    c, _, sim = api
    d = gen(c, 'arc', {'center_x_mm': 140, 'center_y_mm': 0, 'center_z_mm': 100, 'radius_mm': 30,
                       'sweep_deg': 90, 'segments': 4}).json()
    prog = {'version': 1, 'name': 'Yay', 'waypoints': [], 'steps': d['steps']}
    chk = c.post('/api/program/check', json={'program': prog, 'speed_pct': 100}).json()
    assert chk['ok'] and chk['violations'] == 0
    saved = c.post('/api/program/save', json={'program': prog}).json()
    reopened = c.get(f"/api/program/{saved['slug']}").json()['program']
    assert reopened['steps'] == d['steps']                      # orientation:'free' kayıtta korunur
    assert c.post('/api/program/run', json={'program': prog, 'speed_pct': 100}).status_code == 202
    drive(c, sim, lambda st: st['state'] == 'finished')
    end = d['points'][-1]['pos_m']
    assert sim.snapshot()['tcp']['pos_m'] == pytest.approx(end, abs=1e-3)       # robot gerçekten oraya gitti


def test_an_orientation_free_step_is_what_the_sim_receives(api):
    c, _, sim = api
    seen = []
    real = sim.move_submit
    sim.move_submit = lambda body: (seen.append(body), real(body))[1]
    d = gen(c, 'arc', {'segments': 3, 'sweep_deg': 60, 'radius_mm': 20}).json()
    prog = {'version': 1, 'name': 'Y', 'waypoints': [], 'steps': d['steps']}
    assert c.post('/api/program/run', json={'program': prog, 'speed_pct': 100}).status_code == 202
    drive(c, sim, lambda st: st['state'] == 'finished')
    assert seen and all(b['orientation'] == 'free' and 'rpy_deg' not in b for b in seen)


# --- iç tutarlılık -----------------------------------------------------------------------------------
def test_algo_shares_the_program_pages_move_client(make_client):
    c, _ = make_client('open')
    assert c.app.state.program_mover is not None
    assert gen(c, 'arc').json()['backend']['mock'] is True


def test_without_a_shared_client_the_route_builds_its_own(make_client):
    c, _ = make_client('open')
    del c.app.state.program_mover
    assert gen(c, 'arc').status_code == 200


def test_a_generator_that_breaks_the_program_model_is_a_server_error_not_a_pass(make_client, monkeypatch):
    from control_hub.hub_core.algo_gen import Generated
    from control_hub.server import algo_routes
    c, _ = make_client('open')
    monkeypatch.setattr(algo_routes, 'generate', lambda kind, params: Generated(steps=[{'kind': 'ucuş'}]))
    r = gen(c, 'arc')
    assert r.status_code == 500 and r.json()['error'] == 'generated_invalid'
