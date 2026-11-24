"""Parça deposu (S2): ``HUB_DATA_DIR/parts/<uuid>/{mesh.stl, meta.json}`` (UI_EMIRLER EK-1 D, K6).

* Hub kullanıcı verisi REPO dosyalarına yazılmaz; yalnız ``HUB_DATA_DIR`` altına. Kullanıcı dosya adı ASLA yol
  olmaz: dizin adı ``uuid4().hex`` (``ID_RE`` ile doğrulanır), görünen ad yalnız ``meta.json``'dadır.
* Yazım atomik: parça önce ``.new-<uuid>`` dizininde kurulur, sonra TEK ``os.replace`` ile yerine gelir;
  ``meta.json`` güncellemesi geçici dosya + ``os.replace``. Yarım parça asla görünmez.
* En çok ``MAX_PARTS`` parça. Bozuk bir parça listeyi bozmaz: atlanır, ``problems``'a yazılır.
* Mesh KANONİK ikili STL'dir (``mesh_tools.import_mesh`` çıktısı), ham birimde; birim ``meta.unit``'te.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from control_hub import paths
from control_hub.hub_core.mesh_tools import (
    ImportedMesh,
    MeshToolError,
    clean_display_name,
    size_warning,
    unit_factor,
)

DATA_DIR_ENV = 'HUB_DATA_DIR'
MAX_PARTS = 20
KINDS = ('workpiece', 'tool', 'environment')
KIND_LABELS = {'workpiece': 'iş parçası', 'tool': 'takım', 'environment': 'ortam'}
ID_RE = re.compile(r'^[0-9a-f]{32}$')
META_VERSION = 1
MAX_POS_M = 10.0                  # konum sınırı: bunun ötesi yazım hatasıdır (mm yerine m yazılmış olabilir)
SCALE_MIN, SCALE_MAX = 0.001, 1000.0
RPY_LIMIT_DEG = 360.0
EDITABLE = ('name', 'kind', 'visible', 'unit', 'scale', 'pose', 'tool_offset')
ZERO_POSE = {'pos_m': [0.0, 0.0, 0.0], 'rpy_deg': [0.0, 0.0, 0.0]}


class PartError(Exception):
    """Depo işlemi reddedildi; ``str(exc)`` kullanıcıya güvenle gösterilir (dosya yolu içermez)."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def data_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Hub kullanıcı veri klasörü: ``HUB_DATA_DIR``, yoksa kullanıcı klasörünün ``data`` alt dizini."""
    env = os.environ if env is None else env
    value = env.get(DATA_DIR_ENV)
    return Path(value).expanduser() if value else paths.user_dir() / 'data'


# ---------------------------------------------------------------------------------------------------
# saf doğrulayıcılar
# ---------------------------------------------------------------------------------------------------
def _vec3(value: Any, what: str, limit: float) -> List[float]:
    ok = isinstance(value, (list, tuple)) and len(value) == 3 and \
        all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)
    if not ok:
        raise PartError(400, 'bad_value', f'{what}: 3 sonlu sayı olmalı')
    if any(abs(v) > limit for v in value):
        raise PartError(400, 'bad_value', f'{what}: |değer| en çok {limit:g} olabilir')
    return [float(v) for v in value]


def check_pose(value: Any, what: str = 'poz') -> Dict[str, List[float]]:
    if not isinstance(value, Mapping) or set(value) - {'pos_m', 'rpy_deg'}:
        raise PartError(400, 'bad_value', f'{what}: {{pos_m, rpy_deg}} olmalı')
    return {'pos_m': _vec3(value.get('pos_m'), f'{what} konumu (m)', MAX_POS_M),
            'rpy_deg': _vec3(value.get('rpy_deg'), f'{what} yönelimi (°)', RPY_LIMIT_DEG)}


def check_scale(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not SCALE_MIN <= value <= SCALE_MAX:
        raise PartError(400, 'bad_value', f'Ölçek {SCALE_MIN:g} ile {SCALE_MAX:g} arasında olmalı')
    return float(value)


def check_kind(value: Any) -> str:
    if value not in KINDS:
        raise PartError(400, 'bad_value', f'Tür {", ".join(KINDS)} olmalı')
    return value


def check_id(part_id: Any) -> str:
    if not isinstance(part_id, str) or not ID_RE.match(part_id):
        raise PartError(404, 'no_part', 'Böyle bir parça yok')
    return part_id


def validate_patch(patch: Any) -> Dict[str, Any]:
    """``PUT`` gövdesi: yalnız ``EDITABLE`` alanlar, her biri doğrulanmış/normalleştirilmiş."""
    if not isinstance(patch, Mapping) or not patch:
        raise PartError(400, 'bad_request', 'Değiştirilecek alan yok')
    unknown = sorted(set(patch) - set(EDITABLE))
    if unknown:
        raise PartError(400, 'bad_request', f'Değiştirilemeyen alan: {", ".join(map(str, unknown))}')
    out: Dict[str, Any] = {}
    for key, value in patch.items():
        if key == 'name':
            out[key] = clean_display_name(value, fallback='')
            if not out[key]:
                raise PartError(400, 'bad_value', 'Ad boş olamaz')
        elif key == 'kind':
            out[key] = check_kind(value)
        elif key == 'visible':
            if not isinstance(value, bool):
                raise PartError(400, 'bad_value', 'visible true/false olmalı')
            out[key] = value
        elif key == 'unit':
            try:
                unit_factor(value)
            except MeshToolError as exc:
                raise PartError(400, 'bad_value', str(exc)) from None
            out[key] = value
        elif key == 'scale':
            out[key] = check_scale(value)
        elif key == 'pose':
            out[key] = check_pose(value, 'Parça pozu')
        elif key == 'tool_offset':
            out[key] = None if value is None else check_pose(value, 'Takım ofseti')
    return out


# ---------------------------------------------------------------------------------------------------
# depo
# ---------------------------------------------------------------------------------------------------
def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def public_view(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Ön uca giden biçim: meta + mesh adresi + metre cinsinden sınır kutusu boyutu + birim uyarısı."""
    lo, hi = meta['bbox_raw']['min'], meta['bbox_raw']['max']
    f = unit_factor(meta['unit'])
    return {**{k: v for k, v in meta.items() if k != 'bbox_raw'},
            'url': f'/api/parts/{meta["id"]}/mesh',
            'bbox_raw': meta['bbox_raw'], 'unit_m': f,
            'size_m': [round((b - a) * f * meta['scale'], 6) for a, b in zip(lo, hi)],
            'size_warning': size_warning(tuple(lo), tuple(hi), meta['unit'], meta['scale'])}


class PartsStore:
    def __init__(self, root: Path):
        self.root = Path(root) / 'parts'
        self._lock = threading.Lock()

    # --- yardımcılar ---------------------------------------------------------------------------
    def _dir(self, part_id: str) -> Path:
        return self.root / check_id(part_id)

    def _read_meta(self, d: Path) -> Dict[str, Any]:
        meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
        if meta.get('version') != META_VERSION or meta.get('id') != d.name or not (d / 'mesh.stl').is_file():
            raise ValueError('meta sürümü/kimliği ya da mesh uyuşmuyor')
        return meta

    def _write_meta(self, d: Path, meta: Dict[str, Any]) -> None:
        _atomic_write(d / 'meta.json', json.dumps(meta, ensure_ascii=False, indent=1).encode('utf-8'))

    def _storage_error(self, exc: OSError) -> PartError:
        why = os.strerror(exc.errno) if exc.errno else type(exc).__name__
        return PartError(500, 'storage_error', f'Parça klasörüne yazılamadı/okunamadı: {why}')

    # --- okuma ---------------------------------------------------------------------------------
    def list(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        """(parçalar, sorunlar). Yarım/bozuk parça atlanır; klasör yoksa boş liste (oluşturulmaz)."""
        if not self.root.is_dir():
            return [], []
        parts, problems = [], []
        try:
            dirs = sorted(self.root.iterdir())
        except OSError as exc:
            raise self._storage_error(exc) from None
        for d in dirs:
            if not d.is_dir() or not ID_RE.match(d.name):
                continue
            try:
                parts.append(self._read_meta(d))
            except (OSError, ValueError, KeyError, TypeError):
                problems.append(f'Bozuk parça atlandı ({d.name[:8]}…)')
        parts.sort(key=lambda m: (m.get('created_at', 0), m['id']))
        return parts, problems

    def get(self, part_id: str) -> Dict[str, Any]:
        d = self._dir(part_id)
        try:
            return self._read_meta(d)
        except FileNotFoundError:
            raise PartError(404, 'no_part', 'Böyle bir parça yok') from None
        except (OSError, ValueError, KeyError, TypeError):
            raise PartError(500, 'corrupt_part', 'Parça dosyaları bozuk') from None

    def read_mesh(self, part_id: str) -> bytes:
        self.get(part_id)
        try:
            return (self._dir(part_id) / 'mesh.stl').read_bytes()
        except OSError as exc:
            raise self._storage_error(exc) from None

    # --- yazma ---------------------------------------------------------------------------------
    def add(self, *, name: str, kind: str, unit: str, filename: str, mesh: ImportedMesh) -> Dict[str, Any]:
        check_kind(kind)
        unit_factor(unit)
        part_id = uuid.uuid4().hex
        meta = {'version': META_VERSION, 'id': part_id, 'name': clean_display_name(name), 'kind': kind,
                'unit': unit, 'scale': 1.0, 'visible': True, 'pose': json.loads(json.dumps(ZERO_POSE)),
                'tool_offset': json.loads(json.dumps(ZERO_POSE)) if kind == 'tool' else None,
                'triangles': mesh.triangles, 'source_format': mesh.source_format,
                'source_name': clean_display_name(filename, fallback='dosya'),
                'bytes': len(mesh.stl), 'created_at': round(time.time(), 3),
                'bbox_raw': {'min': [float(v) for v in mesh.bbox_min],
                             'max': [float(v) for v in mesh.bbox_max]}}
        with self._lock:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                self._sweep()
                if sum(1 for d in self.root.iterdir() if ID_RE.match(d.name)) >= MAX_PARTS:
                    raise PartError(409, 'too_many_parts', f'En çok {MAX_PARTS} parça: önce birini sil')
                tmp = self.root / f'.new-{part_id}'
                tmp.mkdir()
                try:
                    _atomic_write(tmp / 'mesh.stl', mesh.stl)
                    self._write_meta(tmp, meta)
                    os.replace(tmp, self.root / part_id)
                except BaseException:
                    shutil.rmtree(tmp, ignore_errors=True)
                    raise
            except OSError as exc:
                raise self._storage_error(exc) from None
        return meta

    def update(self, part_id: str, patch: Mapping[str, Any]) -> Dict[str, Any]:
        changes = validate_patch(patch)
        with self._lock:
            meta = self.get(part_id)
            meta.update(changes)
            # ofset yalnız takım türünde anlamlı; takımda her zaman bir değeri vardır (varsayılan: sıfır)
            meta['tool_offset'] = (meta.get('tool_offset') or json.loads(json.dumps(ZERO_POSE))) \
                if meta['kind'] == 'tool' else None
            try:
                self._write_meta(self._dir(part_id), meta)
            except OSError as exc:
                raise self._storage_error(exc) from None
        return meta

    def delete(self, part_id: str) -> None:
        with self._lock:
            self.get(part_id)
            d = self._dir(part_id)
            gone = self.root / f'.deleted-{part_id}'
            try:
                os.replace(d, gone)                  # önce görünmez yap (atomik), sonra sil
                shutil.rmtree(gone, ignore_errors=True)
            except OSError as exc:
                raise self._storage_error(exc) from None

    def _sweep(self) -> None:
        """Yarım kalmış ``.new-*`` / ``.deleted-*`` dizinlerini temizle (çökme artığı)."""
        for d in self.root.iterdir():
            if d.is_dir() and (d.name.startswith('.new-') or d.name.startswith('.deleted-')):
                shutil.rmtree(d, ignore_errors=True)
