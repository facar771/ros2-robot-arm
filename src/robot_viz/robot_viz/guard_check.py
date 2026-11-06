"""End-to-end check of the path guard (F4b), measured from the simulation. Run via
``run_selftest pathguard`` (mock_components + MoveIt + the Robot API):

    ros2 run robot_viz guard_check [--url http://127.0.0.1:8080]

Positions come from TF (the URDF kinematics) and from the check's own /joint_states samples, NOT
from the Robot API or the guard itself.

 A. park transit: ``ready`` from ``home`` is the straight slow joint move (<= 30 % of the maximum
    speed, every sampled TCP inside the park envelope), the arm ends at ``ready``
 B. the safe box ALONG the path: ready -> B (inside) passes; B -> A (both inside, the straight joint
    line leaves the box) is REJECTED with the violation point and coordinates, the arm does not
    move; B -> C (inside) passes
 C. strict named pose: from a random point ``go_to_named_pose('home')`` is REJECTED (arm unmoved),
    while the ``home`` command ("Eve dön") goes to ready first and parks: the arm ends at home
 D. ``ready`` -> ``home`` PASSES (park move), speed <= 30 %, TCP inside the park envelope
 E. emergency stop mid park move: the arm is measured stopped within 2 s, then reset and "Eve dön"
    brings it home again
Exit code 0 only if every check passed.
"""
import argparse
import bisect
import dataclasses
import json
import os
import sys
import time

import numpy as np
import rclpy
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache, MoveItMotion
from robot_controller.config_loader import load_robot_config, load_safety_config
from robot_controller.guard_setup import build_path_guard, load_park_config
from robot_core.jogapi import READY_NAME, READY_POSE
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from tf2_ros import Buffer, TransformException, TransformListener

from robot_viz.jobs_check import wait_job
from robot_viz.stop_check import WAIT_S, Api, Sampler, wait_for

JSON = {'Content-Type': 'application/json'}
# two points INSIDE the box whose straight joint line leaves it (midpoint 18 cm outside), and a
# third one that B reaches without leaving the box (tests/test_pathguard.py checks all of this)
POINT_A = [-0.56, 1.31, 0.15, 0.0, -1.48, 0.0]
POINT_B = [0.57, 0.57, 1.25, 0.0, 1.3, 0.0]
POINT_C = [0.355, 0.908, 1.049, 0.0, 1.361, 0.0]
SPEED_CAP = 0.3            # park speed, fraction of the 1.0 rad/s joint maximum
SPEED_SLACK = 1.3          # own measurement over 0.1 s windows may read a bit above the cap
POSE_TOLERANCE = 0.02      # rad
STOP_DEADLINE_S = 2.0
MIN_CHECKS = 20


def tool_xyz(buffer, tip):
    try:
        t = buffer.lookup_transform('world', tip, rclpy.time.Time(),
                                    rclpy.duration.Duration(seconds=5.0)).transform
    except TransformException:
        return None
    return np.array([t.translation.x, t.translation.y, t.translation.z])


def max_speed(samples, t0, t1, window=0.1):
    """Largest joint speed (rad/s) over ``window`` s in [t0, t1] from (time, q) samples."""
    data = [s for s in samples if t0 - 1.0 <= s[0] <= t1]
    times = [s[0] for s in data]
    best = 0.0
    for t, q in data:
        if t < t0:
            continue
        k = bisect.bisect_right(times, t - window) - 1
        if k >= 0 and t - times[k] > 0:
            best = max(best, max(abs(a - b) for a, b in zip(q, data[k][1])) / (t - times[k]))
    return best


def inside(chain, zone, samples, t0, t1, step=3):
    """True when the TCP of every (step-th) sample in [t0, t1] lies inside ``zone``."""
    lo, hi = np.array(zone.lower), np.array(zone.upper)
    for _, q in [s for s in samples if t0 <= s[0] <= t1][::step]:
        p = chain.fk(q)[:3, 3]
        if np.any(p < lo - 1e-9) or np.any(p > hi + 1e-9):
            return False
    return True


def run(base: str, node: Node, config, state: JointStateCache, buffer: Buffer,
        sampler: Sampler) -> list:
    results = []
    api = Api(base)
    names = config.joint_names
    profile = load_profile(os.environ.get('ROBOT_PROFILE') or PROFILE_PATH)
    chain = Chain(profile)
    park = load_park_config()
    home = [0.0] * len(names)
    ready = list(READY_POSE)

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}', flush=True)
        return bool(ok)

    def joints():
        pos = state.joint_positions() or {}
        return [pos.get(n, float('nan')) for n in names]

    def near(target, tol=POSE_TOLERANCE):
        return max(abs(a - b) for a, b in zip(joints(), target)) <= tol

    def request(method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        status, _, data = api.http.request(method, path, JSON, body, 60.0)
        try:
            return status, json.loads(data) if data else None
        except ValueError:
            return status, None

    def run_job(command, state_name='done', timeout=WAIT_S):
        t0 = time.monotonic()
        status, _, body = api.post('/api/jobs', {'command': command}, timeout=30.0)
        job = wait_job(api, body['id'], state_name, timeout) if status == 202 else None
        time.sleep(0.8)
        return job, t0, time.monotonic()

    wait_for(lambda: (api.robot() or {}).get('mode'), WAIT_S, 0.5)
    wait_for(lambda: state.joint_positions(), WAIT_S, 0.2)
    zone_park = park.zone

    # ---- A. park transit home -> ready
    record('A başlangıç: kol home konumunda', near(home), f'{np.round(joints(), 3).tolist()}')
    job, t0, t1 = run_job('ready')
    speed = max_speed(sampler.samples, t0, t1)
    record('A home -> ready (park geçişi): iş tamam, kol ready\'de',
           bool(job) and near(ready), str(job and job.get('result')))
    record('A park hızı ölçüldü: ≤ %30 (azami 1 rad/s) ve kol gerçekten hareket etti',
           0.05 < speed <= SPEED_CAP * SPEED_SLACK, f'azami eklem hızı {speed:.3f} rad/s')
    record('A ölçülen TCP yolu park zarfının içinde (örneklenen gerçek yürütme)',
           inside(chain, zone_park, sampler.samples, t0, t1), '')

    # ---- B. the safe box along the path
    for name, q in (('wp_b', POINT_B), ('wp_a', POINT_A), ('wp_c', POINT_C)):
        status, _ = request('PUT', f'/api/waypoints/{name}', {'joints': q, 'frame': 'World'})
        if status not in (200, 201):
            print(f'[!] {name} kaydedilemedi: {status}', flush=True)
    job, _, _ = run_job('goto_waypoint wp_b')
    record('B ready -> B (kutu içi yol) geçti, kol B\'de', bool(job) and near(POINT_B, 0.03),
           str(job and job.get('result')))
    before = joints()
    t_before = time.monotonic()
    job, _, t_after = run_job('goto_waypoint wp_a', 'failed', 120.0)
    text = json.dumps(job, ensure_ascii=False) if job else ''
    record('B B -> A (ikisi de kutu içinde, yol dışarı çıkıyor): REDDEDİLDİ, ihlal noktası + '
           'koordinat söyleniyor, "hareket başlamadı"',
           bool(job) and 'güvenli kutunun dışında' in text and 'TCP (' in text
           and 'hareket başlamadı' in text, text[:300])
    drift = max(abs(a - b) for a, b in zip(before, joints()))
    record('B reddedilen hareket: kol KIPIRDAMADI (ölçülen)',
           drift < 0.005 and sampler.displacement(t_before, t_after) < 0.005,
           f'(kayma {drift:.4f} rad)')
    job, _, _ = run_job('goto_waypoint wp_c')
    record('B B -> C (kutu içi yol) yine geçti: reddetme yalnız ihlalde', bool(job) and near(
        POINT_C, 0.03), str(job and job.get('result')))

    # ---- C. strict named pose vs "Eve dön"
    motion = MoveItMotion(node, dataclasses.replace(
        config, poses={**config.poses, READY_NAME: READY_POSE}),
        max_velocity_scale=load_safety_config().max_velocity_scale)
    cfg = dataclasses.replace(config, poses={**config.poses, READY_NAME: READY_POSE})
    guard = build_path_guard(motion, state, cfg, load_safety_config(), None,
                             lambda m: print(f'[!] {m}', flush=True))
    before = joints()
    out = guard.move_to_named_pose('home', tuple(home))
    record('C rastgele nokta -> home (go_to_named_pose): REDDEDİLDİ, kol KIPIRDAMADI',
           (not out.ok) and out.code_name == 'PARK_REJECTED' and 'Eve dön' in out.message
           and max(abs(a - b) for a, b in zip(before, joints())) < 0.005, out.message[:160])
    job, t0, t1 = run_job('home')
    record('C "Eve dön" (home komutu) rastgele noktadan: önce ready, sonra park, kol home\'da',
           bool(job) and near(home), str(job and job.get('result')))
    tcp = tool_xyz(buffer, config.tip_link)
    record('C home konumu ölçüldü (TF): TCP (0, 0, 1.24)', tcp is not None and abs(tcp[2] - 1.24)
           < 0.02 and abs(tcp[0]) < 0.02, f'TCP {None if tcp is None else np.round(tcp, 3)}')

    # ---- F. the demo scenario's way: from home straight to a Cartesian target inside the box
    from robot_core.skills import Skills
    from robot_core.supervisor import EStop, GuardedMotion
    skills = Skills(cfg, GuardedMotion(motion, EStop(), None, guard), GuardedMotion(
        motion, EStop(), None, guard), None, zone=load_safety_config().zone)
    out = skills.return_home()
    record('F "Eve dön" zaten home\'dayken sorunsuz (hareketsiz başarı)', out.ok and near(home),
           out.message)
    out = skills.go_to_position(0.40, -0.10, 0.35)
    print(f'       home -> go_to_position(0.40, -0.10, 0.35): ok={out.ok} {out.message[:240]}',
          flush=True)
    record('F home\'dan kutu içi kartezyen hedefe yaklaşma kuralıyla gidildi', out.ok,
           out.message[:200])
    out = skills.return_home()
    record('F kartezyen hedeften "Eve dön" geçti', out.ok and near(home), out.message[:200])

    # ---- D. ready -> home passes
    job, _, _ = run_job('ready')
    before = joints()
    job, t0, t1 = run_job('home')
    speed = max_speed(sampler.samples, t0, t1)
    record('D ready -> home GEÇTİ (park geçişi), kol home\'da', bool(job) and near(home),
           str(job and job.get('result')))
    record('D park hızı ≤ %30 ve ölçülen TCP park zarfında',
           0.05 < speed <= SPEED_CAP * SPEED_SLACK and inside(
               chain, zone_park, sampler.samples, t0, t1), f'azami eklem hızı {speed:.3f} rad/s')

    # ---- E. emergency stop in the middle of a park move
    status, _, body = api.post('/api/jobs', {'command': 'ready'}, timeout=30.0)
    wait_for(lambda: max(abs(a - b) for a, b in zip(joints(), home)) > 0.2, 60.0, 0.1)
    t_stop = time.monotonic()
    s_stop, _, answer = api.post('/api/stop', {'reason': 'guard_check'}, timeout=30.0)
    wait_for(lambda: sampler.last_moving_time(t_stop + 0.1, time.monotonic()) is None
             and time.monotonic() > t_stop + 0.5, 20.0, 0.1)
    last = sampler.last_moving_time(t_stop, time.monotonic())
    record('E acil dur park geçişinin ortasında: kol ölçülerek ≤ 2 s içinde durdu',
           s_stop == 200 and (last is None or last - t_stop <= STOP_DEADLINE_S),
           f'(durma süresi {None if last is None else round(last - t_stop, 2)} s)')
    job = wait_job(api, body['id'], 'cancelled', 30.0) if status == 202 else None
    record('E iş "cancelled" (acil dur)', bool(job), '')
    s_reset, _, _ = api.post('/api/reset', {})
    record('E reset (kol duruyor) -> 200', s_reset == 200, f'{s_reset}')
    job, _, _ = run_job('home')
    record('E acil duruş sonrası "Eve dön" kolu home\'a getirdi', bool(job) and near(home),
           str(job and job.get('result')))
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('guard_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    sampler = Sampler(state, config.joint_names)
    sampler.start()
    try:
        results = run(opts.url, node, config, state, buffer, sampler)
    finally:
        sampler.close()
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= MIN_CHECKS else 1)


if __name__ == '__main__':
    main()
