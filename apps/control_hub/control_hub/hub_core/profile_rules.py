"""Parametreler sayfasının doğrulama kuralları (P4): SAF işlevler, sınır değerleri testli.

İki katman vardır ve ikisi de burada birleşir:
1. **Bu dosyanın kuralları** (``check_all``): kullanıcıya satırında ve ANINDA söylenen mantık hataları
   (min ≥ max, hız ≤ 0, home aralık/kutu dışında, pot ucu geçersiz bölgede ...). Her kural küçük bir işlevdir.
2. **``robot_core`` doğrulayıcısı** (``profile_view.py`` çağırır): hakem odur; hata metni YAML'daki yeri
   söyler, ``locate`` onu form alanına çevirir. Buradaki kurallar onun yerini ALMAZ, yalnız daha erken ve
   daha yakın söyler (robot_core'un söylemediği "home güvenli kutuda mı", "pot ucu geçersiz bölgede mi" gibi
   denetimler yalnız buradadır).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from control_hub.hub_core.profile_fields import POT_FIELDS, Issue

BOX_FLOOR_MM = 20.0                  # robot_core.profile.BOX_FLOOR (0.02 m): kutu zemine yapışmaz
MIN_PULSE_SPAN_US = 200.0            # robot_core.hardware.MIN_PULSE_SPAN_US: daha dar aralık açıyı çözemez
MAX_SERVO_SPAN_DEG = 360.0           # robot_core.hardware.MAX_SERVO_SPAN (2π rad)
POT_INVALID_LOW = 8                  # ham <= 8 ya da >= 1015: GEÇERSİZ bölge (firmware/POT_TELEMETRI.md)
POT_INVALID_HIGH = 1015
POT_MIN_SPAN = 100                   # bundan dar ham aralık açıyı kabaca çözer: yalnız UYARI
Values = Mapping[str, Optional[float]]


def _v(v: Values, key: str) -> Optional[float]:
    return v.get(key)


def _have(v: Values, *keys: str) -> bool:
    return all(v.get(k) is not None for k in keys)


def check_axis(v: Values, n: str) -> List[Issue]:
    """Bir eksenin satırı: aralık, hız/ivme, home, sürücü tavanları, step ve servo özel alanları."""
    a = f'axes.{n}'
    out: List[Issue] = []
    if _have(v, f'{a}.min_deg', f'{a}.max_deg') and v[f'{a}.min_deg'] >= v[f'{a}.max_deg']:
        out.append(Issue(f'{a}.min_deg', 'en küçük açı, en büyük açıdan küçük olmalı (min ≥ max)'))
    for suffix, text in (('vel_dps', 'hız tavanı 0’dan büyük olmalı'),
                         ('acc_dps2', 'ivme tavanı 0’dan büyük olmalı'),
                         ('drive_vel_dps', 'sürücü hız tavanı 0’dan büyük olmalı'),
                         ('drive_acc_dps2', 'sürücü ivme tavanı 0’dan büyük olmalı'),
                         ('slew_dps', 'servo hız tavanı 0’dan büyük olmalı'),
                         ('gear_ratio', 'dişli oranı 0’dan büyük olmalı')):
        val = _v(v, f'{a}.{suffix}')
        if val is not None and val <= 0:
            out.append(Issue(f'{a}.{suffix}', text))
    if _have(v, f'{a}.home_deg', f'{a}.min_deg', f'{a}.max_deg') and \
            not v[f'{a}.min_deg'] <= v[f'{a}.home_deg'] <= v[f'{a}.max_deg']:
        out.append(Issue(f'{a}.home_deg', 'home açısı [en küçük, en büyük] aralığının dışında'))
    if _have(v, f'{a}.drive_min_deg', f'{a}.drive_max_deg') and \
            v[f'{a}.drive_min_deg'] >= v[f'{a}.drive_max_deg']:
        out.append(Issue(f'{a}.drive_min_deg', 'sürücü en küçük açısı en büyüğünden küçük olmalı'))
    if _have(v, f'{a}.pulse_min_us', f'{a}.pulse_max_us'):
        lo, hi = v[f'{a}.pulse_min_us'], v[f'{a}.pulse_max_us']
        if lo >= hi:
            out.append(Issue(f'{a}.pulse_min_us', 'en küçük darbe, en büyük darbeden küçük olmalı'))
        elif hi - lo < MIN_PULSE_SPAN_US:
            out.append(Issue(f'{a}.pulse_max_us', f'darbe aralığı en az {MIN_PULSE_SPAN_US:g} µs olmalı'))
    if _have(v, f'{a}.angle_at_min_deg', f'{a}.angle_at_max_deg'):
        span = abs(v[f'{a}.angle_at_max_deg'] - v[f'{a}.angle_at_min_deg'])
        if span == 0:
            out.append(Issue(f'{a}.angle_at_max_deg', 'iki uçtaki açı aynı olamaz'))
        elif span > MAX_SERVO_SPAN_DEG:
            out.append(Issue(f'{a}.angle_at_max_deg', f'servo açı aralığı {MAX_SERVO_SPAN_DEG:g}° üstünde'))
    return out


def check_box(v: Values) -> List[Issue]:
    out: List[Issue] = []
    for ax in 'xyz':
        lo, hi = _v(v, f'safety.{ax}_min_mm'), _v(v, f'safety.{ax}_max_mm')
        if lo is not None and hi is not None and lo >= hi:
            out.append(Issue(f'safety.{ax}_min_mm', f'{ax} alt sınırı üst sınırdan küçük olmalı (min ≥ max)'))
    zmin = _v(v, 'safety.z_min_mm')
    if zmin is not None and zmin < BOX_FLOOR_MM:
        out.append(Issue('safety.z_min_mm', f'kutunun alt yüzü zeminden en az {BOX_FLOOR_MM:g} mm yukarıda '
                                            'olmalı'))
    return out


def home_in_box(v: Values, tcp_mm: Sequence[float]) -> Optional[Issue]:
    """Home pozunda alet ucu (``tcp_mm``, ileri kinematikten) güvenli kutunun içinde mi."""
    bad = []
    for ax, x in zip('xyz', tcp_mm):
        lo, hi = _v(v, f'safety.{ax}_min_mm'), _v(v, f'safety.{ax}_max_mm')
        if lo is not None and hi is not None and not lo <= x <= hi:
            bad.append(f'{ax}={x:.1f} mm, kutu [{lo:.1f}, {hi:.1f}]')
    if not bad:
        return None
    return Issue('safety', 'home pozunda alet ucu güvenli kutunun DIŞINDA (' + '; '.join(bad) + ')')


def check_pot(v: Values, names: Sequence[str]) -> List[Issue]:
    out: List[Issue] = []
    used: Dict[int, str] = {}
    for n in names:
        keys = [f'pot.{n}.{f}' for f in POT_FIELDS]
        given = [k for k in keys if v.get(k) is not None]
        if not given:
            continue
        for k in keys:
            if v.get(k) is None:
                out.append(Issue(k, 'kalibrasyon eksik: bu alanı doldur ya da satırdaki hepsini sil'))
        ch = _v(v, f'pot.{n}.channel')
        if ch is not None:
            other = used.setdefault(int(ch), n)
            if other != n:
                out.append(Issue(f'pot.{n}.channel', f'kanal {int(ch)} zaten {other} için kullanılıyor'))
        lo, hi = _v(v, f'pot.{n}.raw_min'), _v(v, f'pot.{n}.raw_max')
        if lo is not None and hi is not None:
            if lo >= hi:
                out.append(Issue(f'pot.{n}.raw_min', 'ham en küçük, ham en büyükten küçük olmalı'))
            if lo <= POT_INVALID_LOW:
                out.append(Issue(f'pot.{n}.raw_min', f'ham {POT_INVALID_LOW} ve altı GEÇERSİZ bölge'))
            if hi >= POT_INVALID_HIGH:
                out.append(Issue(f'pot.{n}.raw_max', f'ham {POT_INVALID_HIGH} ve üstü GEÇERSİZ bölge'))
            if lo < hi and hi - lo < POT_MIN_SPAN:
                out.append(Issue(f'pot.{n}.raw_max',
                                 f'ham aralık {POT_MIN_SPAN} sayıdan dar: açı kaba çözülür', 'warning'))
        a0, a1 = _v(v, f'pot.{n}.angle_at_min_deg'), _v(v, f'pot.{n}.angle_at_max_deg')
        if a0 is not None and a1 is not None and a0 == a1:
            out.append(Issue(f'pot.{n}.angle_at_max_deg', 'iki uçtaki açı aynı olamaz'))
    return out


def check_all(v: Values, joints: Sequence[str], pot_names: Sequence[str]) -> List[Issue]:
    out: List[Issue] = []
    for n in joints:
        out += check_axis(v, n)
    return out + check_box(v) + check_pot(v, pot_names)


def pot_angle(raw: float, raw_min: float, raw_max: float, angle_min: float, angle_max: float) -> float:
    """Ham ADC -> derece (doğrusal; kalibrasyon ucunun dışı kırpılmaz, kullanıcıya olduğu gibi gösterilir)."""
    return angle_min + (raw - raw_min) / (raw_max - raw_min) * (angle_max - angle_min)


# --- robot_core doğrulayıcısının hata konumunu forma çevir ------------------------------------------------
_LOCATORS: Tuple[Tuple[re.Pattern, Any], ...] = tuple((re.compile(p), f) for p, f in (
    (r'^joints\.(\w+)\.limits\.position', lambda m: [f'axes.{m[1]}.min_deg', f'axes.{m[1]}.max_deg']),
    (r'^joints\.(\w+)\.limits\.velocity', lambda m: [f'axes.{m[1]}.vel_dps']),
    (r'^joints\.(\w+)\.limits\.acceleration', lambda m: [f'axes.{m[1]}.acc_dps2']),
    (r'^joints\.(\w+)\.offset\[(\d)\]', lambda m: [f'kin.{m[1]}.offset_{"xyz"[int(m[2]) % 3]}_mm']),
    (r'^links\.(\w+)\.length', lambda m: [f'kin.{m[1]}.length_mm']),
    (r'^moveit\.initial_positions\.(\w+)', lambda m: [f'axes.{m[1]}.home_deg']),
    (r'^moveit\.named_poses\.home\.(\w+)', lambda m: [f'axes.{m[1]}.home_deg']),
    (r'^safety\.box\.([xyz])', lambda m: [f'safety.{m[1]}_min_mm', f'safety.{m[1]}_max_mm']),
    (r'^safety', lambda m: ['safety']),
    (r'^tcp\.offset\[(\d)\]', lambda m: [f'tcp.{"xyz"[int(m[1]) % 3]}_mm']),
    (r'^tcp\.wpr\[(\d)\]', lambda m: [f'tcp.{"wpr"[int(m[1]) % 3]}_deg']),
    (r'^tcp', lambda m: ['tcp']),
    (r'^axes\.(\w+)\.limits\.position',
     lambda m: [f'axes.{m[1]}.drive_min_deg', f'axes.{m[1]}.drive_max_deg']),
    (r'^axes\.(\w+)\.limits\.velocity', lambda m: [f'axes.{m[1]}.drive_vel_dps']),
    (r'^axes\.(\w+)\.limits\.acceleration', lambda m: [f'axes.{m[1]}.drive_acc_dps2']),
    (r'^axes\.(\w+)\.motor\.steps_per_rev', lambda m: [f'axes.{m[1]}.steps_per_rev']),
    (r'^axes\.(\w+)\.driver\.microstep', lambda m: [f'axes.{m[1]}.microstep']),
    (r'^axes\.(\w+)\.gear_ratio', lambda m: [f'axes.{m[1]}.gear_ratio']),
    (r'^axes\.(\w+)\.direction', lambda m: [f'axes.{m[1]}.direction']),
    (r'^axes\.(\w+)\.(homing|reference|servo\.rest)', lambda m: [f'axes.{m[1]}.home_deg']),
    (r'^axes\.(\w+)\.servo\.pulse_range',
     lambda m: [f'axes.{m[1]}.pulse_min_us', f'axes.{m[1]}.pulse_max_us']),
    (r'^axes\.(\w+)\.servo\.slew_limit', lambda m: [f'axes.{m[1]}.slew_dps']),
    (r'^axes\.(\w+)', lambda m: [f'axes.{m[1]}']),
))


def locate(message: str, known: Optional[Mapping[str, Any]] = None) -> Tuple[List[str], str]:
    """``'joints.joint_2.limits.position: alt sınır ...'`` -> (alan anahtarları, ``alt sınır ...``).
    Konum tanınmazsa (``['_general']``, tüm metin). ``known``: yalnız bu anahtarlar kalır (yoksa genel)."""
    where, sep, rest = message.partition(': ')
    text = rest if sep else message
    inner = re.match(r'box\.([xyz]): (.*)', text) if where == 'safety' else None
    if inner:                                   # robot_core: "safety: box.x: ..." (SafeZone hatası sarılı)
        where, text = f'safety.box.{inner[1]}', inner[2]
    for pattern, build in _LOCATORS:
        m = pattern.match(where)
        if m:
            keys = [k for k in build(m) if known is None or k in known or k.count('.') < 2]
            if keys:
                return keys, text
    return ['_general'], message
