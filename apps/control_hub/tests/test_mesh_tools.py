"""hub_core/mesh_tools.py: STL (ikili+ASCII) ve OBJ ayrıştırma, K6 reddi, sınır kutusu, birim makullüğü."""
import math
import struct

import pytest

from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core.meshes import check_binary_stl
from tests.helpers_parts import ascii_stl, binary_stl, cube_triangles, obj_cube


def reject(filename, data, code):
    with pytest.raises(mt.MeshToolError) as exc:
        mt.import_mesh(filename, data)
    assert exc.value.code == code, (exc.value.code, str(exc.value))
    return str(exc.value)


# --- kabul: üç biçim aynı küpü verir ---------------------------------------------------------------
@pytest.mark.parametrize('name,data,fmt', [
    ('kup.stl', binary_stl(cube_triangles(10)), 'stl_binary'),
    ('kup.STL', ascii_stl(cube_triangles(10)), 'stl_ascii'),
    ('kup.obj', obj_cube(10), 'obj'),
    ('kup.obj', obj_cube(10, negative=True), 'obj'),                 # negatif (sondan) indeks
    ('kup.obj', obj_cube(10, slashes=True), 'obj'),                  # v//vn biçimi
])
def test_cube_in_every_format(name, data, fmt):
    m = mt.import_mesh(name, data)
    assert m.source_format == fmt and m.triangles == 12 and m.dropped == 0
    assert m.bbox_min == (0, 0, 0) and m.bbox_max == (10, 10, 10)
    assert check_binary_stl(m.stl) == 12                              # çıktı kanonik ikili STL


def test_output_normals_are_recomputed_outward():
    m = mt.import_mesh('k.stl', binary_stl(cube_triangles(1)))        # girdi normalleri 0
    first = struct.unpack_from('<3f', m.stl, mt.STL_HEADER)
    assert first == pytest.approx((0, 0, -1))                         # alt yüz (z=0), dışa bakar
    for i in range(12):
        nx, ny, nz = struct.unpack_from('<3f', m.stl, mt.STL_HEADER + i * mt.STL_TRI)
        assert math.isclose(math.sqrt(nx * nx + ny * ny + nz * nz), 1.0, rel_tol=1e-5)


def test_binary_stl_with_solid_header_is_still_binary():
    m = mt.import_mesh('k.stl', binary_stl(cube_triangles(2), header=b'solid fake ascii header'))
    assert m.source_format == 'stl_binary' and m.triangles == 12


def test_polygon_faces_are_fan_triangulated():
    obj = b'v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0.5 2 0\nf 1 2 3 4 5\n'      # beşgen -> 3 üçgen
    assert mt.import_mesh('p.obj', obj).triangles == 3


def test_degenerate_triangles_are_dropped_and_reported():
    tris = cube_triangles(1) + [((0, 0, 0), (1, 1, 1), (2, 2, 2))]            # doğrusal: alan 0
    m = mt.import_mesh('k.stl', binary_stl(tris))
    assert m.triangles == 12 and m.dropped == 1 and 'alansız' in m.warnings[0]


# --- ret (K6) --------------------------------------------------------------------------------------
@pytest.mark.parametrize('name', ['../x.stl', '..\\x.stl', '/etc/x.stl', 'C:\\x.stl', 'a/b.stl', '.gizli.stl',
                                  'x.stl\x00.exe', '', ' ', 'a..b.stl'])
def test_path_like_names_are_rejected(name):
    reject(name, binary_stl(cube_triangles()), 'bad_filename')


@pytest.mark.parametrize('name', ['x.exe', 'x.stl.exe', 'x', 'x.py', 'x.zip', 'x.3mf', 'x.dll'])
def test_extension_whitelist(name):
    reject(name, binary_stl(cube_triangles()), 'bad_extension')


@pytest.mark.parametrize('name', ['x.step', 'x.STP', 'x.iges', 'x.igs'])
def test_step_is_refused_with_a_clear_message(name):
    msg = reject(name, b'ISO-10303-21;', 'step_unsupported')
    assert "STL'e çevirip yükle" in msg


def test_non_string_filename_is_rejected():
    for bad in (None, 5, ['a.stl'], b'a.stl'):
        with pytest.raises(mt.MeshToolError):
            mt.extension_of(bad)


def test_size_limit_is_50_mb():
    reject('x.stl', b'\0' * (50 * 1024 * 1024 + 1), 'too_large')
    reject('x.stl', b'', 'empty')


def test_broken_binary_stl_is_rejected():
    good = binary_stl(cube_triangles())
    reject('x.stl', good[:-1], 'bad_stl')                                      # eksik bayt
    reject('x.stl', good + b'\0', 'bad_stl')                                   # fazla bayt
    reject('x.stl', b'rastgele bayt ' * 20, 'bad_stl')
    reject('x.stl', b'\0' * 80 + struct.pack('<I', 0), 'empty')                # 84 bayt: ikili ama 0 üçgen
    reject('x.stl', b'MZ\x90\x00' + b'\0' * 200, 'bad_stl')                    # çalıştırılabilir başlığı


def test_nan_and_inf_are_rejected():
    for bad in (float('nan'), float('inf'), -float('inf')):
        tris = cube_triangles(1)
        tris[0] = ((bad, 0, 0), tris[0][1], tris[0][2])
        reject('x.stl', binary_stl(tris), 'not_finite')
    nan_stl = ascii_stl(cube_triangles(1)).replace(b'vertex 0.0 0.0 0.0', b'vertex nan 0.0 0.0', 1)
    reject('x.stl', nan_stl, 'not_finite')


def test_absurd_coordinates_are_rejected():
    reject('x.stl', binary_stl(cube_triangles(1e9)), 'bad_coords')


def test_ascii_stl_errors():
    reject('x.stl', b'solid x\nvertex 0 0 0\nvertex 1 0 0\nendsolid', 'bad_stl')            # 2 köşe
    reject('x.stl', b'solid x\nvertex a b c\nvertex 1 0 0\nvertex 0 1 0\n', 'bad_stl')
    reject('x.stl', b'solid x\nendsolid x', 'bad_stl')


def test_triangle_limit_is_500k_and_checked_before_parsing():
    n = mt.MAX_TRIANGLES + 1
    data = b'\0' * 80 + struct.pack('<I', n) + b'\0' * (n * mt.STL_TRI)           # boyut başlıkla uyuşur
    msg = reject('x.stl', data, 'too_many_triangles')
    assert str(mt.MAX_TRIANGLES) in msg
    reject('x.obj', b'v 0 0 0\n' * 3 + b'f 1 1 1\n' * (mt.MAX_TRIANGLES + 1), 'too_many_triangles')


def test_obj_errors():
    reject('x.obj', b'v 0 0 0\nv 1 0 0\nf 1 2 3\n', 'bad_obj')                  # var olmayan köşe
    reject('x.obj', b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 0\n', 'bad_obj')         # 0 indeksi yok
    reject('x.obj', b'v 0 0\nf 1 1 1\n', 'bad_obj')
    reject('x.obj', b'v 0 0 x\n', 'bad_obj')
    reject('x.obj', b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2\n', 'bad_obj')           # 2 köşeli yüz
    reject('x.obj', b'# yalniz yorum\nv 0 0 0\n', 'empty')


def test_error_messages_never_leak_paths():
    for name, data in [('../etc/passwd.stl', b''), ('x.stl', b'bozuk'), ('x.exe', b'MZ')]:
        with pytest.raises(mt.MeshToolError) as exc:
            mt.import_mesh(name, data)
        assert 'passwd' not in str(exc.value) and '\\' not in str(exc.value)


# --- birim / boyut ---------------------------------------------------------------------------------
def test_unit_factors_and_size():
    assert mt.unit_factor('mm') == 0.001 and mt.unit_factor('in') == 0.0254 and mt.unit_factor('m') == 1.0
    with pytest.raises(mt.MeshToolError):
        mt.unit_factor('cm')
    assert mt.size_m((0, 0, 0), (10, 20, 30), 'mm') == pytest.approx((0.01, 0.02, 0.03))
    assert mt.size_m((0, 0, 0), (1, 1, 1), 'in', scale=2) == pytest.approx((0.0508,) * 3)


def test_size_warning_suggests_a_sane_unit():
    assert mt.size_warning((0, 0, 0), (100, 50, 20), 'mm') is None                    # 10 cm: makul
    w = mt.size_warning((0, 0, 0), (0.1, 0.05, 0.02), 'mm')                           # 0.1 mm: metre sanılmış
    assert w and '"m"' in w
    big = mt.size_warning((0, 0, 0), (100, 50, 20), 'm')                              # 100 m: mm sanılmalı
    assert big and '"mm"' in big
    assert mt.size_warning((0, 0, 0), (100, 50, 20), 'mm', scale=1000) is not None    # ölçek de hesaba girer


def test_display_name_cleaning():
    assert mt.clean_display_name('  Ali\x00 \n  Veli\t') == 'Ali Veli'
    assert mt.clean_display_name('x' * 200) == 'x' * 64
    assert mt.clean_display_name('', fallback='Parça') == 'Parça'
    assert mt.clean_display_name(None) == 'Parça'
