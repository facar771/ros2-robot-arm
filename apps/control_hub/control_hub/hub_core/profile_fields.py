"""Parametreler sayfasının alan kataloğu: form alanı <-> profil/donanım YAML'ı eşlemesi (P4).

SAF modül: dosya, ağ ve Hub yok; yalnız sözlükler (testlenebilir). Profil dosyaları burada okunmaz ve
ASLA yazılmaz: bellekteki KOPYALAR değişir (``Docs.clone``), kayıt ``profile_view.py``'nin taslak yoludur.

Her alanın bir **anahtarı** vardır (``axes.joint_2.min_deg``); arayüz yalnız DEĞİŞTİRDİĞİ alanları
gönderir (``{anahtar: değer}``, arayüz birimiyle: derece, mm, °/s). Burası değeri ham birime (rad, m)
çevirip kopyaya yazar. Dokunulmayan alan bayt bayt aynı kalır; kullanıcı gösterilen yuvarlak değeri
aynen yazarsa da değişiklik sayılmaz (``same_value``).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

RAD_TO_DEG = 180.0 / math.pi
M_TO_MM = 1000.0
RAW_DECIMALS = 9                       # ham değerin yuvarlanışı (kayan nokta gürültüsü yazılmaz)
SERVO = 'hobby_servo'
STEP = 'step'
KIND_FLOAT, KIND_INT, KIND_SIGN = 'float', 'int', 'sign'
Path = Tuple[Union[str, int], ...]
_MISSING = object()

# Birim sağduyusu sınırları (arayüz biriminde, sınırlar DAHİL). robot_core.profile'ın MAX_* sabitleriyle
# aynı kaynaktan: test eşitliği denetler (profile_rules_check), biri değişirse test kırılır.
MAX_ANGLE_DEG = 720.0                  # 4π rad: fazlası "derece yazılmış" demektir
MAX_SPEED_DPS = 50.0 * RAD_TO_DEG      # MAX_SPEED = 50 rad/s
MAX_ACCEL_DPS2 = 1000.0 * RAD_TO_DEG   # MAX_ACCEL = 1000 rad/s²
MAX_LENGTH_MM = 5000.0                 # MAX_LENGTH = 5 m
MAX_TCP_MM = 1000.0                    # MAX_TCP_OFFSET = 1 m
PULSE_US_MIN, PULSE_US_MAX = 300.0, 3000.0           # hardware.PULSE_US_BOUNDS
MAX_SERVO_SPAN_DEG = 360.0
ADC_MAX = 1023
POT_CHANNEL_MAX = 255                  # çerçevede `ch u8` (firmware/POT_TELEMETRI.md)
POT_FIELDS = ('channel', 'raw_min', 'raw_max', 'angle_at_min_deg', 'angle_at_max_deg')


@dataclass(frozen=True)
class Issue:
    """Form hatası/uyarısı. ``field``: alan anahtarı ya da satır/grup (``axes.joint_2``, ``safety``)."""
    field: str
    message: str
    severity: str = 'error'            # 'error' kaydı engeller; 'warning' yalnız uyarır

    def as_dict(self) -> Dict[str, str]:
        return {'field': self.field, 'severity': self.severity, 'message': self.message}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    unit: str                          # arayüz birimi: '°', 'mm', '°/s', 'µs', ''
    kind: str                          # float | int | sign
    doc: str                           # 'profile' | 'hardware' | 'pot'
    path: Path                         # ham belgedeki yer
    group: str                         # axes | kin | safety | tcp | pot
    scale: float = 1.0                 # arayüz = ham × scale
    decimals: int = 3
    default: Any = None                # ham birim; belgede yoksa bu kullanılır
    quality: Optional[str] = None      # robot_core.maturity alan yolu (rozet)
    lo: Optional[float] = None         # birim sağduyusu (arayüz birimi)
    hi: Optional[float] = None
    seed_path: Optional[Path] = None   # belgede yoksa önce BU kap oluşturulur ...
    seed: Any = None                   # ... bu değerle (örn. offset [0,0,0], kutu {x,y,z})
    row: str = ''                      # satır adı (eksen/link) — yerleşim için


@dataclass
class Docs:
    """Düzenlenebilir ham belgeler (YAML'dan okunmuş sözlükler). ``pot``: taslağa özgü kalibrasyon."""
    profile: Dict[str, Any]
    hardware: Optional[Dict[str, Any]] = None
    pot: Dict[str, Any] = field(default_factory=dict)

    def clone(self) -> 'Docs':
        return Docs(copy.deepcopy(self.profile), copy.deepcopy(self.hardware), copy.deepcopy(self.pot))

    def doc(self, name: str) -> Optional[Dict[str, Any]]:
        return {'profile': self.profile, 'hardware': self.hardware, 'pot': self.pot}[name]


# --- ham belgede gezinme ------------------------------------------------------------------------------
def _get(doc: Any, path: Path, default: Any = None) -> Any:
    cur = doc
    for p in path:
        if isinstance(p, int):
            if not isinstance(cur, (list, tuple)) or not -len(cur) <= p < len(cur):
                return default
            cur = cur[p]
        else:
            if not isinstance(cur, Mapping) or p not in cur:
                return default
            cur = cur[p]
    return cur


def _put(doc: Dict[str, Any], path: Path, value: Any) -> None:
    cur: Any = doc
    for p, nxt in zip(path[:-1], path[1:]):
        if isinstance(p, int):
            cur = cur[p]
            continue
        if p not in cur or cur[p] is None:
            cur[p] = {} if isinstance(nxt, str) else []
        cur = cur[p]
    last = path[-1]
    if isinstance(last, int):
        cur[last] = value
    else:
        cur[last] = value


def _drop(doc: Dict[str, Any], path: Path) -> None:
    """Alanı siler; boşalan üst sözlükleri de temizler (``pot.joint_1`` hiç alan kalmadıysa kalkar)."""
    parents: List[Tuple[Any, Any]] = []
    cur: Any = doc
    for p in path[:-1]:
        if not isinstance(cur, Mapping) or p not in cur:
            return
        parents.append((cur, p))
        cur = cur[p]
    if isinstance(cur, dict):
        cur.pop(path[-1], None)
    for parent, key in reversed(parents):
        if isinstance(parent[key], dict) and not parent[key]:
            del parent[key]
        else:
            break


def to_ui(spec: FieldSpec, raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        return None
    value = raw * spec.scale
    return int(round(value)) if spec.kind != KIND_FLOAT else value


def to_raw(spec: FieldSpec, ui: float) -> Union[int, float]:
    if spec.kind != KIND_FLOAT:
        return int(round(ui))
    return round(ui / spec.scale, RAW_DECIMALS)


def read_value(spec: FieldSpec, docs: Docs) -> Optional[float]:
    """Alanın bugünkü değeri (arayüz birimi); belgede yoksa ``default``; hiçbiri yoksa ``None``."""
    doc = docs.doc(spec.doc)
    raw = _get(doc, spec.path, _MISSING) if doc is not None else _MISSING
    if raw is _MISSING:
        raw = spec.default
    return to_ui(spec, raw)


def same_value(spec: FieldSpec, a: Optional[float], b: Optional[float]) -> bool:
    """Gösterilen duyarlılık içinde eşit mi (gösterilen yuvarlak değeri yazmak değişiklik değildir)."""
    if a is None or b is None:
        return a is b
    if spec.kind != KIND_FLOAT:
        return int(a) == int(b)
    return abs(a - b) < 0.5 * 10 ** -spec.decimals


def steps_per_degree(steps_per_rev: float, microstep: float, gear_ratio: float) -> float:
    """Eklem derecesi başına mikroadım: ``steps_per_rev × dişli × mikroadım / 360`` (UI_EMIRLER P4-1)."""
    return steps_per_rev * microstep * gear_ratio / 360.0


# --- katalog -------------------------------------------------------------------------------------------
def _spec(key: str, label: str, unit: str, doc: str, path: Path, group: str, **kw: Any) -> FieldSpec:
    kw.setdefault('kind', KIND_FLOAT)
    return FieldSpec(key=key, label=label, unit=unit, doc=doc, path=path, group=group, **kw)


def _joint_specs(n: str, j: Any, home: float, hw: Optional[Mapping[str, Any]]) -> List[FieldSpec]:
    a = f'axes.{n}'
    motor_type = ((hw or {}).get('motor') or {}).get('type')
    home_q = {STEP: f'axes.{n}.homing.position', SERVO: f'axes.{n}.reference.pose'}.get(motor_type)
    angle = dict(scale=RAD_TO_DEG, lo=-MAX_ANGLE_DEG, hi=MAX_ANGLE_DEG, row=n)
    out = [
        _spec(f'{a}.min_deg', f'{n} · en küçük açı', '°', 'profile', ('joints', n, 'limits', 'position', 0),
              'axes', default=j.lower, quality=f'joints.{n}.limits.position', **angle),
        _spec(f'{a}.max_deg', f'{n} · en büyük açı', '°', 'profile', ('joints', n, 'limits', 'position', 1),
              'axes', default=j.upper, quality=f'joints.{n}.limits.position', **angle),
        _spec(f'{a}.vel_dps', f'{n} · hız tavanı', '°/s', 'profile', ('joints', n, 'limits', 'velocity'),
              'axes', scale=RAD_TO_DEG, default=j.velocity, hi=MAX_SPEED_DPS, row=n),
        _spec(f'{a}.acc_dps2', f'{n} · ivme tavanı', '°/s²', 'profile',
              ('joints', n, 'limits', 'acceleration'), 'axes', scale=RAD_TO_DEG, default=j.acceleration,
              hi=MAX_ACCEL_DPS2, row=n),
        _spec(f'{a}.home_deg', f'{n} · home', '°', 'profile', ('moveit', 'initial_positions', n), 'axes',
              default=home, seed_path=('moveit', 'initial_positions'), quality=home_q, **angle),
    ]
    if hw is None:
        return out
    drive = dict(scale=RAD_TO_DEG, lo=-MAX_ANGLE_DEG, hi=MAX_ANGLE_DEG, row=n)
    out += [
        _spec(f'{a}.drive_min_deg', f'{n} · sürücü en küçük açı', '°', 'hardware',
              ('axes', n, 'limits', 'position', 0), 'axes', quality=f'axes.{n}.limits.position', **drive),
        _spec(f'{a}.drive_max_deg', f'{n} · sürücü en büyük açı', '°', 'hardware',
              ('axes', n, 'limits', 'position', 1), 'axes', quality=f'axes.{n}.limits.position', **drive)]
    if motor_type == STEP:
        out += [
            _spec(f'{a}.direction', f'{n} · yön', '', 'hardware', ('axes', n, 'direction'), 'axes',
                  kind=KIND_SIGN, row=n),
            _spec(f'{a}.steps_per_rev', f'{n} · motor adım/tur', '', 'hardware',
                  ('axes', n, 'motor', 'steps_per_rev'), 'axes', kind=KIND_INT, lo=1, hi=100000, row=n,
                  quality=f'axes.{n}.motor.steps_per_rev'),
            _spec(f'{a}.microstep', f'{n} · mikroadım', '', 'hardware', ('axes', n, 'driver', 'microstep'),
                  'axes', kind=KIND_INT, lo=1, hi=256, row=n, quality=f'axes.{n}.driver.microstep'),
            _spec(f'{a}.gear_ratio', f'{n} · dişli oranı', '', 'hardware', ('axes', n, 'gear_ratio'), 'axes',
                  decimals=4, lo=0.0, hi=10000.0, row=n, quality=f'axes.{n}.gear_ratio'),
            _spec(f'{a}.drive_vel_dps', f'{n} · sürücü hız tavanı', '°/s', 'hardware',
                  ('axes', n, 'limits', 'velocity'), 'axes', scale=RAD_TO_DEG, hi=MAX_SPEED_DPS, row=n),
            _spec(f'{a}.drive_acc_dps2', f'{n} · sürücü ivme tavanı', '°/s²', 'hardware',
                  ('axes', n, 'limits', 'acceleration'), 'axes', scale=RAD_TO_DEG, hi=MAX_ACCEL_DPS2, row=n)]
    elif motor_type == SERVO:
        pr = ('axes', n, 'servo', 'pulse_range')
        q = f'axes.{n}.servo.pulse_range'
        span = dict(scale=RAD_TO_DEG, lo=-MAX_ANGLE_DEG, hi=MAX_ANGLE_DEG, row=n, quality=q)
        out += [
            _spec(f'{a}.pulse_min_us', f'{n} · darbe en küçük', 'µs', 'hardware', pr + ('min_us',), 'axes',
                  decimals=0, lo=PULSE_US_MIN, hi=PULSE_US_MAX, row=n, quality=q),
            _spec(f'{a}.pulse_max_us', f'{n} · darbe en büyük', 'µs', 'hardware', pr + ('max_us',), 'axes',
                  decimals=0, lo=PULSE_US_MIN, hi=PULSE_US_MAX, row=n, quality=q),
            _spec(f'{a}.angle_at_min_deg', f'{n} · en küçük darbedeki açı', '°', 'hardware',
                  pr + ('angle_at_min_us',), 'axes', **span),
            _spec(f'{a}.angle_at_max_deg', f'{n} · en büyük darbedeki açı', '°', 'hardware',
                  pr + ('angle_at_max_us',), 'axes', **span),
            _spec(f'{a}.slew_dps', f'{n} · servo hız tavanı', '°/s', 'hardware',
                  ('axes', n, 'servo', 'slew_limit'), 'axes', scale=RAD_TO_DEG, hi=MAX_SPEED_DPS, row=n,
                  quality=f'axes.{n}.servo.slew_limit')]
    return out


def build_catalog(docs: Docs, profile: Any, tool_actuators: Sequence[str] = ()) -> Dict[str, FieldSpec]:
    """Düzenlenebilir tüm alanlar, GÖSTERİM SIRASIYLA. ``profile``: ``robot_core.profile.Profile``."""
    cat: Dict[str, FieldSpec] = {}
    hw_axes = (docs.hardware or {}).get('axes')
    hw_axes = hw_axes if isinstance(hw_axes, Mapping) else {}
    for j in profile.joints:
        hw = hw_axes.get(j.name) if docs.hardware is not None else None
        hw = hw if isinstance(hw, Mapping) else None
        for s in _joint_specs(j.name, j, profile.initial_positions[j.name], hw):
            cat[s.key] = s
    for j in profile.joints:
        off = ('joints', j.name, 'offset')
        for i, ax in enumerate('xyz'):
            cat[f'kin.{j.name}.offset_{ax}_mm'] = _spec(
                f'kin.{j.name}.offset_{ax}_mm', f'{j.name} · ofset {ax}', 'mm', 'profile', off + (i,), 'kin',
                scale=M_TO_MM, default=0.0, lo=-MAX_LENGTH_MM, hi=MAX_LENGTH_MM, row=j.name,
                seed_path=off, seed=[0.0, 0.0, 0.0])
    for lk in profile.links:
        cat[f'kin.{lk.name}.length_mm'] = _spec(
            f'kin.{lk.name}.length_mm', f'{lk.name} · uzunluk', 'mm', 'profile', ('links', lk.name, 'length'),
            'kin', scale=M_TO_MM, default=lk.length, lo=0.0, hi=MAX_LENGTH_MM, row=lk.name,
            quality=f'links.{lk.name}.length')
    zone = (profile.base_safety or profile.safety).zone
    box = ('safety', 'box')
    seed = {a: [zone.lower[i], zone.upper[i]] for i, a in enumerate('xyz')}
    for i, ax in enumerate('xyz'):
        for k, side in enumerate(('min', 'max')):
            key = f'safety.{ax}_{side}_mm'
            cat[key] = _spec(key, f'güvenli kutu {ax} {side}', 'mm', 'profile', box + (ax, k), 'safety',
                             scale=M_TO_MM, default=(zone.lower if k == 0 else zone.upper)[i],
                             lo=-MAX_LENGTH_MM, hi=MAX_LENGTH_MM, seed_path=box, seed=seed)
    tool = docs.profile.get('active_tool')
    base = ('tools', tool, 'tcp') if tool else ('tcp',)
    tcp = profile.tools[tool].tcp if tool and tool in profile.tools else profile.base_tcp
    for i, ax in enumerate('xyz'):
        key = f'tcp.{ax}_mm'
        cat[key] = _spec(key, f'alet ofseti {ax}', 'mm', 'profile', base + ('offset', i), 'tcp',
                         scale=M_TO_MM, default=tcp.offset[i], lo=-MAX_TCP_MM, hi=MAX_TCP_MM,
                         seed_path=base + ('offset',), seed=list(tcp.offset))
    for i, ax in enumerate('wpr'):
        cat[f'tcp.{ax}_deg'] = _spec(f'tcp.{ax}_deg', f'alet yönelimi {ax.upper()}', '°', 'profile',
                                     base + ('wpr', i), 'tcp', scale=RAD_TO_DEG, default=tcp.wpr[i],
                                     lo=-MAX_ANGLE_DEG, hi=MAX_ANGLE_DEG, seed_path=base + ('wpr',),
                                     seed=list(tcp.wpr))
    for n in [j.name for j in profile.joints] + list(tool_actuators):
        for f, unit, kind, lo, hi in (('channel', '', KIND_INT, 1, POT_CHANNEL_MAX),
                                      ('raw_min', 'ADC', KIND_INT, 0, ADC_MAX),
                                      ('raw_max', 'ADC', KIND_INT, 0, ADC_MAX),
                                      ('angle_at_min_deg', '°', KIND_FLOAT, -MAX_ANGLE_DEG, MAX_ANGLE_DEG),
                                      ('angle_at_max_deg', '°', KIND_FLOAT, -MAX_ANGLE_DEG, MAX_ANGLE_DEG)):
            cat[f'pot.{n}.{f}'] = _spec(f'pot.{n}.{f}', f'{n} · pot {f}', unit, 'pot', (n, f), 'pot',
                                        kind=kind, lo=lo, hi=hi, row=n)
    return cat


def current_values(docs: Docs, catalog: Mapping[str, FieldSpec]) -> Dict[str, Optional[float]]:
    return {k: read_value(s, docs) for k, s in catalog.items()}


# --- düzenleme uygulama --------------------------------------------------------------------------------
def check_edit(spec: FieldSpec, value: Any) -> Optional[str]:
    """Tek değerin yapısal denetimi (tür, sonluluk, birim sağduyusu). Sorun yoksa ``None``."""
    if value is None:
        return None if spec.doc == 'pot' else 'boş bırakılamaz'
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return 'sonlu bir sayı olmalı'
    if spec.kind == KIND_INT and float(value) != int(value):
        return 'tam sayı olmalı'
    if spec.kind == KIND_SIGN and value not in (-1, 1):
        return '+1 ya da -1 olmalı'
    if spec.lo is not None and value < spec.lo:
        return f'{value:g}, alt sınır {spec.lo:g} {spec.unit} altında (birim yanlış olabilir)'
    if spec.hi is not None and value > spec.hi:
        return f'{value:g}, üst sınır {spec.hi:g} {spec.unit} üstünde (birim yanlış olabilir)'
    return None


def _near(a: Any, b: float) -> bool:
    return isinstance(a, (int, float)) and not isinstance(a, bool) and abs(a - b) < 1e-9


def _sync_home(docs: Docs, joint: str, index: int, old: float, new: float) -> None:
    """Home değişince aynı değere EŞİT olan yan kayıtlar da kayar (hardware.profile.yaml'ın "home = robot
    profilinin home pozu" kuralı). Bilerek farklı olan kayıta dokunulmaz."""
    poses = (docs.profile.get('moveit') or {}).get('named_poses') or {}
    home = poses.get('home')
    if isinstance(home, list) and index < len(home) and _near(home[index], old):
        home[index] = new
    ax = ((docs.hardware or {}).get('axes') or {}).get(joint)
    if not isinstance(ax, dict):
        return
    for path in (('homing', 'position'), ('servo', 'rest', 'angle'), ('reference', 'pose')):
        if _near(_get(ax, path), old):
            _put(ax, path, new)


def apply_edits(docs: Docs, catalog: Mapping[str, FieldSpec], edits: Mapping[str, Any],
                joint_order: Sequence[str] = ()) -> Tuple[Docs, List[Issue], List[Dict[str, Any]]]:
    """``edits``'i KOPYAYA uygular. Döner: (yeni belgeler, yapısal hatalar, değişiklik listesi).
    Hatalı alan yazılmaz; değeri değişmeyen alan değişiklik sayılmaz."""
    new = docs.clone()
    issues: List[Issue] = []
    changes: List[Dict[str, Any]] = []
    for key, value in edits.items():
        spec = catalog.get(key)
        if spec is None:
            issues.append(Issue(str(key)[:80], 'bilinmeyen alan'))
            continue
        bad = check_edit(spec, value)
        if bad:
            issues.append(Issue(key, bad))
            continue
        before = read_value(spec, docs)
        if same_value(spec, before, value):
            continue
        doc = new.doc(spec.doc)
        if doc is None:
            issues.append(Issue(key, 'bu robotta donanım profili yok'))
            continue
        if value is None:
            _drop(doc, spec.path)
        else:
            if spec.seed_path is not None and _get(doc, spec.seed_path, _MISSING) is _MISSING:
                seed = spec.seed if spec.seed is not None else _seed_home(catalog)
                _put(doc, spec.seed_path, copy.deepcopy(seed))
            old_raw = _get(doc, spec.path, spec.default)
            _put(doc, spec.path, to_raw(spec, value))
            if key.endswith('.home_deg'):
                joint = spec.row
                idx = list(joint_order).index(joint) if joint in joint_order else -1
                if idx >= 0 and isinstance(old_raw, (int, float)):
                    _sync_home(new, joint, idx, float(old_raw), to_raw(spec, value))
        changes.append({'field': key, 'label': spec.label, 'unit': spec.unit, 'group': spec.group,
                        'before': before, 'after': value})
    return new, issues, changes


def _seed_home(catalog: Mapping[str, FieldSpec]) -> Dict[str, float]:
    """``moveit.initial_positions`` belgede yoksa: bütün eklemlerin bugünkü home'u (ham, rad)."""
    return {s.row: s.default for k, s in catalog.items() if k.endswith('.home_deg')}
