"""Program deposu (P2): program modeli (JSON, ``version: 1``), katı doğrulama, kuru çalıştırma ve
``HUB_DATA_DIR/programs`` kaydı.

Neden böyle:
* Model KATI doğrulanır (bilinmeyen anahtar, NaN/∞, aralık dışı sayı, 500+ adım reddedilir): dosya elle ya da
  dışarıdan (içe aktarma) gelebilir; koşucu yalnız doğrulanmış modeli görür (K6).
* Kullanıcının yazdığı ad ASLA yol olarak kullanılmaz: dosya adı ``slugify(ad)`` ile üretilir ([a-z0-9_-]),
  yol ``programs/`` dışına çıkamaz, yazım atomiktir (geçici dosya + ``os.replace``).
* Hub robotun kurallarını gevşetemez (K3c): burada yalnız şekil/aralık denetimi var. Güvenli kutu, eklem
  limiti, ulaşılabilirlik ve yol engeli hareket arka ucunda denetlenir (EK-1 B ``/api/move/check``).
* Silinmiş waypoint'e bağlı adım KAYDEDİLEBİLİR (düzeltilebilsin diye); kuru çalıştırma onu kırmızı gösterir,
  ``run`` reddeder.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from control_hub.hub_core.atomic import write_bytes_atomic
from control_hub.paths import user_dir

SCHEMA_VERSION = 1
MAX_STEPS = 500                 # tanımdaki adım sayısı (döngü ve gövdesi dahil)
MAX_EXEC_ACTIONS = 20000        # döngüler açılınca çalışacak toplam hareket/bekleme/alet adımı
MAX_WAYPOINTS = 200
MAX_LOOP_COUNT = 1000
MAX_JOINTS = 8
MAX_PROGRAMS = 200
MAX_JSON_BYTES = 1_000_000      # K6: yükleme üst sınırı 50 MB; program JSON'u bundan çok daha küçüktür
MAX_TEXT = 200
MAX_WAIT_S = 3600.0
JOINT_ABS_DEG, RPY_ABS_DEG, POS_ABS_M, OFFSET_ABS_M = 720.0, 720.0, 10.0, 1.0
KINDS = ('move_j', 'move_l', 'wait', 'tool', 'comment', 'loop')
NAME_RE = re.compile(r'^[0-9A-Za-zÇĞİÖŞÜçğıöşü][0-9A-Za-zÇĞİÖŞÜçğıöşü _.-]{0,39}$')
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
_TR = str.maketrans('çğıöşüÇĞİÖŞÜ', 'cgiosuCGIOSU')


class ProgramError(Exception):
    """Depo/doğrulama hatası: ``status`` HTTP kodu, ``code`` makine kodu, ``extra`` ek alanlar."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def slugify(name: str) -> str:
    """Görünen addan dosya adı: Türkçe harfler ASCII'ye, kalan her şey tek ``-``; en çok 40 karakter."""
    return re.sub(r'[^a-z0-9]+', '-', str(name).translate(_TR).lower()).strip('-')[:40].strip('-')


# --- doğrulama (saf işlevler) ----------------------------------------------------------------------
def _err(errs: List[Dict[str, str]], path: str, message: str) -> None:
    errs.append({'path': path, 'message': message})


def _num(v: Any, lo: float, hi: float, path: str, errs: List[Dict[str, str]]) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _err(errs, path, 'sayı olmalı')
        return None
    try:
        f = float(v)
    except OverflowError:
        f = math.inf
    if not math.isfinite(f):
        _err(errs, path, 'sonlu sayı olmalı (NaN/∞ olmaz)')
        return None
    if not lo <= f <= hi:
        _err(errs, path, f'{lo:g} … {hi:g} aralığında olmalı')
        return None
    return f


def _vec(v: Any, size: Tuple[int, int], lo: float, hi: float, path: str,
         errs: List[Dict[str, str]]) -> Optional[List[float]]:
    if not isinstance(v, list) or not size[0] <= len(v) <= size[1]:
        want = f'{size[0]}' if size[0] == size[1] else f'{size[0]}–{size[1]}'
        _err(errs, path, f'{want} sayılık liste olmalı')
        return None
    out = [_num(x, lo, hi, f'{path}[{i}]', errs) for i, x in enumerate(v)]
    return None if any(x is None for x in out) else [float(x) for x in out]


def _name(v: Any, path: str, errs: List[Dict[str, str]]) -> Optional[str]:
    if not isinstance(v, str) or not NAME_RE.fullmatch(v) or not slugify(v):
        _err(errs, path, 'ad 1–40 karakter: harf/rakam ile başlar; harf, rakam, boşluk, . _ - içerebilir')
        return None
    return v


def _keys(d: Dict[str, Any], allowed: set, path: str, errs: List[Dict[str, str]]) -> None:
    for k in sorted(set(d) - allowed, key=str):
        _err(errs, f'{path}.{k}'.lstrip('.'), 'bilinmeyen alan')


def _waypoint(raw: Any, path: str, errs: List[Dict[str, str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        _err(errs, path, 'waypoint nesne olmalı')
        return out
    _keys(raw, {'name', 'joints_deg', 'pos_m', 'rpy_deg'}, path, errs)
    out['name'] = _name(raw.get('name'), f'{path}.name', errs)
    if 'joints_deg' in raw:
        out['joints_deg'] = _vec(raw['joints_deg'], (1, MAX_JOINTS), -JOINT_ABS_DEG, JOINT_ABS_DEG,
                                 f'{path}.joints_deg', errs)
    if ('pos_m' in raw) != ('rpy_deg' in raw):
        _err(errs, path, 'pos_m ve rpy_deg birlikte verilmeli')
    elif 'pos_m' in raw:
        out['pos_m'] = _vec(raw['pos_m'], (3, 3), -POS_ABS_M, POS_ABS_M, f'{path}.pos_m', errs)
        out['rpy_deg'] = _vec(raw['rpy_deg'], (3, 3), -RPY_ABS_DEG, RPY_ABS_DEG, f'{path}.rpy_deg', errs)
    if 'joints_deg' not in raw and 'pos_m' not in raw:
        _err(errs, path, 'waypoint en az eklemleri (joints_deg) ya da TCP\'yi (pos_m + rpy_deg) taşımalı')
    return out


def _step(raw: Any, path: str, errs: List[Dict[str, str]], in_loop: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        _err(errs, path, 'adım nesne olmalı')
        return out
    kind = raw.get('kind')
    if kind not in KINDS:
        _err(errs, f'{path}.kind', f'adım türü {", ".join(KINDS)} olmalı')
        return out
    out['kind'] = kind
    if kind == 'move_j':
        _keys(raw, {'kind', 'joints_deg', 'waypoint'}, path, errs)
        if ('joints_deg' in raw) == ('waypoint' in raw):
            _err(errs, path, 'move_j: joints_deg ya da waypoint (ikisinden tam biri)')
        elif 'waypoint' in raw:
            out['waypoint'] = _name(raw['waypoint'], f'{path}.waypoint', errs)
        else:
            out['joints_deg'] = _vec(raw['joints_deg'], (1, MAX_JOINTS), -JOINT_ABS_DEG, JOINT_ABS_DEG,
                                     f'{path}.joints_deg', errs)
    elif kind == 'move_l':
        _keys(raw, {'kind', 'pos_m', 'rpy_deg', 'waypoint', 'offset_m', 'orientation'}, path, errs)
        ori = raw.get('orientation', 'fixed')
        free = ori == 'free'                      # yalnız konum hedefi; yönelimi çözücü belirler (#108)
        if not free and ori != 'fixed':
            _err(errs, f'{path}.orientation', "yönelim 'fixed' ya da 'free' olmalı")
        if free:
            out['orientation'] = 'free'                        # 'fixed' varsayılandır: kayda yazılmaz
        if 'waypoint' in raw:
            out['waypoint'] = _name(raw['waypoint'], f'{path}.waypoint', errs)
            out['offset_m'] = _vec(raw.get('offset_m', [0, 0, 0]), (3, 3), -OFFSET_ABS_M, OFFSET_ABS_M,
                                   f'{path}.offset_m', errs)
            if 'pos_m' in raw or 'rpy_deg' in raw:
                _err(errs, path, 'move_l: waypoint ile pos_m/rpy_deg birlikte verilmez')
        elif free and 'rpy_deg' in raw:
            _err(errs, path, "move_l: orientation:'free' ile rpy_deg birlikte verilmez")
        elif 'pos_m' in raw and ('rpy_deg' in raw or free) and 'offset_m' not in raw:
            out['pos_m'] = _vec(raw['pos_m'], (3, 3), -POS_ABS_M, POS_ABS_M, f'{path}.pos_m', errs)
            if not free:
                out['rpy_deg'] = _vec(raw['rpy_deg'], (3, 3), -RPY_ABS_DEG, RPY_ABS_DEG,
                                      f'{path}.rpy_deg', errs)
        else:
            _err(errs, path, "move_l: (pos_m + rpy_deg | pos_m + orientation:'free') "
                             'ya da (waypoint [+ offset_m])')
    elif kind == 'wait':
        _keys(raw, {'kind', 's'}, path, errs)
        out['s'] = _num(raw.get('s'), 0.0, MAX_WAIT_S, f'{path}.s', errs)
    elif kind == 'tool':
        _keys(raw, {'kind', 'open_pct'}, path, errs)
        out['open_pct'] = _num(raw.get('open_pct'), 0.0, 100.0, f'{path}.open_pct', errs)
    elif kind == 'comment':
        _keys(raw, {'kind', 'text'}, path, errs)
        text = raw.get('text', '')
        if not isinstance(text, str) or len(text) > MAX_TEXT or any(ord(c) < 32 for c in text):
            _err(errs, f'{path}.text', f'metin en çok {MAX_TEXT} karakter, denetim karakteri olmaz')
        else:
            out['text'] = text
    else:                                                      # loop (tek seviye)
        _keys(raw, {'kind', 'count', 'steps'}, path, errs)
        count = raw.get('count')
        if in_loop:
            _err(errs, path, 'döngü içinde döngü olmaz (tek seviye)')
        elif isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_LOOP_COUNT:
            _err(errs, f'{path}.count', f'tekrar sayısı 1 … {MAX_LOOP_COUNT} tamsayı olmalı')
        elif not isinstance(raw.get('steps'), list) or not raw['steps']:
            _err(errs, f'{path}.steps', 'döngü gövdesi boş olamaz')
        else:
            out['count'] = count
            out['steps'] = [_step(s, f'{path}.steps[{j}]', errs, True) for j, s in enumerate(raw['steps'])]
    return out


def _raw_total(steps: Any) -> int:
    """Ham listedeki adım sayısı (döngü gövdesi dahil); ağır doğrulamaya girmeden boyut sınırı için."""
    n = 0
    for s in steps if isinstance(steps, list) else []:
        n += 1
        if isinstance(s, dict) and isinstance(s.get('steps'), list):
            n += len(s['steps'])
    return n


def count_actions(program: Dict[str, Any]) -> int:
    """Döngüler açılınca çalışacak adım sayısı (yorumlar ve döngü satırı sayılmaz)."""
    n = 0
    for s in program['steps']:
        if s['kind'] == 'loop':
            n += s['count'] * sum(1 for b in s['steps'] if b['kind'] != 'comment')
        elif s['kind'] != 'comment':
            n += 1
    return n


def validate_program(raw: Any) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]]]:
    """(temiz program, hata listesi). Hata varsa temiz program ``None``; hatalar ``{path, message}``."""
    errs: List[Dict[str, str]] = []
    if not isinstance(raw, dict):
        return None, [{'path': '', 'message': 'program bir JSON nesnesi olmalı'}]
    _keys(raw, {'version', 'name', 'waypoints', 'steps'}, '', errs)
    if isinstance(raw.get('version'), bool) or raw.get('version') != SCHEMA_VERSION:
        _err(errs, 'version', f'sürüm {SCHEMA_VERSION} olmalı')
    name = _name(raw.get('name'), 'name', errs)
    wps_raw, steps_raw = raw.get('waypoints', []), raw.get('steps')
    if not isinstance(wps_raw, list) or len(wps_raw) > MAX_WAYPOINTS:
        _err(errs, 'waypoints', f'liste olmalı, en çok {MAX_WAYPOINTS} waypoint')
        wps_raw = []
    if not isinstance(steps_raw, list) or _raw_total(steps_raw) > MAX_STEPS:
        _err(errs, 'steps', f'liste olmalı, en çok {MAX_STEPS} adım (döngü gövdesi dahil)')
        steps_raw = []
    wps = [_waypoint(w, f'waypoints[{i}]', errs) for i, w in enumerate(wps_raw)]
    seen = [w['name'] for w in wps if w.get('name')]
    if len(seen) != len(set(seen)):
        _err(errs, 'waypoints', 'waypoint adları benzersiz olmalı')
    steps = [_step(s, f'steps[{i}]', errs, False) for i, s in enumerate(steps_raw)]
    clean = {'version': SCHEMA_VERSION, 'name': name, 'waypoints': wps, 'steps': steps}
    if not errs and count_actions(clean) > MAX_EXEC_ACTIONS:
        _err(errs, 'steps', f'döngüler açılınca {MAX_EXEC_ACTIONS} adımı aşıyor')
    return (None, errs) if errs else (clean, [])


def _reject_const(token: str) -> Any:
    raise ValueError(f'JSON sabiti kabul edilmez: {token}')        # NaN / Infinity / -Infinity


def parse_text(text: str) -> Dict[str, Any]:
    """İçe aktarma/dosya metni -> doğrulanmış program (``ProgramError``: 413/400/422)."""
    if len(text.encode('utf-8', 'replace')) > MAX_JSON_BYTES:
        raise ProgramError(413, 'too_large', f'program dosyası en çok {MAX_JSON_BYTES // 1000} KB olabilir')
    try:
        raw = json.loads(text, parse_constant=_reject_const)
    except (ValueError, RecursionError) as exc:
        raise ProgramError(400, 'bad_json', f'Geçersiz JSON: {str(exc)[:80]}') from None
    clean, errs = validate_program(raw)
    if clean is None:
        raise ProgramError(422, 'invalid_program', f'Program geçersiz ({len(errs)} hata)', errors=errs[:50])
    return clean


# --- kayıt: HUB_DATA_DIR/programs/<slug>.json -------------------------------------------------------
def data_dir(env: Optional[Dict[str, str]] = None) -> Path:
    """Hub kullanıcı verisi (EK-1 D): ``HUB_DATA_DIR``; yoksa kullanıcı klasörü altında ``data``."""
    explicit = (os.environ if env is None else env).get('HUB_DATA_DIR')
    return Path(explicit) if explicit else user_dir() / 'data'


class ProgramStore:
    def __init__(self, base: Optional[Path] = None):
        self._base = Path(base) if base else data_dir()

    @property
    def root(self) -> Path:
        return self._base / 'programs'

    def path_for(self, slug: str) -> Path:
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise ProgramError(400, 'bad_name', 'Geçersiz program adı')
        p = self.root / f'{slug}.json'
        if p.resolve().parent != self.root.resolve():          # sembolik bağ ile dışarı kaçış
            raise ProgramError(400, 'bad_name', 'Geçersiz program adı')
        return p

    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.glob('*.json')):
            if not SLUG_RE.fullmatch(p.stem):
                continue
            try:
                doc = self.load(p.stem)
                out.append({'slug': p.stem, 'name': doc['name'], 'steps': len(doc['steps']),
                            'waypoints': len(doc['waypoints']), 'modified': p.stat().st_mtime})
            except (ProgramError, OSError):
                out.append({'slug': p.stem, 'name': p.stem, 'corrupt': True})
        return sorted(out, key=lambda d: d['name'].lower())

    def load(self, slug: str) -> Dict[str, Any]:
        p = self.path_for(slug)
        try:
            if p.stat().st_size > MAX_JSON_BYTES:
                raise ProgramError(413, 'too_large', 'Program dosyası çok büyük')
            text = p.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise ProgramError(404, 'not_found', 'Böyle bir program yok') from None
        except (OSError, UnicodeDecodeError):
            raise ProgramError(500, 'read_failed', 'Program dosyası okunamadı') from None
        try:
            return parse_text(text)
        except ProgramError as exc:
            raise ProgramError(422, 'corrupt', 'Program dosyası geçersiz',
                               errors=exc.extra.get('errors', [])) from None

    def save(self, program: Dict[str, Any]) -> str:
        """Doğrulanmış programı yazar; dosya adı (slug) görünen addan türetilir. Aynı ad üzerine yazar."""
        slug = slugify(program['name'])
        path = self.path_for(slug)
        if not path.exists() and len(list(self.root.glob('*.json'))) >= MAX_PROGRAMS:
            raise ProgramError(409, 'too_many', f'en çok {MAX_PROGRAMS} program saklanır')
        data = json.dumps(program, ensure_ascii=False, indent=2).encode('utf-8')
        if len(data) > MAX_JSON_BYTES:
            raise ProgramError(413, 'too_large', 'Program çok büyük')
        try:
            write_bytes_atomic(path, data)
        except OSError:
            raise ProgramError(500, 'write_failed', 'Program yazılamadı') from None
        return slug

    def delete(self, slug: str) -> None:
        p = self.path_for(slug)
        try:
            p.unlink()
        except FileNotFoundError:
            raise ProgramError(404, 'not_found', 'Böyle bir program yok') from None
        except OSError:
            raise ProgramError(500, 'delete_failed', 'Program silinemedi') from None
