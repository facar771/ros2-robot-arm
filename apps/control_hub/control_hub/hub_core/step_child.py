"""STEP/IGES alt süreci (S2b, UI_EMIRLER K5/K6): cascadio==0.1.1 yalnız BURADA yüklenir.

* Hub süreci bu modülü yalnız sabitler ve GLB okuyucu için içe aktarır; ``cascadio`` ancak
  ``convert_main`` içinde, alt süreçte yüklenir (yerli C++ kodu bozuk dosyada süreci düşürebilir).
* Tek başına çalışır: ``python -I step_child.py ...`` (yalnız stdlib + tembel ``cascadio``;
  ``control_hub`` içinden hiçbir şey içe aktarmaz). Tek dosya .exe'de ``ControlHub.exe --convert``
  (exe_entry.py) aynı ``convert_main``'i çağırır.
* Birim tuzağı (ölçüldü, cascadio 0.1.1): STEP çıktısı METRE, IGES çıktısı MİLİMETRE (dosyanın
  bildirdiği birim ne olursa olsun). ``GLB_TO_MM`` bunu sabitler; tests/test_step_convert.py her
  biçim için mm/inç/m dosyalarıyla sınar, sürüm değişirse kırılır. Çocuk, sürüm ``PINNED_VERSION``
  değilse çalışmayı REDDEDER.
* Çıktı ham float32 (9 sayı = 1 üçgen, **milimetre**); stdout atılır, sonuç yalnız çıktı dosyası +
  çıkış kodudur (``EXIT_*``).
"""
from __future__ import annotations

import json
import math
import struct
import sys
from array import array
from typing import List, Sequence

PINNED_VERSION = '0.1.1'
TOL_LINEAR = 0.01                            # OCCT doğrusal tolerans
TOL_ANGULAR = 0.5                            # rad
GLB_TO_MM = {'step': 1000.0, 'iges': 1.0}    # GLB koordinatı -> mm (ölçüldü: STEP m, IGES mm)
SELF_KILL_GRACE_S = 3.0                      # çocuk kendini zaman aşımından bu kadar SONRA öldürür
FLOATS_PER_TRI = 9
MAX_NODES = 100_000
MAX_DEPTH = 64

# çocuk çıkış kodları (0 = tamam). Bunların dışındaki her kod "çöktü" sayılır.
EXIT_OK, EXIT_USAGE, EXIT_NO_MODULE, EXIT_FAILED, EXIT_VERSION, EXIT_TOO_MANY, EXIT_BAD_OUTPUT = range(7)


class StepError(Exception):
    """Dönüşüm başarısız; ``code`` MeshToolError koduna eşlenir, ``str(exc)`` kullanıcıya gösterilebilir."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------------------------------
# GLB -> üçgenler (saf; cascadio çıktısı: düğüm hiyerarşisi + dönüşüm matrisleri + indeksli POSITION)
# ---------------------------------------------------------------------------------------------------
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)  # sütun öncelikli
_INDEX_TYPES = {5121: ('B', 1), 5123: ('H', 2), 5125: ('I', 4)}
TOO_MANY = 'Üçgen sayısı çok fazla (>{n}): parçayı CAD programında basitleştirip tekrar dene.'


class GlbError(Exception):
    pass


def _mat_mul(a: Sequence[float], b: Sequence[float]) -> tuple:
    """4x4 sütun öncelikli matris çarpımı a·b."""
    return tuple(sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4)) for c in range(4) for r in range(4))


def _node_matrix(node: dict) -> Sequence[float]:
    m = node.get('matrix')
    if m is not None:
        if not (isinstance(m, list) and len(m) == 16 and all(isinstance(v, (int, float)) for v in m)):
            raise GlbError('düğüm matrisi geçersiz')
        return [float(v) for v in m]
    t = node.get('translation', [0, 0, 0])
    q = node.get('rotation', [0, 0, 0, 1])
    s = node.get('scale', [1, 1, 1])
    if len(t) != 3 or len(q) != 4 or len(s) != 3:
        raise GlbError('düğüm dönüşümü geçersiz')
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    r = (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0.0,
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0.0,
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0.0, 0.0, 0.0, 0.0, 1.0)
    return (r[0] * s[0], r[1] * s[0], r[2] * s[0], 0.0, r[4] * s[1], r[5] * s[1], r[6] * s[1], 0.0,
            r[8] * s[2], r[9] * s[2], r[10] * s[2], 0.0, t[0], t[1], t[2], 1.0)


def _split_glb(glb: bytes):
    if len(glb) < 20 or glb[:4] != b'glTF' or struct.unpack_from('<I', glb, 4)[0] != 2:
        raise GlbError('GLB başlığı geçersiz')
    total = struct.unpack_from('<I', glb, 8)[0]
    if total > len(glb):
        raise GlbError('GLB kesilmiş')
    off, js, binary = 12, None, b''
    while off + 8 <= total:
        size, kind = struct.unpack_from('<II', glb, off)
        body = glb[off + 8:off + 8 + size]
        if len(body) != size:
            raise GlbError('GLB parçası kesilmiş')
        if kind == 0x4E4F534A:
            js = json.loads(body.decode('utf-8'))
        elif kind == 0x004E4942 and not binary:
            binary = bytes(body)
        off += 8 + size + (-size % 4)
    if not isinstance(js, dict):
        raise GlbError('GLB JSON bölümü yok')
    return js, binary


def _accessor(js: dict, index: object, want_type: str, comps: Sequence[int]):
    accs = js.get('accessors', [])
    if not isinstance(index, int) or not 0 <= index < len(accs):
        raise GlbError('erişimci yok')
    acc = accs[index]
    count = acc.get('count')
    if acc.get('type') != want_type or acc.get('componentType') not in comps or 'sparse' in acc:
        raise GlbError('erişimci türü desteklenmiyor')
    if not isinstance(count, int) or count < 0:
        raise GlbError('erişimci sayısı geçersiz')
    views = js.get('bufferViews', [])
    view = acc.get('bufferView')
    if not isinstance(view, int) or not 0 <= view < len(views):
        raise GlbError('arabellek görünümü yok')
    return acc, views[view], count


def _read_positions(js: dict, binary: bytes, index: object) -> array:
    acc, bv, count = _accessor(js, index, 'VEC3', (5126,))
    stride = bv.get('byteStride') or 12
    start = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    if count and (stride < 12 or start + stride * (count - 1) + 12 > len(binary)):
        raise GlbError('POSITION arabelleği sınır dışı')
    out = array('f')
    if stride == 12:
        out.frombytes(binary[start:start + 12 * count])
        if sys.byteorder == 'big':
            out.byteswap()
        return out
    for i in range(count):
        out.extend(struct.unpack_from('<3f', binary, start + i * stride))
    return out


def _read_indices(js: dict, binary: bytes, index: object, vertex_count: int) -> List[int]:
    acc, bv, count = _accessor(js, index, 'SCALAR', tuple(_INDEX_TYPES))
    code, size = _INDEX_TYPES[acc['componentType']]
    stride = bv.get('byteStride') or size
    start = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    if count % 3 or (count and (stride < size or start + stride * (count - 1) + size > len(binary))):
        raise GlbError('indeks arabelleği geçersiz')
    if stride == size:
        idx = list(struct.unpack_from(f'<{count}{code}', binary, start))
    else:
        idx = [struct.unpack_from('<' + code, binary, start + i * stride)[0] for i in range(count)]
    if idx and max(idx) >= vertex_count:
        raise GlbError('indeks var olmayan köşeye başvuruyor')
    return idx


def _roots(js: dict) -> list:
    nodes, scenes = js.get('nodes', []), js.get('scenes', [])
    if scenes:
        return list(scenes[js.get('scene', 0)].get('nodes', []))
    children = {c for n in nodes for c in n.get('children', [])}
    return sorted(set(range(len(nodes))) - children)


def _emit(out: array, pos: array, idx: List[int], world: Sequence[float], scale: float) -> None:
    """``idx`` sırasıyla köşeleri ``world`` matrisiyle dönüştürüp ``scale`` ile çarparak ``out``'a ekler."""
    if tuple(world) == IDENTITY:
        for v in idx:
            out.extend((pos[3 * v] * scale, pos[3 * v + 1] * scale, pos[3 * v + 2] * scale))
        return
    m = [c * scale for c in world]
    for v in idx:
        x, y, z = pos[3 * v], pos[3 * v + 1], pos[3 * v + 2]
        out.extend((m[0] * x + m[4] * y + m[8] * z + m[12],
                    m[1] * x + m[5] * y + m[9] * z + m[13],
                    m[2] * x + m[6] * y + m[10] * z + m[14]))


def glb_to_triangles(glb: bytes, scale: float, max_triangles: int) -> array:
    """GLB (cascadio) -> düz float32 üçgen dizisi; koordinatlar ``scale`` ile çarpılır (mm'ye). Düğüm
    dönüşümleri uygulanır (montaj örnekleri). ``max_triangles`` aşılırsa ``StepError``
    (kod ``too_many_triangles``)."""
    js, binary = _split_glb(glb)
    nodes, meshes = js.get('nodes', []), js.get('meshes', [])
    out, visits, cache = array('f'), 0, {}
    limit = max_triangles * FLOATS_PER_TRI
    stack = [(r, IDENTITY, 0) for r in _roots(js)]
    while stack:
        i, parent, depth = stack.pop()
        visits += 1
        if visits > MAX_NODES or depth > MAX_DEPTH or not isinstance(i, int) or not 0 <= i < len(nodes):
            raise GlbError('düğüm ağacı geçersiz ya da çok büyük')
        node = nodes[i]
        world = _mat_mul(parent, _node_matrix(node))
        stack.extend((c, world, depth + 1) for c in node.get('children', []))
        if 'mesh' not in node:
            continue
        for prim in meshes[node['mesh']].get('primitives', []):
            if prim.get('mode', 4) != 4:
                continue
            pi = prim.get('attributes', {}).get('POSITION')
            if pi not in cache:
                cache[pi] = _read_positions(js, binary, pi)
            pos = cache[pi]
            n_vert = len(pos) // 3
            if 'indices' in prim:
                idx = _read_indices(js, binary, prim['indices'], n_vert)
            else:
                idx = list(range(n_vert // 3 * 3))
            if len(out) + len(idx) * 3 > limit:
                raise StepError('too_many_triangles', TOO_MANY.format(n=max_triangles))
            _emit(out, pos, idx, world, scale)
    if not out:
        raise GlbError('GLB içinde üçgen yok')
    return out


# ---------------------------------------------------------------------------------------------------
# ALT SÜREÇ (çocuk): cascadio'yu burada yükler
# ---------------------------------------------------------------------------------------------------
def convert_main(argv: Sequence[str]) -> int:
    """``<biçim:step|iges> <girdi> <çıktı> <en_çok_üçgen> <zaman_aşımı_sn>``; çıkış kodu ``EXIT_*``.
    Yazdığı tek şey ``<çıktı>`` dosyasıdır (ham float32, mm)."""
    try:
        fmt, src, dst, max_tris, timeout = argv[0], argv[1], argv[2], int(argv[3]), float(argv[4])
        if fmt not in GLB_TO_MM or max_tris < 1 or timeout <= 0:
            return EXIT_USAGE
    except (IndexError, ValueError):
        return EXIT_USAGE
    import faulthandler
    faulthandler.dump_traceback_later(timeout + SELF_KILL_GRACE_S, exit=True)   # C iş parçacığı: GIL'siz
    try:
        return _convert_child(fmt, src, dst, max_tris)
    finally:
        faulthandler.cancel_dump_traceback_later()                              # süreç-içi çağrıda (testler)


def _convert_child(fmt: str, src: str, dst: str, max_tris: int) -> int:
    try:
        import cascadio
    except Exception:                                                           # noqa: BLE001 (yerli kütüphane)
        return EXIT_NO_MODULE
    if getattr(cascadio, '__version__', None) != PINNED_VERSION:
        return EXIT_VERSION
    try:
        with open(src, 'rb') as fh:
            data = fh.read()
        kind = cascadio.FileType.STEP if fmt == 'step' else cascadio.FileType.IGES
        glb = cascadio.to_glb_bytes(data, file_type=kind, tol_linear=TOL_LINEAR,
                                    tol_angular=TOL_ANGULAR, tol_relative=False)
    except Exception:                                                           # noqa: BLE001
        return EXIT_FAILED
    try:
        tris = glb_to_triangles(glb, GLB_TO_MM[fmt], max_tris)
    except StepError:
        return EXIT_TOO_MANY
    except Exception:                                                           # noqa: BLE001
        return EXIT_BAD_OUTPUT
    if not all(math.isfinite(v) for v in tris):
        return EXIT_BAD_OUTPUT
    if sys.byteorder == 'big':
        tris.byteswap()
    with open(dst, 'wb') as fh:
        fh.write(tris.tobytes())
    return EXIT_OK


if __name__ == '__main__':                  # alt süreç girişi: python -I step_child.py ...
    raise SystemExit(convert_main(sys.argv[1:]))
