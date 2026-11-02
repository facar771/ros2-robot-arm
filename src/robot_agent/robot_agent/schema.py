"""A small JSON-Schema subset validator (stdlib only): enough for the tool schemas.

Supported: ``type: object`` with ``properties`` / ``required`` / ``additionalProperties: false``;
property types ``string`` (``enum``, ``maxLength``), ``number`` and ``integer`` (``minimum``,
``maximum``; NaN/inf and booleans are refused), ``boolean``. Anything else in a schema is a
programming error and raises ``ValueError`` when the schema is checked.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

TYPES = ('string', 'number', 'integer', 'boolean')
KNOWN = {'type', 'enum', 'maxLength', 'minimum', 'maximum', 'description', 'default'}


def check_schema(schema: Dict[str, Any]) -> None:
    if schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
        raise ValueError("araç şeması 'type: object' ve 'additionalProperties: false' olmalı")
    props = schema.get('properties', {})
    for name, prop in props.items():
        if prop.get('type') not in TYPES:
            raise ValueError(f'{name}: desteklenmeyen tür {prop.get("type")!r}')
        extra = set(prop) - KNOWN
        if extra:
            raise ValueError(f'{name}: desteklenmeyen anahtar(lar) {sorted(extra)}')
    for name in schema.get('required', []):
        if name not in props:
            raise ValueError(f'required içindeki {name!r} properties içinde yok')


def validate(schema: Dict[str, Any], value: Any) -> List[str]:
    """Problems with ``value`` (empty list: valid), in plain Turkish."""
    if not isinstance(value, dict):
        return ['argümanlar bir nesne (sözlük) olmalı']
    props = schema.get('properties', {})
    problems = [f"bilinmeyen argüman '{key}'" for key in value if key not in props]
    for name in schema.get('required', []):
        if name not in value:
            problems.append(f"'{name}' zorunlu")
    for name, prop in props.items():
        if name in value:
            problems += _check(name, prop, value[name])
    return problems


def _check(name: str, prop: Dict[str, Any], value: Any) -> List[str]:
    kind = prop['type']
    if kind == 'string':
        if not isinstance(value, str):
            return [f"'{name}' metin olmalı"]
        if len(value) > prop.get('maxLength', 64):
            return [f"'{name}' çok uzun"]
        if 'enum' in prop and value not in prop['enum']:
            return [f"'{name}' şunlardan biri olmalı: {', '.join(prop['enum'])} (verilen: "
                    f"{value[:20]!r})"]
        return []
    if kind == 'boolean':
        return [] if isinstance(value, bool) else [f"'{name}' doğru/yanlış olmalı"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [f"'{name}' sayı olmalı"]
    if kind == 'integer' and not float(value).is_integer():
        return [f"'{name}' tam sayı olmalı"]
    if not math.isfinite(value):
        return [f"'{name}' sonlu bir sayı olmalı"]
    problems = []
    if 'minimum' in prop and value < prop['minimum']:
        problems.append(f"'{name}' en az {prop['minimum']:g} olmalı (verilen: {value:g})")
    if 'maximum' in prop and value > prop['maximum']:
        problems.append(f"'{name}' en çok {prop['maximum']:g} olmalı (verilen: {value:g})")
    return problems
