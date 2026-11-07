"""Sahte robotun jog/çerçeve davranışı: Robot API sözleşmesinin (``docs/INTERFACES.md``, "Jog (J2)")
**Hub'ın görmesi gereken** kısmının aynası. Amaç arayüzü ve Hub yolunu ROS'suz denemek; kinematik YOK
(uç konum doğrudan kartezyen toplanır, dönüş yalnızca W-P-R sayacı), sayılar gerçek robotu temsil etmez.

Aynalananlar: kiralama (tek istemci, 409), sunucu TTL'i (50-500 ms, varsayılan 250), ``ttl_ms: 0`` bırakır,
acil dur 423, artımlı/basılı-tut ayrımı (403), iş çalışırken 409, çerçeve doğrulaması (400) ve jog etkinken
çerçeve yazma 409. Çerçeveler **bellekte** tutulur: dosyaya hiçbir şey yazılmaz.

L8 (küresel çerçeve) aynası: ``GET /api/frames`` ``types``/``spheres``/``max_spheres`` taşır; ``PUT``
gövdesinde ``spheres`` YOKSA küreler korunur, bilinmeyen ``type`` 400 ile adlandırılır;
``POST /api/frames/reach`` küre için erişim aralığı döndürür. Erişim hesabı SAHTEDİR (başlangıç
pozundan bağımsız basit bir halka zarfı); gerçek IK/kutu/tekillik hesabı değildir.
``sphere_support=False`` küreyi bilmeyen eski robotu taklit eder.
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

TTL_DEFAULT_MS, TTL_MAX_MS, TTL_MIN_MS = 250, 500, 50
LEASE_RE = re.compile(r'^[A-Za-z0-9_-]{8,64}$')
AMOUNTS = (0.1, 1.0, 10.0)
LEASE_HOLD_S = 2.0
MAX_FRAMES = 10
MAX_SPHERES = 10
SPHERE_MIN_R, SPHERE_MAX_R, MAX_COORD = 0.05, 2.0, 5.0     # frames.py ile aynı sınırlar
TOOL_AXES = ('x', 'y', 'z', '-x', '-y', '-z')
NAME_RE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,23}')
FAKE_REACH_M = (0.20, 0.75)            # sahte erişim halkası: tabandan uzaklık (min, max)
MAX_V = 0.05                                   # m/s (sahte tavan; gerçek robotta hız tavanı sunucudadır)
Reply = Tuple[int, Dict[str, Any]]


def _err(status: int, message: str, **extra: Any) -> Reply:
    return status, {'ok': False, 'error': message, **extra}


def np_sub(a, b):
    return [x - y for x, y in zip(a, b)]


class FakeJog:
    def __init__(self, *, get_tool: Callable[[], Tuple[float, float, float]],
                 set_tool: Callable[[Tuple[float, float, float]], None],
                 is_halted: Callable[[], bool], is_busy: Callable[[], bool],
                 hold_enabled: bool = True, clock: Callable[[], float] = time.monotonic,
                 sphere_support: bool = True):
        self._get, self._set, self._halted, self._busy = get_tool, set_tool, is_halted, is_busy
        self.hold_enabled, self._clock = hold_enabled, clock
        self._lock = threading.Lock()
        self._lease: Optional[str] = None
        self._mode: Optional[str] = None
        self._frame = 'World'
        self._expires = self._last_cmd = self._t = 0.0
        self._ttl_ms = TTL_DEFAULT_MS
        self._v = (0.0, 0.0, 0.0)
        self._rot = [0.0, 0.0, 0.0]
        self.frames: List[Dict[str, Any]] = []
        self.spheres: List[Dict[str, Any]] = []
        self.sphere_support = sphere_support

    # --- zaman içinde ilerleme (basılı-tut): TTL dolunca hız sıfır ------------------------------
    def _advance(self, now: float) -> None:
        upto = min(now, self._expires)
        dt = upto - self._t
        if self._mode == 'hold' and dt > 0 and any(self._v):
            x, y, z = self._get()
            self._set((x + self._v[0] * dt, y + self._v[1] * dt, max(0.02, z + self._v[2] * dt)))
        self._t = max(self._t, now)
        if now > self._expires:
            self._v = (0.0, 0.0, 0.0)

    def active(self) -> bool:
        with self._lock:
            return self._lease is not None and self._clock() - self._last_cmd < LEASE_HOLD_S

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status(self._clock())

    def _status(self, now: float) -> Dict[str, Any]:
        self._advance(now)
        live = self._lease is not None and now - self._last_cmd < LEASE_HOLD_S
        return {'enabled': True, 'active': live, 'mode': self._mode if live else None,
                'frame': self._frame if live else None, 'status': 'ok', 'messages': [],
                'moving': any(self._v) and now <= self._expires, 'hold_enabled': self.hold_enabled,
                'speed': {'max_linear_m_s': MAX_V, 'max_angular_rad_s': 0.2},
                'ttl_max_ms': TTL_MAX_MS, 'increments': list(AMOUNTS)}

    def tcp(self) -> Dict[str, Any]:
        with self._lock:
            self._advance(self._clock())
        return {'frame': 'World', 'position': [round(v, 4) for v in self._get()],
                'wpr_deg': [round(v, 2) for v in self._rot], 'warning': None}

    # --- POST /api/jog -------------------------------------------------------------------------
    def command(self, body: Any, *, remote: bool = False) -> Reply:
        now = self._clock()
        with self._lock:
            self._advance(now)
            if self._halted():
                self._end()
                return _err(423, 'ACİL DUR kilidi açık: önce kilidi aç', state='estopped')
            allowed = {'lease', 'frame', 'twist', 'increment', 'ttl_ms'}
            if not isinstance(body, dict) or set(body) - allowed or 'lease' not in body:
                return _err(400, 'gövde {"lease", "frame", "twist" | "increment", "ttl_ms"} olmalı')
            lease = body['lease']
            if not isinstance(lease, str) or not LEASE_RE.fullmatch(lease):
                return _err(400, 'lease 8-64 karakterlik harf/rakam/_/- bir belirteç olmalı')
            raw_ttl = body.get('ttl_ms')
            if raw_ttl is not None and (isinstance(raw_ttl, bool) or not isinstance(raw_ttl, (int, float))
                                        or not math.isfinite(raw_ttl)):
                return _err(400, f'ttl_ms sayı olmalı, verilen: {raw_ttl!r}')
            ttl = 0 if raw_ttl == 0 else int(min(TTL_MAX_MS, max(TTL_MIN_MS, raw_ttl or TTL_DEFAULT_MS)))
            clamped = raw_ttl not in (None, 0) and ttl != raw_ttl
            if self._lease is not None and self._lease != lease:
                if now <= self._expires or now - self._last_cmd < LEASE_HOLD_S:
                    return _err(409, 'jog başka bir istemcinin kiralamasında', state='jogging')
                self._end()
            if ttl == 0:
                if self._lease == lease:
                    self._end()
                return 200, self._reply(now, False)
            if self._busy():
                return _err(409, 'bir iş çalışıyor: jog ve işler birbirini dışlar', state='busy')
            has_twist, has_inc = 'twist' in body, 'increment' in body
            if has_twist == has_inc:
                return _err(400, 'tam olarak biri gerekir: "twist" (basılı-tut) ya da "increment"')
            frame = body.get('frame', 'World')
            known = ('World', 'Tool', *[f['name'] for f in self.frames])
            if not isinstance(frame, str) or frame not in known:
                return _err(400, f'bilinmeyen çerçeve: {frame!r}')
            if has_inc:
                bad = self._increment(body['increment'])
                if bad:
                    return bad
                mode = 'increment'
            else:
                if not self.hold_enabled:
                    return _err(403, 'basılı-tut jog bu sunucuda kapalı (yalnızca artımlı); '
                                     'sunucu yapılandırmasıyla açılır')
                if remote:
                    return _err(403, 'basılı-tut jog uzak (loopback dışı) istemcilere kapalı')
                tw = body['twist']
                if not (isinstance(tw, list) and len(tw) == 6 and all(
                        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                        and abs(v) <= 1.0 for v in tw)):
                    return _err(400, 'twist 6 sonlu sayı olmalı [vx vy vz wx wy wz] (|değer| <= 1)')
                self._v = tuple(max(-MAX_V, min(MAX_V, v)) for v in tw[:3])    # type: ignore[assignment]
                mode = 'hold'
            self._lease, self._frame, self._mode = lease, frame, mode
            self._last_cmd, self._t, self._ttl_ms = now, now, ttl
            self._expires = now + ttl / 1000.0
            return 200, self._reply(now, bool(clamped))

    def _increment(self, inc: Any) -> Optional[Reply]:
        ok = (isinstance(inc, dict) and set(inc) == {'axis', 'amount'} and isinstance(inc['axis'], int)
              and not isinstance(inc['axis'], bool) and 0 <= inc['axis'] <= 5
              and isinstance(inc['amount'], (int, float)) and not isinstance(inc['amount'], bool)
              and math.isfinite(inc['amount']))
        if not ok:
            return _err(400, 'increment {"axis": 0-5 (X Y Z W P R), "amount": ±(0.1|1|10) mm ya da °} olmalı')
        if not any(math.isclose(abs(inc['amount']), a, rel_tol=1e-9) for a in AMOUNTS):
            return _err(400, 'artım yalnızca 0.1, 1, 10 (mm ya da °) olabilir')
        if inc['axis'] < 3:
            pos = list(self._get())
            pos[inc['axis']] += inc['amount'] / 1000.0
            pos[2] = max(0.02, pos[2])
            self._set((pos[0], pos[1], pos[2]))
        else:
            self._rot[inc['axis'] - 3] += inc['amount']
        self._v = (0.0, 0.0, 0.0)
        return None

    def _end(self) -> None:
        self._lease = self._mode = None
        self._v = (0.0, 0.0, 0.0)

    def _reply(self, now: float, clamped: bool) -> Dict[str, Any]:
        return {**self._status(now), 'ok': True, 'ttl_ms': self._ttl_ms, 'ttl_clamped': clamped,
                'stop_ramp_ms': 100}

    # --- çerçeveler (bellekte) -----------------------------------------------------------------
    def frames_doc(self) -> Dict[str, Any]:
        doc: Dict[str, Any] = {'builtin': ['World', 'Tool'], 'max_frames': MAX_FRAMES,
                               'frames': [{'type': 'user', **f} for f in self.frames]}
        if self.sphere_support:
            doc.update(types=['user', 'sphere'], spheres=[dict(sp) for sp in self.spheres],
                       max_spheres=MAX_SPHERES)
        return doc

    def frames_put(self, body: Any) -> Reply:
        keys = {'frames', 'spheres'} if self.sphere_support else {'frames'}
        if not (isinstance(body, dict) and {'frames'} <= set(body) <= keys
                and isinstance(body['frames'], list)):
            return _err(400, 'gövde {"frames": [{"name", "points": {"origin", "x", "xy"}}, ...]'
                             + (', "spheres": [...] (isteğe bağlı)' if self.sphere_support else '')
                             + '} olmalı')
        if len(body['frames']) > MAX_FRAMES:
            return _err(400, f'en çok {MAX_FRAMES} çerçeve')
        out, names = [], set()
        for i, f in enumerate(body['frames']):
            kind = f.get('type', 'user') if isinstance(f, dict) else 'user'
            if kind != 'user':
                if kind == 'sphere' and self.sphere_support:
                    return _err(400, f'frames[{i}]: küre çerçeveleri "spheres" listesinde '
                                     'gönderilir, "frames"te değil')
                known = ", ".join(self.frames_doc().get("types", ["user"]))
                return _err(400, f'frames[{i}]: bilinmeyen çerçeve türü {kind!r} (desteklenen: {known}); '
                                 'sessizce atlanmaz')
            name = f.get('name') if isinstance(f, dict) else None
            pts = f.get('points') if isinstance(f, dict) else None
            if not (isinstance(name, str) and NAME_RE.fullmatch(name)) \
                    or name in ('World', 'Tool') or name in names:
                return _err(400, f'çerçeve adı geçersiz ya da yinelenmiş: {name!r}')
            if not (isinstance(pts, dict) and set(pts) == {'origin', 'x', 'xy'} and all(
                    isinstance(p, list) and len(p) == 3 and all(
                        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                        for v in p)
                    for p in pts.values())):
                return _err(400, f'{name}: points {{"origin","x","xy"}} üç noktalı olmalı (3 sayı)')
            px, pv = (np_sub(pts[k], pts['origin']) for k in ('x', 'xy'))
            dist = math.sqrt(sum(v * v for v in px))
            if dist < 0.020:
                return _err(400, f'{name}: orijin ile X noktası arası en az 20 mm olmalı (X yönü belirsiz)')
            cross = [px[1] * pv[2] - px[2] * pv[1], px[2] * pv[0] - px[0] * pv[2],
                     px[0] * pv[1] - px[1] * pv[0]]
            lv = math.sqrt(sum(v * v for v in pv))
            sin = math.sqrt(sum(v * v for v in cross)) / (dist * lv) if lv > 1e-9 else 0.0
            if sin < 0.17:
                return _err(400, f'{name}: XY noktası X ekseniyle neredeyse aynı doğru üzerinde '
                                 f'(sin={sin:.3f} < 0.17); düzlem belirsiz, noktayı X ekseninden uzaklaştır')
            names.add(name)
            out.append({'name': name, 'points': pts, 'pose': {'position': pts['origin']},
                        'wpr_deg': [0.0, 0.0, 0.0], 'warnings': ['sahte robot: dönüş hesaplanmaz']})
        new_spheres: Optional[List[Dict[str, Any]]] = None
        if 'spheres' in body:                      # YOKSA mevcut küreler korunur (eski Hub'ın PUT'u)
            parsed = self._parse_spheres(body['spheres'])
            if isinstance(parsed, tuple):
                return parsed
            new_spheres = parsed
        kept = self.spheres if new_spheres is None else new_spheres
        clash = {n.lower() for n in names} & {sp['name'].lower() for sp in kept}
        if clash:
            return _err(400, f'çerçeve adı hem kullanıcı hem küre olamaz: {", ".join(sorted(clash))}')
        with self._lock:
            if self._lease is not None and self._clock() - self._last_cmd < LEASE_HOLD_S:
                return _err(409, 'jog etkinken çerçeve listesi değiştirilemez')
            self.frames = out
            if new_spheres is not None:
                self.spheres = new_spheres
        return 200, self.frames_doc()

    @staticmethod
    def _sphere_geometry(entry: Dict[str, Any]) -> Optional[str]:
        """Geçerliyse ``None``, değilse neden (robotun ``sphere_frame`` doğrulamasının aynası)."""
        c, r, axis = entry.get('center'), entry.get('radius'), entry.get('tool_axis', 'z')
        if not (isinstance(c, list) and len(c) == 3 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in c)):
            return 'küre merkezi 3 sonlu sayı olmalı [x, y, z] (metre)'
        if max(abs(v) for v in c) > MAX_COORD:
            return f'küre merkezi {list(c)} m: {MAX_COORD:g} m dışında (birim mm mi?)'
        if (isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r)
                or not SPHERE_MIN_R <= r <= SPHERE_MAX_R):
            return f'yarıçap [{SPHERE_MIN_R:g}, {SPHERE_MAX_R:g}] m aralığında olmalı, bulunan: {r!r}'
        if axis not in TOOL_AXES:
            return f'takım ekseni {sorted(TOOL_AXES)} içinden biri olmalı: {axis!r}'
        return None

    def _parse_spheres(self, entries: Any) -> Any:
        if not self.sphere_support:
            return _err(400, 'küre çerçeveleri bu sunucuda kullanılamıyor')
        if not isinstance(entries, list):
            return _err(400, 'spheres: liste olmalı')
        if len(entries) > MAX_SPHERES:
            return _err(400, f'en çok {MAX_SPHERES} küre çerçevesi')
        out: List[Dict[str, Any]] = []
        for i, e in enumerate(entries):
            where = f'spheres[{i}]'
            if not isinstance(e, dict):
                return _err(400, f'{where}: {{"type": "sphere", "name", "center", "radius"}} olmalı')
            kind = e.get('type', 'sphere')
            if kind != 'sphere':
                return _err(400, f'{where}: bilinmeyen çerçeve türü {kind!r} '
                                 '(desteklenen: user, sphere); sessizce atlanmaz')
            extra = set(e) - {'type', 'name', 'center', 'radius', 'tool_axis'}
            missing = {'name', 'center', 'radius'} - set(e)
            if extra or missing:
                return _err(400, f'{where}: bilinmeyen {sorted(extra) or "-"}, '
                                 f'eksik {sorted(missing) or "-"}')
            name = e['name']
            if not (isinstance(name, str) and NAME_RE.fullmatch(name)) or name in ('World', 'Tool'):
                return _err(400, f'{where}: ad geçersiz: {name!r}')
            bad = self._sphere_geometry(e)
            if bad:
                return _err(400, f'{where}: {bad}')
            if name.lower() in {o['name'].lower() for o in out}:
                return _err(400, f'{where}: ad tekrar ediyor: {name!r}')
            out.append({'type': 'sphere', 'name': name, 'center': [float(v) for v in e['center']],
                        'radius': float(e['radius']), 'tool_axis': e.get('tool_axis', 'z')})
        return out

    # --- POST /api/frames/reach (SAHTE hesap: tabandan uzaklık halkası) ---------------------------
    def frames_reach(self, body: Any) -> Reply:
        if not self.sphere_support:
            return _err(403, 'küre çerçevesi bu sunucuda kapalı')
        if not (isinstance(body, dict)
                and {'center', 'radius'} <= set(body) <= {'center', 'radius', 'tool_axis'}):
            return _err(400, 'gövde {"center": [x,y,z], "radius": r, "tool_axis"?} olmalı')
        bad = self._sphere_geometry(body)
        if bad:
            return _err(400, bad)
        if self._halted():
            return _err(423, 'ACİL DUR kilidi açık: önce kilidi aç', state='estopped')
        with self._lock:
            if (self._lease is not None and self._clock() - self._last_cmd < LEASE_HOLD_S) or self._busy():
                return _err(409, 'jog ya da iş sürerken erişim aralığı hesaplanmaz', state='busy')
        cx, cy, cz = (float(v) for v in body['center'])
        r = float(body['radius'])
        azs = [float(a) for a in range(0, 360, 15)]
        els = [float(e) for e in range(-80, 81, 10)]
        cells = []
        for el_ in els:
            row = []
            for az in azs:
                d = (math.cos(math.radians(el_)) * math.cos(math.radians(az)),
                     math.cos(math.radians(el_)) * math.sin(math.radians(az)), math.sin(math.radians(el_)))
                p = (cx + r * d[0], cy + r * d[1], cz + r * d[2])
                dist = math.sqrt(sum(v * v for v in p))
                row.append(FAKE_REACH_M[0] <= dist <= FAKE_REACH_M[1] and p[2] >= 0.05)
            cells.append(row)
        rows = []
        for el_, row in zip(els, cells):
            if any(row):
                runs, start = [], None
                for i, ok in enumerate(row + [False]):
                    if ok and start is None:
                        start = i
                    elif not ok and start is not None:
                        runs.append([azs[start], azs[i - 1]])
                        start = None
                rows.append({'elevation_deg': el_, 'azimuth_intervals_deg': runs})
        reached = [r_['elevation_deg'] for r_ in rows]
        rng = [min(reached), max(reached)] if reached else None
        return 200, {
            'ok': True, 'center': [cx, cy, cz], 'radius': r, 'tool_axis': body.get('tool_axis', 'z'),
            'reachable': bool(reached), 'elevation_range_deg': rng, 'rows': rows,
            'grid': {'azimuth_deg': azs, 'elevation_deg': els,
                     'cells': [''.join('1' if c else '0' for c in row) for row in cells]},
            'summary': (f'Yükselme {rng[0]:g}° … {rng[1]:g}° erişilebilir (SAHTE robot hesabı)' if rng
                        else 'Bu küreye erişilemiyor (SAHTE robot hesabı)'),
            'note': 'SAHTE robot: tabandan uzaklık halkası; gerçek IK, güvenli kutu, eklem limiti ve '
                    'tekillik hesaplanmaz. Izgara kaba (15° azimut, 10° yükselme).'}
