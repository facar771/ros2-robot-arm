"""P2 koşucu birimi: SAHTE saat + SAHTE hareket arka ucu (iş parçacığı yok, advance() elle çağrılır)."""
import pytest

from control_hub.hub_core import EventLog
from control_hub.hub_core.program_runner import MoveRefused, ProgramRunner
from control_hub.hub_core.program_store import validate_program
from control_hub.hub_core.runner import BusyError, EstopLatchedError


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeMover:
    """Her ``start`` bir iş açar; ``finish`` ile elle bitirilir."""
    label, measured = 'sahte', False

    def __init__(self):
        self.started, self.jobs, self.calls = [], {}, []
        self.refuse = self.boom = None
        self.fail_with = None

    def check(self, body):
        return {'ok': True, 'violations': []}

    def start(self, body):
        if self.refuse:
            raise MoveRefused(409, *self.refuse)
        if self.boom:
            raise self.boom
        self.started.append(body)
        jid = f'j{len(self.started)}'
        self.jobs[jid] = {'state': 'running', 'message': '', 'error': None}
        return jid

    def status(self, jid):
        return self.jobs[jid]

    def finish(self, state='done', message='', error=None):
        jid = f'j{len(self.started)}'
        self.jobs[jid] = {'state': state, 'message': message, 'error': error}

    def pause(self):
        self.calls.append('pause')

    def resume(self):
        self.calls.append('resume')

    def stop(self):
        self.calls.append('stop')

    def pose(self):
        return None


def program(steps, waypoints=None):
    clean, errs = validate_program({'version': 1, 'name': 'T', 'waypoints': waypoints or [], 'steps': steps})
    assert not errs, errs
    return clean


MJ = {'kind': 'move_j', 'joints_deg': [1, 2, 3]}
ML = {'kind': 'move_l', 'pos_m': [0.3, 0, 0.4], 'rpy_deg': [0, 0, 0]}


@pytest.fixture
def env():
    clock, mover, ev = Clock(), FakeMover(), EventLog()
    state = {'estop': False}
    r = ProgramRunner(mover, ev, is_estopped=lambda: state['estop'], clock=clock, threaded=False)
    return r, mover, clock, state, ev


def tick(r, n=1):
    for _ in range(n):
        r.advance()


def wait_alive(r, clock, seconds):
    """Saati ileri alır; tarayıcı bağlıymış gibi her saniye kalp atışı gönderir."""
    left = seconds
    while left > 1e-9:
        dt = min(1.0, left)
        clock.t += dt
        left -= dt
        r.heartbeat()
        r.advance()


def test_steps_run_in_order_and_finish(env):
    r, m, clock, _, ev = env
    r.start(program([MJ, {'kind': 'wait', 's': 2}, ML, {'kind': 'tool', 'open_pct': 30}]), 40)
    tick(r)
    assert m.started == [{'type': 'joint', 'joints_deg': [1.0, 2.0, 3.0], 'speed_pct': 40.0}]
    assert r.status()['line'] == 0 and r.status()['state'] == 'running'
    tick(r, 3)
    assert len(m.started) == 1                                  # iş bitmedi: sıradaki adıma geçmez
    m.finish()
    tick(r, 2)                                                  # bitiş + bekleme başlar
    assert r.status()['line'] == 1 and len(m.started) == 1
    clock.t += 1.9
    tick(r)
    assert r.status()['line'] == 1                              # 2 sn dolmadı
    clock.t += 0.2
    tick(r, 2)
    assert m.started[1]['type'] == 'linear' and r.status()['line'] == 2
    m.finish()
    tick(r, 2)
    assert m.started[2] == {'type': 'tool', 'open_pct': 30.0, 'speed_pct': 40.0}
    m.finish()
    tick(r, 2)
    st = r.status()
    assert st['state'] == 'finished' and st['done'] == 4 and not st['running']
    kinds = [e.kind for e in ev.since(0)]
    assert kinds[0] == 'program_started' and kinds[-1] == 'program_finished'


def test_loop_expands_and_reports_iteration(env):
    r, m, *_ = env
    r.start(program([{'kind': 'loop', 'count': 2, 'steps': [MJ, {'kind': 'tool', 'open_pct': 0}]}]), 10)
    seen = []
    for _ in range(40):
        tick(r)
        st = r.status()
        if st['state'] == 'finished':
            break
        if m.started and st['line'] is not None and (st['line'], st['sub'], st['iteration']) not in seen:
            seen.append((st['line'], st['sub'], st['iteration']))
        m.finish()
    assert seen == [(0, 0, 1), (0, 1, 1), (0, 0, 2), (0, 1, 2)] and len(m.started) == 4


def test_pause_mid_move_resumes_the_same_job_and_wait_keeps_its_remaining_time(env):
    r, m, clock, *_ = env
    r.start(program([MJ, {'kind': 'wait', 's': 10}]), 30)
    tick(r)
    assert r.pause() and m.calls == ['pause'] and r.status()['state'] == 'paused'
    tick(r, 5)
    assert len(m.started) == 1                                  # duraklayınca ilerlemez
    r.resume()
    assert m.calls == ['pause', 'resume'] and r.status()['state'] == 'running'
    m.finish()
    tick(r, 2)                                                  # bekleme başladı (10 sn)
    wait_alive(r, clock, 4)
    assert r.pause()
    clock.t += 3600                                             # duraklamada geçen süre sayılmaz
    r.resume()
    wait_alive(r, clock, 5.9)
    assert r.status()['state'] == 'running'                     # 6 sn kalmıştı: henüz bitmedi
    wait_alive(r, clock, 0.2)
    tick(r, 2)
    assert r.status()['state'] == 'finished'
    assert r.pause() is False and r.cancel() is False           # bitmiş programa dokunulmaz


def test_step_mode_runs_exactly_one_step_per_press(env):
    r, m, *_ = env
    r.start(program([MJ, ML, {'kind': 'tool', 'open_pct': 5}]), 20, step_mode=True)
    assert r.status()['state'] == 'paused' and r.status()['step_mode'] and m.started == []
    tick(r, 3)
    assert m.started == []                                      # adım basılmadan hiçbir şey çalışmaz
    for n in (1, 2):
        r.step()
        tick(r, 2)
        m.finish()
        tick(r, 2)
        assert len(m.started) == n and r.status()['state'] == 'paused' and r.status()['done'] == n
    r.resume()                                                  # "Devam": kalanı sürekli çalıştır
    tick(r, 2)
    m.finish()
    tick(r, 2)
    assert r.status()['state'] == 'finished' and not r.status()['step_mode']


def test_step_cannot_be_used_while_running(env):
    r, *_ = env
    r.start(program([MJ]), 20)
    for fn in (r.step, r.resume):
        with pytest.raises(BusyError):
            fn()


def test_move_failure_stops_the_program_with_line_and_reason(env):
    r, m, *_ = env
    r.start(program([{'kind': 'comment', 'text': 'x'}, MJ, ML]), 20)
    tick(r)
    m.finish('failed', 'hedefe ulaşılamadı', 'unreachable')
    tick(r)
    st = r.status()
    assert st['state'] == 'failed' and st['error'] == {
        'line': 1, 'sub': None, 'iteration': None, 'message': 'hedefe ulaşılamadı', 'code': 'unreachable'}
    assert len(m.started) == 1 and m.calls[-1] == 'stop'      # sonraki adıma geçilmedi, robot durduruldu
    tick(r, 3)
    assert r.status()['state'] == 'failed'


def test_refused_move_is_a_failure_with_the_backend_code(env):
    r, m, *_ = env
    m.refuse = ('outside_safe_zone', 'hedef güvenli kutunun dışında')
    r.start(program([MJ]), 20)
    tick(r)
    st = r.status()
    assert st['state'] == 'failed' and st['error']['code'] == 'outside_safe_zone' and 'kutunun' in st['say']


def test_backend_crash_never_escapes_the_runner(env):
    r, m, *_ = env
    m.boom = RuntimeError('C:\\gizli\\yol.py patladı')
    r.start(program([MJ]), 20)
    tick(r)
    st = r.status()
    assert st['state'] == 'failed' and st['error']['code'] == 'backend_error'
    assert 'gizli' not in str(st)                               # iç hata metni/yol arayüze sızmaz


def test_dangling_waypoint_fails_instead_of_moving(env):
    r, m, *_ = env
    r.start(program([{'kind': 'move_j', 'waypoint': 'Yok'}]), 20)
    tick(r)
    assert r.status()['error']['code'] == 'missing_waypoint' and m.started == []


def test_cancel_stops_the_robot_and_is_final(env):
    r, m, *_ = env
    r.start(program([MJ, ML]), 20)
    tick(r)
    assert r.cancel() is True
    st = r.status()
    assert st['state'] == 'cancelled' and not st['running'] and m.calls[-1] == 'stop'
    tick(r, 3)
    assert len(m.started) == 1
    with pytest.raises(BusyError):
        r.resume()                                              # iptal edilmiş program devam ettirilemez


def test_estop_cancels_and_nothing_resumes_after_reset(env):
    r, m, _, state, ev = env
    r.start(program([MJ, ML]), 20)
    tick(r)
    state['estop'] = True
    tick(r)
    st = r.status()
    assert st['state'] == 'cancelled' and st['estop'] and m.calls[-1] == 'stop'
    with pytest.raises(EstopLatchedError):
        r.start(program([MJ]), 20)                              # kilitliyken başlatılamaz (rota 423)
    state['estop'] = False                                      # kilit açıldı (Reset)
    tick(r, 5)
    assert r.status()['state'] == 'cancelled' and len(m.started) == 1      # kendiliğinden devam YOK
    with pytest.raises(BusyError):
        r.resume()
    assert any(e.kind == 'program_cancelled' for e in ev.since(0))


def test_on_estop_listener_cancels_immediately(env):
    r, m, *_ = env
    r.start(program([MJ]), 20)
    r.on_estop()
    assert r.status()['state'] == 'cancelled' and r.status()['estop'] and m.calls == ['stop']


def test_estop_blocks_step_and_resume_even_when_paused(env):
    r, _, _, state, _ = env
    r.start(program([MJ, ML]), 20, step_mode=True)
    state['estop'] = True
    for fn in (r.step, r.resume):
        with pytest.raises(EstopLatchedError):
            fn()


def test_lost_heartbeat_pauses_after_three_seconds(env):
    r, m, clock, *_ = env
    r.start(program([MJ]), 20)
    tick(r)
    clock.t += 2.9
    tick(r)
    assert r.status()['state'] == 'running'
    r.heartbeat()
    clock.t += 2.9
    tick(r)
    assert r.status()['state'] == 'running'                     # kalp atışı süreyi yeniledi
    clock.t += 0.2
    tick(r)
    st = r.status()
    assert st['state'] == 'paused' and '3 sn' in st['pause_reason'] and m.calls == ['pause']
    clock.t += 60
    tick(r, 3)
    assert r.status()['state'] == 'paused'                      # kendiliğinden devam etmez
    r.resume()
    assert r.status()['state'] == 'running' and m.calls == ['pause', 'resume']
    tick(r)
    assert r.status()['state'] == 'running'                     # devam, kalp atışını da tazeler


def test_failed_pause_stops_the_program_for_safety(env):
    r, m, *_ = env
    r.start(program([MJ]), 20)
    tick(r)

    def bad_pause():
        raise OSError('ulaşılamıyor')
    m.pause = bad_pause
    assert r.pause() is False
    assert r.status()['state'] == 'failed' and r.status()['error']['code'] == 'pause_failed'


def test_only_one_program_at_a_time_and_no_empty_program(env):
    r, *_ = env
    r.start(program([MJ]), 20)
    with pytest.raises(BusyError):
        r.start(program([ML]), 20)
    r.cancel()
    with pytest.raises(ValueError):
        r.start(program([{'kind': 'comment', 'text': 'sadece yorum'}]), 20)
    r.start(program([ML]), 20)                                  # iptalden sonra yenisi başlar
    assert r.status()['run_id'] == 2


def test_runner_thread_runs_to_the_end_with_real_time():
    """Gerçek iş parçacığı + gerçek saat: tek duman testi (kısa)."""
    import time
    m = FakeMover()
    r = ProgramRunner(m, EventLog(), heartbeat_timeout_s=30)
    r.start(program([{'kind': 'wait', 's': 0.05}, MJ]), 20)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not m.started:
        time.sleep(0.02)
    assert m.started
    m.finish()
    while time.monotonic() < deadline and r.status()['state'] != 'finished':
        time.sleep(0.02)
    assert r.status()['state'] == 'finished'
