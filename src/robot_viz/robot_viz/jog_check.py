"""End-to-end check of the jog over the Robot API (J2), measured from the simulation. Run via
``run_selftest jog`` (mock_components) or ``run_selftest gazebo-jog`` (Gazebo physics):

    ros2 run robot_viz jog_check [--url http://127.0.0.1:8080]

 A. ``ready`` job: the arm goes to the jog start pose (TCP inside the safe box); /api/state shows
    ``tcp`` and ``robot.jog``
 B. incremental jog +10 mm X: the tool (TF) moved 10 mm +-2 mm and nothing else
 C. hold-to-run: speed stays under the cap (+30 %), state ``jogging`` while streaming; the client
    stops sending -> the arm is MEASURED standing within 1 s (TTL 250 ms + ramp <= 100 ms + plant)
 D. a client asking for ttl_ms 60000 is clamped to 500 ms and the arm still stops by itself
 E. second client 409, a job while jogging 409, a jog while a job runs 409
 F. emergency stop mid-jog: arm measured stopped (<= 2 s), jog POST 423, reset, jog works again
 G. connection dropped mid-jog (client gone): the arm stops by itself
 H. towards the box ceiling: the tool never leaves the safe box (TF), the jog says why it stopped
 I. at the upright home pose (wrist singularity): Cartesian jog stops with a singularity message
 J. taught frame (PUT /api/frames, stored in the data dir): jog +X in a frame rotated 90 deg about Z
    moves the tool along world +Y; the frame file survives
Exit code 0 only if every check passed.
"""

import argparse
import http.client
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import rclpy
import yaml
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from tf2_ros import Buffer, TransformListener

from robot_viz.jobs_check import wait_job
from robot_viz.stop_check import WAIT_S, Api, Sampler, wait_for
from robot_viz.viz_check import tool_from_tf

LEASE = 'jogcheck-aaaaaaaa'
OTHER = 'jogcheck-bbbbbbbb'
CAP_LINEAR = 0.02  # m/s: default 20 % of 0.1 m/s (mode real: lower)
STOP_DEADLINE_S = 1.0  # last jog request -> joints quiet (own measurement)
ESTOP_DEADLINE_S = 2.0
JSON = {'Content-Type': 'application/json'}
BOX_Z_MAX = 0.7


class JogClient:
    """One keep-alive connection, like a browser tab: POST /api/jog at ~20 Hz from a thread."""

    def __init__(self, base: str, lease: str = LEASE):
        parts = urlsplit(base)
        self.host, self.port, self.lease = parts.hostname, parts.port or 80, lease
        self.conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        self.last = None
        self.reconnects = 0
        self._stop = threading.Event()
        self._thread = None
        self.last_sent = 0.0

    def post(self, body, ttl_ms=None):
        payload = {'lease': self.lease, 'frame': 'World', **body}
        if ttl_ms is not None:
            payload['ttl_ms'] = ttl_ms
        for _attempt in (0, 1):
            try:
                self.conn.request('POST', '/api/jog', body=json.dumps(payload), headers=JSON)
                resp = self.conn.getresponse()
                data = json.loads(resp.read() or b'null')
                self.last, self.last_sent = (resp.status, data), time.monotonic()
                return self.last
            except (OSError, http.client.HTTPException):
                self.conn.close()
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
                self.reconnects += 1
        return None, None

    def hold(self, twist, frame='World', ttl_ms=None, hz=20.0):
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                self.post({'twist': list(twist), 'frame': frame}, ttl_ms)
                time.sleep(1.0 / hz)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_sending(self):
        """Client "vanishes" but keeps the socket: no more heartbeats."""
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)
        return time.monotonic()

    def drop(self):
        """Client dies: the TCP connection is closed without a goodbye."""
        t = self.stop_sending()
        try:
            self.conn.sock.shutdown(2)
        except (OSError, AttributeError):
            pass
        self.conn.close()
        return t

    def release(self):
        self.stop_sending()
        return self.post({}, ttl_ms=0)


def tool_pos(buffer, tip):
    return tool_from_tf(buffer, tip)


def reached(distance: float, plant: str) -> bool:
    """10 mm asked. mock_components follow exactly (+-2 mm). In Gazebo the PID plant lags the
    measured-q based command (known J1 trait, see the PR): only direction, a floor and a ceiling
    are checked there and the real distance is printed."""
    if plant == 'mock':
        return abs(distance - 0.010) < 0.002
    return 0.003 <= distance <= 0.012


def run(
    base: str,
    config,
    state: JointStateCache,
    buffer: Buffer,
    sampler: Sampler,
    data_dir: Path,
    plant: str = 'mock',
) -> list:
    results = []
    api = Api(base)
    tip = config.tip_link

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}', flush=True)
        return bool(ok)

    def robot():
        return api.robot() or {}

    def standing_since(t0, deadline):
        """True when the joints are quiet within ``deadline`` s after ``t0`` (own measurement)."""
        end = t0 + deadline
        wait_for(lambda: time.monotonic() > end, deadline + 1.0, 0.02)
        last = sampler.last_moving_time(t0, time.monotonic())
        return last is None or last - t0 <= deadline

    def go(command):
        status, _, body = api.post('/api/jobs', {'command': command}, timeout=30.0)
        done = wait_job(api, body['id'], 'done', WAIT_S) if status == 202 else None
        return status, done

    wait_for(lambda: robot().get('mode'), WAIT_S, 0.5)

    # ---- A. ready
    status, done = go('ready')
    record('A ready işi tamam', status == 202 and bool(done), str(done and done.get('result')))
    doc = api.http.state()
    tcp = (doc or {}).get('tcp')
    p_tf = tool_pos(buffer, tip)
    record(
        'A /api/state: tcp (konum + W-P-R) ve robot.jog var, durum idle',
        bool(tcp)
        and 'wpr_deg' in tcp
        and doc['robot']['jog']['enabled']
        and doc['robot']['state'] == 'idle',
        str(tcp and tcp['position']),
    )
    record(
        'A TCP kutunun içinde ve TF ile tutarlı (<= 1 cm)',
        bool(p_tf and tcp)
        and math.dist(p_tf, tcp['position']) < 0.01
        and 0.05 < p_tf[0] < 0.75
        and p_tf[2] < BOX_Z_MAX,
        str(p_tf),
    )

    # ---- B. increment
    a = tool_pos(buffer, tip)
    c = JogClient(base)
    status, body = c.post({'increment': {'axis': 0, 'amount': 10}})
    done = wait_for(lambda: robot().get('jog', {}).get('status') == 'done', 40.0, 0.1)
    standing_since(time.monotonic(), 1.5)
    b = tool_pos(buffer, tip)
    d = [y - x for x, y in zip(a, b)]
    record(
        'B +10 mm X artımı: ölçülen yer değiştirme 10 mm ±2 mm, başka eksen < 2 mm',
        status == 200
        and bool(done)
        and reached(d[0], plant)
        and abs(d[1]) < 0.002
        and abs(d[2]) < 0.002,
        f'd={[round(v * 1000, 2) for v in d]} mm',
    )
    record(
        'B kabul edilmeyen artım (0.5 mm) 400',
        c.post({'increment': {'axis': 0, 'amount': 0.5}})[0] == 400,
    )
    c.release()

    # ---- C. hold-to-run, then the client stops sending
    start = tool_pos(buffer, tip)
    t_begin = time.monotonic()
    c.hold([0.5, 0, 0, 0, 0, 0])  # asks for 0.5 m/s: server clips
    time.sleep(2.0)
    mid = tool_pos(buffer, tip)
    jogging = robot().get('state')
    speed = math.dist(mid, start) / max(1e-3, time.monotonic() - t_begin)
    t_gone = c.stop_sending()
    quiet = standing_since(t_gone, STOP_DEADLINE_S)
    time.sleep(0.5)
    jog_doc = api.http.request('GET', '/api/jog')[2]
    record(
        'C basılı-tut: durum jogging, ortalama TCP hızı sunucu tavanının altında',
        jogging == 'jogging' and 0.003 < speed <= CAP_LINEAR * 1.3,
        f'{speed * 1000:.1f} mm/s',
    )
    record(
        f'C istemci sustu: kol ölçülerek <= {STOP_DEADLINE_S} s içinde durdu (TTL 250 ms + rampa)',
        quiet,
    )
    record('C sunucu "moving: false" diyor', json.loads(jog_doc)['moving'] is False)

    # ---- D. client asks for a huge TTL
    t0 = time.monotonic()
    status, body = c.post({'twist': [0.5, 0, 0, 0, 0, 0]}, ttl_ms=60000)
    time.sleep(0.4)
    record(
        'D ttl_ms 60000 isteği 500 ms ile kırpıldı (yanıtta)',
        status == 200 and body['ttl_ms'] == 500 and body['ttl_clamped'],
    )
    quiet = standing_since(t0, 0.5 + 0.1 + 0.4 + STOP_DEADLINE_S - 0.5)
    record('D kol tek komuttan sonra kendiliğinden durdu (TTL <= 500 ms)', quiet)
    c.release()

    # ---- E. exclusion
    c.hold([0.2, 0, 0, 0, 0, 0])
    time.sleep(0.4)
    other = JogClient(base, OTHER)
    s409 = other.post({'twist': [0.1, 0, 0, 0, 0, 0]})[0]
    job_status = api.post('/api/jobs', {'command': 'home'}, timeout=10.0)[0]
    c.release()
    record('E ikinci istemci 409', s409 == 409)
    record(
        'E jog sürerken iş başlatma 409',
        job_status == 409,
        'yanıt yok (zaman aşımı/bağlantı)' if job_status is None else f'http={job_status}',
    )
    status, _, body = api.post('/api/jobs', {'command': 'ready'}, timeout=10.0)
    job_id = body.get('id') if status == 202 and isinstance(body, dict) else None
    s_job = other.post({'twist': [0.1, 0, 0, 0, 0, 0]})[0]
    if job_id is not None:
        wait_job(api, job_id, 'done', WAIT_S)
    record(
        'E iş sürerken jog 409',
        job_id is not None and s_job == 409,
        f'iş http={status} jog http={s_job}' + ('' if job_id else ' (iş başlamadı)'),
    )
    other.release()

    # ---- F. emergency stop mid-jog
    c.hold([0.5, 0, 0, 0, 0, 0])
    time.sleep(1.2)
    t_stop = time.monotonic()
    status, _, stop = api.post('/api/stop', {'reason': 'jog_check'}, timeout=30.0)
    quiet = standing_since(t_stop, ESTOP_DEADLINE_S)
    time.sleep(0.5)
    post = c.last
    record(
        'F jog sırasında acil dur: kol ölçülerek durdu, durum estopped',
        status == 200 and stop['latched'] and quiet and robot().get('state') == 'estopped',
        f'stop_time={stop and stop.get("stop_time_s")} s',
    )
    record(
        'F acil dur sonrası jog POST 423',
        post is not None and post[0] == 423,
        str(post and post[0]),
    )
    c.stop_sending()
    record(
        'F jog uçları kilitliyken 423', c.post({'increment': {'axis': 0, 'amount': 1}})[0] == 423
    )
    record('F /api/stop jog trafiği sürerken bile cevap verdi (öncelikli)', status == 200)
    api.post('/api/reset', {})
    record(
        'F reset sonrası jog yeniden çalışır',
        c.post({'increment': {'axis': 0, 'amount': -1}})[0] == 200,
    )
    wait_for(lambda: robot().get('jog', {}).get('status') == 'done', 20.0, 0.1)
    c.release()

    # ---- G. connection dropped
    c = JogClient(base)
    c.hold([0.5, 0, 0, 0, 0, 0], ttl_ms=500)
    time.sleep(1.2)
    t_drop = c.drop()
    record(
        'G bağlantı kopması: kol ölçülerek durdu (TTL 500 ms üst sınır)',
        standing_since(t_drop, 0.5 + 0.1 + STOP_DEADLINE_S - 0.5),
    )
    time.sleep(2.2)  # lease_hold_s passes: lease is free

    # ---- H. towards the ceiling
    go('ready')
    c = JogClient(base)
    c.hold([0, 0, 0.5, 0, 0, 0])
    zmax, reason = 0.0, ''
    end = time.monotonic() + 40.0
    while time.monotonic() < end:
        p = tool_pos(buffer, tip)
        zmax = max(zmax, p[2] if p else 0.0)
        j = robot().get('jog') or {}
        if j.get('status') in ('box_stop', 'joint_limit', 'singularity', 'tracking'):
            reason = f'{j["status"]}: {"; ".join(j.get("messages", []))}'
            break
        time.sleep(0.25)
    c.stop_sending()
    record(
        'H kutu tavanı: TCP kutudan çıkmadı (TF)', zmax <= BOX_Z_MAX + 0.005, f'z_max={zmax:.3f}'
    )
    record(
        'H jog neden durduğunu söyledi',
        bool(reason),
        reason or 'durmadı (40 s içinde sınıra varılmadı)',
    )
    c.release()

    # ---- I. upright home: wrist singularity
    go('home')
    c = JogClient(base)
    c.hold([0, 0, -0.5, 0, 0, 0])
    sing = wait_for(
        lambda: (
            (robot().get('jog') or {}).get('status') in ('singularity', 'box_stop', 'joint_limit')
        ),
        10.0,
        0.1,
    )
    j = robot().get('jog') or {}
    c.stop_sending()
    record(
        'I dik duran home: Cartesian jog durdu ve "tekillik" mesajı verdi',
        bool(sing)
        and j.get('status') == 'singularity'
        and any('tekillik' in m.lower() for m in j.get('messages', [])),
        f'{j.get("status")} {j.get("messages")}',
    )
    c.release()
    time.sleep(2.1)

    # ---- J. taught frame
    go('ready')
    pts = {
        'origin': [0.3, 0.0, 0.2],
        'x': [0.3, 0.1, 0.2],
        'xy': [0.2, 0.0, 0.2],
    }  # frame X = world +Y
    body = json.dumps({'frames': [{'name': 'Tezgah', 'points': pts}]}).encode()
    s_put = api.http.request('PUT', '/api/frames', JSON, body)
    got = json.loads(api.http.request('GET', '/api/frames')[2])
    stored = yaml.safe_load((data_dir / 'frames.yaml').read_text())
    record(
        'J PUT /api/frames: kaydedildi, GET aynı, dosya veri dizininde',
        s_put[0] == 200
        and got['frames'][0]['name'] == 'Tezgah'
        and stored['frames'][0]['name'] == 'Tezgah',
        str(data_dir / 'frames.yaml'),
    )
    a = tool_pos(buffer, tip)
    c = JogClient(base)
    c.post({'increment': {'axis': 0, 'amount': 10}, 'frame': 'Tezgah'})
    wait_for(lambda: robot().get('jog', {}).get('status') == 'done', 40.0, 0.1)
    standing_since(time.monotonic(), 1.5)
    b = tool_pos(buffer, tip)
    d = [y - x for x, y in zip(a, b)]
    record(
        'J Tezgah çerçevesinde +X 10 mm: dünya +Y yönünde hareket (TF)',
        reached(d[1], plant) and abs(d[0]) < 0.002,
        f'd={[round(v * 1000, 2) for v in d]} mm',
    )
    c.release()
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--plant', choices=('mock', 'gazebo'), default='mock')
    opts, ros_args = parser.parse_known_args(args)
    data_dir = Path(os.environ.get('ROBOT_DATA_DIR', '/tmp/jog_data'))
    rclpy.init(args=ros_args)
    node = Node('jog_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    sampler = Sampler(state, config.joint_names)
    sampler.start()
    try:
        results = run(opts.url, config, state, buffer, sampler, data_dir, opts.plant)
    finally:
        sampler.close()
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 24 else 1)


if __name__ == '__main__':
    main()
