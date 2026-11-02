"""The single outside entry point (robot_plugins.invoke.run_plugin) and the first-frame wait.

Every shipped plugin is called through it in a REAL subprocess (the runner), the way the agent layer
will call it. follow_color talks to a stub Robot API over HTTP on 127.0.0.1."""
import threading
import time

import pytest
from plugins_support import PLUGINS, StubRobotServer, make_plugin
from robot_plugins.context import PluginContext
from robot_plugins.frames import FrameSource, SyntheticFrameSource
from robot_plugins.invoke import catalog, robot_url_ok, run_plugin
from robot_plugins.robot_api import FakeRobotApi

KEYS = {'api_version', 'ok', 'plugin', 'tool', 'motion', 'data', 'error', 'code', 'warning',
        'robot', 'frames', 'duration_s'}


class DelayedFrames(FrameSource):
    """Like a live camera: nothing for ``delay`` s after start, then frames."""

    def __init__(self, delay):
        self._t0 = time.monotonic() + delay
        self._inner = SyntheticFrameSource()

    def latest(self):
        return self._inner.latest() if time.monotonic() >= self._t0 else None


def ctx(source, wait=0.0, stop=None):
    return PluginContext('x', {}, frozenset({'camera:read'}), source, FakeRobotApi(),
                         lambda lv, msg: None, stop, frame_wait_s=wait)


# ---- (a) first frame -------------------------------------------------------------------------
def test_latest_frame_waits_for_the_first_frame():
    c = ctx(DelayedFrames(0.4), wait=3.0)
    t0 = time.monotonic()
    assert c.camera.latest_frame() is not None
    assert 0.3 < time.monotonic() - t0 < 2.0


def test_latest_frame_returns_none_after_the_wait_and_wait_zero_never_waits():
    c = ctx(DelayedFrames(60), wait=0.3)
    t0 = time.monotonic()
    assert c.camera.latest_frame() is None
    assert 0.25 < time.monotonic() - t0 < 1.5
    t0 = time.monotonic()
    assert c.camera.latest_frame(wait_s=0) is None
    assert time.monotonic() - t0 < 0.2


def test_the_wait_ends_at_once_when_the_stop_token_is_set():
    stop = threading.Event()
    c = ctx(DelayedFrames(60), wait=30.0, stop=stop)
    threading.Timer(0.2, stop.set).start()
    t0 = time.monotonic()
    assert c.camera.latest_frame() is None
    assert time.monotonic() - t0 < 2.0


def test_default_context_does_not_wait():
    t0 = time.monotonic()
    assert ctx(DelayedFrames(60)).camera.latest_frame() is None
    assert time.monotonic() - t0 < 0.2


def test_find_object_in_a_fresh_process_waits_for_a_late_camera():
    """The reported bug: one-shot call, the camera delivers its first frame a moment later."""
    r = run_plugin('find_object', 'find_object', {'color': 'red'}, frames='delayed:2.5')
    assert r['ok'] is True and r['data']['found'] is True, r
    assert r['duration_s'] >= 2.0


def test_a_camera_that_never_delivers_is_an_error_after_the_wait():
    r = run_plugin('find_object', 'find_object', {'color': 'red'}, frames='delayed:600',
                   frame_wait_s=0.5)
    assert r['ok'] is False and r['code'] == 'plugin_error' and 'kare' in r['error']
    assert 0.5 <= r['duration_s'] < 10


# ---- (b) contract ----------------------------------------------------------------------------
def test_catalog_declares_what_moves():
    motion = {c['name']: c['motion'] for c in catalog(PLUGINS)}
    assert motion == {'find_object': 'none', 'scan_extent': 'camera', 'follow_color': 'arm'}
    for c in catalog(PLUGINS):
        assert c['description'] and c['input_schema']['type'] == 'object' and c['timeout_s'] > 0


@pytest.mark.parametrize('url,ok', [('fake', True), ('http://127.0.0.1:8080', True),
                                    ('http://localhost:8080', True),
                                    ('http://10.0.0.5:8080', False),
                                    ('http://example.com', False), ('ftp://127.0.0.1', False)])
def test_robot_url_must_be_fake_or_loopback(url, ok):
    assert robot_url_ok(url) is ok


def test_unknown_plugin_tool_and_bad_arguments_never_raise():
    r = run_plugin('nope', 'x', {})
    assert set(r) == KEYS and r['ok'] is False and r['code'] == 'unknown_plugin'
    r = run_plugin('find_object', 'nope', {})
    assert r['code'] == 'unknown_tool' and 'find_object' in r['error']
    r = run_plugin('find_object', 'find_object', {'color': 'purple'}, frames='synthetic')
    assert r['ok'] is False and r['code'] in ('bad_arguments', 'bad_parameters')
    r = run_plugin('follow_color', 'follow_color', {'color': 'red'}, robot_url='http://10.1.1.1')
    assert r['code'] == 'bad_robot_url'


def test_tool_may_be_omitted_when_the_plugin_has_exactly_one():
    r = run_plugin('find_object', None, {'color': 'any'}, frames='synthetic')
    assert r['ok'] and r['tool'] == 'find_object'


# ---- (c) every shipped plugin through the entry point ----------------------------------------
def test_find_object_looks_only():
    r = run_plugin('find_object', 'find_object', {'color': 'any'}, frames='synthetic')
    assert set(r) == KEYS and r['ok'] is True and r['motion'] == 'none' and r['code'] is None
    assert r['data']['count'] == 3 and r['warning'] is None


def test_find_object_without_a_camera_is_an_error_not_a_green_ok():
    r = run_plugin('find_object', 'find_object', {'color': 'red'}, frames='none')
    assert r['ok'] is False and r['code'] in ('plugin_error', 'permission_denied')


def test_scan_extent_moves_only_the_camera():
    r = run_plugin('scan_extent', 'scan_extent', {}, frames='synthetic', panner='synthetic')
    assert r['ok'] is True and r['motion'] == 'camera' and r['data']['count'] >= 3


def test_follow_color_moves_the_arm_through_the_robot_api():
    srv = StubRobotServer(job_duration=0.2)
    try:
        r = run_plugin('follow_color', 'follow_color', {'color': 'red', 'duration_s': 1.0},
                       params={'poll_s': 0.1}, robot_url=srv.url, frames='synthetic')
        assert r['ok'] is True and r['motion'] == 'arm' and r['data']['reason'] == 'duration'
        assert r['data']['jobs'] >= 1 and r['warning'] is None
        assert any(c.startswith('above red') for c in srv.fake.history)
    finally:
        srv.close()


def test_follow_color_with_the_fake_robot_says_nothing_moved():
    r = run_plugin('follow_color', 'follow_color', {'color': 'red', 'duration_s': 1.0},
                   params={'poll_s': 0.1}, frames='synthetic')
    assert r['ok'] is True and 'BENZETİM' in r['warning']


def test_follow_color_estop_is_reported_as_estop_and_not_retried():
    srv = StubRobotServer()
    try:
        srv.fake.stop('test')
        r = run_plugin('follow_color', 'follow_color', {'color': 'red', 'duration_s': 10},
                       params={'poll_s': 0.1}, robot_url=srv.url, frames='synthetic')
        assert r['ok'] is False and r['code'] == 'estop'
        assert srv.fake.history == []
    finally:
        srv.close()


def test_timeout_stops_the_plugin_and_warns_for_arm_tools():
    srv = StubRobotServer(job_duration=0.2)
    try:
        t0 = time.monotonic()
        r = run_plugin('follow_color', 'follow_color', {'color': 'red', 'duration_s': 60},
                       params={'poll_s': 0.1}, robot_url=srv.url, frames='synthetic',
                       timeout_s=2.0, grace_s=1.0)
        assert r['ok'] is False and r['code'] in ('timeout', 'stopped')
        assert 'api/state' in r['warning'] and time.monotonic() - t0 < 10
    finally:
        srv.close()


def test_stop_event_stops_a_running_call():
    ev = threading.Event()
    threading.Timer(1.5, ev.set).start()
    srv = StubRobotServer(job_duration=0.2)
    try:
        t0 = time.monotonic()
        r = run_plugin('follow_color', 'follow_color', {'color': 'red', 'duration_s': 60},
                       params={'poll_s': 0.1}, robot_url=srv.url, frames='synthetic',
                       stop_event=ev, grace_s=1.0)
        assert time.monotonic() - t0 < 12
        assert r['code'] == 'stopped' or (r['ok'] and r['data']['reason'] == 'stopped')
    finally:
        srv.close()


def test_a_crashing_plugin_gives_an_error_result(tmp_path):
    make_plugin(tmp_path, 'boomer', permissions=['camera:read'], code='''
        from robot_plugins import PluginBase

        class Plugin(PluginBase):
            def call(self, tool, args):
                raise RuntimeError('kaboom')
    ''')
    r = run_plugin('boomer', 'echo', {'text': 'x'}, plugins_dir=tmp_path)
    assert r['ok'] is False and r['code'] and 'kaboom' in r['error']
