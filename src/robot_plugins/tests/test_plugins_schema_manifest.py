import pytest
import yaml
from plugins_support import PLUGINS, make_plugin
from robot_plugins.manifest import ManifestError, load_manifest, parse_manifest
from robot_plugins.schema import (
    SchemaError,
    ValidationError,
    apply_defaults,
    check_schema,
    validate,
)

# ---- schema ------------------------------------------------------------------------------------
SCHEMA = {'type': 'object', 'required': ['color'], 'additionalProperties': False, 'properties': {
    'color': {'type': 'string', 'enum': ['red', 'blue']},
    'n': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'default': 2},
    'xs': {'type': 'array', 'items': {'type': 'number'}, 'maxItems': 2}}}


@pytest.mark.parametrize('value', [{'color': 'red'}, {'color': 'blue', 'n': 5, 'xs': [1, 2.5]}])
def test_valid_values_pass(value):
    validate(value, SCHEMA)


@pytest.mark.parametrize('value, where', [
    ({}, 'color'), ({'color': 'green'}, 'color'), ({'color': 'red', 'n': 0}, 'n'),
    ({'color': 'red', 'n': 2.5}, 'n'), ({'color': 'red', 'n': True}, 'n'),
    ({'color': 'red', 'xs': [1, 'a']}, 'xs[1]'), ({'color': 'red', 'xs': [1, 2, 3]}, 'xs'),
    ({'color': 'red', 'extra': 1}, 'extra'), ('not an object', ''),
])
def test_invalid_values_name_the_offending_path(value, where):
    with pytest.raises(ValidationError) as exc:
        validate(value, SCHEMA)
    assert exc.value.path == where


def test_defaults_are_filled_but_not_over_given_values():
    assert apply_defaults({'color': 'red'}, SCHEMA) == {'color': 'red', 'n': 2}
    assert apply_defaults({'color': 'red', 'n': 4}, SCHEMA)['n'] == 4


def test_unsupported_schema_keywords_are_rejected_not_ignored():
    with pytest.raises(SchemaError):
        check_schema({'type': 'object', 'oneOf': []})
    with pytest.raises(SchemaError):
        check_schema({'type': 'blob'})
    with pytest.raises(SchemaError):
        check_schema({'type': 'string', 'pattern': '('})


# ---- manifest ----------------------------------------------------------------------------------
GOOD = {'name': 'demo', 'version': '1.2.3', 'description': 'x', 'entry': 'main:Plugin',
        'permissions': ['camera:read'], 'tools': [{'name': 'tool_a', 'description': 'd'}]}


def test_good_manifest_parses_and_always_grants_the_emergency_stop():
    m = parse_manifest(GOOD)
    assert m.permissions == ('camera:read',)
    assert 'robot:stop' in m.granted and 'robot:jobs' not in m.granted
    assert m.describe()['tools'][0]['input_schema'] == {'type': 'object', 'properties': {}}


def test_listing_robot_stop_is_harmless_and_deduplicated():
    m = parse_manifest({**GOOD, 'permissions': ['robot:stop', 'camera:read', 'camera:read']})
    assert m.permissions == ('camera:read',)
    assert m.granted.count('robot:stop') == 1


@pytest.mark.parametrize('patch, text', [
    ({'permissions': ['root']}, 'bilinmeyen izin'),
    ({'deny': ['robot:stop']}, 'reddedilemez'),
    ({'name': 'Bad Name'}, 'name'), ({'name': 'x'}, 'name'),
    ({'version': '1.0'}, 'version'), ({'entry': 'main'}, 'entry'),
    ({'entry': '../x:Plugin'}, 'entry'),
    ({'description': ''}, 'description'), ({'surprise': 1}, 'bilinmeyen anahtar'),
    ({'parameters': {'type': 'array'}}, 'object'),
    ({'tools': [{'name': 'tool_a', 'description': 'd'},
                {'name': 'tool_a', 'description': 'd'}]}, 'tekrar'),
    ({'tools': [{'name': 'tool_a'}]}, 'description'),
    ({'tools': [{'name': 'tool_a', 'description': 'd', 'input_schema': {'type': 'object',
                                                                    'oneOf': []}}]}, 'unsupported'),
])
def test_bad_manifests_are_refused_with_a_reason(patch, text):
    with pytest.raises(ManifestError) as exc:
        parse_manifest({**GOOD, **patch})
    assert text in str(exc.value)


def test_parameters_get_defaults_and_validation():
    m = parse_manifest({**GOOD, 'parameters': {'type': 'object', 'properties': {
        'rate': {'type': 'number', 'minimum': 1, 'default': 5}}}})
    assert m.resolve_params() == {'rate': 5}
    with pytest.raises(ValidationError):
        m.resolve_params({'rate': 0.1})


def test_manifest_name_must_match_directory(tmp_path):
    d = make_plugin(tmp_path, 'alpha')
    data = yaml.safe_load((d / 'package.yaml').read_text())
    data['name'] = 'beta'
    (d / 'package.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError, match='dizin adıyla'):
        load_manifest(d)
    with pytest.raises(ManifestError, match='yok'):
        load_manifest(tmp_path / 'nothing')


@pytest.mark.parametrize('name', ['find_object', 'follow_color', 'scan_extent'])
def test_shipped_plugins_have_valid_manifests_with_llm_tools(name):
    m = load_manifest(PLUGINS / name)
    assert m.tools and all(t.description for t in m.tools)
    assert 'robot:stop' in m.granted
    assert ('robot:jobs' in m.permissions) == (name == 'follow_color')   # only motion needs jobs
