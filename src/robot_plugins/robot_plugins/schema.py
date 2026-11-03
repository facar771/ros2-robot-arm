"""A small JSON-Schema subset validator (no third-party dependency).

Supported keywords: ``type`` (object, array, string, number, integer, boolean, null; or a list of
them), ``properties``, ``required``, ``additionalProperties`` (bool), ``enum``, ``minimum``,
``maximum``, ``minLength``, ``maxLength``, ``pattern``, ``items``, ``minItems``, ``maxItems``,
``default`` (only used by :func:`apply_defaults`). Anything else raises :class:`SchemaError` when
the *schema* is checked, so a manifest cannot silently promise validation it does not get.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List

SUPPORTED = {'type', 'properties', 'required', 'additionalProperties', 'enum', 'minimum', 'maximum',
             'minLength', 'maxLength', 'pattern', 'items', 'minItems', 'maxItems', 'default',
             'description', 'title'}
TYPES = {'object', 'array', 'string', 'number', 'integer', 'boolean', 'null'}


class SchemaError(ValueError):
    """The schema itself is malformed or uses an unsupported keyword."""


class ValidationError(ValueError):
    """A value does not satisfy its schema; ``path`` points at the offending part."""

    def __init__(self, path: str, message: str):
        super().__init__(f'{path or "$"}: {message}')
        self.path = path
        self.message = message


def check_schema(schema: Any, path: str = '$') -> None:
    """Raise :class:`SchemaError` unless ``schema`` only uses the supported subset correctly."""
    if not isinstance(schema, dict):
        raise SchemaError(f'{path}: schema must be an object')
    for key in schema:
        if key not in SUPPORTED:
            raise SchemaError(f'{path}: unsupported keyword {key!r}')
    t = schema.get('type')
    for one in ([t] if isinstance(t, str) else (t or [])):
        if one not in TYPES:
            raise SchemaError(f'{path}: unknown type {one!r}')
    for name, sub in (schema.get('properties') or {}).items():
        check_schema(sub, f'{path}.properties.{name}')
    if 'items' in schema:
        check_schema(schema['items'], f'{path}.items')
    if 'required' in schema and not all(isinstance(r, str) for r in schema['required']):
        raise SchemaError(f'{path}: required must be a list of names')
    for key in ('minimum', 'maximum'):
        if key in schema and (isinstance(schema[key], bool)
                              or not isinstance(schema[key], (int, float))
                              or not math.isfinite(schema[key])):
            raise SchemaError(f'{path}: {key} must be a finite number')
    if 'pattern' in schema:
        try:
            re.compile(schema['pattern'])
        except re.error as exc:
            raise SchemaError(f'{path}: bad pattern ({exc})') from exc


def _is(value: Any, name: str) -> bool:
    if name == 'object':
        return isinstance(value, dict)
    if name == 'array':
        return isinstance(value, list)
    if name == 'string':
        return isinstance(value, str)
    if name == 'boolean':
        return isinstance(value, bool)
    if name == 'null':
        return value is None
    if name == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if name == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def validate(value: Any, schema: Dict[str, Any], path: str = '') -> None:
    """Raise :class:`ValidationError` if ``value`` violates ``schema`` (first problem only)."""
    if isinstance(value, float) and not math.isfinite(value):
        # JSON from Python's parser may carry NaN / Infinity; no schema ever means to accept them
        # (a comparison like ``nan > maximum`` is False and would let them through).
        raise ValidationError(path, f'sonlu bir sayı olmalı, {value!r} geldi')
    t = schema.get('type')
    if t is not None:
        names: List[str] = [t] if isinstance(t, str) else list(t)
        if not any(_is(value, n) for n in names):
            raise ValidationError(
                path, f'tür {"/".join(names)} olmalı, {type(value).__name__} geldi')
    if 'enum' in schema and value not in schema['enum']:
        raise ValidationError(path, f'{schema["enum"]} değerlerinden biri olmalı, {value!r} geldi')
    if _is(value, 'number'):
        if 'minimum' in schema and value < schema['minimum']:
            raise ValidationError(path, f'{schema["minimum"]} değerinden küçük olamaz ({value})')
        if 'maximum' in schema and value > schema['maximum']:
            raise ValidationError(path, f'{schema["maximum"]} değerinden büyük olamaz ({value})')
    if isinstance(value, str):
        if 'minLength' in schema and len(value) < schema['minLength']:
            raise ValidationError(path, f'en az {schema["minLength"]} karakter olmalı')
        if 'maxLength' in schema and len(value) > schema['maxLength']:
            raise ValidationError(path, f'en çok {schema["maxLength"]} karakter olmalı')
        if 'pattern' in schema and not re.search(schema['pattern'], value):
            raise ValidationError(path, f'{schema["pattern"]!r} desenine uymuyor')
    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            raise ValidationError(path, f'en az {schema["minItems"]} öğe olmalı')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            raise ValidationError(path, f'en çok {schema["maxItems"]} öğe olmalı')
        if 'items' in schema:
            for i, item in enumerate(value):
                validate(item, schema['items'], f'{path}[{i}]')
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        for name in schema.get('required') or []:
            if name not in value:
                raise ValidationError(f'{path}.{name}' if path else name, 'zorunlu alan eksik')
        if schema.get('additionalProperties') is False:
            for name in value:
                if name not in props:
                    raise ValidationError(f'{path}.{name}' if path else name, 'bilinmeyen alan')
        for name, sub in props.items():
            if name in value:
                validate(value[name], sub, f'{path}.{name}' if path else name)


def apply_defaults(value: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of ``value`` with missing top-level properties filled from their ``default``."""
    out = dict(value)
    for name, sub in (schema.get('properties') or {}).items():
        if name not in out and 'default' in sub:
            out[name] = sub['default']
    return out
