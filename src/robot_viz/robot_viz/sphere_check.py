"""End-to-end check of sphere frames over the Robot API (L8), measured from the simulation. Run via
``run_selftest sphere`` (mock_components):

    ros2 run robot_viz sphere_check [--url http://127.0.0.1:8080]

Tool pose is read from TF (the URDF kinematics), NOT from the Robot API or the controller's own FK.

 A. ``GET /api/frames`` lists ``types`` [user, sphere]; ``POST /api/frames/reach`` (C, r) says the
    start region is reachable; PUT stores the sphere in ``spheres.yaml`` (``frames.yaml`` unchanged)
 B. compatibility: an old client's PUT (user frames only) keeps the sphere; unknown ``type`` and bad
    spheres are 400 with the reason and change nothing
 C. ``goto_waypoint`` (MoveIt) to a pose on the sphere: |tool - C| = r, tool axis looks at C
 D. increments: azimuth +10 deg, elevation +1 deg, r +10 mm (after E): each measured, the other two
    unchanged, tool axis keeps looking at C
 E. hold: azimuth rate for 3 s keeps r and look-at; the client stops sending -> arm measured quiet
 F. off the sphere (three +10 mm world X steps): sphere jog refuses and does not move; ``align``
    brings the tool back onto the sphere looking at C
 G. emergency stop mid sphere-jog: arm measured stopped, POST jog 423, PUT frames still answers,
    reset, sphere jog works again
Exit code 0 only if every check passed.
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import rclpy
import yaml
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from robot_core.frames import sphere_frame
from robot_core.jog import solve_sphere_pose
from robot_core.kinematics import Chain
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.se3 import matrix_from_quat
from tf2_ros import Buffer, TransformException, TransformListener

from robot_viz.jobs_check import wait_job
from robot_viz.jog_check import JogClient
from robot_viz.stop_check import WAIT_S, Api, Sampler, wait_for

CENTER, RADIUS, TOOL_AXIS = (0.0, 0.0, 0.2), 0.7, '-z'
SEED = [0.0, 0.357, 1.79, 0.0, -0.751, -0.142]         # same pose family as tests/test_sphere.py
START_EL, START_ROLL = math.radians(10), -3.0
JSON = {'Content-Type': 'application/json'}
DOME = {'type': 'sphere', 'name': 'dome', 'center': list(CENTER), 'radius': RADIUS,
        'tool_axis': TOOL_AXIS}
THREE = {'origin': [0.3, 0.0, 0.1], 'x': [0.4, 0.0, 0.1], 'xy': [0.3, 0.1, 0.1]}
MIN_CHECKS = 23


def tool_pose(buffer, tip):
    """(position, rotation matrix) of the tool link in 'world' from TF, or None."""
    try:
        t = buffer.lookup_transform('world', tip, rclpy.time.Time(),
                                    rclpy.duration.Duration(seconds=5.0)).transform
    except TransformException:
        return None
    q = t.rotation
    return (np.array([t.translation.x, t.translation.y, t.translation.z]),
            matrix_from_quat(np.array([q.x, q.y, q.z, q.w])))


def on_sphere(pose):
    """(azimuth deg, elevation deg, r m, look-at error deg) of a TF tool pose around DOME."""
    pos, rot = pose
    d = pos - np.array(CENTER)
    r = float(np.linalg.norm(d))
    axis = np.array([0.0, 0.0, -1.0]) if TOOL_AXIS == '-z' else np.array([0.0, 0.0, 1.0])
    look = rot @ axis
    err = math.degrees(math.acos(max(-1.0, min(1.0, float(look @ -d / r)))))
    return (math.degrees(math.atan2(d[1], d[0])), math.degrees(math.asin(d[2] / r)), r, err)


def run(base: str, config, state: JointStateCache, buffer: Buffer, sampler: Sampler,
        data_dir: Path) -> list:
    results = []
    api = Api(base)
    tip = config.tip_link

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}', flush=True)
        return bool(ok)

    def robot():
        return api.robot() or {}

    def request(method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        status, _, data = api.http.request(method, path, JSON, body, 60.0)
        try:
            return status, json.loads(data) if data else None
        except ValueError:
            return status, None

    def measured():
        pose = tool_pose(buffer, tip)
        return None if pose is None else on_sphere(pose)

    def settle(seconds=0.6):
        time.sleep(seconds)
        wait_for(lambda: sampler.last_moving_time(time.monotonic() - 0.5, time.monotonic())
                 is None, 20.0, 0.1)

    def quiet_within(t0, deadline):
        wait_for(lambda: time.monotonic() > t0 + deadline, deadline + 1.0, 0.02)
        last = sampler.last_moving_time(t0, time.monotonic())
        return last is None or last - t0 <= deadline

    def jog_until_done(client, body, timeout=60.0):
        status, _ = client.post({'frame': 'dome', **body})
        done = wait_for(lambda: robot().get('jog', {}).get('status') == 'done', timeout, 0.1)
        settle()
        return status, bool(done)

    wait_for(lambda: robot().get('mode'), WAIT_S, 0.5)

    # ---- A. listing, reach, create
    status, doc = request('GET', '/api/frames')
    record('A GET /api/frames: types [user, sphere], spheres boş', status == 200
           and doc['types'] == ['user', 'sphere'] and doc['spheres'] == [],
           str(doc and doc['types']))
    far = request('POST', '/api/frames/reach', {'center': list(CENTER), 'radius': 2.0})[1]
    record('A erişilemeyen küre (r 2 m): reachable false, aralık yok',
           far is not None and far['reachable'] is False and far['elevation_range_deg'] is None)
    frames_before = (data_dir / 'frames.yaml').read_bytes() if (
        data_dir / 'frames.yaml').exists() else None
    status, doc = request('PUT', '/api/frames', {'frames': [{'name': 'tbl', 'points': THREE}],
                                                 'spheres': [DOME]})
    stored = yaml.safe_load((data_dir / 'spheres.yaml').read_text(encoding='utf-8'))
    record('A PUT: küre spheres.yaml\'a yazıldı (type sphere, sürüm 1)', status == 200
           and stored['spheres_version'] == 1 and stored['spheres'][0]['type'] == 'sphere'
           and doc['spheres'] == [DOME])
    frames_yaml = yaml.safe_load((data_dir / 'frames.yaml').read_text(encoding='utf-8'))
    record('A frames.yaml eski biçimde (frames_version 1, küre yok)',
           frames_yaml['frames_version'] == 1 and 'sphere' not in json.dumps(frames_yaml)
           and frames_before in (None, frames_before))

    # ---- B. compatibility and refusals
    status, doc = request('PUT', '/api/frames', {'frames': [{'name': 'tbl', 'points': THREE}]})
    record('B eski istemci PUT\'u (yalnız frames) küreyi silmedi', status == 200
           and [s['name'] for s in doc['spheres']] == ['dome'])
    status, doc = request('PUT', '/api/frames', {'frames': [{'type': 'torus', 'name': 'x',
                                                             'points': THREE}]})
    record('B bilinmeyen type: 400, adı söylüyor, hiçbir şey değişmedi', status == 400
           and 'torus' in doc['error'] and request('GET', '/api/frames')[1]['spheres'] == [DOME])
    status, doc = request('PUT', '/api/frames', {'frames': [], 'spheres': [{**DOME, 'radius': 9}]})
    record('B geçersiz yarıçap: 400 + neden, küre duruyor', status == 400
           and 'yarıçap' in doc['error'] and request('GET', '/api/frames')[1]['spheres'] == [DOME])

    # ---- C. go to a pose on the sphere with MoveIt
    profile = load_profile(os.environ.get('ROBOT_PROFILE') or PROFILE_PATH)
    chain = Chain(profile)
    frame = sphere_frame('dome', CENTER, RADIUS, TOOL_AXIS)
    q0 = solve_sphere_pose(chain, frame, 0.0, START_EL, SEED, RADIUS, START_ROLL)
    status, _ = request('PUT', '/api/waypoints/domestart', {'joints': [float(v) for v in q0],
                                                              'frame': 'World'})
    s_job, _, body = api.post('/api/jobs', {'command': 'goto_waypoint domestart'}, timeout=30.0)
    done = wait_job(api, body['id'], 'done', WAIT_S) if s_job == 202 else None
    settle(1.0)
    m = measured()
    record('C goto_waypoint (MoveIt) ile küre üzerindeki poza gidildi', status in (200, 201)
           and bool(done), str(done and done.get('result')))
    record('C ölçülen (TF): |TCP - C| = r ±5 mm, takım ekseni C\'ye bakıyor (< 2°)',
           bool(m) and abs(m[2] - RADIUS) < 0.005 and m[3] < 2.0,
           f'r={m and round(m[2], 4)} bakış={m and round(m[3], 2)}°')

    status, reach = request('POST', '/api/frames/reach', {'center': list(CENTER),
                                                           'radius': RADIUS,
                                                           'tool_axis': TOOL_AXIS})
    lo_hi = (reach or {}).get('elevation_range_deg')
    record('C erişim aralığı: bu küre mevcut pozdan erişilebilir, 10° yükselme aralıkta',
           status == 200 and reach['reachable'] and lo_hi and lo_hi[0] <= 10 <= lo_hi[1]
           and (reach['grid']['cells']), str(reach and reach.get('summary')))

    # ---- D. increments
    c = JogClient(base)
    m0 = measured()
    status, done = jog_until_done(c, {'increment': {'axis': 0, 'amount': 10}})
    m1 = measured()
    record('D azimut +10°: ölçülen 10° ±0.5°, yükselme ve r değişmedi, bakış < 2°',
           status == 200 and done and abs((m1[0] - m0[0]) - 10.0) < 0.5
           and abs(m1[1] - m0[1]) < 0.5 and abs(m1[2] - m0[2]) < 0.004 and m1[3] < 2.0,
           f'Δaz={m1[0] - m0[0]:.2f}° Δel={m1[1] - m0[1]:.2f}° Δr={(m1[2] - m0[2]) * 1000:.1f} mm '
           f'bakış={m1[3]:.2f}° http={status} done={done}')
    status, done = jog_until_done(c, {'increment': {'axis': 1, 'amount': 1}})
    m2 = measured()
    record('D yükselme +1°: ölçülen 1° ±0.3°, azimut ve r değişmedi, bakış < 2°',
           status == 200 and done and abs((m2[1] - m1[1]) - 1.0) < 0.3
           and abs(m2[0] - m1[0]) < 0.5 and abs(m2[2] - m1[2]) < 0.004 and m2[3] < 2.0,
           f'Δel={m2[1] - m1[1]:.2f}°')
    m3 = m2
    bad = c.post({'frame': 'dome', 'increment': {'axis': 0, 'amount': 5}})
    record('D izin verilmeyen artım (5°) 400', bad[0] == 400, f'http={bad[0]} {bad[1]}')
    c.release()

    # ---- E. hold-to-run along the sphere, then the client goes quiet
    r_hold = measured()[2]
    t_begin = time.monotonic()
    c.hold([0.3, 0, 0], frame='dome')
    time.sleep(3.0)
    mid = measured()
    state_doc = robot()
    t_gone = c.stop_sending()
    quiet = quiet_within(t_gone, 1.0)
    settle()
    end = measured()
    record('E azimut basılı-tut: durum jogging, azimut ilerledi, r ±5 mm, bakış < 2°',
           state_doc.get('state') == 'jogging' and abs(mid[0] - m3[0]) > 1.0
           and abs(mid[2] - r_hold) < 0.005 and mid[3] < 2.0,
           f'durum={state_doc.get("state")} Δaz={mid[0] - m3[0]:.2f}° r_hata='
           f'{(mid[2] - r_hold) * 1000:.1f} mm bakış={mid[3]:.2f}° '
           f'({time.monotonic() - t_begin:.1f} s)')
    record('E istemci sustu: kol ölçülerek ≤ 1 s içinde durdu ve küre üzerinde kaldı', quiet
           and abs(end[2] - r_hold) < 0.005 and end[3] < 2.0,
           f'durdu={quiet} r_hata={(end[2] - r_hold) * 1000:.1f} mm bakış={end[3]:.2f}°')
    c.release()

    m3 = end
    status, done = jog_until_done(c, {'increment': {'axis': 2, 'amount': 10}})
    m4 = measured()
    record('D r +10 mm: ölçülen 10 mm ±2 mm, açılar değişmedi, bakış < 2°',
           status == 200 and done and abs((m4[2] - m3[2]) - 0.010) < 0.002
           and abs(m4[0] - m3[0]) < 0.5 and abs(m4[1] - m3[1]) < 0.5 and m4[3] < 2.0,
           f'Δr={(m4[2] - m3[2]) * 1000:.1f} mm')
    c.release()

    # ---- F. off the sphere: refused, then align
    w = JogClient(base)
    for _ in range(3):
        w.post({'increment': {'axis': 0, 'amount': 10}, 'frame': 'World'})
        wait_for(lambda: robot().get('jog', {}).get('status') == 'done', 30.0, 0.1)
        settle(0.3)
    w.release()
    off = measured()
    record('F küre dışına çıkıldı (|TCP-C| ≥ 15 mm değişti)', abs(off[2] - end[2]) > 0.015,
           f'Δr={(off[2] - end[2]) * 1000:.1f} mm')
    before = tool_pose(buffer, tip)[0]
    c.post({'frame': 'dome', 'twist': [0.3, 0, 0]})
    time.sleep(1.0)
    jog_doc = json.loads(api.http.request('GET', '/api/jog')[2])
    after = tool_pose(buffer, tip)[0]
    record('F küre jog\'u reddetti (hizalayın) ve kol KIPIRDAMADI', 'hizalayın' in ' '.join(
        jog_doc['messages']) and float(np.linalg.norm(after - before)) < 0.001,
           ' '.join(jog_doc['messages'])[:100])
    c.release()
    aligned = False
    for _ in range(400):
        c.post({'frame': 'dome', 'align': True})
        time.sleep(0.05)
        if 'hizalı' in ' '.join(json.loads(api.http.request('GET', '/api/jog')[2])['messages']):
            aligned = True
            break
    c.release()
    settle()
    fin = measured()
    record('F align: ölçülen |TCP-C| = r ±5 mm ve bakış < 2°', aligned and abs(fin[2] - RADIUS
           ) < 0.012 and fin[3] < 2.0, f'r={fin[2]:.4f} bakış={fin[3]:.2f}°')

    # ---- G. emergency stop mid sphere-jog
    c.hold([0.3, 0, 0], frame='dome')
    time.sleep(1.0)
    t_stop = time.monotonic()
    s_stop = api.post('/api/stop', {'reason': 'sphere_check'}, timeout=30.0)[0]
    c.stop_sending()
    quiet = quiet_within(t_stop, 2.0)
    s_jog = c.post({'frame': 'dome', 'twist': [0.3, 0, 0]})[0]
    s_put = request('PUT', '/api/frames', {'frames': [], 'spheres': [DOME]})[0]
    p1 = tool_pose(buffer, tip)[0]
    time.sleep(0.8)
    p2 = tool_pose(buffer, tip)[0]
    record('G acil dur: kol ölçülerek ≤ 2 s içinde durdu ve kıpırdamıyor', s_stop == 200 and quiet
           and float(np.linalg.norm(p2 - p1)) < 0.001)
    record('G kilitliyken küre jog\'u 423', s_jog == 423, f'http={s_jog}')
    record('G kilitliyken çerçeve listesi hâlâ yazılabilir (200)', s_put == 200)
    s_reset = api.post('/api/reset', {}, timeout=30.0)[0]
    s_after = c.post({'frame': 'dome', 'increment': {'axis': 0, 'amount': 1}})[0]
    done = wait_for(lambda: robot().get('jog', {}).get('status') == 'done', 40.0, 0.1)
    record('G reset sonrası küre jog\'u yine çalışıyor', s_reset == 200 and s_after == 200
           and bool(done), f'reset={s_reset} jog={s_after}')
    c.release()
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    data_dir = Path(os.environ.get('ROBOT_DATA_DIR', '/tmp/jog_data'))
    rclpy.init(args=ros_args)
    node = Node('sphere_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    sampler = Sampler(state, config.joint_names)
    sampler.start()
    try:
        results = run(opts.url, config, state, buffer, sampler, data_dir)
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
