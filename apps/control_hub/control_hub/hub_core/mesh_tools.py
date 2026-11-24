"""Parça (CAD) mesh araçları: SAF işlevler, dosya/ağ yok (S2, UI_EMIRLER K6).

* Girdi: kullanıcının yüklediği STL (ikili + ASCII) ya da OBJ baytları. ÇIKTI her zaman **normali yeniden
  hesaplanmış ikili STL** (yüklenen biçimden bağımsız tek biçim; ön uç yalnız onu okur).
* Yüklenen içerik ASLA çalıştırılmaz ya da ``import`` edilmez; yalnız sayı olarak okunur.
* Hesap ham birimde yapılır (``mm``/``in``/``m``); metreye çevirme ``UNIT_FACTORS`` ile, görünüm tarafında.
* Yalnız stdlib (Docker imajında numpy yok).
"""
from __future__ import annotations

import math
import re
import struct
from array import array
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAX_FILE_BYTES = 50 * 1024 * 1024            # K6: yükleme sınırı
MAX_TRIANGLES = 500_000                      # K6: fazlası reddedilir (kullanıcıya söylenir)
MAX_VERTICES = 3 * MAX_TRIANGLES             # OBJ: yüzeysiz köşe şişmesine karşı
MAX_ABS_COORD = 1.0e7                        # ham birimde; bunun ötesi bozuk/yanlış dosya sayılır
ALLOWED_EXT = ('.stl', '.obj')               # K6: uzantı beyaz listesi
STEP_EXT = ('.step', '.stp', '.iges', '.igs')
STEP_MESSAGE = "STEP/IGES için dönüştürücü kurulu değil: CAD programında STL'e çevirip yükle."
UNIT_FACTORS: Dict[str, float] = {'mm': 0.001, 'in': 0.0254, 'm': 1.0}
DEFAULT_UNIT = 'mm'
SANE_MIN_M = 0.005                           # en büyük boyut bunun altındaysa birim yanlış olabilir
SANE_MAX_M = 3.0                             # ... bunun üstündeyse de (robot çalışma alanı ~1 m)
DEGENERATE_AREA2 = 1.0e-18                   # |çapraz çarpım|² eşiği: alanı ~0 olan üçgen atılır
STL_HEADER = 84                              # 80 B başlık + uint32 üçgen sayısı
STL_TRI = 50                                 # normal(12) + 3 köşe(36) + öznitelik(2)
STL_TITLE = b'Control Hub parca (normaller yeniden hesaplandi)'

_VERTEX_RE = re.compile(rb'vertex\s+(\S+)\s+(\S+)\s+(\S+)', re.IGNORECASE)


class MeshToolError(Exception):
    """Mesh kabul edilemez; ``str(exc)`` kullanıcıya güvenle gösterilir (dosya yolu içermez)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ImportedMesh:
    stl: bytes                                   # ikili STL (ham birimde)
    triangles: int
    bbox_min: Tuple[float, float, float]
    bbox_max: Tuple[float, float, float]
    source_format: str                           # 'stl_binary' | 'stl_ascii' | 'obj'
    dropped: int = 0                             # alanı sıfır olduğu için atılan üçgen
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------
# dosya adı / uzantı
# ---------------------------------------------------------------------------------------------------
def extension_of(filename: object) -> str:
    """Güvenli uzantı çıkarımı (küçük harf).

    Yol ayırıcı, ``..``, mutlak yol, sürücü harfi, NUL: ``MeshToolError``.

    Kullanıcı dosya adı yalnız ekranda gösterilir; ASLA dosya yolu olarak kullanılmaz (K6), yine de bir adın
    yol gibi görünmesi baştan reddedilir.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise MeshToolError('bad_filename', 'Dosya adı yok')
    name = filename.strip()
    if len(name) > 128 or any(ord(c) < 32 for c in name) or '/' in name or '\\' in name or ':' in name \
            or '..' in name or name.startswith('.'):
        raise MeshToolError('bad_filename',
                            'Dosya adı geçersiz (yol ayırıcı, "..", sürücü ya da gizli ad olamaz)')
    dot = name.rfind('.')
    ext = name[dot:].lower() if dot > 0 else ''
    if ext in STEP_EXT:
        raise MeshToolError('step_unsupported', STEP_MESSAGE)
    if ext not in ALLOWED_EXT:
        raise MeshToolError('bad_extension',
                            f'Yalnız {", ".join(ALLOWED_EXT)} yüklenebilir (gelen: {ext or "uzantısız"})')
    return ext


def clean_display_name(raw: object, fallback: str = 'Parça') -> str:
    """Ekranda gösterilecek ad: denetim karakterleri atılır, boşluklar sadeleşir, ≤ 64 karakter."""
    text = raw if isinstance(raw, str) else ''
    text = ''.join(c if c.isprintable() else ' ' for c in text)
    text = re.sub(r'\s+', ' ', text).strip()[:64]
    return text or fallback


def unit_factor(unit: object) -> float:
    if unit not in UNIT_FACTORS:
        raise MeshToolError('bad_unit', f'Birim {", ".join(UNIT_FACTORS)} olmalı')
    return UNIT_FACTORS[unit]  # type: ignore[index]


# ---------------------------------------------------------------------------------------------------
# ayrıştırma (hepsi düz float32 dizisi döndürür: 9 sayı = 1 üçgen)
# ---------------------------------------------------------------------------------------------------
def _parse_stl_binary(data: bytes) -> array:
    n = struct.unpack_from('<I', data, 80)[0]
    if n == 0:
        raise MeshToolError('empty', 'STL içinde üçgen yok')
    if n > MAX_TRIANGLES:
        raise MeshToolError('too_many_triangles',
                            f'Üçgen sayısı çok fazla ({n}); en çok {MAX_TRIANGLES}. CAD programında seyrelt.')
    out = array('f')
    for rec in struct.iter_unpack('<12fH', memoryview(data)[STL_HEADER:]):
        out.extend(rec[3:12])
    return out


def _parse_stl_ascii(data: bytes) -> array:
    out = array('f')
    try:
        for m in _VERTEX_RE.finditer(data):
            out.append(float(m.group(1)))
            out.append(float(m.group(2)))
            out.append(float(m.group(3)))
            if len(out) > 9 * MAX_TRIANGLES:
                raise MeshToolError('too_many_triangles',
                                    f'Üçgen sayısı çok fazla (>{MAX_TRIANGLES}); CAD programında seyrelt.')
    except ValueError:
        raise MeshToolError('bad_stl', 'ASCII STL içinde sayı olmayan köşe değeri var') from None
    if not out or len(out) % 9:
        raise MeshToolError('bad_stl', 'ASCII STL bozuk: köşe sayısı 3\'ün katı değil ya da hiç köşe yok')
    return out


def parse_stl(data: bytes) -> Tuple[array, str]:
    """İkili ya da ASCII STL -> (düz köşe dizisi, biçim). Boyut başlıkla uyuşan dosya İKİLİ sayılır
    ('solid' ile başlayan ikili dosyalar da var); aksi halde ``solid`` ile başlıyorsa ASCII."""
    if len(data) >= STL_HEADER:
        n = struct.unpack_from('<I', data, 80)[0]
        if STL_HEADER + n * STL_TRI == len(data):
            return _parse_stl_binary(data), 'stl_binary'
    if data.lstrip(b'\xef\xbb\xbf \t\r\n')[:5].lower() == b'solid':
        return _parse_stl_ascii(data), 'stl_ascii'
    raise MeshToolError('bad_stl',
                        'STL ne ikili (boyut başlıkla uyuşmuyor) ne de ASCII ("solid" ile başlamıyor)')


def _obj_index(tok: str, count: int) -> int:
    """OBJ yüz köşesi 'v', 'v/vt', 'v/vt/vn', 'v//vn' -> 0 tabanlı köşe sırası (negatif = sondan)."""
    try:
        i = int(tok.split('/', 1)[0])
    except ValueError:
        raise MeshToolError('bad_obj', 'OBJ yüzünde geçersiz köşe sırası') from None
    j = i - 1 if i > 0 else count + i
    if i == 0 or not 0 <= j < count:
        raise MeshToolError('bad_obj', 'OBJ yüzü var olmayan bir köşeye başvuruyor')
    return j


def parse_obj(data: bytes) -> array:
    """OBJ: ``v`` köşeleri + ``f`` yüzleri (çokgenler yelpaze ile üçgenlenir). Diğer satırlar yok sayılır."""
    verts: List[Tuple[float, float, float]] = []
    out = array('f')
    for raw in data.decode('latin-1').splitlines():
        parts = raw.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == 'v':
            if len(parts) < 4:
                raise MeshToolError('bad_obj', 'OBJ köşesi (v) en az 3 sayı ister')
            try:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                raise MeshToolError('bad_obj', 'OBJ köşesinde sayı olmayan değer var') from None
            if len(verts) > MAX_VERTICES:
                raise MeshToolError('too_many_triangles', f'OBJ köşe sayısı çok fazla (>{MAX_VERTICES})')
        elif tag == 'f':
            if len(parts) < 4:
                raise MeshToolError('bad_obj', 'OBJ yüzü (f) en az 3 köşe ister')
            idx = [_obj_index(t, len(verts)) for t in parts[1:]]
            for k in range(1, len(idx) - 1):
                for j in (idx[0], idx[k], idx[k + 1]):
                    out.extend(verts[j])
                if len(out) > 9 * MAX_TRIANGLES:
                    raise MeshToolError(
                        'too_many_triangles',
                        f'Üçgen sayısı çok fazla (>{MAX_TRIANGLES}); CAD programında seyrelt.')
    if not out:
        raise MeshToolError('empty', 'OBJ içinde yüz (üçgen) yok')
    return out


# ---------------------------------------------------------------------------------------------------
# doğrulama, sınır kutusu, yazma
# ---------------------------------------------------------------------------------------------------
def check_finite(tris: array) -> None:
    """Hepsi sonlu mu? (float32 toplamı çift duyarlıkta taşmaz; NaN/Inf toplamı bozar.)"""
    if not math.isfinite(sum(tris)):
        raise MeshToolError('not_finite', 'Mesh sonlu olmayan sayı (NaN/Inf) içeriyor')


def bounds(tris: array) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    if not tris:
        raise MeshToolError('empty', 'Mesh boş')
    lo = tuple(min(tris[k::3]) for k in range(3))
    hi = tuple(max(tris[k::3]) for k in range(3))
    return lo, hi  # type: ignore[return-value]


def drop_degenerate(tris: array) -> Tuple[array, int]:
    """Alanı (neredeyse) sıfır üçgenleri at; normali tanımsız olur ve gölgelemeyi bozar."""
    kept, dropped = array('f'), 0
    for i in range(0, len(tris), 9):
        ax, ay, az, bx, by, bz, cx, cy, cz = tris[i:i + 9]
        ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        if nx * nx + ny * ny + nz * nz <= DEGENERATE_AREA2:
            dropped += 1
        else:
            kept.extend(tris[i:i + 9])
    return kept, dropped


def to_binary_stl(tris: array) -> bytes:
    """Normalleri köşelerden yeniden hesaplayıp ikili STL yazar (dosyadaki normallere güvenilmez)."""
    n = len(tris) // 9
    buf = bytearray(STL_TITLE.ljust(80, b'\0'))
    buf += struct.pack('<I', n)
    for i in range(0, len(tris), 9):
        ax, ay, az, bx, by, bz, cx, cy, cz = tris[i:i + 9]
        ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        buf += struct.pack('<12fH', nx / ln, ny / ln, nz / ln, ax, ay, az, bx, by, bz, cx, cy, cz, 0)
    return bytes(buf)


def import_mesh(filename: object, data: bytes) -> ImportedMesh:
    """Yüklemenin tek giriş noktası: ad/uzantı -> boyut -> ayrıştır -> doğrula -> kanonik ikili STL."""
    ext = extension_of(filename)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise MeshToolError('empty', 'Dosya boş')
    if len(data) > MAX_FILE_BYTES:
        raise MeshToolError('too_large', f'Dosya çok büyük ({len(data) // (1024 * 1024)} MB); en çok '
                                         f'{MAX_FILE_BYTES // (1024 * 1024)} MB')
    data = bytes(data)
    if ext == '.obj':
        tris, fmt = parse_obj(data), 'obj'
    else:
        tris, fmt = parse_stl(data)
    check_finite(tris)
    lo, hi = bounds(tris)
    if max(abs(v) for v in (*lo, *hi)) > MAX_ABS_COORD:
        raise MeshToolError('bad_coords',
                            'Mesh koordinatları olağan dışı büyük: dosya bozuk ya da yanlış birimde')
    tris, dropped = drop_degenerate(tris)
    if not tris:
        raise MeshToolError('empty', 'Mesh içinde alanı sıfırdan büyük üçgen yok')
    lo, hi = bounds(tris)
    warnings = [f'{dropped} alansız (bozuk) üçgen atıldı'] if dropped else []
    return ImportedMesh(stl=to_binary_stl(tris), triangles=len(tris) // 9, bbox_min=lo, bbox_max=hi,
                        source_format=fmt, dropped=dropped, warnings=warnings)


# ---------------------------------------------------------------------------------------------------
# boyut / birim makullüğü
# ---------------------------------------------------------------------------------------------------
def size_m(bbox_min: Tuple[float, float, float], bbox_max: Tuple[float, float, float], unit: str,
           scale: float = 1.0) -> Tuple[float, float, float]:
    f = unit_factor(unit) * scale
    return tuple((b - a) * f for a, b in zip(bbox_min, bbox_max))  # type: ignore[return-value]


def size_warning(bbox_min: Tuple[float, float, float], bbox_max: Tuple[float, float, float], unit: str,
                 scale: float = 1.0) -> Optional[str]:
    """En büyük boyut makul aralıkta değilse uyarı metni (yoksa ``None``); işe yarar bir birim önerir."""
    big = max(size_m(bbox_min, bbox_max, unit, scale))
    if SANE_MIN_M <= big <= SANE_MAX_M:
        return None
    guess = [u for u in UNIT_FACTORS
             if SANE_MIN_M <= max(size_m(bbox_min, bbox_max, u, scale)) <= SANE_MAX_M]
    hint = f' "{guess[0]}" birimi makul görünüyor.' if guess and guess[0] != unit else ''
    return f'Parçanın en büyük boyutu {big * 1000:.1f} mm görünüyor; birim yanlış olabilir.{hint}'
