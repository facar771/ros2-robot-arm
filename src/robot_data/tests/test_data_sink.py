import logging
import math
import threading

import pytest
from data_support import backend_params, fresh_url
from robot_core.ports import NullEventSink
from robot_data.sink import DatabaseEventSink, error_report_for, normalize_url, to_json
from sqlalchemy import select
from sqlalchemy.orm import Session

from robot_data import event_sink_from_env


@pytest.fixture(params=backend_params())
def url(request, tmp_path):
    return fresh_url(request.param, tmp_path)


@pytest.fixture
def sink(url):
    s = DatabaseEventSink(url, 'pytest', {'note': 'x'})
    yield s
    s.close()


def rows(sink, model):
    from robot_data.models import ErrorReport, Event, Run  # noqa: F401
    with Session(sink._engine) as session:
        return session.scalars(select(model).order_by(model.id)).all()


def test_first_event_migrates_the_schema_and_creates_a_run(sink):
    from robot_data.models import Event, Run
    assert sink.run_id is None
    sink.emit('obstacle_added', {'name': 'obj_red_1', 'pose': [0.35, 0.15, 0.03],
                                 'size': [0.07, 0.07, 0.07]})
    (run,) = rows(sink, Run)
    assert run.source == 'pytest' and run.meta == {'note': 'x'} and run.ended_at is None
    (event,) = rows(sink, Event)
    assert event.run_id == run.id and event.kind == 'obstacle_added'
    assert event.payload['pose'] == [0.35, 0.15, 0.03]
    assert event.ts is not None


def test_events_keep_their_order_and_one_run(sink):
    from robot_data.models import Event, Run
    for i in range(5):
        sink.emit('tick', {'i': i})
    assert [e.payload['i'] for e in rows(sink, Event)] == [0, 1, 2, 3, 4]
    assert len(rows(sink, Run)) == 1


def test_close_stamps_the_end_of_the_run_and_is_idempotent(sink):
    from robot_data.models import Run
    sink.emit('tick', {})
    sink.close()
    sink.close()
    sink.emit('tick', {})                     # after close: ignored, no crash
    # a fresh engine, because close() disposed the old one
    from robot_data.sink import make_engine
    with Session(make_engine(sink._url)) as session:
        (run,) = session.scalars(select(Run)).all()
        assert run.ended_at is not None


def test_failed_motion_also_creates_an_error_report(sink):
    from robot_data.models import ErrorReport, Event
    sink.emit('motion_result', {'target': [0] * 6, 'label': 'home', 'ok': True, 'code': 1,
                                'code_name': 'SUCCESS', 'message': ''})
    assert rows(sink, ErrorReport) == []
    sink.emit('motion_result', {'target': [1] * 6, 'label': None, 'ok': False, 'code': -10,
                                'code_name': 'GOAL_IN_COLLISION', 'message': ''})
    (report,) = rows(sink, ErrorReport)
    assert (report.component, report.code) == ('motion', 'GOAL_IN_COLLISION')
    assert report.message == 'GOAL_IN_COLLISION'
    assert report.details['target'] == [1] * 6
    assert report.event_id == rows(sink, Event)[-1].id


def test_error_report_mapping():
    assert error_report_for('command_rejected', {'line': 'foo'})['message'] == 'geçersiz komut: foo'
    assert error_report_for('command_rejected', {'line': 'move', 'reason': 'x'})['message'] == 'x'
    assert error_report_for('selftest_check', {'name': 'a', 'ok': True}) is None
    assert error_report_for('selftest_check', {'name': 'a', 'ok': False, 'detail': 'd'}) == {
        'component': 'selftest', 'code': 'a', 'message': 'd'}
    assert error_report_for('error', {'component': 'perception', 'message': 'm'})[
        'component'] == 'perception'
    assert error_report_for('skill_result', {'skill': 'go_above', 'ok': True}) is None
    assert error_report_for('skill_result', {'skill': 'go_above', 'ok': False,
                                             'code_name': 'NO_IK_SOLUTION', 'message': 'm'}) == {
        'component': 'skill', 'code': 'NO_IK_SOLUTION', 'message': 'm'}
    assert error_report_for('scenario_result', {'name': 'demo_scenario', 'ok': True}) is None
    assert error_report_for('estop', {'reason': 'buton', 'message': 'robot durdu'}) == {
        'component': 'estop', 'code': 'buton', 'message': 'robot durdu'}
    assert error_report_for('estop_reset', {'had_error': False}) is None
    assert error_report_for('scenario_result', {'name': 'demo_scenario', 'ok': False,
                                                'message': '2. adımda durdu'}) == {
        'component': 'scenario', 'code': 'demo_scenario', 'message': '2. adımda durdu'}
    assert error_report_for('obstacle_added', {}) is None


def test_payload_that_is_not_plain_json_is_still_stored(sink):
    from robot_data.models import Event

    class Odd:
        def __str__(self):
            return 'odd!'

    sink.emit('weird', {'nan': math.nan, 'inf': math.inf, 'tuple': (1, 2), 'odd': Odd(),
                        1: 'int key', 'set': {3}})
    (event,) = rows(sink, Event)
    assert event.payload == {'nan': None, 'inf': None, 'tuple': [1, 2], 'odd': 'odd!',
                             '1': 'int key', 'set': [3]}


def test_to_json_handles_numpy_scalars():
    np = pytest.importorskip('numpy')
    assert to_json({'a': np.float32(0.5), 'b': np.int64(3)}) == {'a': 0.5, 'b': 3}


def test_concurrent_emits_all_arrive(sink):
    from robot_data.models import Event

    def work(n):
        for i in range(25):
            sink.emit('tick', {'thread': n, 'i': i})
    sink.emit('tick', {'warmup': True})       # migration + run row happen once, before the race
    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(rows(sink, Event)) == 101 and sink.dropped == 0


# ---- the port's promise: emit never raises, never hammers a dead database ---------------------
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_emit_never_raises_when_the_database_is_unreachable(tmp_path, caplog):
    clock = Clock()
    bad = f'sqlite:///{tmp_path / "missing_dir" / "robot.db"}'
    sink = DatabaseEventSink(bad, 'pytest', retry_after=30.0, clock=clock)
    with caplog.at_level(logging.ERROR, logger='robot_data'):
        sink.emit('tick', {})                 # must not raise
    assert sink.dropped == 1 and 'yazılamadı' in caplog.text
    caplog.clear()
    for _ in range(5):
        sink.emit('tick', {})                 # breaker open: no attempt, no log spam
    assert sink.dropped == 6 and caplog.text == ''
    sink.close()                              # must not raise either


def test_sink_recovers_when_the_database_comes_back(tmp_path):
    from robot_data.models import Event
    clock = Clock()
    folder = tmp_path / 'later'
    sink = DatabaseEventSink(f'sqlite:///{folder / "robot.db"}', 'pytest', retry_after=30.0,
                             clock=clock)
    sink.emit('lost', {})
    folder.mkdir()
    sink.emit('still_lost', {})               # breaker still open
    assert rows_or_none(sink) is None
    clock.now += 31
    sink.emit('kept', {})
    assert [e.kind for e in rows(sink, Event)] == ['kept'] and sink.dropped == 2
    sink.close()


def rows_or_none(sink):
    from robot_data.models import Event
    try:
        return rows(sink, Event)
    except Exception:       # noqa: BLE001 - the file does not exist yet
        return None


def test_emit_survives_a_non_dict_payload(sink):
    from robot_data.models import Event
    sink.emit('odd', 'just a string')
    assert rows(sink, Event)[0].payload == {'value': 'just a string'}


# ---- event_sink_from_env ----------------------------------------------------------------------
def test_without_url_the_sink_is_a_null_sink():
    assert isinstance(event_sink_from_env('x', environ={}), NullEventSink)
    assert isinstance(event_sink_from_env('x', environ={'ROBOT_DB_URL': '  '}), NullEventSink)


def test_with_url_the_sink_is_a_database_sink_and_makes_no_connection(tmp_path):
    db = tmp_path / 'never_created.db'
    sink = event_sink_from_env('x', environ={'ROBOT_DB_URL': f'sqlite:///{db}'})
    assert isinstance(sink, DatabaseEventSink)
    assert not db.exists()                    # lazy: nothing touched until the first event
    sink.close()


def test_garbage_url_degrades_to_null_sink_instead_of_raising(caplog):
    with caplog.at_level(logging.ERROR, logger='robot_data'):
        sink = event_sink_from_env('x', environ={'ROBOT_DB_URL': 'not a url'})
    assert isinstance(sink, NullEventSink) and 'ROBOT_DB_URL' in caplog.text


def test_postgresql_urls_use_the_psycopg3_driver():
    assert normalize_url('postgresql://u:p@h/db') == 'postgresql+psycopg://u:p@h/db'
    assert normalize_url('postgres://u:p@h/db') == 'postgresql+psycopg://u:p@h/db'
    assert normalize_url('postgresql+psycopg://u:p@h/db') == 'postgresql+psycopg://u:p@h/db'
    assert normalize_url('sqlite:///x.db') == 'sqlite:///x.db'


def test_robot_core_does_not_depend_on_the_data_layer():
    import pathlib
    core = pathlib.Path(__file__).resolve().parents[2] / 'robot_core' / 'robot_core'
    assert not [p for p in core.glob('*.py') if 'robot_data' in p.read_text()
                or 'sqlalchemy' in p.read_text().lower()]
