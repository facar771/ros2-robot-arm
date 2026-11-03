"""The Robot API refuses NaN / Infinity / 1e999 literals (Python's json accepts them, JSON does
not): a non-finite number must never reach a job or a motion target."""
import pytest
from robot_core.webview import _strict_loads

from tests.test_webview_jobs import Rig


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


BAD = ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999']


@pytest.mark.parametrize('literal', BAD)
def test_loader_refuses_non_finite(literal):
    with pytest.raises(ValueError):
        _strict_loads('{"timeout_s": %s}' % literal)
    with pytest.raises(ValueError):
        _strict_loads('[1, %s]' % literal)


def test_loader_keeps_normal_numbers():
    assert _strict_loads('{"a": 1.5, "b": -2, "c": 1e3, "d": [0.25]}') == {
        'a': 1.5, 'b': -2, 'c': 1000.0, 'd': [0.25]}


@pytest.mark.parametrize('literal', BAD)
def test_jobs_post_rejects_non_finite(rig, literal):
    raw = ('{"command": "above green 0.25", "timeout_s": %s}' % literal).encode()
    status, _, doc = rig.request('POST', '/api/jobs', raw=raw)
    assert status == 400 and 'JSON' in doc['error']
    assert rig.skills.calls == []
    assert rig.request('GET', '/api/jobs')[2]['jobs'] == []


@pytest.mark.parametrize('literal', BAD)
def test_legacy_command_post_rejects_non_finite(rig, literal):
    raw = ('{"command": "above green 0.25", "x": %s}' % literal).encode()
    status, _, _ = rig.request('POST', '/api/command', raw=raw)
    assert status == 400 and rig.skills.calls == []


def test_stop_is_never_refused_over_a_bad_body(rig):
    status, _, doc = rig.request('POST', '/api/stop', raw=b'{"reason": NaN}')
    assert status == 200 and rig.estop.is_set
