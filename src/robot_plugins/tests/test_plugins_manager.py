"""PluginManager with REAL subprocesses: lifecycle, permissions, e-stop, crashes."""
import time

import pytest
from plugins_support import PLUGINS, StubRobotServer, make_plugin

from robot_plugins import FakeRobotApi, ManagerError, PluginManager

SLOW_CODE = '''
    from robot_plugins import PluginBase

    class Plugin(PluginBase):
        def call(self, tool, args):
            if tool == 'echo':
                return {'echo': args['text']}
            if tool == 'wait_for_stop':
                self.ctx.log('waiting')
                stopped = self.ctx.stop.wait(30)
                return {'stopped': stopped}
            if tool == 'spin_ignoring_stop':
                while True:
                    pass
            if tool == 'camera':
                return {'frame': self.ctx.camera.latest_frame() is not None}
            if tool == 'move':
                return self.ctx.robot.submit_job('home')
            if tool == 'estop':
                return self.ctx.robot.stop('from plugin')
            if tool == 'die':
                import os
                os._exit(3)
            if tool == 'boom':
                raise RuntimeError('kaboom')
            if tool == 'noisy':
                print('stdout noise that must not break the protocol')
                return {'ok': True}
'''


def tool(name, props=None, required=()):
    return {'name': name, 'description': f'{name} aracı',
            'input_schema': {'type': 'object', 'properties': props or {},
                             'required': list(required), 'additionalProperties': False}}


TOOLS = [tool('echo', {'text': {'type': 'string'}}, ['text']), tool('wait_for_stop'),
         tool('spin_ignoring_stop'), tool('camera'), tool('move'), tool('estop'), tool('die'),
         tool('boom'), tool('noisy')]


@pytest.fixture()
def env(tmp_path):
    robot = StubRobotServer(job_duration=0.2)
    make_plugin(tmp_path, 'lab', code=SLOW_CODE, tools=TOOLS, permissions=['camera:read'])
    make_plugin(tmp_path, 'driver', code=SLOW_CODE, tools=TOOLS, permissions=['robot:jobs'])
    make_plugin(tmp_path, 'bare', code=SLOW_CODE, tools=TOOLS)
    mgr = PluginManager(tmp_path, robot_api=robot.fake, robot_url=robot.url, frames='synthetic',
                        grace_s=1.0)
    yield mgr, robot, tmp_path
    mgr.shutdown()
    robot.close()


def test_discovery_lists_valid_and_invalid_plugins(env):
    mgr, _, root = env
    (root / 'broken').mkdir()
    (root / 'broken' / 'package.yaml').write_text('name: broken\nversion: nope\n')
    (root / '.hidden').mkdir()
    mgr.discover()
    states = {s['name']: s['status'] for s in mgr.list()}
    assert states == {'bare': 'stopped', 'broken': 'invalid', 'driver': 'stopped', 'lab': 'stopped'}
    assert 'description' in next(s for s in mgr.list() if s['name'] == 'broken')['error']
    with pytest.raises(ManagerError):
        mgr.enable('broken')
    with pytest.raises(ManagerError):
        mgr.enable('missing')


def test_shipped_plugins_are_discovered_from_the_repository():
    mgr = PluginManager(PLUGINS)
    assert {'find_object', 'follow_color', 'scan_extent'} <= set(mgr.names())
    assert all(s['status'] == 'stopped' for s in mgr.list())


def test_enable_call_disable_cycle_with_a_real_process(env):
    mgr, _, _ = env
    st = mgr.enable('lab')
    assert st['status'] == 'running' and st['enabled'] and st['pid']
    assert mgr.call('lab', 'echo', {'text': 'merhaba'}) == {'ok': True, 'data': {'echo': 'merhaba'}}
    assert mgr.enable('lab')['pid'] == st['pid']             # idempotent
    st = mgr.disable('lab')
    assert st['status'] == 'stopped' and st['pid'] is None
    assert mgr.call('lab', 'echo', {'text': 'x'})['code'] == 'not_running'


def test_arguments_are_validated_before_they_reach_the_plugin(env):
    mgr, _, _ = env
    mgr.enable('lab')
    assert mgr.call('lab', 'echo', {})['code'] == 'bad_arguments'
    assert mgr.call('lab', 'echo', {'text': 5})['code'] == 'bad_arguments'
    assert mgr.call('lab', 'echo', {'text': 'a', 'x': 1})['code'] == 'bad_arguments'
    assert mgr.call('lab', 'nope')['code'] == 'unknown_tool'


def test_permissions_are_enforced_inside_the_plugin_process(env):
    mgr, robot, _ = env
    mgr.enable('lab')
    mgr.enable('driver')
    mgr.enable('bare')
    assert mgr.call('lab', 'camera')['data'] == {'frame': True}
    assert mgr.call('driver', 'camera')['code'] == 'permission_denied'
    assert mgr.call('lab', 'move')['code'] == 'permission_denied'
    assert mgr.call('bare', 'move')['code'] == 'permission_denied'
    assert robot.fake.history == []                               # nothing reached the robot
    assert mgr.call('driver', 'move')['data']['state'] == 'running'
    assert robot.fake.history == ['home']


def test_every_plugin_can_hit_the_emergency_stop_without_permissions(env):
    mgr, robot, _ = env
    mgr.enable('bare')
    assert mgr.call('bare', 'estop')['ok']
    assert robot.fake.latched and robot.fake.stops == ['from plugin']


def test_plugin_exceptions_and_stdout_noise_do_not_break_the_manager(env):
    mgr, _, _ = env
    mgr.enable('lab')
    boom = mgr.call('lab', 'boom')
    assert boom['code'] == 'exception' and 'kaboom' in boom['error']
    assert mgr.call('lab', 'noisy')['ok'] is True
    assert mgr.call('lab', 'echo', {'text': 'hâlâ yaşıyor'})['ok'] is True
    assert any('kaboom' in line or 'RuntimeError' in line for line in mgr.logs('lab'))


def test_a_crashing_plugin_is_reported_and_its_pending_call_returns(env):
    mgr, _, _ = env
    mgr.enable('lab')
    res = mgr.call('lab', 'die', timeout=10)
    assert res['ok'] is False
    time.sleep(0.3)
    st = mgr.status('lab')
    assert st['status'] == 'crashed' and '3' in st['error']
    assert mgr.enable('lab')['status'] == 'running'               # can be started again


def test_stop_token_ends_a_waiting_tool_on_disable(env):
    mgr, _, _ = env
    mgr.enable('lab')
    import threading
    out = {}
    t = threading.Thread(target=lambda: out.update(mgr.call('lab', 'wait_for_stop', timeout=20)))
    t.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    mgr.disable('lab')
    t.join(5)
    assert not t.is_alive() and time.monotonic() - t0 < 3
    assert out.get('data') == {'stopped': True} or out.get('code') == 'stopped'


def test_a_plugin_that_ignores_the_stop_token_is_killed_after_the_grace_period(env):
    mgr, _, _ = env
    mgr.enable('lab')
    import threading
    threading.Thread(target=lambda: mgr.call('lab', 'spin_ignoring_stop', timeout=30),
                     daemon=True).start()
    time.sleep(0.5)
    pid = mgr.status('lab')['pid']
    t0 = time.monotonic()
    mgr.disable('lab')
    assert time.monotonic() - t0 < 6
    import os
    with pytest.raises(ProcessLookupError):
        for _ in range(30):                                   # the process must be gone
            os.kill(pid, 0)
            time.sleep(0.1)


def test_emergency_stop_stops_all_plugins_and_latches_the_robot(env):
    mgr, robot, _ = env
    mgr.enable('lab')
    mgr.enable('driver')
    out = mgr.emergency_stop('düğme')
    assert out['plugins_stopped'] == ['driver', 'lab']
    assert out['robot']['latched'] and robot.fake.latched and robot.fake.stops == ['düğme']
    assert all(s['status'] == 'stopped' for s in mgr.list())
    with pytest.raises(ManagerError) as exc:
        mgr.enable('lab')
    assert exc.value.code == 'estop'                            # no restart until a human clears it
    mgr.clear_estop()
    assert mgr.enable('lab')['status'] == 'running'


def test_emergency_stop_still_stops_plugins_when_the_robot_is_unreachable(tmp_path):
    from robot_plugins import HttpRobotApi
    make_plugin(tmp_path, 'lab', code=SLOW_CODE, tools=TOOLS)
    mgr = PluginManager(tmp_path, robot_api=HttpRobotApi('http://127.0.0.1:9', timeout=0.5),
                        grace_s=1.0)
    try:
        mgr.enable('lab')
        out = mgr.emergency_stop()
        assert out['plugins_stopped'] == ['lab']
        assert out['robot']['ok'] is False and 'erişilemedi' in out['robot']['error']
    finally:
        mgr.shutdown()


def test_watchdog_stops_plugins_when_the_robot_reports_an_estop(env):
    mgr, robot, _ = env
    mgr.enable('driver')
    mgr.watch_robot(interval=0.1)
    assert mgr.status('driver')['status'] == 'running'
    robot.fake.stop('operatör')                                  # pressed by somebody else
    for _ in range(40):
        if mgr.status('driver')['status'] != 'running':
            break
        time.sleep(0.1)
    assert mgr.status('driver')['status'] == 'stopped'
    robot.fake.reset()                                           # a human resets the robot
    for _ in range(40):
        try:
            mgr.enable('driver')
            break
        except ManagerError:
            time.sleep(0.1)
    assert mgr.status('driver')['status'] == 'running'


def test_state_file_restores_enabled_plugins(env, tmp_path_factory):
    mgr, robot, root = env
    state = tmp_path_factory.mktemp('state') / 'state.json'
    a = PluginManager(root, robot_api=robot.fake, robot_url=robot.url, state_file=state,
                      grace_s=1.0)
    try:
        a.enable('lab')
        assert state.exists()
    finally:
        a.shutdown()
        a._persist()                      # shutdown writes an empty set; write it as 'was running'
    state.write_text('{"lab": {"params": {}}}')
    b = PluginManager(root, robot_api=robot.fake, robot_url=robot.url, state_file=state,
                      grace_s=1.0)
    try:
        assert b.restore() == ['lab'] and b.status('lab')['status'] == 'running'
    finally:
        b.shutdown()


def test_parameters_are_validated_on_enable(tmp_path):
    make_plugin(tmp_path, 'tuned', tools=TOOLS[:1], parameters={
        'type': 'object', 'properties': {'rate': {'type': 'number', 'minimum': 1, 'default': 5}}})
    mgr = PluginManager(tmp_path, grace_s=1.0)
    try:
        with pytest.raises(ManagerError) as exc:
            mgr.enable('tuned', {'rate': 0.1})
        assert exc.value.code == 'bad_parameters'
        assert mgr.enable('tuned', {'rate': 3})['params'] == {'rate': 3}
    finally:
        mgr.shutdown()


def test_the_manager_runs_the_shipped_plugins_end_to_end(tmp_path):
    robot = FakeRobotApi(job_duration=0.1)
    mgr = PluginManager(PLUGINS, robot_api=robot, frames='synthetic', grace_s=2.0)
    try:
        mgr.enable('find_object')
        out = mgr.call('find_object', 'find_object', {'color': 'red'})
        assert out['ok'] and out['data']['objects'][0]['shape'] == 'box'
    finally:
        mgr.shutdown()


def test_scan_extent_runs_in_its_own_process_with_the_synthetic_panner():
    mgr = PluginManager(PLUGINS, panner='synthetic', grace_s=2.0)
    try:
        mgr.enable('scan_extent')
        out = mgr.call('scan_extent', 'scan_extent', {'yaw_min_deg': -100, 'yaw_max_deg': 100,
                                                      'steps': 9}, timeout=60)
        assert out['ok'] and out['data']['count'] == 6 and out['data']['views'] == 9
        assert mgr.call('scan_extent', 'scan_extent', {'steps': 99})['code'] == 'bad_arguments'
    finally:
        mgr.shutdown()
