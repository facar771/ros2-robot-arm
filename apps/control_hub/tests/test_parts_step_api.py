"""POST /api/parts ile STEP/IGES (S2b, #109): dönüşüm alt süreçte; birim hep mm; K6 (yol/uzantı/boyut/üçgen);
bozuk dosya, zaman aşımı ve çökme Hub'ı düşürmez. cascadio kurulu değilse bu dosya atlanır."""
import base64
import sys

import pytest

from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core import step_convert as sc
from tests.helpers_step import iges_box, step_box

pytest.importorskip('cascadio')
BOX = (10.0, 20.0, 30.0)


def body(data, filename='kutu.step', **kw):
    return {'filename': filename, 'data_b64': base64.b64encode(data).decode(), **kw}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / 'hubdata'
    monkeypatch.setenv('HUB_DATA_DIR', str(d))
    return d


@pytest.fixture
def client(data_dir, make_client):
    c, _hub = make_client('open')
    return c


def stored(data_dir):
    return sorted(p.name for p in (data_dir / 'parts').glob('*')) if (data_dir / 'parts').exists() else []


def leftovers(data_dir):
    tmp = data_dir / 'tmp'
    return sorted(p.name for p in tmp.iterdir()) if tmp.exists() else []


def sleeper(fmt, src, dst, mx, to):
    return [sys.executable, '-c', 'import time; time.sleep(120)']


def aborter(fmt, src, dst, mx, to):
    return [sys.executable, '-c', 'import os; os.abort()']


def test_list_announces_the_converter_and_the_license_notice(client):
    doc = client.get('/api/parts').json()
    assert doc['step']['available'] is True and doc['step']['unit'] == 'mm'
    assert doc['step']['notice'] == 'OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna)'
    assert doc['step']['timeout_s'] == 20.0
    assert doc['limits']['formats'] == ['.stl', '.obj', '.step', '.stp', '.iges', '.igs']
    assert doc['limits']['max_file_mb'] == 50 and doc['limits']['max_triangles'] == 500_000


@pytest.mark.parametrize('filename,gen', [('kutu.step', step_box), ('kutu.STP', step_box),
                                          ('kutu.iges', iges_box), ('kutu.IGS', iges_box)])
@pytest.mark.parametrize('unit', ['mm', 'in', 'm'])
def test_upload_is_converted_and_the_unit_is_forced_to_mm(client, data_dir, filename, gen, unit):
    r = client.post('/api/parts', json=body(gen(*BOX, unit=unit), filename, unit=unit))
    assert r.status_code == 201, r.text
    part = r.json()['part']
    assert part['unit'] == 'mm' and part['triangles'] >= 12 and part['size_m'] == pytest.approx(
        [0.01, 0.02, 0.03], abs=1e-5)
    assert part['size_warning'] is None
    note = [w for w in r.json()['warnings'] if 'birim dosyadan okunur' in w]
    assert bool(note) == (unit != 'mm')                       # istemci başka birim dediyse söylenir
    assert part['source_format'] == ('step' if filename.lower().endswith(('.step', '.stp')) else 'iges')
    mesh = client.get(part['url'])
    assert mesh.status_code == 200 and mesh.headers['content-type'] == 'model/stl'
    assert len(mesh.content) == mt.STL_HEADER + mt.STL_TRI * part['triangles']
    assert stored(data_dir) == [part['id']] and leftovers(data_dir) == []


def test_default_unit_is_mm_without_a_note(client):
    r = client.post('/api/parts', json=body(iges_box(*BOX, unit='in'), 'kutu.igs'))
    assert r.status_code == 201 and r.json()['part']['unit'] == 'mm'
    assert not any('birim dosyadan' in w for w in r.json()['warnings'])


def test_user_filename_only_shows_up_in_metadata_never_in_a_path(client, data_dir):
    r = client.post('/api/parts', json=body(step_box(*BOX), 'gizli_ad.step'))
    assert r.status_code == 201 and r.json()['part']['source_name'] == 'gizli_ad.step'
    assert not [p for p in data_dir.rglob('*') if 'gizli_ad' in p.name]


@pytest.mark.parametrize('filename', ['../x.step', '..\\x.step', '/etc/x.step', 'C:\\x.igs', 'a/b.stp',
                                      '.gizli.step', 'x.step\x00.exe', '..%2fx.step'.replace('%2f', '/')])
def test_path_like_step_names_are_refused_and_nothing_is_written(client, data_dir, filename):
    r = client.post('/api/parts', json=body(step_box(*BOX), filename))
    assert r.status_code == 400 and r.json()['error'] == 'bad_filename'
    assert stored(data_dir) == [] and leftovers(data_dir) == [] and str(data_dir) not in r.text


@pytest.mark.parametrize('filename,data', [
    ('bozuk.step', b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=BOZUK(;\nENDSEC;\nEND-ISO-10303-21;\n'),
    ('bozuk.iges', b'x' * 72 + b'S      1\n' + b'cop\n' * 20),
    ('zip.step', b'PK\x03\x04 bu bir STEP degil'),
    ('bos.step', b'\x00' * 64),
])
def test_corrupt_files_get_a_clear_422_and_the_hub_keeps_running(client, data_dir, filename, data):
    r = client.post('/api/parts', json=body(data, filename))
    assert r.status_code == 422 and r.json()['error'] == 'step_failed' and r.json()['ok'] is False
    assert "STL'e çevirip yükle" in r.json()['message'] or 'başlık' in r.json()['message']
    assert str(data_dir) not in r.text and stored(data_dir) == [] and leftovers(data_dir) == []
    assert client.get('/api/parts').json()['parts'] == []              # Hub ayakta
    assert client.post('/api/parts', json=body(step_box(*BOX))).status_code == 201


def test_timeout_returns_422_kills_the_child_and_leaves_nothing(client, data_dir, monkeypatch):
    monkeypatch.setattr(sc, 'TIMEOUT_S', 1.0)
    monkeypatch.setattr(sc, 'child_command', sleeper)
    r = client.post('/api/parts', json=body(step_box(*BOX)))
    assert r.status_code == 422 and r.json()['error'] == 'step_timeout' and '1 sn' in r.json()['message']
    assert stored(data_dir) == [] and leftovers(data_dir) == []


def test_child_crash_returns_422_and_the_next_upload_works(client, data_dir, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(sc, 'child_command', aborter)
        r = client.post('/api/parts', json=body(step_box(*BOX)))
    assert r.status_code == 422 and r.json()['error'] == 'step_crashed'
    assert 'Hub çalışıyor' in r.json()['message']
    assert stored(data_dir) == [] and leftovers(data_dir) == []
    assert client.post('/api/parts', json=body(step_box(*BOX))).status_code == 201


def test_triangle_limit_applies_to_step_output(client, data_dir, monkeypatch):
    monkeypatch.setattr(mt, 'MAX_TRIANGLES', 11)                       # küp = 12 üçgen
    r = client.post('/api/parts', json=body(step_box(*BOX)))
    assert r.status_code == 422 and r.json()['error'] == 'too_many_triangles'
    assert stored(data_dir) == [] and leftovers(data_dir) == []


def test_oversized_step_is_refused_before_conversion(client, data_dir, monkeypatch):
    monkeypatch.setattr(sc, 'child_command', lambda *a: pytest.fail('alt süreç açılmamalıydı'))
    r = client.post('/api/parts', json=body(b'ISO-10303-21;' + b'\0' * (50 * 1024 * 1024)))
    assert r.status_code == 413 and r.json()['error'] == 'too_large' and stored(data_dir) == []


def test_without_the_library_the_old_message_stays(client, data_dir, monkeypatch):
    monkeypatch.setattr(sc, 'available', lambda: False)
    doc = client.get('/api/parts').json()
    assert doc['step']['available'] is False and doc['limits']['formats'] == ['.stl', '.obj']
    assert "STL'e çevirip yükle" in doc['step']['message']
    r = client.post('/api/parts', json=body(step_box(*BOX)))
    assert r.status_code == 415 and r.json()['error'] == 'step_unsupported'
    assert r.json()['message'] == mt.STEP_MESSAGE and stored(data_dir) == []


def test_stl_upload_is_unaffected(client):
    from tests.helpers_parts import binary_stl, cube_triangles
    r = client.post('/api/parts', json=body(binary_stl(cube_triangles(40)), 'k.stl', unit='in'))
    assert r.status_code == 201 and r.json()['part']['unit'] == 'in'   # STL: birim istemciden gelir
