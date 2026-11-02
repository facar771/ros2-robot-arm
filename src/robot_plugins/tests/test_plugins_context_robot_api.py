import threading
import time

import pytest
from plugins_support import StubRobotServer
from robot_plugins.frames import NoFrames, SyntheticFrameSource, make_frame_source
from robot_plugins.robot_api import wait_for_job

from robot_plugins import (
    FakeRobotApi,
    HttpRobotApi,
    PermissionDenied,
    PluginContext,
    RobotApiError,
    RobotBusy,
    RobotLocked,
    RobotRefused,
    Stopped,
    StopToken,
)


def ctx(perms, api=None, frames=None, token=None):
    return PluginContext('t', {}, frozenset(perms), frames or NoFrames(), api or FakeRobotApi(),
                         lambda level, msg: None, token)


# ---- permissions -------------------------------------------------------------------------------
def test_camera_needs_camera_read():
    with pytest.raises(PermissionDenied, match='camera:read'):
        ctx({'robot:stop'}).camera.latest_frame()
    assert ctx({'camera:read'}, frames=SyntheticFrameSource()).camera.latest_frame() is not None


@pytest.mark.parametrize('call', [
    lambda r: r.submit_job('home'), lambda r: r.state(), lambda r: r.job('j1'),
    lambda r: r.cancel('j1'), lambda r: r.wait_for_job('j1')])
def test_every_robot_call_but_stop_needs_robot_jobs(call):
    with pytest.raises(PermissionDenied, match='robot:jobs'):
        call(ctx({'camera:read', 'robot:stop'}).robot)


def test_emergency_stop_works_with_no_permissions_at_all():
    api = FakeRobotApi()
    result = ctx(set(), api).robot.stop('test')
    assert result['latched'] and api.stops == ['test']


def test_plugins_cannot_reset_the_emergency_stop():
    robot = ctx({'robot:jobs'}).robot
    assert not hasattr(robot, 'reset') and not hasattr(robot, '_api_reset')


def test_stop_token_semantics():
    token = StopToken()
    token.check()
    assert not token.is_set() and token.wait(0.01) is False
    threading.Timer(0.05, token.set).start()
    assert token.wait(2.0) is True
    with pytest.raises(Stopped):
        token.check()


def test_frame_source_specs():
    assert isinstance(make_frame_source('none'), NoFrames)
    assert isinstance(make_frame_source('synthetic'), SyntheticFrameSource)
    with pytest.raises(ValueError):
        make_frame_source('webcam')


# ---- FakeRobotApi follows the v1 contract ------------------------------------------------------
def test_fake_robot_one_job_at_a_time_and_validation():
    api = FakeRobotApi(job_duration=0.3)
    job = api.submit_job('above red 0.30')
    assert job['state'] == 'running' and job['id'] == 'j1'
    with pytest.raises(RobotBusy) as busy:
        api.submit_job('home')
    assert busy.value.body['job_id'] == 'j1'
    for bad in ('', 'goto 1 2 3', 'above', 'above red 0.9', 'above red x', 'rm -rf /'):
        with pytest.raises(RobotRefused):
            api.submit_job(bad)
    time.sleep(0.35)
    assert api.job('j1')['state'] == 'done' and api.state()['state'] == 'idle'
    assert api.submit_job('home')['id'] == 'j2'


def test_fake_robot_stop_latches_cancels_and_refuses_new_jobs():
    api = FakeRobotApi(job_duration=5)
    api.submit_job('home')
    api.stop('x')
    assert api.job('j1')['state'] == 'cancelled' and api.job('j1')['cancelled_by'] == 'estop'
    assert api.state()['state'] == 'estopped'
    with pytest.raises(RobotLocked):
        api.submit_job('home')
    api.reset()
    assert api.submit_job('home')['state'] == 'running'


def test_wait_for_job_cancels_when_the_stop_token_is_set():
    api, token = FakeRobotApi(job_duration=30), StopToken()
    job = api.submit_job('demo_scenario')
    threading.Timer(0.1, token.set).start()
    doc = wait_for_job(api, job['id'], stop_token=token, poll=0.02)
    assert doc['state'] == 'cancelled'


# ---- HttpRobotApi against a stub server that speaks v1 -----------------------------------------
@pytest.fixture()
def stub():
    server = StubRobotServer(job_duration=0.2)
    yield server
    server.close()


def test_http_client_round_trip_and_status_code_mapping(stub):
    api = HttpRobotApi(stub.url)
    assert api.state()['state'] == 'idle'
    job = api.submit_job('above green 0.25', timeout_s=30)
    assert job['state'] == 'running'
    with pytest.raises(RobotBusy):
        api.submit_job('home')
    assert wait_for_job(api, job['id'], poll=0.05)['state'] == 'done'
    with pytest.raises(RobotRefused):
        api.submit_job('goto 1 2 3')
    api.stop('http test')
    assert stub.fake.stops == ['http test']
    with pytest.raises(RobotLocked):
        api.submit_job('home')
    with pytest.raises(RobotApiError) as err:
        api.job('j99')
    assert err.value.status == 404


def test_http_client_reports_an_unreachable_robot_as_status_zero():
    with pytest.raises(RobotApiError) as err:
        HttpRobotApi('http://127.0.0.1:9', timeout=0.5).state()
    assert err.value.status == 0
