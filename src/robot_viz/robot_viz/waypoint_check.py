"""End-to-end check of the waypoint API (O2), measured from the simulation. Run via
``run_selftest waypoints`` (mock_components) or ``run_selftest gazebo-waypoints`` (Gazebo):

    ros2 run robot_viz waypoint_check [--url http://127.0.0.1:8080]

 A. at 'ready' POST /api/waypoints/record saves the MEASURED joints and TCP (TF agrees), the file
    is in the data directory
 B. refused and nothing written: record at the upright home pose (TCP outside the box), PUT with
    TCP outside the box, PUT beyond a joint limit
 C. job ``goto_waypoint`` from home: the arm is MEASURED at the saved joints and the tool (TF) at
    the saved position; the planner was MoveIt (the job reports it)
 D. a second point (PUT), there and back
 E. collision: a box on the goal -> the job fails with MoveIt's reason and the arm is MEASURED
    not to have moved; box removed -> the same job succeeds
 F. emergency stop mid-goto: arm measured stopped, job cancelled, goto 423 while latched, point
    edits still work, reset, goto works again
 G. goto while a jog lease is held 409; jog while a goto runs 409
 H. rename / DELETE / unknown name; the file follows; no temp files left behind
Exit code 0 only if every check passed.
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import rclpy
import yaml
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache, PlanningSceneAdapter
from robot_controller.config_loader import load_robot_config
from robot_core.models import BoxObstacle
from tf2_ros import Buffer, TransformListener

from robot_viz.jobs_check import job
from robot_viz.stop_check import WAIT_S, Api, Sampler, wait_for
from robot_viz.viz_check import tool_from_tf

JSON = {'Content-Type': 'application/json'}
LEASE = 'wpcheck-aaaaaaaa'
# 'ready' turned 0.6 rad about the base: TCP ~ (0.42, 0.29, 0.43), 0.3 m from 'ready'
POINT_SIDE = [0.6, -0.1, 1.65, 0.0, 1.2, 0.0]
HOME = [0.0] * 6                                          # TCP z = 1.24 m: outside the box
JOINT_TOL = 0.03       # rad: planner goal tolerance is 0.01, mock/Gazebo settle a little off
TCP_TOL = 0.015        # m
ESTOP_DEADLINE_S = 2.0


def run(base, config, state, buffer, sampler, scene, data_dir: Path) -> list:
    results = []
    api = Api(base)
    tip = config.tip_link
    names = list(config.joint_names)
    wp_file = data_dir / 'waypoints.yaml'

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name} {detail}', flush=True)
        return bool(ok)

    def req(method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        status, _, data = api.http.request(method, path, JSON if body else {}, body, 30.0)
        try:
            return status, (json.loads(data) if data else None)
        except ValueError:
            return status, None

    def robot():
        return api.robot() or {}

    def joints():
        pos = state.joint_positions() or {}
        return [pos.get(n, float('nan')) for n in names]

    def finished(job_id, timeout=WAIT_S):
        doc = wait_for(lambda: (lambda d: d if d and d['state'] != 'running' else None)(
            job(api, job_id)), timeout, 0.1)
        return doc or {}

    def go(command, timeout=WAIT_S):
        status, _, body = api.post('/api/jobs', {'command': command}, timeout=30.0)
        return status, (finished(body['id'], timeout) if status == 202 else {})

    def standing_since(t0, deadline):
        end = t0 + deadline
        wait_for(lambda: time.monotonic() > end, deadline + 1.0, 0.02)
        last = sampler.last_moving_time(t0, time.monotonic())
        return last is None or last - t0 <= deadline

    def names_on_disk():
        return [w['name'] for w in yaml.safe_load(wp_file.read_text())['waypoints']] \
            if wp_file.exists() else []

    wait_for(lambda: robot().get('mode'), WAIT_S, 0.5)
    record('0 GET /api/waypoints: boş liste, eklem adları robotla aynı',
           req('GET', '/api/waypoints')[1] is not None
           and req('GET', '/api/waypoints')[1]['waypoints'] == []
           and req('GET', '/api/waypoints')[1]['joint_names'] == names)

    # ---- A. record at 'ready'
    status, done = go('ready')
    record('A ready işi tamam', status == 202 and done.get('state') == 'done', done.get('state'))
    time.sleep(1.0)
    status, doc = req('POST', '/api/waypoints/record', {'name': 'ready_pt'})
    q = joints()
    p_tf = tool_from_tf(buffer, tip)
    record('A record: 201, kaydedilen eklemler ölçülenle aynı',
           status == 201 and max(abs(a - b) for a, b in zip(doc['joints'], q)) < 0.01,
           f'http={status}')
    record('A kaydedilen TCP, TF ile tutarlı (<= 1.5 cm)',
           bool(p_tf) and math.dist(p_tf, doc['tcp']['position']) < TCP_TOL, str(p_tf))
    record('A dosya veri dizininde ve GET aynı',
           names_on_disk() == ['ready_pt'] and
           [w['name'] for w in req('GET', '/api/waypoints')[1]['waypoints']] == ['ready_pt'],
           str(wp_file))
    saved = doc
    record('A aynı ad ikinci kez: 409', req('POST', '/api/waypoints/record',
                                            {'name': 'ready_pt'})[0] == 409)

    # ---- B. refusals
    status, _ = go('home')
    time.sleep(1.0)
    before = wp_file.read_bytes()
    s1, d1 = req('POST', '/api/waypoints/record', {'name': 'at_home'})
    record('B home pozunu kaydetme: 400 (TCP kutunun dışında), dosya değişmedi',
           s1 == 400 and 'kutusunun dışında' in (d1 or {}).get('error', '')
           and wp_file.read_bytes() == before, str(d1))
    s2, d2 = req('PUT', '/api/waypoints/too_high', {'joints': HOME})
    record('B PUT kutu dışı nokta: 400', s2 == 400 and 'kutusunun dışında' in d2['error'], str(d2))
    over = list(POINT_SIDE)
    over[0] = 9.0
    s3, d3 = req('PUT', '/api/waypoints/over', {'joints': over})
    record('B PUT eklem limiti dışı: 400, dosya değişmedi',
           s3 == 400 and 'limit' in d3['error'] and wp_file.read_bytes() == before, str(d3))

    # ---- C. goto from home
    t0 = time.monotonic()
    status, done = go('goto_waypoint ready_pt')
    time.sleep(1.0)
    q = joints()
    p_tf = tool_from_tf(buffer, tip)
    record('C goto_waypoint işi bitti (MoveIt planı + yürütme)',
           status == 202 and done.get('state') == 'done',
           f'{done.get("state")} {done.get("error")} {time.monotonic() - t0:.1f} s')
    record('C kol ölçülerek kayıtlı eklemlerde',
           max(abs(a - b) for a, b in zip(q, saved['joints'])) < JOINT_TOL,
           str([round(a - b, 4) for a, b in zip(q, saved['joints'])]))
    record('C alet (TF) kayıtlı konumda',
           bool(p_tf) and math.dist(p_tf, saved['tcp']['position']) < TCP_TOL, str(p_tf))

    # ---- D. a second point
    s, d = req('PUT', '/api/waypoints/side', {'joints': POINT_SIDE})
    record('D PUT yeni nokta: 201', s == 201 and names_on_disk() == ['ready_pt', 'side'], str(s))
    status, done = go('goto_waypoint side')
    time.sleep(1.0)
    q = joints()
    record('D side noktasına gidildi (ölçülen)',
           done.get('state') == 'done'
           and max(abs(a - b) for a, b in zip(q, POINT_SIDE)) < JOINT_TOL, str(done.get('error')))

    # ---- E. collision on the goal
    tcp = saved['tcp']['position']
    placed = scene.add_box(BoxObstacle('wp_block', tcp[0], tcp[1], tcp[2], 0.12, 0.12, 0.12,
                                       'world'))
    record('E engel planlama sahnesine eklendi', placed)
    time.sleep(1.0)
    q_before = joints()
    status, done = go('goto_waypoint ready_pt', 120.0)
    time.sleep(0.5)
    q_after = joints()
    record('E hedefte engel: iş başarısız, MoveIt nedeni iletildi (hedef çarpışması)',
           status == 202 and done.get('state') == 'failed'
           and 'hareket başarısız' in (done.get('error') or '')
           and 'START_STATE' not in (done.get('error') or ''), str(done.get('error')))
    record('E kol ölçülerek hareket ETMEDİ',
           max(abs(a - b) for a, b in zip(q_before, q_after)) < 0.005)
    scene.remove_box('wp_block')
    time.sleep(1.0)
    status, done = go('goto_waypoint ready_pt')
    record('E engel kalkınca aynı iş başarılı', done.get('state') == 'done', str(done.get('error')))

    # ---- F. emergency stop mid-goto
    go('goto_waypoint side')
    status, _, body = api.post('/api/jobs', {'command': 'goto_waypoint ready_pt'}, timeout=30.0)
    moving = wait_for(lambda: sampler.last_moving_time(time.monotonic() - 0.4,
                                                       time.monotonic()) is not None, 20.0, 0.05)
    t_stop = time.monotonic()
    s_stop, _, stop = api.post('/api/stop', {'reason': 'waypoint_check'}, timeout=30.0)
    quiet = standing_since(t_stop, ESTOP_DEADLINE_S)
    doc = finished(body['id'], 20.0)
    record('F acil dur: kol ölçülerek durdu, iş iptal/başarısız, durum estopped',
           status == 202 and bool(moving) and s_stop == 200 and quiet
           and doc.get('state') in ('cancelled', 'failed') and robot().get('state') == 'estopped',
           f'{doc.get("state")} stop_time={stop and stop.get("stop_time_s")}')
    s423, d423 = req('POST', '/api/jobs', {'command': 'goto_waypoint side'})
    record('F kilitliyken goto: 423', s423 == 423, str(d423))
    s_put, _ = req('PUT', '/api/waypoints/during_estop', {'joints': POINT_SIDE})
    s_del, _ = req('DELETE', '/api/waypoints/during_estop')
    record('F kilitliyken nokta düzenleme çalışır (hareket yok)', s_put == 201 and s_del == 200)
    api.post('/api/reset', {})
    status, done = go('goto_waypoint side')
    record('F reset sonrası goto yeniden çalışır', done.get('state') == 'done',
           str(done.get('error')))

    # ---- G. job <-> jog exclusion
    s_jog, _ = req('POST', '/api/jog', {'lease': LEASE, 'frame': 'World',
                                        'increment': {'axis': 0, 'amount': 1}, 'ttl_ms': 250})
    s_goto, d_goto = req('POST', '/api/jobs', {'command': 'goto_waypoint ready_pt'})
    record('G jog kiralaması varken goto: 409', s_jog == 200 and s_goto == 409
           and (d_goto or {}).get('state') == 'jogging', str(d_goto))
    req('POST', '/api/jog', {'lease': LEASE, 'frame': 'World', 'ttl_ms': 0})
    time.sleep(2.2)
    status, _, body = api.post('/api/jobs', {'command': 'goto_waypoint ready_pt'}, timeout=30.0)
    s_jog2, _ = req('POST', '/api/jog', {'lease': LEASE, 'frame': 'World',
                                         'increment': {'axis': 0, 'amount': 1}, 'ttl_ms': 250})
    record('G goto sürerken jog: 409', status == 202 and s_jog2 == 409, f'http={s_jog2}')
    s_rec, _ = req('POST', '/api/waypoints/record', {'name': 'moving'})
    record('G goto sürerken record: 409 (ölçülen poz hareket halinde)', s_rec == 409,
           f'http={s_rec}')
    finished(body['id'])

    # ---- H. rename / delete / unknown
    s, d = req('PUT', '/api/waypoints/side', {'rename_to': 'renamed'})
    record('H yeniden adlandırma: dosya takip eder',
           s == 200 and names_on_disk() == ['ready_pt', 'renamed'], str(names_on_disk()))
    s_unk, _, body = api.post('/api/jobs', {'command': 'goto_waypoint side'}, timeout=30.0)
    doc = finished(body['id'], 20.0) if s_unk == 202 else {}
    record('H eski ad artık yok: iş başarısız, robot hareket etmedi',
           doc.get('state') == 'failed' and 'nokta yok' in (doc.get('error') or ''),
           str(doc.get('error')))
    s, d = req('DELETE', '/api/waypoints/renamed')
    record('H DELETE: dosya takip eder, ikinci DELETE 404',
           s == 200 and names_on_disk() == ['ready_pt']
           and req('DELETE', '/api/waypoints/renamed')[0] == 404)
    left = [p.name for p in data_dir.iterdir() if p.name.endswith('.tmp')]
    record('H artık geçici dosya yok', not left, str(left))
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    data_dir = Path(os.environ.get('ROBOT_DATA_DIR', '/tmp/wp_data'))
    rclpy.init(args=ros_args)
    node = Node('waypoint_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    scene = PlanningSceneAdapter(node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    sampler = Sampler(state, config.joint_names)
    sampler.start()
    try:
        results = run(opts.url, config, state, buffer, sampler, scene, data_dir)
    finally:
        sampler.close()
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 28 else 1)


if __name__ == '__main__':
    main()
