"""Parametreler sayfasının sunucu çekirdeği (P4): kaynağı oku -> kopyada düzenle -> doğrula -> diff -> taslak.

* Depo profil dosyaları YALNIZ OKUNUR (K2). Taslak ``HUB_DATA_DIR/profiles/<robot>/draft.yaml``'a ATOMİK
  yazılır (aynı klasörde geçici dosya + ``os.replace``); yolu depo profil klasörünün altına düşerse yazılmaz.
* Robota/Robot API'ye UYGULAMA yok. KARAR GEREKLİ: taslağı robota kim, nasıl uygular — Robot_1 karar verir.
* Hakem ``robot_core`` doğrulayıcısıdır (``parse_profile``/``parse_hardware``); ``profile_rules`` onun
  bilmediği sağduyuyu (min≥max, hız≤0, home kutu dışı, pot) ekler. Hata, alanın üstüne konumlanır.
* ``yaml``/``robot_core``/``numpy`` TEMBEL içe aktarılır: yoksa (Docker ``hub-fake``) sayfa 503 ile
  nedenini söyler, Hub'ın geri kalanı etkilenmez.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from control_hub import paths
from control_hub.hub_core import profile_rules as rules
from control_hub.hub_core import sim_assets
from control_hub.hub_core.profile_fields import (
    SERVO,
    STEP,
    Docs,
    FieldSpec,
    Issue,
    apply_edits,
    build_catalog,
    current_values,
    steps_per_degree,
)

DRAFT_NAME = 'draft.yaml'
DRAFT_VERSION = 1
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_EDITS = 400
TAGS = ('measured', 'estimated', 'placeholder')          # robot_core.maturity.TAGS (testte eşitlik)
DEFAULT_TAG = 'placeholder'
TAG_LABELS = {'measured': 'ölçülü', 'estimated': 'tahmini', 'placeholder': 'yer tutucu', None: 'bilinmiyor'}
DRAFT_HEADER = ('# TASLAK: robota UYGULANMADI (uygulamayı Robot_1 kararlaştırır). Aşağıdaki değerler\n'
                '# ölçülmemiş sayılır (tahmini). Depodaki profil dosyaları bu taslakla değişmez.\n')


class ViewError(Exception):
    """İstemciye gösterilebilir hata; iletide dosya yolu YOKTUR."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


@dataclass
class Source:
    robot: str
    eng: Any
    docs: Docs
    profile: Any
    hardware: Any
    quality: Optional[Dict[str, Optional[str]]]          # None: kalite dosyası yok (.exe): rozet yok
    catalog: Dict[str, FieldSpec]
    hashes: Dict[str, Optional[str]]
    joints: List[str]
    pot_names: List[str]
    notes: List[str] = field(default_factory=list)


@dataclass
class Validation:
    docs: Docs
    issues: List[Issue]
    changes: List[Dict[str, Any]]
    derived: Dict[str, Any]
    home_tcp_mm: Optional[List[float]]

    @property
    def ok(self) -> bool:
        return not any(i.severity == 'error' for i in self.issues)

    def payload(self) -> Dict[str, Any]:
        return {'ok': self.ok, 'issues': [i.as_dict() for i in self.issues], 'changes': self.changes,
                'derived': self.derived, 'home_tcp_mm': self.home_tcp_mm}


# --- kaynak ----------------------------------------------------------------------------------------------
def _engine() -> Any:
    try:
        paths.ensure_robot_core_path()
        import yaml
        from robot_core import kinematics
        from robot_core.hardware import parse_hardware
        from robot_core.profile import parse_profile
    except ImportError:
        raise ViewError(503, 'engine_unavailable',
                        'Bu kurulumda profil motoru (robot_core / numpy) yok; sayfa gösterilemez') from None
    return types.SimpleNamespace(yaml=yaml, Chain=kinematics.Chain, parse_hardware=parse_hardware,
                                 parse_profile=parse_profile)


def _read(eng: Any, path: Path) -> tuple:
    """``(YAML belgesi, sha256)``; dosya yoksa ``(None, None)``. Hata iletisi yol taşımaz."""
    if not path.is_file():
        return None, None
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ViewError(500, 'source_too_big', f'{path.name} beklenenden büyük')
        return eng.yaml.safe_load(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError, eng.yaml.YAMLError):
        raise ViewError(500, 'source_unreadable', f'{path.name} okunamadı ya da geçerli YAML değil') from None


def _quality_tags(doc: Any) -> Dict[str, Optional[str]]:
    """``quality.profile.yaml`` -> {alan yolu: etiket} (``robot_core.maturity`` .exe'de yok)."""
    out: Dict[str, Optional[str]] = {}
    fields = doc.get('fields') if isinstance(doc, Mapping) else None
    for path, entry in (fields.items() if isinstance(fields, Mapping) else ()):
        tag = entry.get('tag') if isinstance(entry, Mapping) else entry
        out[str(path)] = tag if tag in TAGS else None
    return out


def load_source(robot: Optional[str] = None) -> Source:
    eng = _engine()
    try:
        name, profile_path, _ = sim_assets.resolve(robot)
    except sim_assets.SimAssetsError as exc:
        raise ViewError(404, 'robot_not_found', str(exc)) from None
    prof_doc, prof_sha = _read(eng, profile_path)
    hw_doc, hw_sha = _read(eng, profile_path.with_name('hardware.profile.yaml'))
    q_doc, q_sha = _read(eng, profile_path.with_name('quality.profile.yaml'))
    notes: List[str] = []
    try:
        profile = eng.parse_profile(prof_doc)
    except ValueError as exc:
        raise ViewError(500, 'profile_invalid', f'robot profili geçersiz: {exc}') from None
    hardware = None
    if hw_doc is None:
        notes.append('Donanım profili bu kurulumda yok: eksen sürücü alanları gösterilmiyor.')
    else:
        try:
            hardware = eng.parse_hardware(hw_doc, profile)
        except ValueError as exc:
            hw_doc = None
            notes.append(f'Donanım profili geçersiz, sürücü alanları gösterilmiyor: {exc}')
    if q_doc is None:
        notes.append('Kalite dosyası yok: ölçülü/tahmini rozetleri gösterilmiyor.')
    docs = Docs(profile=prof_doc, hardware=hw_doc, pot={})
    tools = list(hardware.tool_actuator_names) if hardware is not None else []
    return Source(robot=name, eng=eng, docs=docs, profile=profile, hardware=hardware,
                  quality=None if q_doc is None else _quality_tags(q_doc),
                  catalog=build_catalog(docs, profile, tools),
                  hashes={'profile': prof_sha, 'hardware': hw_sha, 'quality': q_sha},
                  joints=list(profile.joint_names), pot_names=list(profile.joint_names) + tools, notes=notes)


# --- doğrulama hattı -------------------------------------------------------------------------------------
def _derived(src: Source, v: Mapping[str, Optional[float]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for n in src.joints:
        a = f'axes.{n}'
        spr, ms, gear = v.get(f'{a}.steps_per_rev'), v.get(f'{a}.microstep'), v.get(f'{a}.gear_ratio')
        if None not in (spr, ms, gear):
            out[n] = {'steps_per_deg': round(steps_per_degree(spr, ms, gear), 4)}
        lo, hi = v.get(f'{a}.pulse_min_us'), v.get(f'{a}.pulse_max_us')
        a0, a1 = v.get(f'{a}.angle_at_min_deg'), v.get(f'{a}.angle_at_max_deg')
        if None not in (lo, hi, a0, a1) and a0 != a1:
            slope = (hi - lo) / (a1 - a0)
            out[n] = {'us_per_deg': round(slope, 4), 'servo_dir': 1 if slope > 0 else -1}
    return out


def _home_tcp_mm(src: Source, profile: Any) -> List[float]:
    chain = src.eng.Chain(profile)
    pos = chain.fk([profile.initial_positions[j.name] for j in profile.joints])[:3, 3]
    return [round(float(x) * 1000.0, 3) for x in pos]


def validate(src: Source, edits: Mapping[str, Any]) -> Validation:
    """``edits`` ({alan: değer}, arayüz birimi) KOPYAYA uygulanır ve denetlenir; hiçbir şey yazılmaz."""
    if len(edits) > MAX_EDITS:
        raise ViewError(413, 'too_many_edits', f'en çok {MAX_EDITS} alan değiştirilebilir')
    new, issues, changes = apply_edits(src.docs, src.catalog, edits, src.joints)
    values = current_values(new, src.catalog)
    issues = list(issues) + rules.check_all(values, src.joints, src.pot_names)
    tcp: Optional[List[float]] = None
    try:
        prof = src.eng.parse_profile(new.profile)
        if new.hardware is not None:
            src.eng.parse_hardware(new.hardware, prof)
        tcp = _home_tcp_mm(src, prof)
    except (ValueError, ArithmeticError) as exc:
        taken = {i.field for i in issues if i.severity == 'error'}
        keys, text = rules.locate(str(exc), src.catalog)
        if not taken.intersection(keys):                # kurallar aynı alanı zaten açıkladıysa tekrarlama
            issues += [Issue(k, text) for k in keys]
        elif not taken:                                 # (olamaz; yine de ok=False kalsın)
            issues.append(Issue(keys[0], text))
    if tcp is not None:
        bad = rules.home_in_box(values, tcp)
        if bad is not None:
            issues.append(bad)
    for c in changes:
        c['quality'] = 'estimated'                              # taslak değeri ölçülmemiştir
    return Validation(new, issues, changes, _derived(src, values), tcp)


# --- görünüm modeli --------------------------------------------------------------------------------------
def _badge(src: Source, spec: FieldSpec) -> Optional[Dict[str, Any]]:
    if src.quality is None or spec.quality is None:
        return None
    tag = src.quality.get(spec.quality, DEFAULT_TAG)
    return {'tag': tag, 'label': TAG_LABELS.get(tag, TAG_LABELS[None])}


def view_model(src: Source) -> Dict[str, Any]:
    values = current_values(src.docs, src.catalog)
    base = validate(src, {})
    hw_axes = (src.docs.hardware or {}).get('axes') or {}
    fields = [{'key': k, 'label': s.label, 'unit': s.unit, 'kind': s.kind, 'group': s.group, 'row': s.row,
               'decimals': s.decimals, 'lo': s.lo, 'hi': s.hi, 'value': values[k], 'quality': _badge(src, s)}
              for k, s in src.catalog.items()]
    axes = [{'joint': n, 'motor': ((hw_axes.get(n) or {}).get('motor') or {}).get('type'),
             'keys': [k for k, s in src.catalog.items() if s.group == 'axes' and s.row == n]}
            for n in src.joints]
    return {'ok': True, 'robot': src.robot, 'joints': src.joints, 'pot_names': src.pot_names, 'axes': axes,
            'step': STEP, 'servo': SERVO, 'fields': fields, 'notes': src.notes, 'hashes': src.hashes,
            'has_hardware': src.hardware is not None, 'has_quality': src.quality is not None,
            'active_tool': src.docs.profile.get('active_tool'), 'derived': base.derived,
            'home_tcp_mm': base.home_tcp_mm, 'issues': [i.as_dict() for i in base.issues],
            'tag_labels': {k: v for k, v in TAG_LABELS.items() if k}}


# --- taslak (HUB_DATA_DIR) -------------------------------------------------------------------------------
def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def draft_path(robot: str) -> Path:
    """Taslağın yolu; klasör OLUŞTURMAZ (depo içi koruması önce çalışır, klasörü ``atomic_write`` açar)."""
    if not sim_assets.ROBOT_RE.match(robot):
        raise ViewError(400, 'bad_robot', 'geçersiz robot adı')
    try:
        base = paths.data_subdir('profiles', create=False)
    except ValueError:
        raise ViewError(500, 'data_dir_unusable', 'veri klasörü (HUB_DATA_DIR) kullanılamıyor') from None
    target = base / robot / DRAFT_NAME
    if any(_under(target, root) for root in sim_assets.profile_roots()):
        raise ViewError(500, 'data_dir_in_repo', 'taslak klasörü depo profil klasörünün içinde olamaz')
    return target


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.draft-', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def render_draft(src: Source, val: Validation, saved_at: Optional[str] = None) -> str:
    changed = {c['field']: c['after'] for c in val.changes}
    doc: Dict[str, Any] = {'draft_version': DRAFT_VERSION, 'robot': src.robot, 'uygulandi': False,
                           'saved_at': saved_at or _now(), 'base': dict(src.hashes), 'edits': changed,
                           'tahmini': sorted(changed), 'profile': val.docs.profile}
    if val.docs.hardware is not None:
        doc['hardware'] = val.docs.hardware
    if val.docs.pot:
        doc['pot'] = val.docs.pot
    return DRAFT_HEADER + src.eng.yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _checked(src: Source, edits: Mapping[str, Any]) -> Validation:
    val = validate(src, edits)
    if not val.changes and val.ok:
        raise ViewError(400, 'no_changes', 'kaydedilecek değişiklik yok')
    if not val.ok:
        raise ViewError(422, 'invalid', 'doğrulama hatası: düzeltmeden kaydedilmez', **val.payload())
    return val


def yaml_text(src: Source, edits: Mapping[str, Any]) -> str:
    return render_draft(src, _checked(src, edits))


def save_draft(src: Source, edits: Mapping[str, Any]) -> Dict[str, Any]:
    val = _checked(src, edits)
    target = draft_path(src.robot)
    saved_at = _now()
    try:
        atomic_write(target, render_draft(src, val, saved_at))
    except OSError:
        raise ViewError(500, 'draft_write_failed', 'taslak yazılamadı (önceki taslak korundu)') from None
    return {'ok': True, 'saved_at': saved_at, 'changes': len(val.changes), 'applied': False}


def read_draft(src: Source) -> Dict[str, Any]:
    target = draft_path(src.robot)
    if not target.is_file():
        return {'exists': False, 'edits': {}}
    try:
        doc = _read(src.eng, target)[0]
    except ViewError:
        return {'exists': True, 'edits': {}, 'error': 'taslak okunamadı (bozuk dosya)'}
    doc = doc if isinstance(doc, Mapping) else {}
    edits = doc.get('edits') if isinstance(doc.get('edits'), Mapping) else {}
    base = doc.get('base') if isinstance(doc.get('base'), Mapping) else {}
    return {'exists': True, 'edits': dict(edits), 'saved_at': doc.get('saved_at'),
            'stale': any(base.get(k) != v for k, v in src.hashes.items())}


def delete_draft(src: Source) -> Dict[str, Any]:
    target = draft_path(src.robot)
    try:
        target.unlink()
        return {'ok': True, 'deleted': True}
    except FileNotFoundError:
        return {'ok': True, 'deleted': False}
    except OSError:
        raise ViewError(500, 'draft_delete_failed', 'taslak silinemedi') from None
