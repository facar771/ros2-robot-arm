"""Test yardımcısı: kendi yazdığımız küçük STEP/IGES KUTU üreteçleri (S2b).

Üçüncü taraf CAD dosyası depoya kopyalanmaz; birim denemesi için dosyalar burada üretilir. Aynı fiziksel
kutu (dx×dy×dz **mm**) farklı DOSYA birimleriyle yazılabilir: ``mm`` / ``in`` / ``m``. Koordinatlar dosya
biriminde yazılır, birim bildirimi dosyanın başlığındadır. Dönüştürücü her üçünü AYNI milimetre boyutuna
getirmelidir.
"""
from __future__ import annotations

from itertools import product
from typing import Dict, List, Tuple

MM_PER_UNIT: Dict[str, float] = {'mm': 1.0, 'in': 25.4, 'm': 1000.0}
# yüz köşe sırası: dışarıdan bakınca saat yönünün tersi (dış normal: -x,+x,-y,+y,-z,+z)
_FACES = [((0, 4, 6, 2), (-1, 0, 0)), ((1, 3, 7, 5), (1, 0, 0)), ((0, 1, 5, 4), (0, -1, 0)),
          ((2, 6, 7, 3), (0, 1, 0)), ((0, 2, 3, 1), (0, 0, -1)), ((4, 5, 7, 6), (0, 0, 1))]


def _corners(sx: float, sy: float, sz: float) -> List[Tuple[float, float, float]]:
    """8 köşe; sıra: bit0=x, bit1=y, bit2=z (0 → 0, 1 → boyut)."""
    return [(sx * (i & 1), sy * ((i >> 1) & 1), sz * ((i >> 2) & 1)) for i in range(8)]


def _fmt(v: float) -> str:
    return repr(float(v))


def step_box(dx_mm: float, dy_mm: float, dz_mm: float, unit: str = 'mm') -> bytes:
    """AP214 benzeri, kapalı kabuklu (MANIFOLD_SOLID_BREP) kutu; köşe 0 = orijin, eksenler +x,+y,+z."""
    f = MM_PER_UNIT[unit]
    pts = _corners(dx_mm / f, dy_mm / f, dz_mm / f)
    ents: List[str] = []

    def add(text: str) -> int:
        ents.append(text)
        return len(ents)

    ctx_unit = {'mm': "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )",
                'm': "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.) )"}.get(unit)
    app = add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app})")
    if ctx_unit is None:                                         # inç: mm üzerinden dönüşüm tabanlı birim
        si = add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
        lm = add(f"LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(25.4),#{si})")
        de = add('DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.)')
        lu = add(f"( CONVERSION_BASED_UNIT('INCH',#{lm}) LENGTH_UNIT() NAMED_UNIT(#{de}) )")
    else:
        lu = add(ctx_unit)
    au = add('( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )')
    su = add('( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )')
    unc = add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#{lu},'distance_accuracy_value','')")
    rctx = add(f"( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc})) "
               f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{lu},#{au},#{su})) REPRESENTATION_CONTEXT('Context','3D') )")
    cps = [add(f"CARTESIAN_POINT('',({_fmt(x)},{_fmt(y)},{_fmt(z)}))") for x, y, z in pts]
    vps = [add(f"VERTEX_POINT('',#{c})") for c in cps]
    dirs = {d: add(f"DIRECTION('',({_fmt(d[0])},{_fmt(d[1])},{_fmt(d[2])}))")
            for d in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]}
    edges: Dict[Tuple[int, int], int] = {}
    for a, b in product(range(8), repeat=2):                      # köşe çifti: tek bit farklıysa kenar
        if a < b and bin(a ^ b).count('1') == 1:
            d = tuple((pts[b][k] - pts[a][k]) for k in range(3))
            unit_d = tuple(1 if v > 0 else 0 for v in d)
            vec = add(f"VECTOR('',#{dirs[unit_d]},1.)")
            line = add(f"LINE('',#{cps[a]},#{vec})")
            edges[(a, b)] = add(f"EDGE_CURVE('',#{vps[a]},#{vps[b]},#{line},.T.)")
    faces = []
    for loop, normal in _FACES:
        oes = []
        for k in range(4):
            a, b = loop[k], loop[(k + 1) % 4]
            key, fwd = ((a, b), True) if (a, b) in edges else ((b, a), False)
            oes.append(add(f"ORIENTED_EDGE('',*,*,#{edges[key]},.{'T' if fwd else 'F'}.)"))
        el = add(f"EDGE_LOOP('',({','.join('#%d' % o for o in oes)}))")
        fb = add(f"FACE_OUTER_BOUND('',#{el},.T.)")
        ref = tuple(1 if v > 0 else 0 for v in
                    (pts[loop[1]][0] - pts[loop[0]][0], pts[loop[1]][1] - pts[loop[0]][1],
                     pts[loop[1]][2] - pts[loop[0]][2]))
        ref_d = dirs[ref]
        ax = add(f"AXIS2_PLACEMENT_3D('',#{cps[loop[0]]},#{dirs[normal]},#{ref_d})")
        pl = add(f"PLANE('',#{ax})")
        faces.append(add(f"ADVANCED_FACE('',(#{fb}),#{pl},.T.)"))
    shell = add(f"CLOSED_SHELL('',({','.join('#%d' % x for x in faces)}))")
    solid = add(f"MANIFOLD_SOLID_BREP('Kutu',#{shell})")
    org = add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (add("CARTESIAN_POINT('',(0.,0.,0.))"), dirs[(0, 0, 1)],
                                                       dirs[(1, 0, 0)]))
    sr = add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{org},#{solid}),#{rctx})")
    pc = add("PRODUCT_CONTEXT('',#1,'mechanical')")
    prod = add(f"PRODUCT('Kutu','Kutu','',(#{pc}))")
    pf = add(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
    pdc = add("PRODUCT_DEFINITION_CONTEXT('part definition',#1,'design')")
    pdf = add(f"PRODUCT_DEFINITION('design','',#{pf},#{pdc})")
    pds = add(f"PRODUCT_DEFINITION_SHAPE('','',#{pdf})")
    add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{sr})")
    body = '\n'.join(f'#{i + 1} = {t};' for i, t in enumerate(ents))
    head = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Control Hub test kutusu'),'2;1');\n"
            "FILE_NAME('kutu.step','2026-01-01T00:00:00',(''),(''),'test','test','');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n")
    return (head + body + '\nENDSEC;\nEND-ISO-10303-21;\n').encode('ascii')


# ---------------------------------------------------------------------------------------------------
# IGES (sabit genişlikli 80 sütun): kutu = 6 düzlemsel B-spline yüzey (varlık 128)
# ---------------------------------------------------------------------------------------------------
_IGES_UNIT_FLAG = {'in': (1, '2HIN'), 'mm': (2, '2HMM'), 'm': (6, '1HM')}


def _iges_line(text: str, section: str, seq: int) -> str:
    return f'{text:<72}{section}{seq:7d}'


def _hollerith(s: str) -> str:
    return f'{len(s)}H{s}'


def iges_box(dx_mm: float, dy_mm: float, dz_mm: float, unit: str = 'mm') -> bytes:
    """6 yüzeyli kutu; koordinatlar dosya biriminde, birim Global bölümün 14./15. alanındadır."""
    f = MM_PER_UNIT[unit]
    pts = _corners(dx_mm / f, dy_mm / f, dz_mm / f)
    flag, name = _IGES_UNIT_FLAG[unit]
    hol = _hollerith
    glob = (f",,{hol('KUTU')},{hol('kutu.igs')},{hol('test')},{hol('test')},32,38,7,308,15,,"
            f"1.,{flag},{name},1,0.08,{_hollerith('20260101.000000')},1.E-06,10000.,{_hollerith('test')},,11,0;")
    start = _iges_line('Control Hub test kutusu', 'S', 1)
    g_lines = [glob[i:i + 72] for i in range(0, len(glob), 72)]
    p_lines: List[str] = []
    d_lines: List[str] = []
    for n, (loop, _normal) in enumerate(_FACES):
        a, b, c, d = (pts[k] for k in loop)                    # a→b ve a→d yüzün iki kenarı (u hızlı)
        flat = [0., 0., 1., 1.] * 2 + [1.] * 4 + [v for p in (a, b, d, c) for v in p] + [0., 1., 0., 1.]
        params = '128,1,1,1,1,0,0,1,0,0,' + ','.join(_fmt(v) for v in flat) + ';'
        first = len(p_lines) + 1
        chunks = [params[i:i + 64] for i in range(0, len(params), 64)]
        de1 = 2 * n + 1
        for ch in chunks:
            p_lines.append(f'{ch:<64}{de1:8d}P{len(p_lines) + 1:7d}')
        zeros = f'{0:8d}' * 6
        d_lines.append(f'{128:8d}{first:8d}{zeros}{"00010000":>8}D{2 * n + 1:7d}')
        d_lines.append(f'{128:8d}{0:8d}{0:8d}{len(chunks):8d}{0:8d}{"":8}{"":8}{"":8}{0:8d}D{2 * n + 2:7d}')
    out = [start] + [_iges_line(s, 'G', i + 1) for i, s in enumerate(g_lines)] + d_lines + p_lines
    term = f'S{1:7d}G{len(g_lines):7d}D{len(d_lines):7d}P{len(p_lines):7d}'
    out.append(f'{term:<72}T{1:7d}')
    return ('\n'.join(out) + '\n').encode('ascii')
