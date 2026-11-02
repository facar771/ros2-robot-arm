"""The three shipped plugins, run in-process on the synthetic frame of workspace.sdf."""
import math
import threading
import time

import numpy as np
import pytest
from plugins_support import PLUGINS
from robot_plugins.frames import SyntheticFrameSource
from robot_plugins.loader import load_plugin_class
from robot_plugins.manifest import load_manifest

from robot_plugins import FakeRobotApi, PluginContext


@pytest.fixture(scope='module')
def frames():
    return SyntheticFrameSource()


def make(name, frames, api=None, params=None, token=None):
    m = load_manifest(PLUGINS / name)
    ctx = PluginContext(name, m.resolve_params(params), frozenset(m.granted), frames,
                        api or FakeRobotApi(), lambda lv, msg: None, token)
    return m, load_plugin_class(m)(ctx), ctx


def call(manifest, plugin, tool, args):
    """What the runner does: defaults + validation from the tool's schema, then the call."""
    from robot_plugins.schema import apply_defaults, validate
    schema = manifest.tool(tool).input_schema
    full = apply_defaults(args, schema)
    validate(full, schema)
    return plugin.call(tool, full)


def truth(frames):
    return {s.name.split('_')[0]: s for s in frames.workspace.shapes}


def test_find_object_returns_the_world_file_geometry(frames):
    m, plugin, _ = make('find_object', frames)
    shapes = truth(frames)
    out = call(m, plugin, 'find_object', {'color': 'any'})
    assert out['ok'] and out['count'] == 3
    by = {o['color']: o for o in out['objects']}
    for color, want in (('red', 'box'), ('green', 'cylinder'), ('blue', 'sphere')):
        assert by[color]['shape'] == want
        assert math.dist(by[color]['center'], shapes[color].center) < 0.01
    assert by['red']['grasps'] and by['red']['edge_points']['top_corner_0']
    assert all(len(o['pixel_center']) == 2 for o in out['objects'])


def test_find_object_single_colour_and_missing_colour(frames):
    m, plugin, _ = make('find_object', frames)
    out = call(m, plugin, 'find_object', {'color': 'green'})
    assert [o['color'] for o in out['objects']] == ['green']
    from robot_plugins.frames import NoFrames
    m, blind, _ = make('find_object', NoFrames())
    assert call(m, blind, 'find_object', {'color': 'red'})['ok'] is False


def test_find_object_respects_the_gripper_aperture_parameter(frames):
    m, narrow, _ = make('find_object', frames, params={'max_aperture': 0.05})
    out = call(m, narrow, 'find_object', {'color': 'blue'})['objects'][0]
    assert not any(g['fits'] for g in out['grasps'])         # the ball is 8 cm wide


def test_scan_extent_lists_everything_in_view(frames):
    m, plugin, _ = make('scan_extent', frames)
    out = call(m, plugin, 'scan_extent', {})
    assert out['ok'] and out['count'] == 3 and out['views'] == 1


def test_follow_color_sends_one_job_for_a_static_object_then_stops_on_duration(frames):
    api = FakeRobotApi(job_duration=0.05)
    m, plugin, _ = make('follow_color', frames, api, {'poll_s': 0.1})
    out = call(m, plugin, 'follow_color', {'color': 'red', 'height_m': 0.3, 'duration_s': 1.0,
                                       'min_move_m': 0.03})
    assert out['ok'] and out['reason'] == 'duration' and out['jobs'] == 1
    assert api.history == ['above red 0.30']
    assert math.dist(out['last_target'], truth(frames)['red'].center) < 0.06   # top centre


def test_follow_color_follows_a_moving_object(frames):
    class MovingFrames(SyntheticFrameSource):
        def __init__(self):
            self.workspace = frames.workspace
            self.t0 = time.monotonic()

        def latest(self):
            from dataclasses import replace

            from robot_vision.synth import render
            dy = 0.12 * (time.monotonic() - self.t0)
            shapes = [replace(s, center=(s.center[0], s.center[1] + dy, s.center[2]))
                      if s.name == 'red_box' else s for s in self.workspace.shapes]
            return render(self.workspace.camera, shapes)

    api = FakeRobotApi(job_duration=0.05)
    m, plugin, _ = make('follow_color', MovingFrames(), api, {'poll_s': 0.2})
    out = call(m, plugin, 'follow_color', {'color': 'red', 'duration_s': 2.0, 'min_move_m': 0.05})
    assert out['reason'] == 'duration' and out['jobs'] >= 3
    assert set(api.history) == {'above red 0.30'}


def test_follow_color_gives_up_immediately_on_an_emergency_stop(frames):
    api = FakeRobotApi(job_duration=5)
    m, plugin, _ = make('follow_color', frames, api, {'poll_s': 0.1})
    threading.Timer(0.3, api.stop, args=('test',)).start()
    out = call(m, plugin, 'follow_color', {'color': 'red', 'duration_s': 10})
    assert out['reason'] == 'estop' and out['ok'] is False
    n = len(api.history)
    out2 = call(m, plugin, 'follow_color', {'color': 'red', 'duration_s': 10})   # still latched
    assert out2['reason'] == 'estop' and len(api.history) == n               # no retry, no new job


def test_follow_color_stops_when_the_stop_token_is_set(frames):
    token_holder = {}
    api = FakeRobotApi(job_duration=30)
    from robot_plugins import StopToken
    token = token_holder['t'] = StopToken()
    m, plugin, _ = make('follow_color', frames, api, {'poll_s': 0.1}, token)
    threading.Timer(0.3, token.set).start()
    t0 = time.monotonic()
    out = call(m, plugin, 'follow_color', {'color': 'red', 'duration_s': 60})
    assert out['reason'] == 'stopped' and time.monotonic() - t0 < 3.0
    assert api.job('j1')['state'] == 'cancelled'            # the running job was cancelled


def test_follow_color_reports_a_refused_job_without_retrying(frames):
    class Refusing(FakeRobotApi):
        def submit_job(self, command, timeout_s=None):
            from robot_plugins import RobotRefused
            raise RobotRefused(422, 'hedef erişilemez')

    m, plugin, _ = make('follow_color', frames, Refusing(), {'poll_s': 0.1})
    out = call(m, plugin, 'follow_color', {'color': 'blue', 'duration_s': 5})
    assert out['reason'] == 'refused' and 'erişilemez' in out['error'] and out['jobs'] == 0


def test_follow_color_ends_when_the_object_disappears(frames):
    from robot_plugins.frames import NoFrames
    m, plugin, _ = make('follow_color', NoFrames(), None, {'poll_s': 0.1})
    t0 = time.monotonic()
    out = call(m, plugin, 'follow_color', {'color': 'red', 'duration_s': 30})
    assert out['reason'] == 'not_found' and time.monotonic() - t0 < 8
    assert np.isfinite(t0)


# ---- scan_extent v0.2: the camera turns -------------------------------------------------------
def _scan_ctx(permissions, panner):
    from robot_plugins.frames import NoFrames
    m = load_manifest(PLUGINS / 'scan_extent')
    ctx = PluginContext('scan_extent', m.resolve_params(None), frozenset(permissions), NoFrames(),
                        FakeRobotApi(), lambda lv, msg: None, panner=panner)
    return m, load_plugin_class(m)(ctx), ctx


def test_scan_extent_sweeps_the_ring_and_lists_all_six_objects():
    from robot_vision.scan import SyntheticPanner, ring_world
    cam, shapes = ring_world()
    perms = {'camera:read', 'camera:pan', 'robot:stop'}
    m, plugin, _ = _scan_ctx(perms, SyntheticPanner(cam, shapes))
    out = call(m, plugin, 'scan_extent', {'yaw_min_deg': -100, 'yaw_max_deg': 100, 'steps': 9})
    assert out['ok'] and out['swept'] and out['views'] == 9 and out['count'] == 6
    assert {o['color'] for o in out['objects']} == {'red', 'green', 'blue'}
    truth = {s.name: s for s in shapes}
    for o in out['objects']:
        nearest = min(truth.values(), key=lambda s: math.dist(s.center, o['center']))
        assert math.dist(nearest.center, o['center']) < 0.01 and o['seen_in_views'] >= 1


def test_scan_extent_without_the_pan_permission_cannot_turn_the_camera():
    from robot_vision.scan import SyntheticPanner, ring_world

    from robot_plugins import PermissionDenied
    cam, shapes = ring_world()
    _, _, ctx = _scan_ctx({'camera:read', 'robot:stop'}, SyntheticPanner(cam, shapes))
    assert not ctx.camera.pan.available
    with pytest.raises(PermissionDenied, match='camera:pan'):
        ctx.camera.pan.move_to(0.3)


def test_scan_extent_falls_back_to_one_view_when_the_camera_cannot_turn(frames):
    from robot_plugins.panning import NoPanner
    m, plugin, _ = _scan_ctx({'camera:read', 'camera:pan', 'robot:stop'}, NoPanner())
    plugin.ctx.camera._source = frames                       # a fixed camera with frames
    out = call(m, plugin, 'scan_extent', {})
    assert out['ok'] and out['views'] == 1 and out['swept'] is False
    assert 'döndürülemiyor' in out['note']
    assert out['count'] == 3


def test_scan_extent_stops_midway_when_the_stop_token_is_set():
    from robot_vision.scan import SyntheticPanner, ring_world

    from robot_plugins import Stopped, StopToken
    cam, shapes = ring_world()
    token = StopToken()
    token.set()
    panner = SyntheticPanner(cam, shapes)
    m, plugin, ctx = _scan_ctx({'camera:read', 'camera:pan', 'robot:stop'}, panner)
    ctx.stop.set()
    with pytest.raises(Stopped):
        call(m, plugin, 'scan_extent', {'steps': 9})
    assert panner.moves == []                                 # it never even moved
