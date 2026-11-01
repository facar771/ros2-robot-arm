import time

import pytest

from control_hub.hub_core import BusyError, EstopLatchedError, UnknownScenarioError
from control_hub.hub_core.scenarios import parse_scenarios
from tests.conftest import make_hub


def kinds(hub):
    return [e.kind for e in hub.events.since(0)]


def test_scenario_runs_to_completion(hub):
    hub.run_scenario('demo_tour')
    assert hub.runner.wait_idle(10)
    last = hub.runner.status()['last']
    assert last['ok'] and not last['cancelled'] and '4 adım' in last['message']
    assert hub.backend.history == ['home', 'above green 0.25', 'above red 0.3', 'home']
    assert kinds(hub).count('step_started') == 4 and kinds(hub)[-1] == 'scenario_finished'


def test_failing_step_stops_scenario(hub):
    hub.backend.inject_failure('red', 'erişilemeyen hedef')
    hub.run_scenario('demo_tour')
    assert hub.runner.wait_idle(10)
    last = hub.runner.status()['last']
    assert not last['ok'] and 'adım 3/4' in last['message'] and 'erişilemeyen' in last['message']
    assert hub.backend.history == ['home', 'above green 0.25', 'above red 0.3']   # 4. adım yok


def test_only_one_scenario_at_a_time(scenarios, ui):
    hub = make_hub(scenarios, ui, scale=0.5)
    try:
        hub.run_scenario('demo_tour')
        with pytest.raises(BusyError):
            hub.run_scenario('home')
        assert hub.runner.status()['running']
        hub.cancel_scenario()
        assert hub.runner.wait_idle(5)
    finally:
        hub.close()


def test_unknown_scenario(hub):
    with pytest.raises(UnknownScenarioError):
        hub.run_scenario('yok')


def test_cancel_interrupts_motion_and_skips_rest(scenarios, ui):
    hub = make_hub(scenarios, ui, scale=1.0)           # gerçek hızda: hareket ~1 sn
    try:
        hub.run_scenario('demo_tour')
        time.sleep(0.15)
        t0 = time.monotonic()
        assert hub.cancel_scenario()
        assert hub.runner.wait_idle(3)
        assert time.monotonic() - t0 < 1.0              # hareket bitmesi beklenmedi
        last = hub.runner.status()['last']
        assert last['cancelled'] and not last['ok']
        assert len(hub.backend.history) == 1
    finally:
        hub.close()


def test_estop_cuts_motion_latches_and_blocks(scenarios, ui):
    hub = make_hub(scenarios, ui, scale=1.0)
    try:
        hub.run_scenario('demo_tour')
        time.sleep(0.15)
        t0 = time.monotonic()
        hub.estop('test')
        assert hub.runner.wait_idle(3) and time.monotonic() - t0 < 1.0
        assert hub.estopped and hub.snapshot()['estop']['latched']
        assert hub.runner.status()['last']['cancelled']
        with pytest.raises(EstopLatchedError):
            hub.run_scenario('home')
        hub.estop('test')                                # idempotent
        with pytest.raises(EstopLatchedError):
            hub.run_scenario('home')
        hub.reset_estop('test')
        assert not hub.estopped
        hub.run_scenario('home')
        assert hub.runner.wait_idle(5) and hub.runner.status()['last']['ok']
    finally:
        hub.close()


def test_estop_with_nothing_running_still_latches(hub):
    hub.estop('test')
    assert hub.estopped
    assert 'estop' in kinds(hub)


def test_estop_survives_failing_backend_and_listener(hub):
    def boom():
        raise RuntimeError('x')
    called = []
    hub.backend.emergency_stop = boom                    # type: ignore[method-assign]
    hub.add_estop_listener(boom)
    hub.add_estop_listener(lambda: called.append(1))
    hub.estop('test')
    assert hub.estopped and called == [1]
    assert kinds(hub).count('estop_step_failed') == 2


def test_wait_step_is_cancellable(scenarios, ui):
    sc = parse_scenarios({'scenarios': [{'id': 'w', 'label': 'W', 'steps': [{'wait': 30}]}]})
    hub = make_hub(sc, ui)
    try:
        hub.run_scenario('w')
        time.sleep(0.1)
        hub.cancel_scenario()
        assert hub.runner.wait_idle(2)
    finally:
        hub.close()


def test_backend_exception_does_not_kill_runner(hub):
    def boom(command, cancel):
        raise RuntimeError('beklenmedik')
    hub.backend.execute = boom                           # type: ignore[method-assign]
    hub.run_scenario('home')
    assert hub.runner.wait_idle(5)
    last = hub.runner.status()['last']
    assert not last['ok'] and 'iç hata' in last['message']
    hub.backend.execute = type(hub.backend).execute.__get__(hub.backend)   # type: ignore
    hub.run_scenario('home')                              # koşucu hâlâ kullanılabilir
    assert hub.runner.wait_idle(5) and hub.runner.status()['last']['ok']
