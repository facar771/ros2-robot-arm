"""tools/bench/bench.py testi: gerçek HwLink + gerçek `K9_BENCH` çekirdeği (g++ ile derlenen sürücü, SAHTE motor HAL'i).
Donanımda ÇALIŞMAZ. Çalıştır:  cd <repo> && PYTHONPATH=src/robot_hw_bridge python3 -m pytest tools/bench -q
"""
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / 'firmware' / 'host'))

import bench                                                  # noqa: E402
from k9_hostlib import DriverTransport, build                 # noqa: E402
from robot_hw_bridge.host import HwLink                       # noqa: E402
from robot_hw_bridge.protocol import Result, State, StatusFlag  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which('g++') is None, reason='g++ yok')


@pytest.fixture(scope='module')
def bench_bin(tmp_path_factory):
    return build(tmp_path_factory.mktemp('tb') / 'k9_bench', '-DK9_BENCH=1')


@pytest.fixture(scope='module')
def plain_bin(tmp_path_factory):
    return build(tmp_path_factory.mktemp('tp') / 'k9_plain')


class Session:
    def __init__(self, binary, answer='EVET', j7_spr=None, *args):
        self.lines = []
        self.link = HwLink(DriverTransport(binary, *args, steps_per_poll=20)).start()
        assert self.link.wait_link(3.0)
        assert self.link.wait_for(lambda s: True, 2.0)
        self.b = bench.Bench(self.link, self.lines.append, lambda prompt: (self.lines.append(prompt) or answer), j7_spr)

    def run(self, line):
        self.b.dispatch(line.split())

    @property
    def text(self):
        return '\n'.join(self.lines)

    def state(self):
        return self.link.wait_for(lambda s: True, 1.0) and self.link.status.state

    def wait_state(self, st):
        assert self.link.wait_for(lambda s: s.state == st, 2.0), self.text

    def pos(self, idx):
        return self.link.status.axes[idx].pos

    def done(self):
        self.b.close()


@pytest.fixture
def s(bench_bin):
    x = Session(bench_bin)
    yield x
    x.done()


def test_status_shows_bench_mode_and_estimate_label(s):
    s.run('status')
    assert 'BENCH_MODE' in s.text and 'KESTİRİM' in s.text and 'UNHOMED' in s.text


def test_zero_needs_exact_evet_and_sends_nothing_otherwise(bench_bin):
    x = Session(bench_bin, answer='evet')
    try:
        x.run('zero j1')
        assert 'Onay verilmedi' in x.text
        assert 'motor yükten ayrık, güç kesmeye hazırım: EVET' in x.text
        assert not x.link.status.axes[0].homed and x.link.status.state == State.UNHOMED
    finally:
        x.done()


def test_full_flow_zero_enable_move_disable(s):
    s.run('zero j1')
    s.run('enable')
    s.wait_state(State.ENABLED)
    s.run('move j1 30')
    assert 'vardı: konum 30' in s.text, s.text
    s.run('move j1 -30')                                              # ikinci hareket: ilk-hareket sınırı kalktı
    assert s.pos(0) == 0
    s.run('move j1 100')
    assert s.pos(0) == 100
    s.run('disable')
    s.wait_state(State.HOMED)
    assert s.link.status.axes[0].homed and not s.link.status.axes[0].enabled


def test_host_side_limits_send_nothing(s):
    s.run('zero j1')
    s.run('enable')
    s.wait_state(State.ENABLED)
    s.run('move j1 57')                                               # ilk hareket tavanı: 5° = 56 adım
    assert 'ilk hareketi en çok 56' in s.text
    s.run('move j1 5deg')                                             # 5° = 56 adıma kırpılır, kabul
    assert 'vardı: konum 56' in s.text, s.text
    s.run('move j1 300')                                              # sınır aşımı (56+300 > 256), gönderilmez
    assert 'sınır -256..256' in s.text and s.pos(0) == 56
    s.run('move j1 abc')
    s.run('move j1 0')
    s.run('move j9 5')
    s.run('move j2 5')                                                # J2 sıfırlanmadı
    assert s.text.count('Komut gönderilmedi') >= 2 and s.pos(1) == 0


def test_j7_degrees_need_steps_per_rev(bench_bin):
    x = Session(bench_bin)
    try:
        x.run('zero j7')
        x.run('enable')
        x.wait_state(State.ENABLED)
        x.run('move j7 5deg')
        assert 'adım/devir bilinmiyor' in x.text
        x.run('move j7 11')                                           # bilinmiyorsa ilk hareket <=10 adım
        assert 'en çok 10 adım' in x.text
        x.run('move j7 10')
        assert 'vardı: konum 10' in x.text, x.text
        x.run('move j7 41')                                           # 10+41 > 50
        assert 'sınır -50..50' in x.text
    finally:
        x.done()
    y = Session(bench_bin, j7_spr=200)
    try:
        y.run('zero j7')
        y.run('enable')
        y.wait_state(State.ENABLED)
        y.run('move j7 5deg')                                         # 200 adım/devir: 2,77 -> 2 adım
        assert 'vardı: konum 2' in y.text, y.text
    finally:
        y.done()


def test_bench_mode_gate_blocks_before_sending(plain_bin):
    x = Session(plain_bin)
    sent = []
    x.link.home = lambda **kw: sent.append(('home', kw))
    x.link.enable = lambda: sent.append('enable')
    x.link.move = lambda *a: sent.append(('move', a))
    try:
        assert not x.link.status.flags & StatusFlag.BENCH_MODE
        for line in ('zero j1', 'enable', 'move j1 5'):
            x.run(line)
        assert sent == [] and x.text.count('BENCH_MODE yok') == 3, x.text
        x.run('status')
        assert 'UYARI: BENCH_MODE yok' in x.text
    finally:
        x.done()


def test_loop_open_blocks_commands(bench_bin):
    x = Session(bench_bin, None, None, 'open')                    # açılışta D13 açık
    try:
        x.run('zero j1')
        assert 'acil dur döngüsü açık' in x.text and x.link.status.state == State.ESTOPPED
    finally:
        x.done()


def test_estop_clear_and_rezero(s):
    s.run('zero j1')
    s.run('enable')
    s.wait_state(State.ENABLED)
    s.run('estop')
    s.wait_state(State.ESTOPPED)
    s.run('move j1 5')
    s.run('zero j1')
    assert s.text.count('Komut gönderilmedi') == 2
    s.run('clear')
    s.wait_state(State.UNHOMED)
    s.run('enable')
    assert 'önce zero' in s.text
    s.run('zero j1')
    s.run('enable')
    s.wait_state(State.ENABLED)


def test_stop_is_allowed_without_gate(plain_bin):
    x = Session(plain_bin)
    try:
        x.run('stop')
        x.run('stop j2')
        assert x.text.count('stop: TAMAM') == 2
    finally:
        x.done()


def test_close_while_enabled_stops_then_disables_and_stops_link(bench_bin):
    x = Session(bench_bin)
    order = []
    real_stop, real_dis = x.link.stop_axes, x.link.disable
    x.link.stop_axes = lambda **kw: order.append('stop') or real_stop(**kw)
    x.link.disable = lambda: order.append('disable') or real_dis()
    x.run('zero j1')
    x.run('enable')
    x.wait_state(State.ENABLED)
    x.b.close()
    assert order[:2] == ['stop', 'disable'], order


def test_main_one_shot_status_and_enable_refused(bench_bin, capsys):
    lines = []
    factory = lambda: HwLink(DriverTransport(bench_bin, steps_per_poll=20))     # noqa: E731
    assert bench.main(['status'], factory, lines.append) == 0
    assert any('BENCH_MODE' in l for l in lines)
    assert bench.main(['enable'], factory, lines.append) == 2
    assert bench.main(['move', 'j1', '5'], factory, lines.append) == 2


def test_error_in_session_still_disables(bench_bin, monkeypatch):
    calls = []
    factory = lambda: HwLink(DriverTransport(bench_bin, steps_per_poll=20))     # noqa: E731
    real_close = bench.Bench.close
    monkeypatch.setattr(bench.Bench, 'close', lambda self: calls.append('close') or real_close(self))
    monkeypatch.setattr(bench.Bench, 'dispatch', lambda self, w: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError):
        bench.main(['status'], factory, lambda m: None)
    assert calls == ['close']


def test_parse_amount_and_first_cap():
    j1, j7 = bench.AXES['j1'], bench.AXES['j7']
    assert bench.parse_amount('30', j1) == 30 and bench.parse_amount('-5deg', j1) == -56
    assert bench.parse_amount('5°', j1) == 56 and bench.first_move_cap(j1) == 56
    assert bench.first_move_cap(j7) == 10
    for bad in ('1.5', 'x', '5 rad', ''):
        with pytest.raises(ValueError):
            bench.parse_amount(bad, j1)
    with pytest.raises(ValueError):
        bench.parse_amount('5deg', j7)


def test_limits_match_firmware_config():
    """Host ve firmware sınırları ayrışmasın: k9_config.h'den okunur."""
    cfg = (HERE.parents[1] / 'firmware' / 'uno' / 'lib' / 'k9core' / 'k9_config.h').read_text()
    for needle in ('(a == 0 || a == 1) ? -256 : (a == 6 ? -50 : 0)', '(a == 0 || a == 1) ? 256 : (a == 6 ? 50 : 0)',
                   '(a == 0 || a == 1) ? 300 : (a == 6 ? 100 : 0)'):
        assert needle in cfg
    assert (bench.AXES['j1'].lo, bench.AXES['j1'].hi, bench.AXES['j1'].vmax, bench.AXES['j1'].accel) == (-256, 256, 300, 300)
    assert (bench.AXES['j7'].lo, bench.AXES['j7'].hi, bench.AXES['j7'].vmax, bench.AXES['j7'].accel) == (-50, 50, 100, 100)
    assert all(a.def_vmax < a.vmax and a.def_accel < a.accel for a in bench.AXES.values())
    assert [a.idx for a in bench.AXES.values()] == [0, 1, 6]
