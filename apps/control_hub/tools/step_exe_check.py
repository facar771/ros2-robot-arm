"""Çalışan bir Hub'a (CI'da DERLENMİŞ .exe'ye) STEP/IGES küpü yükler; gerçekten dönüşüyor mu? (S2b, #109)

    python -m tools.step_exe_check --url http://127.0.0.1:18767 --pin 123456

Gömülü .exe'de dönüşüm ``ControlHub.exe --convert`` ALT SÜRECİYLE yapılır; bu araç onu uçtan uca kanıtlar:
yükleme -> alt süreç -> mesh. Kontroller (STEP ve IGES, her ikisi mm/inç/m birimli dosyalarla):
  * ``/api/parts`` -> ``step.available`` (cascadio .exe'ye gömülü ve bulunuyor)
  * 10x20x30 mm küp yüklenir; 201 döner, depolanan ikili STL'in sınır kutusu 10x20x30 mm (±0.01)
  * bozuk STEP: 422 ``step_failed`` + Hub hâlâ /health veriyor (alt süreç çökmesi Hub'ı düşürmez)
Çıkış kodu 0 = hepsi geçti. Alt süreç gömülü exe'de çalışmıyorsa KIRMIZI biter (UI_EMIRLER #109 koşul 3).
"""
from __future__ import annotations

import argparse
import base64
import struct
import sys
from typing import List, Optional, Tuple

from tests.helpers_step import iges_box, step_box
from tools.smoke_check import FAILED, Client, check, wait_for

EXPECT_MM = (10.0, 20.0, 30.0)
TOL_MM = 0.01


def stl_size_mm(stl: bytes) -> Optional[Tuple[float, float, float]]:
    """İkili STL baytlarından sınır kutusu boyutu (ham birim = mm); geçersizse ``None``."""
    if len(stl) < 84:
        return None
    n = struct.unpack_from('<I', stl, 80)[0]
    if n == 0 or len(stl) != 84 + 50 * n:
        return None
    lo, hi = [1e30] * 3, [-1e30] * 3
    for i in range(n):
        v = struct.unpack_from('<12f', stl, 84 + 50 * i)[3:]
        for k in range(3):
            for j in range(3):
                lo[k], hi[k] = min(lo[k], v[3 * j + k]), max(hi[k], v[3 * j + k])
    return tuple(h - lo_ for h, lo_ in zip(hi, lo))                      # type: ignore[return-value]


def upload(c: Client, filename: str, raw: bytes, unit: str = 'mm') -> Tuple[int, dict]:
    st, doc, _ = c.request('POST', '/api/parts', {'filename': filename, 'kind': 'workpiece', 'unit': unit,
                                                  'data_b64': base64.b64encode(raw).decode()})
    return st, doc if isinstance(doc, dict) else {}


def mesh_bytes(c: Client, part: dict) -> bytes:
    """Hub JSON dışı gövdeyi ``Client`` ile metne çevirir; ikili STL için ham okuma gerekir."""
    import http.client
    conn = http.client.HTTPConnection(c.host, c.port, timeout=30)
    try:
        conn.request('GET', part['url'], headers={'Cookie': c.cookie or ''})
        return conn.getresponse().read()
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--url', default='http://127.0.0.1:8765')
    ap.add_argument('--pin', default='123456')
    ap.add_argument('--wait', type=float, default=60.0)
    a = ap.parse_args(argv)
    c = Client(a.url)

    def health() -> bool:
        try:
            return c.request('GET', '/health')[0] == 200
        except OSError:
            return False
    if not check('Hub açıldı (/health)', wait_for(health, a.wait)):
        return 1
    st, _, _ = c.request('POST', '/api/login', {'pin': a.pin})
    if not check('PIN ile giriş', st == 200 and bool(c.cookie), f'HTTP {st}'):
        return 1
    st, doc, _ = c.request('GET', '/api/parts')
    step = doc.get('step', {}) if isinstance(doc, dict) else {}
    if not check('dönüştürücü gömülü ve bulundu (step.available)',
                 st == 200 and step.get('available') is True, f'HTTP {st} {step}'):
        return 1
    formats = doc['limits']['formats']
    check('.step/.iges kabul listesinde', all(e in formats for e in ('.step', '.iges')), str(formats))
    cases = [(f'kup_{u}.{ext}', gen(*EXPECT_MM, unit=u), u)
             for ext, gen in (('step', step_box), ('iges', iges_box)) for u in ('mm', 'in', 'm')]
    for name, raw, unit in cases:
        st, res = upload(c, name, raw, unit)
        part = res.get('part', {})
        ok = st == 201 and res.get('ok') is True and part.get('triangles', 0) >= 12 \
            and part.get('unit') == 'mm'
        if not check(f'{name}: yüklendi, alt süreçte dönüştü', ok, f'HTTP {st} {res.get("message", "")}'):
            continue
        size = stl_size_mm(mesh_bytes(c, part))
        good = size is not None and all(abs(s - e) <= TOL_MM for s, e in zip(size, EXPECT_MM))
        check(f'{name}: boyut 10x20x30 mm', good, str(size))
        c.request('DELETE', f'/api/parts/{part["id"]}')
    bad = b'ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1=BOZUK(;\nENDSEC;\nEND-ISO-10303-21;\n'
    st, res = upload(c, 'bozuk.step', bad)
    check('bozuk STEP: 422 step_failed', st == 422 and res.get('error') == 'step_failed', f'HTTP {st} {res}')
    check('bozuk dosyadan sonra Hub yaşıyor', health())
    print(f'\n{len(FAILED)} KALDI' if FAILED else '\nHEPSİ GEÇTİ')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
