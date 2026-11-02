"""``package.yaml``: what a plugin is, what it may do and which tools it offers.

    name: find_object                 # slug: a-z, 0-9, _  (also the directory name)
    version: 0.1.0                    # semantic version
    description: ...
    entry: main:Plugin                # module (a .py file in the plugin directory) : class
    permissions: [camera:read]        # camera:read, robot:jobs (robot:stop is implicit, see below)
    parameters: {type: object, properties: {...}}      # JSON-Schema subset, defaults applied
    tools:                            # what an LLM (Robot chat, stage 7) may call
      - name: find_object
        description: ...
        input_schema: {type: object, properties: {...}, required: [...]}

``robot:stop`` is NOT a permission a plugin asks for or can lose: every plugin context can always
call the emergency stop. Listing it is harmless; trying to *deny* it (``deny: [robot:stop]``) or any
unknown permission makes the manifest invalid.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import yaml

from robot_plugins.schema import SchemaError, apply_defaults, check_schema, validate

NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
VERSION_RE = re.compile(r'^\d+\.\d+\.\d+([+-][0-9A-Za-z.-]+)?$')
ENTRY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*$')
PERMISSIONS = ('camera:read', 'robot:jobs')
ALWAYS_GRANTED = ('robot:stop',)
KNOWN_KEYS = {'name', 'version', 'description', 'entry', 'permissions', 'parameters', 'tools'}


class ManifestError(ValueError):
    """``package.yaml`` is missing, unreadable or breaks a rule; the message says which."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    description: str
    entry: str
    permissions: Tuple[str, ...]
    parameters: Dict[str, Any]
    tools: Tuple[Tool, ...]
    directory: Path = field(default=Path('.'), compare=False)

    @property
    def granted(self) -> Tuple[str, ...]:
        """Everything the plugin may do: what it asked for plus the always-on ``robot:stop``."""
        return tuple(self.permissions) + ALWAYS_GRANTED

    def tool(self, name: str) -> Tool:
        for t in self.tools:
            if t.name == name:
                return t
        raise KeyError(name)

    def resolve_params(self, given: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Defaults + ``given``, validated against ``parameters``."""
        params = apply_defaults(dict(given or {}), self.parameters)
        validate(params, self.parameters)
        return params

    def describe(self) -> Dict[str, Any]:
        """JSON-able summary (also the LLM tool catalogue: ``tools`` is the list to hand over)."""
        return {'name': self.name, 'version': self.version, 'description': self.description,
                'permissions': list(self.granted), 'parameters': self.parameters,
                'tools': [{'name': t.name, 'description': t.description,
                           'input_schema': t.input_schema} for t in self.tools]}


def parse_manifest(data: Any, directory: Union[str, Path] = '.') -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError('package.yaml bir eşleme (anahtar: değer) olmalı')
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise ManifestError(f'bilinmeyen anahtar(lar): {sorted(unknown)}'
                            + (' (robot:stop reddedilemez)' if 'deny' in unknown else ''))
    for key in ('name', 'version', 'description', 'entry'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ManifestError(f'{key}: zorunlu, metin olmalı')
    if not NAME_RE.match(data['name']):
        raise ManifestError('name: a-z ile başlayan, a-z 0-9 _ içeren 2-40 karakter olmalı')
    if not VERSION_RE.match(data['version']):
        raise ManifestError('version: MAJOR.MINOR.PATCH biçiminde olmalı')
    if not ENTRY_RE.match(data['entry']):
        raise ManifestError("entry: 'modül:Sınıf' biçiminde olmalı (ör. main:Plugin)")
    perms = data.get('permissions', [])
    if not isinstance(perms, list) or not all(isinstance(p, str) for p in perms):
        raise ManifestError('permissions: metin listesi olmalı')
    bad = [p for p in perms if p not in PERMISSIONS and p not in ALWAYS_GRANTED]
    if bad:
        raise ManifestError(f'bilinmeyen izin(ler): {bad}; geçerli: {list(PERMISSIONS)}')
    perms = tuple(dict.fromkeys(p for p in perms if p not in ALWAYS_GRANTED))
    params = data.get('parameters', {'type': 'object', 'properties': {}})
    try:
        check_schema(params, 'parameters')
        if params.get('type') != 'object':
            raise SchemaError("parameters: kök tür 'object' olmalı")
    except SchemaError as exc:
        raise ManifestError(str(exc)) from exc
    tools: List[Tool] = []
    for i, raw in enumerate(data.get('tools', [])):
        if not isinstance(raw, dict) or set(raw) - {'name', 'description', 'input_schema'}:
            raise ManifestError(f'tools[{i}]: yalnızca name, description, input_schema olabilir')
        name, desc = raw.get('name'), raw.get('description')
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ManifestError(f'tools[{i}].name: geçersiz')
        if not isinstance(desc, str) or not desc.strip():
            raise ManifestError(f'tools[{i}].description: zorunlu (LLM bu metni okur)')
        schema = raw.get('input_schema', {'type': 'object', 'properties': {}})
        try:
            check_schema(schema, f'tools[{i}].input_schema')
        except SchemaError as exc:
            raise ManifestError(str(exc)) from exc
        if schema.get('type') != 'object':
            raise ManifestError(f"tools[{i}].input_schema: kök tür 'object' olmalı")
        if name in {t.name for t in tools}:
            raise ManifestError(f'tools[{i}].name: {name!r} tekrar ediyor')
        tools.append(Tool(name, desc.strip(), schema))
    return Manifest(name=data['name'], version=data['version'], description=data['description'],
                    entry=data['entry'], permissions=perms, parameters=params,
                    tools=tuple(tools), directory=Path(directory))


def load_manifest(directory: Union[str, Path]) -> Manifest:
    """Read ``<directory>/package.yaml``; the manifest name must equal the directory name."""
    directory = Path(directory)
    path = directory / 'package.yaml'
    if not path.is_file():
        raise ManifestError(f'{path} yok')
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f'{path} okunamadı: {exc}') from exc
    manifest = parse_manifest(data, directory)
    if manifest.name != directory.name:
        raise ManifestError(
            f'name ({manifest.name!r}) dizin adıyla ({directory.name!r}) aynı olmalı')
    return manifest
