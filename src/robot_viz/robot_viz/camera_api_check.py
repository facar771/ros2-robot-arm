"""End-to-end check of the camera platform on the Robot API (H4), MEASURED from the simulation.
Run via ``run_selftest gazebo-camera-api`` (tracking.sdf platform + camera_manual + web_view):

    ros2 run robot_viz camera_api_check [--url http://127.0.0.1:8080]

 A. GET /api/camera: available, pose, limits (tilt: plus = down), idle
 B. goto over the API: the platform is MEASURED (own /platform/joint_states subscription, not the
    API's report) at the target, and the live frame (/frame/rgb.png) changed accordingly
 C. relative step: measured pose + delta
 D. refusals leave the platform where it was: target outside the limits, rate over the cap, bad
    bodies, NaN literal
 E. jog dead-man: one 250 ms jog command moves it and it STOPS BY ITSELF (nobody sent a stop)
 F. frames: query + credential headers are ignored, nothing is echoed back
 G. emergency stop mid-goto (/api/stop): the platform is MEASURED stopped, motion is 423 while
    latched, ``stop`` is still accepted. LAST: the arm-less sim cannot verify 'quiet' for /api/reset
Exit code 0 only if every check passed.
"""

import argparse
import json
import sys
import threading
import time

import numpy as np
import rclpy
from rclpy.node import Node
from robot_core.viz import decode_png
from sensor_msgs.msg import JointState

from robot_viz.stop_check import Api, wait_for

NAMES = {'pan': 'yaw_joint', 'slide': 'slide_joint', 'tilt': 'pitch_joint'}
JSON = {'Content-Type': 'application/json'}
TOL_RAD = 0.03
TOL_M = 0.03
QUIET_RATE = 0.01       # rad/s or m/s


class Platform:
    """Own subscription to /platform/joint_states: what the simulation says, not the API."""

    def __init__(self, node: Node):
        self._lock = threading.Lock()
        self.q = None
        self.samples = []
        node.create_subscription(JointState, '/platform/joint_states', self._on, 50)

    def _on(self, msg):
        pos = dict(zip(msg.name, msg.position))
        if all(n in pos for n in NAMES.values()):
            with self._lock:
                self.q = {k: pos[n] for k, n in NAMES.items()}
                self.samples.append((time.monotonic(), dict(self.q)))
                del self.samples[:-4000]

    def now(self):
        with self._lock:
            return None if self.q is None else dict(self.q)

    def moved_between(self, t0, t1, thresh=0.01):
        """True if any axis changed by more than ``thresh`` inside [t0, t1]."""
        with self._lock:
            data = [q for t, q in self.samples if t0 <= t <= t1]
        if len(data) < 2:
            return False
        return any(max(q[k] for q in data) - min(q[k] for q in data) > thresh for k in NAMES)

    def quiet(self, seconds=0.6):
        """True when no axis moved more than QUIET_RATE*seconds over the last ``seconds``."""
        end = time.monotonic()
        with self._lock:
            data = [q for t, q in self.samples if end - seconds <= t <= end]
        if len(data) < 3:
            return False
        return all(max(q[k] for q in data) - min(q[k] for q in data) <= QUIET_RATE * seconds
                   for k in NAMES)


def run(base, platform: Platform) -> list:
    results = []
    api = Api(base)

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}', flush=True)
        return bool(ok)

    def req(method, path, payload=None, raw=None, headers=None):
        body = raw
        if body is None and payload is not None:
            body = json.dumps(payload).encode()
        h = dict(JSON if body is not None else {})
        h.update(headers or {})
        status, hdrs, data = api.http.request(method, path, h, body, 30.0)
        try:
            return status, (json.loads(data) if data else None), hdrs
        except ValueError:
            return status, data, hdrs

    def cam():
        return req('GET', '/api/camera')[1] or {}

    def goto(**axes):
        return req('POST', '/api/camera', {'op': 'goto', **axes})

    def settled(timeout=40.0):
        return wait_for(lambda: cam().get('state') == 'idle' and platform.quiet(0.5), timeout, 0.2)

    def reached(**target):
        """Wait until the MEASURED pose is at the target and quiet (the status is only 5 Hz and the
        simulation slow, so 'state idle' right after the POST proves nothing)."""
        def ok():
            q = platform.now()
            return q and all(abs(q[k] - v) < TOL_RAD for k, v in target.items()) \
                and platform.quiet(0.5)
        return wait_for(ok, 90.0, 0.2)

    def frame():
        status, data, _ = req('GET', '/frame/rgb.png')
        return decode_png(data).astype(np.int16) if status == 200 else None

    ready = wait_for(lambda: cam().get('available') and platform.now() is not None, 90.0, 1.0)
    doc = cam()
    lim = doc.get('limits') or {}
    record('A GET /api/camera: platform hazır, boşta, sınırlar ve poz var',
           bool(ready) and doc.get('state') == 'idle' and doc.get('q') is not None
           and lim.get('pan') == [-1.2, 1.2] and lim.get('tilt') == [-0.6, 1.2]
           and lim.get('slide') == [0.0, 2.5] and 'AŞAĞI' in doc.get('tilt_note', ''),
           str(doc)[:200])
    if not ready:
        return results

    # ---- B. goto
    settled()
    f0, f1 = frame(), frame()
    noise = float(np.abs(f0 - f1).mean()) if f0 is not None and f1 is not None else None
    status, body, _ = goto(pan=0.4, tilt=0.3, speed=1.0)
    record('B goto: 200 kabul edildi', status == 200 and body.get('ok'), f'http={status} {body}')
    done = reached(pan=0.4, tilt=0.3)
    q = platform.now()
    record('B platform ölçülerek hedefte (pan 0.4, tilt 0.3)',
           bool(done) and abs(q['pan'] - 0.4) < TOL_RAD and abs(q['tilt'] - 0.3) < TOL_RAD, str(q))
    f2 = frame()
    diff = float(np.abs(f0 - f2).mean()) if f0 is not None and f2 is not None else None
    record('B canlı kare (/frame/rgb.png) platform hareketiyle değişti',
           diff is not None and noise is not None and diff > 3 * noise + 0.5,
           f'fark={diff} gürültü={noise}')
    api_q = (cam().get('q') or {})
    record('B API\'nin bildirdiği poz, bağımsız ölçümle uyumlu',
           all(abs(api_q.get(k, 9) - q[k]) < 0.05 for k in ('pan', 'tilt', 'slide')), str(api_q))

    # ---- C. step
    before = platform.now()
    status, body, _ = req('POST', '/api/camera', {'op': 'step', 'pan': -0.2, 'speed': 1.0})
    reached(pan=before['pan'] - 0.2)
    after = platform.now()
    record('C göreli adım: ölçülen poz = önceki + delta',
           status == 200 and abs(after['pan'] - (before['pan'] - 0.2)) < TOL_RAD,
           f'http={status} {body} {before} -> {after}')

    # ---- D. refusals
    settled()
    keep = platform.now()
    t0 = time.monotonic()
    s1, b1, _ = goto(pan=5.0)
    s2, b2, _ = req('POST', '/api/camera', {'op': 'jog', 'pan_rate': 9.0})
    s3, _, _ = req('POST', '/api/camera', {'op': 'goto', 'pan': 'x'})
    s4, _, _ = req('POST', '/api/camera', raw=b'{"op":"goto","pan":NaN}')
    s5, _, _ = req('POST', '/api/camera', {'op': 'goto', 'slide': 3.0})
    time.sleep(1.0)
    record('D sınır dışı hedef/hız aşımı/kötü gövde/NaN reddedildi (400), kırpılmadı',
           (s1, s2, s3, s4, s5) == (400, 400, 400, 400, 400)
           and 'out_of_limits' in str(b1) and 'rate_too_high' in str(b2), str((s1, s2, s3, s4, s5)))
    record('D reddedilen komutlar platformu ölçülerek HAREKET ETTİRMEDİ',
           not platform.moved_between(t0, time.monotonic(), 0.005) and platform.now() is not None
           and abs(platform.now()['pan'] - keep['pan']) < 0.005)

    # ---- E. jog dead-man
    settled()
    p0 = platform.now()
    t0 = time.monotonic()
    status, _, _ = req('POST', '/api/camera', {'op': 'jog', 'pan_rate': 0.3, 'ttl_ms': 250})
    time.sleep(0.4)
    moved = platform.moved_between(t0, time.monotonic(), 0.003)
    stopped = wait_for(lambda: platform.quiet(0.6), 6.0, 0.1)
    p1 = platform.now()
    record('E jog: 250 ms TTL ile bir komut, platform hareket etti ve KENDİLİĞİNDEN durdu',
           status == 200 and moved and bool(stopped) and 0.003 < abs(p1['pan'] - p0['pan']) < 0.3,
           f'pan {p0["pan"]:.3f} -> {p1["pan"]:.3f}')
    status, body, _ = req('POST', '/api/camera', {'op': 'jog', 'pan_rate': 0.3, 'ttl_ms': 60000})
    t_long = time.monotonic()
    stopped = wait_for(lambda: platform.quiet(0.6) and time.monotonic() - t_long > 0.7, 8.0, 0.1)
    record('E uzun TTL isteği sunucuda ≤ 500 ms\'e kırpılır (3 s içinde durmuş)',
           status == 200 and bool(stopped) and time.monotonic() - t_long < 4.0)

    # ---- F. frames never see credentials
    secret = 'PIN-4711-SECRET'
    s_rgb, data, hdrs = req('GET', f'/frame/rgb.png?pin={secret}',
                            headers={'Authorization': f'Bearer {secret}',
                                     'Cookie': f'session={secret}'})
    low = {k.lower(): v for k, v in hdrs.items()}
    leaked = (secret.encode() in (data if isinstance(data, bytes) else json.dumps(data).encode())
              or 'set-cookie' in low or secret in json.dumps(low))
    record('F kare yolu sorguyu/kimlik başlıklarını yok sayar, hiçbirini geri yansıtmaz',
           s_rgb == 200 and not leaked and 'no-store' in low.get('cache-control', ''))
    depth_status = req('GET', '/frame/depth.png')[0]
    record('F /frame/depth.png de çalışıyor', depth_status == 200, f'http={depth_status}')

    # ---- G. emergency stop mid-goto (last: no arm here, so /api/reset cannot be verified)
    goto(pan=0.0, tilt=0.0, speed=1.0)
    reached(pan=0.0, tilt=0.0)
    status, _, _ = goto(pan=-1.0, tilt=0.0, speed=0.3)
    t_move = time.monotonic()
    moving = wait_for(lambda: platform.moved_between(t_move, time.monotonic(), 0.02), 15.0, 0.05)
    t_stop = time.monotonic()
    holder = {}
    poster = threading.Thread(target=lambda: holder.update(r=req('POST', '/api/stop', {})),
                              daemon=True)
    poster.start()
    # the platform must be quiet long before POST /api/stop returns (without an arm that call
    # waits for its stop timeout): measured concurrently, not after the answer
    quiet = wait_for(lambda: platform.quiet(0.5), 3.0, 0.05)
    t_quiet = time.monotonic() - t_stop
    poster.join(30.0)
    s_stop = holder.get('r', (None,))[0]
    q_stop = platform.now()
    time.sleep(1.0)
    q_later = platform.now()
    record('G acil dur: platform hareket halindeyken ölçülerek ≤ 3 s içinde durdu, yerinde kaldı',
           status == 200 and bool(moving) and s_stop == 200 and bool(quiet)
           and abs(q_later['pan'] - q_stop['pan']) < 0.01 and q_stop['pan'] > -0.95,
           f'durma={t_quiet:.2f}s pan={q_stop["pan"]:.3f}')
    status, body, _ = goto(pan=0.0)
    doc = cam()
    record('G kilitliyken kamera hareketi 423, durum belgesinde estop', status == 423
           and doc.get('estop') is True and doc.get('state') in ('idle', None), str(body))
    status, _, _ = req('POST', '/api/camera', {'op': 'stop'})
    record('G kilitliyken stop yine kabul edilir', status == 200)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('camera_api_check_node')
    node.set_parameters([rclpy.parameter.Parameter('use_sim_time',
                                                   rclpy.Parameter.Type.BOOL, True)])
    platform = Platform(node)
    spinner = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spinner.start()
    try:
        results = run(opts.url, platform)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 13 else 1)


if __name__ == '__main__':
    main()
