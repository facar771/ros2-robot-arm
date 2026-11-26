"""hub_core/atomic.py (MIMARI.md §7 R4): atomik yazım yardımcısı + dört çağıranın ESKİ davranışı sabit.

İki grup: (1) yardımcının sözleşmesi: yarım yazma, hedef var, dizin değiştirme; (2) çağıranlar (program_store,
parts_store, settings, profile_view) taşımadan önceki geçici ad / fsync / hatada temizlik davranışını korur.
"""
import os
from pathlib import Path

import pytest

from control_hub.desktop import settings as st
from control_hub.hub_core import atomic
from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core import parts_store as parts
from control_hub.hub_core import profile_view as pv
from control_hub.hub_core import program_store as progs
from tests.helpers_parts import binary_stl, cube_triangles


@pytest.fixture
def replaces(monkeypatch):
    """os.replace çağrılarını (kaynak ad, hedef ad, aynı dizin mi) kaydeder; çağrı gerçekten yapılır."""
    seen = []
    real = os.replace

    def spy(a, b):
        seen.append((Path(a).name, Path(b).name, Path(a).parent == Path(b).parent))
        return real(a, b)
    monkeypatch.setattr(os, 'replace', spy)
    return seen


@pytest.fixture
def fsyncs(monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, 'fsync', lambda fd: (calls.append(fd), real(fd))[1])
    return calls


def names(folder):
    return sorted(p.name for p in Path(folder).iterdir())


def boom(*_a, **_k):
    raise OSError(28, 'disk dolu')


# --- write_bytes_atomic: temel ----------------------------------------------------------------------
def test_writes_new_file_creates_parents_and_leaves_no_temp(tmp_path, replaces):
    target = tmp_path / 'a' / 'b' / 'x.bin'
    atomic.write_bytes_atomic(target, b'\x00\x01veri')
    assert target.read_bytes() == b'\x00\x01veri' and names(target.parent) == ['x.bin']
    (src, dst, same_dir), = replaces
    assert dst == 'x.bin' and same_dir and src.startswith('.tmp-') and src.endswith('.part')


def test_make_parents_false_does_not_invent_directories(tmp_path):
    with pytest.raises(OSError):
        atomic.write_bytes_atomic(tmp_path / 'yok' / 'x.bin', b'1', make_parents=False)
    assert names(tmp_path) == []


def test_existing_target_is_replaced_whole(tmp_path):
    target = tmp_path / 'x.bin'
    target.write_bytes(b'e' * 5000)
    atomic.write_bytes_atomic(target, b'yeni')
    assert target.read_bytes() == b'yeni' and names(tmp_path) == ['x.bin']


def test_unique_temp_name_uses_prefix_and_suffix_in_the_target_directory(tmp_path, replaces):
    atomic.write_bytes_atomic(tmp_path / 'x.json', b'{}', prefix='settings.', suffix='.tmp')
    (src, dst, same_dir), = replaces
    assert src.startswith('settings.') and src.endswith('.tmp') and dst == 'x.json' and same_dir


def test_fixed_temp_name_is_exactly_the_given_name(tmp_path, replaces):
    atomic.write_bytes_atomic(tmp_path / 'meta.json', b'{}', tmp_name='meta.json.tmp')
    assert replaces == [('meta.json.tmp', 'meta.json', True)] and names(tmp_path) == ['meta.json']


@pytest.mark.parametrize('bad', ['', '.', '..', 'a/b', '../x'])
def test_fixed_temp_name_must_be_a_plain_file_name(tmp_path, bad):
    with pytest.raises(ValueError):
        atomic.write_bytes_atomic(tmp_path / 'x', b'1', tmp_name=bad)
    assert names(tmp_path) == []


def test_fsync_is_called_once_by_default_and_skipped_on_request(tmp_path, fsyncs):
    atomic.write_bytes_atomic(tmp_path / 'a', b'1')
    assert len(fsyncs) == 1
    atomic.write_bytes_atomic(tmp_path / 'b', b'2', fsync=False)
    assert len(fsyncs) == 1 and (tmp_path / 'b').read_bytes() == b'2'


# --- write_bytes_atomic: yarım yazma -----------------------------------------------------------------
@pytest.mark.parametrize('failing', ['fsync', 'replace'])
def test_failure_keeps_old_content_and_removes_the_temp_file(tmp_path, monkeypatch, failing):
    target = tmp_path / 'x.bin'
    target.write_bytes(b'ESKI')
    monkeypatch.setattr(os, failing, boom)
    with pytest.raises(OSError):
        atomic.write_bytes_atomic(target, b'YENI-ve-uzun' * 1000)
    assert target.read_bytes() == b'ESKI' and names(tmp_path) == ['x.bin']


def test_failure_on_a_brand_new_target_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        atomic.write_bytes_atomic(tmp_path / 'yeni.bin', b'1')
    assert names(tmp_path) == []


def test_bad_data_type_fails_before_the_target_is_touched(tmp_path):
    target = tmp_path / 'x.bin'
    target.write_bytes(b'ESKI')
    with pytest.raises(TypeError):
        atomic.write_bytes_atomic(target, 'metin, bayt değil')      # type: ignore[arg-type]
    assert target.read_bytes() == b'ESKI' and names(tmp_path) == ['x.bin']


def test_keyboard_interrupt_also_cleans_up(tmp_path, monkeypatch):
    def stop(*_a):
        raise KeyboardInterrupt
    monkeypatch.setattr(os, 'replace', stop)
    with pytest.raises(KeyboardInterrupt):
        atomic.write_bytes_atomic(tmp_path / 'x.bin', b'1')
    assert names(tmp_path) == []


def test_cleanup_false_keeps_the_old_leftover_behaviour(tmp_path, monkeypatch):
    """settings/parts eskiden hatada geçici dosyayı silmezdi; bayrak bunu AYNEN korur (R4: davranış aynı)."""
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        atomic.write_bytes_atomic(tmp_path / 'x.json', b'1', tmp_name='x.json.tmp', cleanup=False)
    assert names(tmp_path) == ['x.json.tmp']
    (tmp_path / 'x.json.tmp').unlink()
    with pytest.raises(OSError):
        atomic.write_bytes_atomic(tmp_path / 'y.json', b'1', prefix='settings.', suffix='.tmp', cleanup=False)
    (left,) = names(tmp_path)
    assert left.startswith('settings.') and left.endswith('.tmp')


# --- write_text_atomic -------------------------------------------------------------------------------
def test_text_is_utf8_and_newline_follows_open_semantics(tmp_path, monkeypatch):
    t = tmp_path / 't'
    atomic.write_text_atomic(t, 'ğüşiöç\nİ', newline='\n')
    assert t.read_bytes() == 'ğüşiöç\nİ'.encode('utf-8')
    atomic.write_text_atomic(t, 'a\nb', newline='\r\n')
    assert t.read_bytes() == b'a\r\nb'
    atomic.write_text_atomic(t, 'a\nb', newline='')
    assert t.read_bytes() == b'a\nb'
    monkeypatch.setattr(os, 'linesep', '\r\n')                  # newline=None: platform satır sonu (Windows)
    atomic.write_text_atomic(t, 'a\nb')
    assert t.read_bytes() == b'a\r\nb'
    monkeypatch.setattr(os, 'linesep', '\n')
    atomic.write_text_atomic(t, 'a\nb')
    assert t.read_bytes() == b'a\nb'


def test_text_options_reach_the_byte_writer(tmp_path, replaces):
    atomic.write_text_atomic(tmp_path / 'd.yaml', 'x: 1\n', newline='\n', prefix='.draft-', suffix='.tmp')
    (src, dst, _), = replaces
    assert src.startswith('.draft-') and src.endswith('.tmp') and dst == 'd.yaml'


# --- replace_dir -------------------------------------------------------------------------------------
def make_dir(path, **files):
    path.mkdir()
    for name, text in files.items():
        (path / name).write_text(text)
    return path


def test_replace_dir_moves_a_prepared_directory_under_the_new_name(tmp_path, replaces):
    src = make_dir(tmp_path / '.new-1', **{'a.txt': 'x'})
    atomic.replace_dir(src, tmp_path / '1')
    assert names(tmp_path) == ['1'] and (tmp_path / '1' / 'a.txt').read_text() == 'x'
    assert replaces == [('.new-1', '1', True)]


def test_replace_dir_refuses_a_non_empty_target_and_touches_neither(tmp_path):
    src = make_dir(tmp_path / '.new-1', **{'yeni.txt': 'Y'})
    dst = make_dir(tmp_path / '1', **{'eski.txt': 'E'})
    with pytest.raises(OSError):                                 # POSIX: ENOTEMPTY, Windows: PermissionError
        atomic.replace_dir(src, dst)
    assert (src / 'yeni.txt').read_text() == 'Y' and (dst / 'eski.txt').read_text() == 'E'
    assert not (dst / 'yeni.txt').exists() and names(tmp_path) == ['.new-1', '1']


def test_replace_dir_requires_a_directory_source(tmp_path):
    (tmp_path / 'dosya').write_text('x')
    with pytest.raises(NotADirectoryError):
        atomic.replace_dir(tmp_path / 'dosya', tmp_path / 'hedef')
    with pytest.raises(NotADirectoryError):
        atomic.replace_dir(tmp_path / 'yok', tmp_path / 'hedef')
    assert names(tmp_path) == ['dosya']


def test_replace_dir_can_hide_a_directory_before_it_is_deleted(tmp_path):
    """parts_store.delete kalıbı: önce görünmez yap (tek os.replace), sonra sil."""
    import shutil
    d = make_dir(tmp_path / 'abc', **{'meta.json': '{}'})
    gone = tmp_path / '.deleted-abc'
    atomic.replace_dir(d, gone)
    assert names(tmp_path) == ['.deleted-abc']
    shutil.rmtree(gone)
    assert names(tmp_path) == []


# --- çağıranlar: taşımadan önceki davranış SABİT ------------------------------------------------------
def program():
    return progs.validate_program({'version': 1, 'name': 'Montaj 1', 'waypoints': [
        {'name': 'A', 'joints_deg': [0, 10, 20, 0, 0, 0], 'pos_m': [0.3, 0.0, 0.4], 'rpy_deg': [0, 0, 0]}],
        'steps': [{'kind': 'move_j', 'waypoint': 'A'}]})[0]


def test_program_store_temp_name_dir_fsync_and_cleanup(tmp_path, replaces, fsyncs, monkeypatch):
    store = progs.ProgramStore(tmp_path)
    slug = store.save(program())
    (src, dst, same_dir), = replaces
    assert src.startswith('.tmp-') and src.endswith('.part') and dst == slug + '.json' and same_dir
    assert len(fsyncs) == 1 and names(store.root) == [slug + '.json']
    before = (store.root / (slug + '.json')).read_bytes()
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(progs.ProgramError) as exc:
        store.save(program())
    assert exc.value.code == 'write_failed'
    assert (store.root / (slug + '.json')).read_bytes() == before and names(store.root) == [slug + '.json']


def test_parts_store_uses_fixed_meta_tmp_name_and_swaps_the_whole_directory(tmp_path, replaces):
    store = parts.PartsStore(tmp_path / 'parts')
    meta = store.add(name='Kutu', kind='workpiece', unit='mm', filename='k.stl',
                     mesh=mt.import_mesh('k.stl', binary_stl(cube_triangles(10))))
    pid = meta['id']
    assert replaces == [('mesh.stl.tmp', 'mesh.stl', True), ('meta.json.tmp', 'meta.json', True),
                        ('.new-' + pid, pid, True)]                       # sıra: mesh -> meta -> dizin
    assert names(store.root) == [pid]
    assert names(store.root / pid) == ['mesh.stl', 'meta.json']
    replaces.clear()
    store.delete(pid)
    assert replaces == [(pid, '.deleted-' + pid, True)] and names(store.root) == []


def test_parts_store_failed_meta_update_keeps_old_meta_and_leaves_the_old_tmp_artefact(tmp_path, monkeypatch):
    store = parts.PartsStore(tmp_path / 'parts')
    pid = store.add(name='Kutu', kind='workpiece', unit='mm', filename='k.stl',
                    mesh=mt.import_mesh('k.stl', binary_stl(cube_triangles(10))))['id']
    before = (store.root / pid / 'meta.json').read_bytes()
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(parts.PartError):
        store.update(pid, {'visible': False})
    assert (store.root / pid / 'meta.json').read_bytes() == before
    assert names(store.root / pid) == ['mesh.stl', 'meta.json', 'meta.json.tmp']   # eski: artık silinmez


def test_settings_save_keeps_no_fsync_no_cleanup_and_its_temp_name(tmp_path, replaces, fsyncs, monkeypatch):
    path = tmp_path / 'ayar' / 'settings.json'
    st.save(st.Settings(pin='123456'), path)
    (src, dst, same_dir), = replaces
    assert src.startswith('settings.') and src.endswith('.tmp') and dst == 'settings.json' and same_dir
    assert fsyncs == [] and names(path.parent) == ['settings.json']
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        st.save(st.Settings(pin='654321'), path)
    left = [n for n in names(path.parent) if n != 'settings.json']
    assert len(left) == 1 and left[0].startswith('settings.') and left[0].endswith('.tmp')


def test_profile_draft_temp_name_lf_newlines_fsync_and_cleanup(tmp_path, replaces, fsyncs, monkeypatch):
    target = tmp_path / 'sub' / 'draft.yaml'
    pv.atomic_write(target, 'a: 1\nb: 2\n')
    (src, dst, same_dir), = replaces
    assert src.startswith('.draft-') and src.endswith('.tmp') and dst == 'draft.yaml' and same_dir
    assert target.read_bytes() == b'a: 1\nb: 2\n' and len(fsyncs) == 1       # her platformda LF
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        pv.atomic_write(target, 'c: 3\n')
    assert target.read_bytes() == b'a: 1\nb: 2\n' and names(target.parent) == ['draft.yaml']
