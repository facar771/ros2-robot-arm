"""hub_core/step_convert.py (S2b, #109): cascadio==0.1.1 ALT SÜREÇTE; birim tuzağı, çökme/zaman aşımı
yalıtımı, bozuk dosya, üçgen sınırı, GLB okuyucu. Üçüncü taraf CAD dosyası yok: tests/helpers_step.py."""
import json
import os
import re
import struct
import subprocess
import sys
import time
import types
from array import array
from pathlib import Path

import pytest

from control_hub.hub_core import mesh_tools as mt
from control_hub.hub_core import step_convert as sc
from tests.helpers_step import iges_box, step_box

ROOT = Path(__file__).resolve().parents[1]
BOX = (10.0, 20.0, 30.0)                                   # mm
CASCADIO = pytest.importorskip('cascadio')                  # kurulu değilse (Docker/yerel) bu dosya atlanır
FORMATS = [('.step', step_box), ('.iges', iges_box)]
UNITS = ['mm', 'in', 'm']


def size_of(tris) -> tuple:
    xs, ys, zs = tris[0::3], tris[1::3], tris[2::3]
    return tuple(round(max(a) - min(a), 4) for a in (xs, ys, zs))


# --- sürüm sabit (koşul 1) + birim tuzağı (koşul 4) -------------------------------------------------
def test_version_is_pinned_in_code_requirements_and_installed_module():
    assert sc.PINNED_VERSION == '0.1.1' and CASCADIO.__version__ == '0.1.1'
    req = (ROOT / 'requirements-step.txt').read_text(encoding='utf-8')
    assert re.search(r'^cascadio==0\.1\.1$', req, re.M) and not re.search(r'cascadio\s*[<>~]', req)
    assert sc.TOL_LINEAR == 0.01                                      # koşul 5


@pytest.mark.parametrize('unit', UNITS)
def test_native_output_units_are_pinned(unit):
    """cascadio 0.1.1 ham çıktısı: STEP -> METRE, IGES -> MİLİMETRE (dosya birimi ne olursa olsun). Sürüm
    değişip bu davranış kayarsa BU TEST KIRILIR; ``GLB_TO_MM`` elle güncellenmeden sürüm yükseltilemez."""
    for fmt, gen, expect in (('step', step_box, (0.01, 0.02, 0.03)), ('iges', iges_box, (10.0, 20.0, 30.0))):
        kind = CASCADIO.FileType.STEP if fmt == 'step' else CASCADIO.FileType.IGES
        glb = CASCADIO.to_glb_bytes(gen(*BOX, unit=unit), file_type=kind, tol_linear=sc.TOL_LINEAR,
                                    tol_angular=sc.TOL_ANGULAR, tol_relative=False)
        raw = sc.glb_to_triangles(glb, 1.0, 1000)
        assert size_of(raw) == pytest.approx(expect, abs=1e-6), (fmt, unit)
        assert size_of(sc.glb_to_triangles(glb, sc.GLB_TO_MM[fmt], 1000)) == pytest.approx(BOX, abs=1e-4)
    assert sc.GLB_TO_MM == {'step': 1000.0, 'iges': 1.0}


@pytest.mark.parametrize('ext,gen', FORMATS)
@pytest.mark.parametrize('unit', UNITS)
def test_three_cubes_come_out_the_same_size_in_mm(ext, gen, unit):
    """Üç küp (mm / inç / m ile yazılmış AYNI 10x20x30 mm kutu) -> hep 10x20x30 mm."""
    tris = sc.convert(ext, gen(*BOX, unit=unit), max_triangles=mt.MAX_TRIANGLES)
    assert size_of(tris) == pytest.approx(BOX, abs=0.01) and len(tris) >= 12 * 9


def test_one_inch_cube_is_25_point_4_mm():
    tris = sc.convert('.step', step_box(25.4, 25.4, 25.4, unit='in'), max_triangles=1000)
    assert size_of(tris) == pytest.approx((25.4,) * 3, abs=0.01)


@pytest.mark.parametrize('name,gen,fmt', [('k.step', step_box, 'step'), ('k.STP', step_box, 'step'),
                                          ('k.iges', iges_box, 'iges'), ('k.IGS', iges_box, 'iges')])
def test_import_mesh_returns_canonical_stl_in_mm(name, gen, fmt, tmp_path):
    m = mt.import_mesh(name, gen(*BOX, unit='in'), str(tmp_path))
    assert m.source_format == fmt and m.triangles >= 12
    size = tuple(round(b - a, 3) for a, b in zip(m.bbox_min, m.bbox_max))
    assert size == pytest.approx(BOX, abs=0.01) and len(m.stl) == mt.STL_HEADER + mt.STL_TRI * m.triangles
    assert list(tmp_path.iterdir()) == []                          # geçici dosyalar silinir


def test_user_filename_is_never_used_as_a_path(tmp_path):
    seen = []
    real = sc.child_command

    def spy(fmt, src, dst, mx, to):
        seen.append((src, dst))
        return real(fmt, src, dst, mx, to)
    mt_args = dict(workdir=str(tmp_path), command=spy)
    sc.convert('.step', step_box(*BOX), max_triangles=1000, **mt_args)
    assert [Path(p).name for p in seen[0]] == ['in.bin', 'out.f32']
    assert all(Path(p).parent.parent == tmp_path for p in seen[0])


# --- bozuk dosya / içerik ---------------------------------------------------------------------------
@pytest.mark.parametrize('ext,data', [
    ('.step', b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=BOZUK(;\nENDSEC;\nEND-ISO-10303-21;\n'),
    ('.step', b'ISO-10303-21;\n'),
    ('.stp', b'ISO-10303-21;\n' + bytes(range(256)) * 20),
    ('.iges', b'x' * 72 + b'S      1\n' + b'garbage\n' * 10),
    ('.igs', b'x' * 72 + b'S      1\n'),
])
def test_corrupt_files_fail_cleanly_with_step_failed(ext, data, tmp_path):
    with pytest.raises(sc.StepError) as exc:
        sc.convert(ext, data, max_triangles=1000, workdir=str(tmp_path))
    assert exc.value.code == 'step_failed' and "STL'e çevirip yükle" in str(exc.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('ext,data', [('.step', b'PK\x03\x04zip'), ('.step', b'solid x\nendsolid'),
                                      ('.iges', b'short'), ('.iges', b'\x00' * 200)])
def test_wrong_header_is_refused_without_spawning(ext, data, monkeypatch):
    monkeypatch.setattr(sc, 'child_command', lambda *a: pytest.fail('alt süreç açılmamalıydı'))
    with pytest.raises(sc.StepError) as exc:
        sc.convert(ext, data, max_triangles=1000)
    assert exc.value.code == 'step_failed' and 'başlık' in str(exc.value)


def test_sniff():
    assert sc.sniff('step', b'ISO-10303-21;') and sc.sniff('step', b'\xef\xbb\xbf  \r\nISO-10303-21;')
    assert not sc.sniff('step', b'ISO-10303-2') and not sc.sniff('iges', b'S' * 10)
    assert sc.sniff('iges', iges_box(*BOX)) and not sc.sniff('iges', step_box(*BOX))


# --- alt süreç yalıtımı: zaman aşımı, çökme, sürüm, modül yok ---------------------------------------
def fake_child(code: str, *extra: str):
    return lambda fmt, src, dst, mx, to: [sys.executable, '-c', code, dst, *extra]


def pid_dead(pid: int) -> bool:
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except OSError:
        return True
    return stat.rsplit(')', 1)[1].split()[0] == 'Z'                 # zombi = ölü (init'in toplamasını bekler)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='süreç ağacı denetimi /proc ister')
def test_timeout_kills_the_whole_process_tree(tmp_path):
    pids = tmp_path / 'pids.txt'
    code = ("import os, subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "open(sys.argv[2], 'w').write(f'{os.getpid()} {g.pid}')\n"
            "time.sleep(120)\n")
    t0 = time.monotonic()
    with pytest.raises(sc.StepError) as exc:
        sc.convert('.step', step_box(*BOX), max_triangles=1000, timeout_s=1.5, workdir=str(tmp_path / 'w'),
                   command=fake_child(code, str(pids)))
    assert exc.value.code == 'step_timeout' and '1.5 sn' in str(exc.value) and time.monotonic() - t0 < 15
    child, grand = (int(p) for p in pids.read_text().split())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (pid_dead(child) and pid_dead(grand)):
        time.sleep(0.05)
    assert pid_dead(child) and pid_dead(grand)                       # ağaç: çocuk + torunu
    assert list((tmp_path / 'w').iterdir()) == []


def test_timeout_is_20_seconds_by_default():
    assert sc.TIMEOUT_S == 20.0
    assert sc.child_command('step', 'a', 'b', 5, 20.0)[-1] == '20.0'


@pytest.mark.parametrize('code', ["import os; os.abort()", "import os; os._exit(42)",
                                  "import sys; sys.exit(99)", "raise SystemExit('x')"])
def test_crashing_child_does_not_take_the_hub_down(code, tmp_path):
    with pytest.raises(sc.StepError) as exc:
        sc.convert('.iges', iges_box(*BOX), max_triangles=1000, workdir=str(tmp_path),
                   command=fake_child(code))
    assert exc.value.code == 'step_crashed' and 'Hub çalışıyor' in str(exc.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('payload', [b'', b'abc', struct.pack('<9f', *range(9)) * 2 + b'x'])
def test_invalid_child_output_is_refused(payload, tmp_path):
    code = f"import sys; open(sys.argv[1], 'wb').write({payload!r})"
    with pytest.raises(sc.StepError) as exc:
        sc.convert('.step', step_box(*BOX), max_triangles=1000, workdir=str(tmp_path),
                   command=fake_child(code))
    assert exc.value.code == 'step_failed'


def test_child_output_over_the_triangle_limit_is_refused(tmp_path):
    code = "import sys, struct; open(sys.argv[1], 'wb').write(struct.pack('<9f', *range(9)) * 13)"
    with pytest.raises(sc.StepError) as exc:
        sc.convert('.step', step_box(*BOX), max_triangles=12, workdir=str(tmp_path), command=fake_child(code))
    assert exc.value.code == 'step_failed'


def stub_cascadio(monkeypatch, **attrs):
    stub = types.SimpleNamespace(__version__='0.1.1', FileType=types.SimpleNamespace(STEP=1, IGES=2), **attrs)
    monkeypatch.setitem(sys.modules, 'cascadio', stub)


def run_main(tmp_path, fmt='step', limit='1000', timeout='20'):
    src, dst = tmp_path / 'in.bin', tmp_path / 'out.f32'
    src.write_bytes(b'x')
    return sc.convert_main([fmt, str(src), str(dst), limit, timeout]), dst


def test_child_refuses_other_versions(monkeypatch, tmp_path):
    stub_cascadio(monkeypatch, to_glb_bytes=lambda *a, **k: pytest.fail('çağrılmamalıydı'))
    sys.modules['cascadio'].__version__ = '0.1.2'
    assert run_main(tmp_path)[0] == sc.EXIT_VERSION
    code, text = sc._MESSAGES[sc.EXIT_VERSION]
    assert code == 'step_unsupported' and '0.1.1' in text


def test_child_reports_missing_module_and_bad_arguments(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'cascadio', None)               # import -> ImportError
    assert run_main(tmp_path)[0] == sc.EXIT_NO_MODULE
    assert sc.convert_main([]) == sc.EXIT_USAGE
    assert sc.convert_main(['xx', 'a', 'b', '1', '1']) == sc.EXIT_USAGE
    assert sc.convert_main(['step', 'a', 'b', 'x', '1']) == sc.EXIT_USAGE
    assert sc.convert_main(['step', 'a', 'b', '0', '1']) == sc.EXIT_USAGE


def test_child_exit_codes_for_library_failures(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError('OCCT')
    stub_cascadio(monkeypatch, to_glb_bytes=boom)
    assert run_main(tmp_path)[0] == sc.EXIT_FAILED
    stub_cascadio(monkeypatch, to_glb_bytes=lambda *a, **k: b'')            # cascadio hatada boş bayt döner
    assert run_main(tmp_path)[0] == sc.EXIT_BAD_OUTPUT


def test_library_missing_gives_the_old_message(monkeypatch):
    monkeypatch.setattr(sc, 'available', lambda: False)
    with pytest.raises(mt.MeshToolError) as exc:
        mt.extension_of('kutu.step')
    assert exc.value.code == 'step_unsupported' and str(exc.value) == mt.STEP_MESSAGE
    assert mt.cad_formats() == ['.stl', '.obj']
    with pytest.raises(sc.StepError) as exc2:
        sc.convert('.step', step_box(*BOX), max_triangles=10)
    assert exc2.value.code == 'step_unsupported'


def test_frozen_exe_runs_itself_with_convert(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', r'C:\x\ControlHub.exe')
    cmd = sc.child_command('iges', 'i', 'o', 500000, 20.0)
    assert cmd == [r'C:\x\ControlHub.exe', '--convert', 'iges', 'i', 'o', '500000', '20.0']


def test_exe_entry_convert_mode_works_like_the_frozen_exe(tmp_path):
    """exe_entry.py --convert <...> = gömülü .exe'nin alt süreç girişi (PyInstaller olmadan aynı yol)."""
    src, dst = tmp_path / 'in.bin', tmp_path / 'out.f32'
    src.write_bytes(step_box(*BOX, unit='in'))
    r = subprocess.run([sys.executable, str(ROOT / 'exe_entry.py'), '--convert', 'step', str(src), str(dst),
                        '500000', '20.0'], cwd=ROOT, capture_output=True, timeout=60)
    assert r.returncode == sc.EXIT_OK, r.stderr
    tris = array('f')
    tris.frombytes(dst.read_bytes())
    assert size_of(tris) == pytest.approx(BOX, abs=0.01)
    bad = subprocess.run([sys.executable, str(ROOT / 'exe_entry.py'), '--convert', 'step'], cwd=ROOT,
                         capture_output=True, timeout=60)
    assert bad.returncode == sc.EXIT_USAGE


def test_child_kills_itself_if_the_native_call_hangs(tmp_path):
    """Ebeveyn öldürmese bile çocuk kendi saatiyle çıkar (faulthandler.exit); yerli çağrı asılsa da."""
    code = (f"import sys, time, types\nsys.path.insert(0, {str(ROOT)!r})\n"
            "sys.modules['cascadio'] = types.SimpleNamespace(__version__='0.1.1', "
            "FileType=types.SimpleNamespace(STEP=1, IGES=2), to_glb_bytes=lambda *a, **k: time.sleep(120))\n"
            "from control_hub.hub_core import step_convert as sc\nsc.SELF_KILL_GRACE_S = 0.3\n"
            f"sys.exit(sc.convert_main(['step', {str(tmp_path / 'in')!r}, "
            f"{str(tmp_path / 'out')!r}, '10', '0.3']))\n")
    (tmp_path / 'in').write_bytes(b'x')
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=60)
    assert r.returncode != 0 and time.monotonic() - t0 < 20 and not (tmp_path / 'out').exists()


def test_sweep_removes_only_old_step_dirs(tmp_path):
    old, new, other = tmp_path / 'step-old', tmp_path / 'step-new', tmp_path / 'baska'
    for d in (old, new, other):
        d.mkdir()
    past = time.time() - 3600
    for d in (old, other):
        os.utime(d, (past, past))
    assert sc.sweep(str(tmp_path)) == 1 and not old.exists() and new.exists() and other.exists()
    assert sc.sweep(str(tmp_path / 'yok')) == 0


# --- GLB okuyucu ------------------------------------------------------------------------------------
def make_glb(positions, indices=None, *, index_type=5123, nodes=None, stride=None, drop_bin=0, tamper=None):
    pos = array('f', [c for p in positions for c in p])
    binary = bytearray()
    views, accessors = [], []
    pos_bytes = pos.tobytes()
    if stride:                                                           # araya doldurma baytlı köşeler
        pos_bytes = b''.join(struct.pack('<3f', *p) + b'\0' * (stride - 12) for p in positions)
    view = {'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos_bytes)}
    if stride:
        view['byteStride'] = stride
    views.append(view)
    accessors.append({'bufferView': 0, 'componentType': 5126, 'count': len(positions), 'type': 'VEC3'})
    binary += pos_bytes
    prim = {'attributes': {'POSITION': 0}, 'mode': 4}
    if indices is not None:
        fmt = {5121: 'B', 5123: 'H', 5125: 'I'}[index_type]
        idx_bytes = struct.pack(f'<{len(indices)}{fmt}', *indices)
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(idx_bytes)})
        accessors.append({'bufferView': 1, 'componentType': index_type, 'count': len(indices),
                          'type': 'SCALAR'})
        prim['indices'] = 1
        binary += idx_bytes
    js = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
          'nodes': nodes or [{'mesh': 0}], 'meshes': [{'primitives': [prim]}], 'accessors': accessors,
          'bufferViews': views, 'buffers': [{'byteLength': len(binary)}]}
    if tamper:
        tamper(js)
    jb = json.dumps(js).encode()
    jb += b' ' * (-len(jb) % 4)
    binary = bytes(binary)[:len(binary) - drop_bin]
    binary += b'\0' * (-len(binary) % 4)
    body = struct.pack('<II', len(jb), 0x4E4F534A) + jb + struct.pack('<II', len(binary), 0x004E4942) + binary
    return b'glTF' + struct.pack('<II', 2, 12 + len(body)) + body


TRI = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]


@pytest.mark.parametrize('itype', [5121, 5123, 5125])
def test_glb_index_types_and_scale(itype):
    out = sc.glb_to_triangles(make_glb(TRI, [0, 1, 2], index_type=itype), 1000.0, 10)
    assert list(out) == [0, 0, 0, 1000, 0, 0, 0, 1000, 0]


def test_glb_without_indices_and_with_stride():
    assert len(sc.glb_to_triangles(make_glb(TRI * 2), 1.0, 10)) == 18
    out = sc.glb_to_triangles(make_glb(TRI, [2, 1, 0], stride=16), 2.0, 10)
    assert list(out) == [0, 2, 0, 2, 0, 0, 0, 0, 0]


def test_glb_node_transforms_are_applied_with_hierarchy():
    nodes = [{'children': [1], 'translation': [10, 0, 0]},
             {'mesh': 0, 'scale': [2, 2, 2], 'rotation': [0, 0, 0.7071068, 0.7071068]}]   # z çevresinde +90°
    out = sc.glb_to_triangles(make_glb(TRI, [0, 1, 2], nodes=nodes), 1.0, 10)
    assert list(map(lambda v: round(v, 4), out)) == [10, 0, 0, 10, 2, 0, 8, 0, 0]
    mat = {'mesh': 0, 'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 6, 7, 1]}
    assert list(sc.glb_to_triangles(make_glb(TRI, [0, 1, 2], nodes=[mat]), 1.0, 10))[:3] == [5, 6, 7]


def test_glb_triangle_limit_is_exact():
    cube = [(0, 0, 0)] * 36
    assert len(sc.glb_to_triangles(make_glb(cube, list(range(36))), 1.0, 12)) == 12 * 9
    with pytest.raises(sc.StepError) as exc:
        sc.glb_to_triangles(make_glb(cube, list(range(36))), 1.0, 11)
    assert exc.value.code == 'too_many_triangles' and '11' in str(exc.value)


def test_500_001_triangles_are_refused_before_any_work():
    n = mt.MAX_TRIANGLES + 1
    glb = make_glb([(0, 0, 0)] * 3, array('I', [0, 1, 2] * n).tolist(), index_type=5125)
    with pytest.raises(sc.StepError) as exc:
        sc.glb_to_triangles(glb, 1.0, mt.MAX_TRIANGLES)
    assert exc.value.code == 'too_many_triangles'


@pytest.mark.parametrize('breaker', [
    lambda g: g[:10], lambda g: b'XXXX' + g[4:], lambda g: g[:len(g) - 40],
    lambda g: g[:4] + struct.pack('<I', 1) + g[8:], lambda g: g + b'tail',
])
def test_malformed_glb_raises_glb_error_not_a_crash(breaker):
    glb = make_glb(TRI, [0, 1, 2])
    try:
        out = sc.glb_to_triangles(breaker(glb), 1.0, 10)
    except sc.GlbError:
        return
    assert len(out) == 9                                         # zararsız (ek bayt): yine geçerli çıktı


@pytest.mark.parametrize('tamper', [
    lambda j: j['accessors'][1].update(count=6),                       # arabellek dışı
    lambda j: j['accessors'][1].update(count=2),                       # 3'ün katı değil
    lambda j: j['accessors'][0].update(type='VEC4'),
    lambda j: j['accessors'][0].update(sparse={}),
    lambda j: j['accessors'][1].update(bufferView=9),
    lambda j: j['nodes'].append({'children': [1]}) or j['nodes'][0].update(children=[1]),   # döngü
    lambda j: j['nodes'][0].update(matrix=[1, 2]),
    lambda j: j['meshes'][0]['primitives'][0]['attributes'].update(POSITION=7),
])
def test_tampered_glb_is_rejected(tamper):
    with pytest.raises(sc.GlbError):
        sc.glb_to_triangles(make_glb(TRI, [0, 1, 2], tamper=tamper), 1.0, 10)


def test_index_pointing_past_the_vertices_is_rejected_and_empty_glb_too():
    with pytest.raises(sc.GlbError):
        sc.glb_to_triangles(make_glb(TRI, [0, 1, 3]), 1.0, 10)
    with pytest.raises(sc.GlbError):
        sc.glb_to_triangles(make_glb([], []), 1.0, 10)
