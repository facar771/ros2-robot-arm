"""hub_core/parts_store.py: uuid dizini, atomik yazım, 20 parça sınırı, yama doğrulaması, yol güvenliği."""
import json
import os
from pathlib import Path

import pytest

from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core import parts_store as ps
from tests.helpers_parts import binary_stl, cube_triangles


@pytest.fixture
def store(tmp_path):
    return ps.PartsStore(tmp_path / 'data')


def mesh(size=10):
    return mt.import_mesh('k.stl', binary_stl(cube_triangles(size)))


def add(store, name='Kutu', kind='workpiece', unit='mm', **kw):
    return store.add(name=name, kind=kind, unit=unit, filename='kutu.stl', mesh=mesh(), **kw)


def test_data_dir_env_and_default(tmp_path):
    assert ps.data_dir({'HUB_DATA_DIR': str(tmp_path)}) == tmp_path
    assert ps.data_dir({}).name == 'data'                                      # kullanıcı klasörü/data


def test_empty_store_lists_nothing_and_creates_nothing(store):
    assert store.list() == ([], [])
    assert not store.root.exists()                                              # okumak klasör oluşturmaz


def test_add_get_read_mesh_roundtrip(store):
    meta = add(store)
    assert ps.ID_RE.match(meta['id']) and meta['triangles'] == 12 and meta['visible'] is True
    assert meta['pose'] == ps.ZERO_POSE and meta['tool_offset'] is None and meta['scale'] == 1.0
    assert store.get(meta['id']) == meta
    assert store.read_mesh(meta['id']) == mesh().stl
    d = store.root / meta['id']
    assert sorted(p.name for p in d.iterdir()) == ['mesh.stl', 'meta.json']     # geçici dosya artığı yok
    assert json.loads((d / 'meta.json').read_text(encoding='utf-8'))['version'] == 1
    assert not [p for p in store.root.iterdir() if p.name.startswith('.')]


def test_tool_gets_a_zero_offset_and_other_kinds_do_not(store):
    t = add(store, kind='tool')
    assert t['tool_offset'] == ps.ZERO_POSE
    assert store.update(t['id'], {'kind': 'workpiece'})['tool_offset'] is None
    back = store.update(t['id'], {'kind': 'tool'})
    assert back['tool_offset'] == ps.ZERO_POSE


def test_view_has_size_in_metres_and_unit_warning(store):
    m = add(store)                                                              # 10 mm küp
    v = ps.public_view(m)
    assert v['size_m'] == pytest.approx([0.01] * 3) and v['size_warning'] is None
    assert v['url'] == f'/api/parts/{m["id"]}/mesh'
    big = ps.public_view(store.update(m['id'], {'unit': 'm', 'scale': 1000}))
    assert big['size_m'] == pytest.approx([10000.0] * 3) and big['size_warning']


def test_twenty_parts_at_most(store):
    for i in range(ps.MAX_PARTS):
        add(store, name=f'p{i}')
    with pytest.raises(ps.PartError) as exc:
        add(store, name='fazla')
    assert exc.value.status == 409 and exc.value.code == 'too_many_parts'
    assert len(store.list()[0]) == ps.MAX_PARTS
    store.delete(store.list()[0][0]['id'])
    add(store, name='yer açıldı')                                               # silince yeniden eklenir


def test_update_validates_every_field(store):
    pid = add(store)['id']
    ok = store.update(pid, {'name': '  Yeni   ad ', 'visible': False, 'unit': 'in', 'scale': 2.5,
                            'pose': {'pos_m': [0.1, -0.2, 0.3], 'rpy_deg': [0, 90, -45]}})
    assert ok['name'] == 'Yeni ad' and ok['visible'] is False and ok['unit'] == 'in' and ok['scale'] == 2.5
    assert store.get(pid)['pose']['pos_m'] == [0.1, -0.2, 0.3]
    bad = [{}, {'x': 1}, {'name': ''}, {'name': 5}, {'kind': 'robot'}, {'visible': 'evet'}, {'unit': 'cm'},
           {'scale': 0}, {'scale': 1e9}, {'scale': float('nan')}, {'scale': True}, {'scale': '2'},
           {'pose': {'pos_m': [0, 0], 'rpy_deg': [0, 0, 0]}},
           {'pose': {'pos_m': [11, 0, 0], 'rpy_deg': [0, 0, 0]}},                    # 11 m: mm/m karışıklığı
           {'pose': {'pos_m': [float('inf'), 0, 0], 'rpy_deg': [0, 0, 0]}},
           {'pose': {'pos_m': [0, 0, 0], 'rpy_deg': [0, 0, 400]}},
           {'pose': {'pos_m': [0, 0, 0], 'rpy_deg': [0, 0, 0], 'ekstra': 1}},
           {'pose': {'pos_m': ['a', 0, 0], 'rpy_deg': [0, 0, 0]}},
           {'pose': 'sol'}, {'id': 'x' * 32}, {'triangles': 1}, {'bbox_raw': {}}]
    for patch in bad:
        with pytest.raises(ps.PartError) as exc:
            store.update(pid, patch)
        assert exc.value.status == 400, patch
    # reddedilenler hiçbir şeyi değiştirmedi
    assert store.get(pid) == ok


def test_ids_are_validated_so_names_never_become_paths(store):
    add(store)
    for bad in ('../x', '..', 'a/b', '', '0' * 31, 'G' * 32, '0' * 33, None, 5, '%2e%2e'):
        with pytest.raises(ps.PartError) as exc:
            store.get(bad)
        assert exc.value.status == 404
        with pytest.raises(ps.PartError):
            store.delete(bad)
    with pytest.raises(ps.PartError) as exc:
        store.get('0' * 32)                                                     # geçerli biçim, yok
    assert exc.value.code == 'no_part'


def test_user_filename_is_display_only(store):
    meta = store.add(name='', kind='workpiece', unit='mm', filename='ben bir\x00 ad.stl', mesh=mesh())
    assert meta['name'] == 'Parça' and 'ben bir' in meta['source_name']
    assert [p.name for p in store.root.iterdir()] == [meta['id']]               # dizin adı yalnız uuid


def test_delete_removes_everything(store):
    pid = add(store)['id']
    store.delete(pid)
    assert store.list() == ([], []) and list(store.root.iterdir()) == []
    with pytest.raises(ps.PartError):
        store.delete(pid)


def test_corrupt_parts_are_skipped_not_fatal(store):
    good = add(store)
    bad1 = add(store, name='bozuk meta')
    bad2 = add(store, name='mesh yok')
    (store.root / bad1['id'] / 'meta.json').write_text('{ bozuk json', encoding='utf-8')
    (store.root / bad2['id'] / 'mesh.stl').unlink()
    (store.root / 'not-a-part').mkdir()
    parts, problems = store.list()
    assert [p['id'] for p in parts] == [good['id']] and len(problems) == 2
    # kimliğin yalnız başı gösterilir
    assert all(bad1['id'] not in p and bad2['id'] not in p for p in problems)
    with pytest.raises(ps.PartError) as exc:
        store.get(bad1['id'])
    assert exc.value.code == 'corrupt_part'


def test_stale_partial_dirs_are_swept_on_next_add(store):
    add(store)
    (store.root / '.new-deadbeef').mkdir()
    (store.root / '.new-deadbeef' / 'mesh.stl').write_bytes(b'yarim')
    (store.root / '.deleted-cafe').mkdir()
    add(store, name='ikinci')
    assert [p.name for p in store.root.iterdir() if p.name.startswith('.')] == []


def test_failed_add_leaves_no_partial_part(store, monkeypatch):
    def boom(*a, **k):
        raise OSError(28, 'disk dolu')
    monkeypatch.setattr(ps, '_atomic_write', boom)
    with pytest.raises(ps.PartError) as exc:
        add(store)
    assert exc.value.status == 500 and exc.value.code == 'storage_error'
    assert str(store.root) not in str(exc.value)                                # yol sızmaz
    assert store.list() == ([], []) and [p for p in store.root.iterdir()] == []


def test_meta_update_is_atomic_replace(store, monkeypatch):
    pid = add(store)['id']
    seen = []
    real = os.replace
    monkeypatch.setattr(
        os, 'replace', lambda a, b: (seen.append((Path(a).name, Path(b).name)), real(a, b))[1])
    store.update(pid, {'visible': False})
    assert seen == [('meta.json.tmp', 'meta.json')]
