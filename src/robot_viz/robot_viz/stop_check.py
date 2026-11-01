"""End-to-end check of the emergency stop (Robot API v1). Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` and ``ros2 launch robot_viz web_view.launch.py commands:=true`` are up:

    ros2 run robot_viz stop_check            # --url http://127.0.0.1:8080

The arm's motion is measured by this check itself, from its own /joint_states subscription (joint
speed from positions over 0.1 s windows) and from TF, never from what the web view reports:

1. ``/api/state`` carries the v1 ``robot`` block (api_version, mode, state, moving, estop);
2. a long motion (``demo_scenario``) is started; once the tool is > 10 cm away from home the
   check calls ``POST /api/stop``: the call answers ``stopped: true`` within STOP_DEADLINE_S, the
   measured joint speed is back under the threshold within STOP_DEADLINE_S of the request, the arm
   does not creep afterwards, the running command ends (not ``ok``), the state is ``estopped``;
3. while latched every command is ``423`` and the arm does not move; invalid commands stay ``400``;
4. ``POST /api/reset`` (arm standing still) unlocks; a new motion works and, *while it moves*,
   ``reset`` is refused (``409``, the motion goes on); a second stop mid-motion works again;
5. stop while idle latches too; reset; the arm returns home (TF);
6. the doors are guarded: wrong method ``405``; foreign Origin / Host ``403`` and nothing latched.
Exit code 0 only if every check passed.
"""
import argparse
import bisect
import json
import math
import sys
import threading
import time

import rclpy
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from tf2_ros import Buffer, TransformListener

from robot_viz.cmd_check import joint_snapshot, moved
from robot_viz.viz_check import Http, tool_from_tf

WAIT_S = 120.0
STOP_DEADLINE_S = 2.0       # stop request -> arm quiet (own measurement and the API's stop_time_s)
QUIET_SPEED = 0.05          # rad/s, own measurement over 0.1 s windows
WINDOW_S = 0.1
CREEP_RAD = 0.01            # the arm may not move more than this after the stop returned
HOME_TOLERANCE = 0.03       # m
FAR_FROM_HOME = 0.10        # m: stop only once the tool is this far from home
JSON = {'Content-Type': 'application/json'}
LABELS = {'red', 'green', 'blue'}


class Sampler:
    """Records (monotonic time, joint positions) from the check's own /joint_states cache."""

    def __init__(self, state: JointStateCache, names):
        self._state, self._names = state, list(names)
        self.samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(2.0)

    def _run(self):
        while not self._stop.is_set():
            pos = self._state.joint_positions()
            if pos and all(n in pos for n in self._names):
                self.samples.append((time.monotonic(), [pos[n] for n in self._names]))
            time.sleep(0.005)

    def last_moving_time(self, start: float, end: float):
        """Latest time in [start, end] at which the joint speed (positions over >= WINDOW_S)
        exceeded QUIET_SPEED, or None if it never did."""
        data = [s for s in self.samples if start - 1.0 <= s[0] <= end]
        times = [s[0] for s in data]
        last = None
        for t, q in data:
            if t < start:
                continue
            k = bisect.bisect_right(times, t - WINDOW_S) - 1    # newest sample >= WINDOW_S ago
            if k < 0:
                continue
            speed = max(abs(a - b) for a, b in zip(q, data[k][1])) / (t - times[k])
            if speed > QUIET_SPEED:
                last = t
        return last

    def displacement(self, start: float, end: float) -> float:
        window = [q for t, q in self.samples if start <= t <= end]
        if len(window) < 2:
            return float('nan')
        return max(max(q[i] for q in window) - min(q[i] for q in window)
                   for i in range(len(window[0])))


class Api:
    def __init__(self, base: str):
        self.http = Http(base)

    def post(self, path: str, payload=None, headers=None, timeout: float = 400.0):
        body = None if payload is None else json.dumps(payload).encode()
        sent = JSON if headers is None else headers
        status, received, data = self.http.request('POST', path, headers=sent, body=body,
                                                   timeout=timeout)
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = None
        return status, received, parsed

    def command(self, line: str):
        return self.post('/api/command', {'command': line})

    def robot(self):
        s = self.http.state()
        return s['robot'] if s else None


def wait_for(predicate, timeout: float, period: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(period)
    return None


def start_async(api: Api, line: str):
    """POST a command in a thread; returns (thread, holder) where holder['r'] is the answer."""
    holder = {}
    thread = threading.Thread(target=lambda: holder.update(r=api.command(line)), daemon=True)
    thread.start()
    return thread, holder


def run(base: str, config, state: JointStateCache, buffer: Buffer, sampler: Sampler) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    api = Api(base)
    names = list(config.joint_names)
    deadline = time.monotonic() + WAIT_S
    ready = False
    while time.monotonic() < deadline and not ready:
        status, _, body = api.command('objects')
        ready = status == 200 and LABELS <= {o['label'] for o in body['data']['objects']}
        if not ready:
            time.sleep(2.0)
    if not record('komut ucu açık ve 3 nesne planlama sahnesinde', ready):
        return results

    # 1. the v1 robot block ------------------------------------------------------------------
    api.command('home')
    time.sleep(2.0)
    home_ref = tool_from_tf(buffer, config.tip_link)
    top = api.http.state()
    robot = top['robot']
    record('/api/state: api_version 1 (üst düzey ve robot bloğu)',
           top.get('api_version') == 1 and robot.get('api_version') == 1)
    record('robot bloğu: mode gazebo, state idle, moving false, estop kilitli değil, iş yok',
           robot['mode'] == 'gazebo' and robot['state'] == 'idle' and robot['moving'] is False
           and robot['estop']['tripped'] is False and robot['current_job'] is None, f'({robot})')

    # 2. stop in the middle of a long motion --------------------------------------------------
    thread, holder = start_async(api, 'demo_scenario')
    away = wait_for(lambda: (lambda p: p and home_ref and math.dist(p, home_ref) > FAR_FROM_HOME)(
        tool_from_tf(buffer, config.tip_link)), 120.0, 0.1)
    mid = api.robot()
    record('demo_scenario çalışıyor: state busy, moving true, current_job var',
           bool(away) and mid['state'] == 'busy' and mid['moving'] is True
           and mid['current_job'] and mid['current_job']['command'] == 'demo_scenario',
           f"(uç home'dan > {FAR_FROM_HOME * 100:.0f} cm; robot {mid})")
    record('current_job bir kimlik (id) taşıyor',
           bool((mid['current_job'] or {}).get('id')), f"({mid['current_job']})")
    t_stop = time.monotonic()
    status, _, body = api.post('/api/stop', {'reason': 'stop_check'}, timeout=30.0)
    t_back = time.monotonic()
    print(f'       POST /api/stop: durum {status}, {body}; istek {(t_back - t_stop):.2f} s sürdü')
    record('POST /api/stop -> 200, stopped true, latched true, was_running true',
           status == 200 and body and body['stopped'] and body['latched'] and body['was_running'])
    record(f"API'nin bildirdiği stop_time_s <= {STOP_DEADLINE_S} s",
           bool(body) and body['stop_time_s'] <= STOP_DEADLINE_S,
           f"({body and body['stop_time_s']} s)")
    time.sleep(2.0)
    last_moving = sampler.last_moving_time(t_stop, t_back + 1.9)
    quiet_after = 0.0 if last_moving is None else last_moving - t_stop
    record(f'KENDİ ÖLÇÜMÜM: eklem hızı istekten sonra <= {STOP_DEADLINE_S} s içinde '
           f'< {QUIET_SPEED} rad/s oldu', quiet_after <= STOP_DEADLINE_S,
           f'(son hareket istekten {quiet_after:.2f} s sonra)')
    creep = sampler.displacement(t_back + 0.3, t_back + 1.9)
    record(f'durduktan sonra kol sürünmüyor (<= {CREEP_RAD} rad)', creep <= CREEP_RAD,
           f'(en büyük eklem değişimi {creep:.4f} rad)')
    thread.join(60)
    answer = holder.get('r', (None, {}, None))
    record('çalışan komut sonlandı ve başarılı DEĞİL (iptal edildi)',
           not thread.is_alive() and answer[0] in (422, 500) and answer[2]
           and answer[2]['ok'] is False,
           f'(durum {answer[0]}, {answer[2] and answer[2]["message"]})')
    tool = tool_from_tf(buffer, config.tip_link)
    record('senaryo tamamlanmadı: kol home dışında durdu',
           bool(tool and home_ref) and math.dist(tool, home_ref) > 0.05,
           f"(home'a uzaklık {math.dist(tool, home_ref) * 100:.1f} cm)" if tool else '')
    robot = api.robot()
    record('state estopped, moving false, estop.tripped true, neden kaydedildi',
           robot['state'] == 'estopped' and robot['moving'] is False
           and robot['estop']['tripped'] and robot['estop']['reason'] == 'stop_check', f'({robot})')
    record('last_error: iptal edilen komut ve nedeni görünüyor',
           bool(robot['last_error']) and robot['last_error']['command'] == 'demo_scenario')

    # 3. latched: 423 and no motion -------------------------------------------------------------
    before = joint_snapshot(state, names)
    t0 = time.monotonic()
    answers = [(line, api.command(line)) for line in
               ('home', 'above green 0.25', 'demo_scenario', 'objects')]
    record('kilitliyken her komut 423 (hızlı) ve estopped bildirir',
           all(a[0] == 423 and a[2]['state'] == 'estopped' for _, a in answers)
           and time.monotonic() - t0 < 5.0, f'({[(line, a[0]) for line, a in answers]})')
    record('kilitliyken geçersiz komut yine 400', api.command('goto 1 2 3')[0] == 400)
    time.sleep(3.0)
    record('423 sonrası kol hiç oynamadı', moved(before, joint_snapshot(state, names)) < 0.01,
           f'({moved(before, joint_snapshot(state, names)):.4f} rad)')
    record('GET /api/state kilitli durumu hâlâ gösteriyor', api.robot()['state'] == 'estopped')

    # 4. reset, new motion, reset refused while moving, second stop -----------------------------
    status, _, body = api.post('/api/reset', {'x': 1})
    record("reset: gövde {} değilse 400", status == 400)
    status, _, body = api.post('/api/reset', {})
    record('reset (kol duruyor) -> 200, changed true, state idle',
           status == 200 and body['changed'] and body['state'] == 'idle', f'({body})')
    thread, holder = start_async(api, 'above red 0.35')
    moving = wait_for(lambda: (api.robot() or {}).get('moving'), 30.0, 0.05)
    time.sleep(0.3)
    status, _, body = api.post('/api/reset', {})
    record('reset, hareket sürerken reddedildi (409), state busy',
           bool(moving) and status == 409 and body['state'] == 'busy', f'(durum {status}, {body})')
    thread.join(120)
    answer = holder.get('r', (None, {}, None))
    tool = tool_from_tf(buffer, config.tip_link)
    target = (answer[2] or {}).get('data', {}).get('target')
    err = math.dist(tool, target) if tool and target else float('inf')
    record('reddedilen reset hareketi bozmadı: above red 0.3 kesintisiz bitti, hedefte (TF)',
           answer[0] == 200 and err <= 0.015, f'(durum {answer[0]}, hata {err * 100:.2f} cm)')
    thread, holder = start_async(api, 'above green 0.25')
    moving = wait_for(lambda: (api.robot() or {}).get('moving'), 30.0, 0.05)
    time.sleep(0.5)
    t_stop = time.monotonic()
    status, _, body = api.post('/api/stop', {'reason': 'ikinci'}, timeout=30.0)
    t_back = time.monotonic()
    record('ikinci acil dur (başka bir hareketin ortasında) -> stopped true, was_running true',
           bool(moving) and status == 200 and body['stopped'] and body['was_running']
           and body['stop_time_s'] <= STOP_DEADLINE_S, f'({body})')
    time.sleep(1.5)
    last_moving = sampler.last_moving_time(t_stop, t_back + 1.4)
    quiet_after = 0.0 if last_moving is None else last_moving - t_stop
    record('ikinci durdurmada da kendi ölçümüm: kol süre içinde durdu',
           quiet_after <= STOP_DEADLINE_S, f'({quiet_after:.2f} s)')
    thread.join(60)
    status, _, body = api.post('/api/reset', {})
    record('ikinci reset -> 200', status == 200 and body['state'] == 'idle')

    # 5. stop while idle, then back home ---------------------------------------------------------
    status, _, body = api.post('/api/stop', {'reason': 'bosta'}, timeout=30.0)
    record('boştayken acil dur: 200, stopped true, was_running false',
           status == 200 and body['stopped'] and body['was_running'] is False, f'({body})')
    record('boştayken kilitlendi: komut 423', api.command('home')[0] == 423)
    record('reset sonrası kilit açıldı', api.post('/api/reset', {})[0] == 200)
    status, _, body = api.command('home')
    time.sleep(1.5)
    tool = tool_from_tf(buffer, config.tip_link)
    record('stop/reset sonrası robot home\'a dönebiliyor (TF)',
           status == 200 and tool and home_ref and math.dist(tool, home_ref) <= HOME_TOLERANCE,
           f'(durum {status}, uzaklık {math.dist(tool, home_ref) * 100:.2f} cm)' if tool else '')
    status, _, body = api.command('above green 0.25')
    record('sonrasında normal bir hedef hareketi de çalışıyor', status == 200, f'(durum {status})')

    # 6. guards ------------------------------------------------------------------------------------
    for path in ('/api/stop', '/api/reset'):
        status, headers, _ = api.http.request('GET', path)
        record(f'GET {path} -> 405, Allow: POST', status == 405 and headers.get('Allow') == 'POST')
        status, _, _ = api.post(path, {}, {'Origin': 'http://evil.example', **JSON})
        record(f'{path}: yabancı Origin -> 403', status == 403)
        status, _, _ = api.post(path, {}, {'Host': 'evil.example', **JSON})
        record(f'{path}: yabancı Host -> 403', status == 403)
        status, _, _ = api.post(path, {}, {'Sec-Fetch-Site': 'cross-site', **JSON})
        record(f'{path}: Sec-Fetch-Site cross-site -> 403', status == 403)
    record('reddedilen istekler hiçbir şeyi kilitlemedi', api.robot()['state'] == 'idle'
           and api.robot()['estop']['tripped'] is False)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('stop_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    sampler = Sampler(state, config.joint_names)
    sampler.start()
    try:
        results = run(opts.url, config, state, buffer, sampler)
    finally:
        sampler.close()
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 36 else 1)


if __name__ == '__main__':
    main()
