"""Parça uçları (S2): UI_CONTRACT §5 şablonu (otomatik kurulum) + K6 yükleme güvenliği + kalıcılık."""
import base64
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core import parts_store as ps
from control_hub.server import create_app, registry
from tests.helpers_parts import binary_stl, cube_triangles


def delete(c, url):
    """Arayüzün api() işlevi gibi: DELETE de JSON içerik türüyle gider (Hub ara katmanı bunu ister)."""
    return c.request('DELETE', url, json={})


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def body(data=None, filename='kutu.stl', **kw):
    data = binary_stl(cube_triangles(40)) if data is None else data
    return {'filename': filename, 'data_b64': b64(data), **kw}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / 'hubdata'
    monkeypatch.setenv('HUB_DATA_DIR', str(d))
    return d


@pytest.fixture
def client(data_dir, make_client):
    c, hub = make_client('open')
    return c


def only_parts_tree(data_dir: Path):
    """Veri klasöründe parts/<uuid>/{mesh.stl,meta.json} dışında HİÇBİR şey olmamalı."""
    found = data_dir.rglob('*') if data_dir.exists() else []
    seen = sorted(str(p.relative_to(data_dir)).replace('\\', '/') for p in found)
    allowed = r'parts|parts/[0-9a-f]{32}|parts/[0-9a-f]{32}/(mesh\.stl|meta\.json)'
    return [s for s in seen if not re.fullmatch(allowed, s)]


# --- UI_CONTRACT §5 şablonu -----------------------------------------------------------------------
def test_parts_route_is_auto_installed_and_login_gated(client, data_dir, make_client):
    assert 'parts_routes' in registry.route_module_names()
    assert client.get('/api/parts').json()['ok'] is True
    pin, _ = make_client('pin')
    zero = '/api/parts/' + '0' * 32
    for method, url in (('get', '/api/parts'), ('get', zero + '/mesh'),
                        ('post', '/api/parts'), ('put', zero), ('delete', zero)):
        r = pin.get(url) if method == 'get' else pin.request(method.upper(), url, json={})
        assert r.status_code == 401, (method, url)                       # oturumsuz görünmez / yazılamaz


def test_locked_mode_is_read_only(data_dir, scenarios, ui):
    from control_hub.hub_core import AccessControl, FakeBackend, Hub
    hub = Hub(FakeBackend(time_scale=0.02), scenarios, ui, AccessControl('locked', '123456'))
    try:
        c = TestClient(create_app(hub), base_url='http://localhost')
        assert c.get('/api/parts').status_code == 200
        assert c.post('/api/parts', json=body()).status_code == 403
        assert c.put('/api/parts/' + '0' * 32, json={'name': 'x'}).status_code == 403
        assert delete(c, '/api/parts/' + '0' * 32).status_code == 403
    finally:
        hub.close()


# --- yükleme, listeleme, kalıcılık ----------------------------------------------------------------
def test_one_megabyte_stl_uploads_lists_serves_and_survives_restart(client, data_dir, make_client):
    tris = cube_triangles(40) * 1500                                       # 18 000 üçgen ~ 0,9 MB
    data = binary_stl(tris)
    assert 0.8e6 < len(data) < 1.2e6
    r = client.post('/api/parts', json=body(data, kind='tool', name='Pens', unit='mm'))
    assert r.status_code == 201, r.text
    part = r.json()['part']
    assert part['name'] == 'Pens' and part['kind'] == 'tool' and part['triangles'] == 18000
    assert part['tool_offset'] == {'pos_m': [0, 0, 0], 'rpy_deg': [0, 0, 0]}
    assert part['size_m'] == pytest.approx([0.04] * 3) and r.json()['warnings'] == []
    mesh = client.get(part['url'])
    assert mesh.status_code == 200 and mesh.headers['content-type'] == 'model/stl'
    assert len(mesh.content) == 84 + 18000 * 50

    pose = {'pos_m': [0.3, -0.1, 0.05], 'rpy_deg': [0, 0, 90]}
    r = client.put(f'/api/parts/{part["id"]}', json={'pose': pose, 'visible': False})
    assert r.json()['part']['pose'] == pose
    # "sayfa yenilendi": yeni sunucu, aynı klasör
    again, _ = make_client('open')
    parts = again.get('/api/parts').json()['parts']
    assert [p['id'] for p in parts] == [part['id']]
    assert parts[0]['pose'] == pose and parts[0]['visible'] is False
    assert only_parts_tree(data_dir) == []


def test_name_defaults_to_the_file_stem_and_obj_uploads(client):
    from tests.helpers_parts import obj_cube
    r = client.post('/api/parts', json=body(obj_cube(25), filename='Tutucu v2.obj'))
    assert r.status_code == 201 and r.json()['part']['name'] == 'Tutucu v2'
    assert r.json()['part']['source_format'] == 'obj' and r.json()['part']['kind'] == 'workpiece'


def test_wrong_unit_gets_a_warning_not_a_refusal(client):
    r = client.post('/api/parts', json=body(binary_stl(cube_triangles(0.04)), unit='mm'))
    assert r.status_code == 201 and any('birim' in w for w in r.json()['warnings'])


def test_list_reports_limits_and_that_step_is_not_installed(client):
    doc = client.get('/api/parts').json()
    assert doc['parts'] == [] and doc['limits']['max_parts'] == 20 and doc['limits']['max_file_mb'] == 50
    assert doc['limits']['max_triangles'] == 500_000 and doc['limits']['formats'] == ['.stl', '.obj']
    assert doc['step']['available'] is False and "STL'e çevirip yükle" in doc['step']['message']


# --- K6: reddedilenler -----------------------------------------------------------------------------
@pytest.mark.parametrize('filename,status,code', [
    ('../x.stl', 400, 'bad_filename'), ('..\\x.stl', 400, 'bad_filename'),
    ('/etc/passwd.stl', 400, 'bad_filename'), ('C:\\x.stl', 400, 'bad_filename'),
    ('virus.exe', 415, 'bad_extension'), ('x.stl.exe', 415, 'bad_extension'), ('x.py', 415, 'bad_extension'),
    ('kutu.step', 415, 'step_unsupported'), ('kutu.IGES', 415, 'step_unsupported'),
])
def test_bad_names_and_extensions_are_refused_and_nothing_is_written(
        client, data_dir, filename, status, code):
    r = client.post('/api/parts', json=body(filename=filename))
    assert r.status_code == status and r.json()['error'] == code and r.json()['ok'] is False
    assert not data_dir.exists() or list((data_dir / 'parts').glob('*')) == []
    assert str(data_dir) not in r.text


def test_step_message_tells_the_user_what_to_do(client):
    r = client.post('/api/parts', json=body(filename='parca.stp'))
    assert "STL'e çevirip yükle" in r.json()['message']


def test_51_megabyte_file_is_refused(client, data_dir):
    r = client.post('/api/parts', json=body(b'\0' * (51 * 1024 * 1024)))
    assert r.status_code == 413 and r.json()['error'] == 'too_large'
    assert not data_dir.exists() or list((data_dir / 'parts').glob('*')) == []


def test_oversized_declared_body_is_cut_before_it_is_read(client):
    huge = {'filename': 'a.stl', 'data_b64': 'A' * (70 * 1024 * 1024)}
    assert client.post('/api/parts', json=huge).status_code == 413


@pytest.mark.parametrize('payload,status,code', [
    (binary_stl(cube_triangles())[:-7], 422, 'bad_stl'),                         # kırpılmış
    # çalıştırılabilir başlıklı sahte STL
    (b'MZ\x90\x00' + b'\0' * 300, 422, 'bad_stl'),
    (b'solid x\nvertex 0 0 0\nvertex 1 1 1\nendsolid', 422, 'bad_stl'),
    (b'', 422, 'empty'),
])
def test_broken_content_is_refused(client, payload, status, code):
    r = client.post('/api/parts', json={'filename': 'x.stl', 'data_b64': b64(payload)})
    assert r.status_code == status and r.json()['error'] == code


def test_bad_requests(client):
    assert client.post('/api/parts', json=[1, 2]).status_code == 400
    assert client.post('/api/parts', json={'filename': 'a.stl'}).json()['error'] == 'empty'
    r = client.post('/api/parts', json={'filename': 'a.stl', 'data_b64': '***'})
    assert r.json()['error'] == 'bad_base64'
    assert client.post('/api/parts', json=body(kind='robot')).status_code == 400
    assert client.post('/api/parts', json=body(unit='cm')).json()['error'] == 'bad_unit'
    as_json = {'content-type': 'application/json'}
    assert client.post('/api/parts', content=b'{bozuk', headers=as_json).status_code == 400
    as_form = {'content-type': 'multipart/form-data'}
    assert client.post('/api/parts', content=b'x', headers=as_form).status_code == 415


def test_too_many_triangles_is_refused_with_the_limit_in_the_message(client):
    n = mt.MAX_TRIANGLES + 1
    data = b'\0' * 80 + n.to_bytes(4, 'little') + b'\0' * (n * 50)
    r = client.post('/api/parts', json=body(data))
    assert r.status_code == 422 and r.json()['error'] == 'too_many_triangles'
    assert '500000' in r.json()['message']


def test_twenty_first_part_is_refused(client, data_dir):
    for i in range(ps.MAX_PARTS):
        assert client.post('/api/parts', json=body(name=f'p{i}')).status_code == 201
    r = client.post('/api/parts', json=body())
    assert r.status_code == 409 and r.json()['error'] == 'too_many_parts'
    assert len(client.get('/api/parts').json()['parts']) == 20
    assert only_parts_tree(data_dir) == []


def test_update_and_delete_errors(client):
    pid = client.post('/api/parts', json=body()).json()['part']['id']
    assert client.put(f'/api/parts/{pid}', json={'scale': 0}).status_code == 400
    assert client.put(f'/api/parts/{pid}', json={'bilinmeyen': 1}).status_code == 400
    assert client.put('/api/parts/' + '1' * 32, json={'name': 'x'}).status_code == 404
    assert client.get('/api/parts/..%2Fx/mesh').status_code == 404
    assert client.get('/api/parts/' + '1' * 32 + '/mesh').status_code == 404
    assert delete(client, f'/api/parts/{pid}').json() == {'ok': True}
    assert client.get(f'/api/parts/{pid}/mesh').status_code == 404
    assert delete(client, f'/api/parts/{pid}').status_code == 404


def test_errors_carry_the_standard_body_and_never_a_path(client, data_dir):
    for r in (client.post('/api/parts', json=body(filename='../x.stl')), client.get('/api/parts/zz/mesh'),
              client.put('/api/parts/' + '1' * 32, json={'name': 'x'})):
        doc = r.json()
        assert set(doc) >= {'ok', 'error', 'message'} and doc['ok'] is False
        assert str(data_dir) not in json.dumps(doc) and 'hubdata' not in doc['message']


def test_estop_does_not_change_part_endpoints_they_move_nothing(client, data_dir):
    """Parça uçları robota hareket göndermez: ACİL DUR'da da listelenir/yüklenir (hareket: /api/move, S1)."""
    client.post('/api/estop')
    assert client.get('/api/parts').status_code == 200
    assert client.post('/api/parts', json=body()).status_code == 201


def test_no_part_module_writes_to_repo_files():
    """Depo/yapılandırma dosyalarına yazma yok: yalnız data_dir() altı (statik denetim)."""
    root = Path(__file__).resolve().parents[1] / 'control_hub'
    for f in ('hub_core/parts_store.py', 'hub_core/mesh_tools.py', 'server/parts_routes.py'):
        text = (root / f).read_text(encoding='utf-8')
        assert 'src/robot_description' not in text and 'user_config_dir' not in text and 'exec(' not in text
        assert 'import_module' not in text and 'subprocess' not in text and 'pickle' not in text
