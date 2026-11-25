"""Algoritmalar (A1): parametreli üreteçler -> Program adımı. SAF işlevler (DOM, arka uç, saat, dosya YOK).

Üç üreteç: ``grid`` (ızgara/palet pick-place), ``arc`` (daire/yay), ``zigzag`` (zigzag tarama).

Neden böyle:
* Giriş MİLİMETRE (kullanıcı mm yazar), çıkış METRE (program modeli ``pos_m``); yuvarlama 1 µm.
* Her hareket ``move_l`` + ``orientation:'free'`` (S1.1/#108): yalnız konum hedeflenir, yönelimi çözücü
  belirler. Küçük robotta sabit yönelim noktaların çoğuna ulaşamaz.
* Burada YALNIZ şekil/aralık denetimi vardır. Güvenli kutu, ulaşılabilirlik ve yol engeli hareket arka
  ucundadır (K3c: Hub robotun kurallarını gevşetemez); ``algo_routes`` her adımı ``/api/move/check`` ile
  kuru doğrular.
* Üretilen her adım ``program_store.validate_program``'dan geçer (testle sabitlenir); 500 adım üst sınırı
  aşılırsa üretilmez, kullanıcıya hangi parametreyi küçültmesi gerektiği söylenir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from control_hub.hub_core.program_store import MAX_STEPS

MM = 1000.0                      # mm -> m
ROUND_DIGITS = 6                 # 1 µm: planlayıcı bundan ince ayrım görmez
COORD_MM = 1000.0                # koordinat girişi ±1 m (program modeli ±10 m kabul eder; makul sınır)
MAX_GRID_SIDE = 20
MAX_SEGMENTS = 180
MAX_LENGTH_MM = 1000.0
PLANES = {'xy': (0, 1), 'xz': (0, 2), 'yz': (1, 2)}      # düzlemin (u, v) eksenleri (0=x, 1=y, 2=z)
AXES = ('x', 'y')
Step = Dict[str, Any]


class AlgoError(ValueError):
    """Parametre hatası: ``errors`` her alan için ``{field, message}`` (alan boşsa genel hata)."""

    def __init__(self, errors: List[Dict[str, str]]):
        super().__init__('; '.join(f"{e['field'] or 'parametre'}: {e['message']}" for e in errors))
        self.errors = errors


@dataclass(frozen=True)
class Field:
    """Bir üreteç parametresi: arayüz formu bundan kurulur (tek doğruluk kaynağı)."""
    key: str
    label: str
    unit: str
    default: Any
    lo: float = 0.0
    hi: float = 0.0
    integer: bool = False
    options: Tuple[Tuple[str, str], ...] = ()          # (değer, görünen ad): doluysa seçimli alan

    def doc(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {'key': self.key, 'label': self.label, 'unit': self.unit, 'default': self.default}
        if self.options:
            d['options'] = [{'value': v, 'label': t} for v, t in self.options]
        else:
            d.update(min=self.lo, max=self.hi, integer=self.integer)
        return d


def _mm(key: str, label: str, default: float, lo: float = -COORD_MM, hi: float = COORD_MM) -> Field:
    return Field(key, label, 'mm', default, lo, hi)


GRID = (
    Field('rows', 'Satır (+x yönünde)', '', 2, 1, MAX_GRID_SIDE, True),
    Field('cols', 'Sütun (+y yönünde)', '', 3, 1, MAX_GRID_SIDE, True),
    _mm('pitch_mm', 'Aralık', 20, 1, 500),
    _mm('approach_mm', 'Yaklaşma yüksekliği', 30, 1, 500),
    _mm('pick_x_mm', 'Alma noktası x', 120), _mm('pick_y_mm', 'Alma noktası y', -60),
    _mm('pick_z_mm', 'Alma noktası z', 50),
    _mm('place_x_mm', 'İlk hücre x', 100), _mm('place_y_mm', 'İlk hücre y', 0),
    _mm('place_z_mm', 'İlk hücre z', 50),
    Field('open_pct', 'Alet açık', '%', 100, 0, 100), Field('grip_pct', 'Alet tutma', '%', 30, 0, 100),
)
ARC = (
    Field('plane', 'Düzlem', '', 'xy',
          options=(('xy', 'XY (yatay)'), ('xz', 'XZ (dikey)'), ('yz', 'YZ (dikey)'))),
    _mm('center_x_mm', 'Merkez x', 140), _mm('center_y_mm', 'Merkez y', 0),
    _mm('center_z_mm', 'Merkez z', 100),
    _mm('radius_mm', 'Yarıçap', 40, 1, 500),
    Field('start_deg', 'Başlangıç açısı', '°', 0, -360, 360),
    Field('sweep_deg', 'Tarama açısı (360 = tam daire, − = ters yön)', '°', 360, -360, 360),
    Field('segments', 'Parça sayısı', '', 12, 3, MAX_SEGMENTS, True),
)
ZIGZAG = (
    Field('axis', 'Tarama çizgileri', '', 'x',
          options=(('x', 'x eksenine paralel'), ('y', 'y eksenine paralel'))),
    _mm('x0_mm', 'Başlangıç x', 100), _mm('y0_mm', 'Başlangıç y', -40), _mm('z_mm', 'Yükseklik z', 80),
    _mm('size_x_mm', 'Alan x boyu', 60, 1, MAX_LENGTH_MM),
    _mm('size_y_mm', 'Alan y boyu', 80, 1, MAX_LENGTH_MM),
    _mm('pitch_mm', 'Aralık', 20, 1, 500),
)
SPECS: Dict[str, Dict[str, Any]] = {
    'grid': {'title': 'Izgara / palet pick-place', 'fields': GRID,
             'about': 'Her hücre için alma noktasından al, hücreye bırak. Satır +x, sütun +y yönünde artar.'},
    'arc': {'title': 'Daire / yay', 'fields': ARC,
            'about': 'Seçilen düzlemde merkez etrafında doğrusal parçalardan oluşan daire ya da yay.'},
    'zigzag': {'title': 'Zigzag tarama', 'fields': ZIGZAG,
               'about': 'Dikdörtgen alanı gidiş-dönüş çizgilerle tarar; son çizgi alanın dışına çıkmaz.'},
}


def kinds_doc() -> List[Dict[str, Any]]:
    """``GET /api/algo/kinds`` gövdesi: arayüz formunu kuran tanım."""
    return [{'id': k, 'title': s['title'], 'about': s['about'], 'fields': [f.doc() for f in s['fields']]}
            for k, s in SPECS.items()]


# --- parametre doğrulama -----------------------------------------------------------------------------
def _num(f: Field, v: Any) -> Tuple[Optional[float], Optional[str]]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None, 'sayı olmalı'
    try:
        x = float(v)
    except OverflowError:
        return None, 'sonlu sayı olmalı'
    if not math.isfinite(x):
        return None, 'sonlu sayı olmalı (NaN/∞ olmaz)'
    if f.integer and x != int(x):
        return None, 'tamsayı olmalı'
    if not f.lo <= x <= f.hi:
        return None, f'{f.lo:g} … {f.hi:g} aralığında olmalı'
    return (int(x) if f.integer else x), None


def clean_params(kind: str, raw: Any) -> Dict[str, Any]:
    """Ham parametre -> temiz değerler (eksik alan varsayılanı alır). Hata: ``AlgoError``."""
    if kind not in SPECS:
        raise AlgoError([{'field': 'kind', 'message': f"üreteç {', '.join(SPECS)} olmalı"}])
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise AlgoError([{'field': 'params', 'message': 'parametreler bir nesne olmalı'}])
    fields = SPECS[kind]['fields']
    unknown = sorted(set(raw) - {f.key for f in fields}, key=str)
    errs = [{'field': k, 'message': 'bilinmeyen alan'} for k in unknown]
    out: Dict[str, Any] = {}
    for f in fields:
        v = raw.get(f.key, f.default)
        if f.options:
            if v in [o[0] for o in f.options] and isinstance(v, str):
                out[f.key] = v
            else:
                errs.append({'field': f.key, 'message': f"{' | '.join(o[0] for o in f.options)} olmalı"})
            continue
        x, why = _num(f, v)
        if why:
            errs.append({'field': f.key, 'message': why})
        else:
            out[f.key] = x
    if kind == 'arc' and out.get('sweep_deg') == 0:
        errs.append({'field': 'sweep_deg', 'message': '0 olamaz'})
    if errs:
        raise AlgoError(errs)
    return out


# --- adım kurucuları ---------------------------------------------------------------------------------
def _m(mm: float) -> float:
    """mm -> m, 1 µm'ye yuvarla; ``-0.0`` yazılmaz."""
    return round(mm / MM, ROUND_DIGITS) or 0.0


def _move(x_mm: float, y_mm: float, z_mm: float) -> Step:
    return {'kind': 'move_l', 'pos_m': [_m(x_mm), _m(y_mm), _m(z_mm)], 'orientation': 'free'}


@dataclass
class Generated:
    """Üretim sonucu: ``steps`` program adımları; ``marks`` 3B işaret yerleri (``i`` adım sırası, ``n``
    numara, ``role``: pick|place|start|end|line); ``points`` her hareket adımının konumu (yol çizgisi)."""
    steps: List[Step] = field(default_factory=list)
    marks: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, step: Step, role: Optional[str] = None, n: Optional[int] = None) -> int:
        self.steps.append(step)
        if role:
            self.marks.append({'i': len(self.steps) - 1, 'n': n, 'role': role})
        return len(self.steps) - 1

    @property
    def points(self) -> List[Dict[str, Any]]:
        return [{'i': i, 'pos_m': list(s['pos_m'])} for i, s in enumerate(self.steps)
                if s['kind'] == 'move_l']


def grid_steps(p: Dict[str, Any]) -> Generated:
    """Palet pick-place: başta alet AÇ; her hücre (satır satır) için not + 6 hareket + 2 alet = 9 adım
    (alma noktasının üstü, alma, tut, yukarı, hücrenin üstü, bırakma, bırak, yukarı).
    Toplam adım = 1 + 9·satır·sütun."""
    g, up = Generated(), p['approach_mm']
    px, py, pz = p['pick_x_mm'], p['pick_y_mm'], p['pick_z_mm']
    g.add({'kind': 'tool', 'open_pct': float(p['open_pct'])})
    n = 0
    for r in range(p['rows']):
        for c in range(p['cols']):
            n += 1
            cx = p['place_x_mm'] + r * p['pitch_mm']
            cy, cz = p['place_y_mm'] + c * p['pitch_mm'], p['place_z_mm']
            g.add({'kind': 'comment', 'text': f'Hücre {n}: satır {r + 1}, sütun {c + 1}'})
            g.add(_move(px, py, pz + up))
            g.add(_move(px, py, pz), 'pick' if n == 1 else None)
            g.add({'kind': 'tool', 'open_pct': float(p['grip_pct'])})
            g.add(_move(px, py, pz + up))
            g.add(_move(cx, cy, cz + up))
            g.add(_move(cx, cy, cz), 'place', n)
            g.add({'kind': 'tool', 'open_pct': float(p['open_pct'])})
            g.add(_move(cx, cy, cz + up))
    return g


def arc_points_mm(p: Dict[str, Any]) -> List[Tuple[float, float, float]]:
    """Daire/yay noktaları (mm): ``segments + 1`` nokta; tam daire (|tarama| = 360) ilk noktada kapanır."""
    u, v = PLANES[p['plane']]
    centre = (p['center_x_mm'], p['center_y_mm'], p['center_z_mm'])
    out = []
    for k in range(p['segments'] + 1):
        a = math.radians(p['start_deg'] + p['sweep_deg'] * k / p['segments'])
        pt = list(centre)
        pt[u] += p['radius_mm'] * math.cos(a)
        pt[v] += p['radius_mm'] * math.sin(a)
        out.append((pt[0], pt[1], pt[2]))
    return out


def arc_steps(p: Dict[str, Any]) -> Generated:
    g = Generated()
    pts = arc_points_mm(p)
    for k, pt in enumerate(pts):
        last = k == len(pts) - 1
        role = 'start' if k == 0 else 'end' if last and abs(p['sweep_deg']) < 360 else None
        g.add(_move(*pt), role, 1 if k == 0 else None)
    return g


def zigzag_steps(p: Dict[str, Any]) -> Generated:
    """Gidiş-dönüş çizgiler: n = ⌊dik boy / aralık⌋ + 1 çizgi, çizgi başına 2 hareket (toplam 2n); bir
    çizginin bitişi sonraki çizginin başlangıcına dik yönde doğrusal geçiştir (ayrı adım gerekmez)."""
    along_x = p['axis'] == 'x'
    a0, a_len = (p['x0_mm'], p['size_x_mm']) if along_x else (p['y0_mm'], p['size_y_mm'])
    b0, b_len = (p['y0_mm'], p['size_y_mm']) if along_x else (p['x0_mm'], p['size_x_mm'])
    lines = int(b_len / p['pitch_mm'] + 1e-9) + 1
    g = Generated()
    for k in range(lines):
        b = b0 + k * p['pitch_mm']
        ends = (a0, a0 + a_len) if k % 2 == 0 else (a0 + a_len, a0)
        for j, a in enumerate(ends):
            step = _move(a, b, p['z_mm']) if along_x else _move(b, a, p['z_mm'])
            g.add(step, 'line' if j == 0 else None, k + 1)
    return g


_BUILDERS = {'grid': grid_steps, 'arc': arc_steps, 'zigzag': zigzag_steps}


def generate(kind: str, raw: Any) -> Generated:
    """Ham parametre -> adımlar. Hata: ``AlgoError`` (parametre ya da 500 adım sınırı)."""
    p = clean_params(kind, raw)
    g = _BUILDERS[kind](p)
    if len(g.steps) > MAX_STEPS:
        hint = {'grid': 'satır/sütunu azalt', 'arc': 'parça sayısını azalt',
                'zigzag': 'aralığı büyüt ya da alanı küçült'}
        msg = f'{len(g.steps)} adım üretilecek, en çok {MAX_STEPS}: {hint[kind]}'
        raise AlgoError([{'field': '', 'message': msg}])
    return g
