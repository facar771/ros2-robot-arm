"""End-to-end check of the asynchronous jobs (Robot API v1). Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` and ``ros2 launch robot_viz web_view.launch.py commands:=true`` are up:

    ros2 run robot_viz jobs_check            # --url http://127.0.0.1:8080

As in ``stop_check`` the arm's motion is measured by this check itself (own /joint_states
subscription and TF), never taken from what the web view reports:

1. ``POST /api/jobs`` answers 202 at once while ``demo_scenario`` runs for a minute; polling shows
   state ``running``, a growing ``progress.step`` and the same id in ``/api/state``; a second job
   and ``/api/command`` get 409 naming the running job; invalid bodies are 400/404;
2. ``POST /api/jobs/<id>/cancel`` in the middle of the motion: the job becomes ``cancelled`` and the
   *arm is measured to stop* within CANCEL_DEADLINE_S, without creeping; nothing is latched (state
   ``idle``, e-stop off) and a new job works at once, without a reset;
3. a job with ``timeout_s`` ends ``failed`` + ``timed_out`` and the arm stops likewise;
4. an emergency stop during a job ends it ``cancelled`` by ``estop``;
5. a finished job keeps its result; ``GET /api/jobs`` lists them; the old ``POST /api/command``
   still works and is marked deprecated; ``/api/state`` reports the safety limits.
Exit code 0 only if every check passed.
"""
import argparse
import json
import math
import sys
import time

import rclpy
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from tf2_ros import Buffer, TransformListener

from robot_viz.stop_check import (
    CREEP_RAD,
    FAR_FROM_HOME,
    HOME_TOLERANCE,
    LABELS,
    WAIT_S,
    Api,
    Sampler,
    wait_for,
)
from robot_viz.viz_check import tool_from_tf

CANCEL_DEADLINE_S = 2.5     # cancel / timeout -> arm quiet (own measurement)
ACCEPT_DEADLINE_S = 1.5     # POST /api/jobs must answer this fast
TIMEOUT_S = 6


def job(api: Api, job_id: str):
    status, _, body = api.http.request('GET', f'/api/jobs/{job_id}')
    return json.loads(body) if status == 200 and body else None


def wait_job(api: Api, job_id: str, state: str, timeout: float):
    return wait_for(lambda: (lambda d: d if d and d['state'] == state else None)(job(api, job_id)),
                    timeout, 0.1)


def start_far(api: Api, buffer: Buffer, tip: str, home_ref, command='demo_scenario', **extra):
    """Submit a long job and wait until the tool is FAR_FROM_HOME away. Returns (answer, t)."""
    t0 = time.monotonic()
    answer = api.post('/api/jobs', {'command': command, **extra}, timeout=30.0)
    took = time.monotonic() - t0
    away = wait_for(lambda: (lambda p: p and math.dist(p, home_ref) > FAR_FROM_HOME)(
        tool_from_tf(buffer, tip)), 120.0, 0.1)
    return answer, took, bool(away)


def run(base: str, config, state: JointStateCache, buffer: Buffer, sampler: Sampler) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    api = Api(base)
    tip = config.tip_link
    deadline = time.monotonic() + WAIT_S
    ready = False
    while time.monotonic() < deadline and not ready:
        status, _, body = api.post('/api/jobs', {'command': 'objects'})
        if status == 202:
            done = wait_job(api, body['id'], 'done', 30.0)
            ready = bool(done) and LABELS <= {o['label'] for o in
                                              done['result']['data']['objects']}
        if not ready:
            time.sleep(2.0)
    if not record("ilk iş: 'objects' asenkron çalıştı, 3 nesne planlama sahnesinde", ready):
        return results

    status, _, body = api.post('/api/jobs', {'command': 'home'})
    done = wait_job(api, body['id'], 'done', 90.0)
    time.sleep(1.5)
    home_ref = tool_from_tf(buffer, tip)
    record('home işi bitti ve sonucu saklıyor',
           bool(done) and done['result']['ok'] and done['finished_at'] is not None and home_ref)

    # 1. async + progress + busy -----------------------------------------------------------
    (status, headers, doc), took, away = start_far(api, buffer, tip, home_ref)
    jid = doc['id'] if doc else None
    record(f'POST /api/jobs -> 202 {ACCEPT_DEADLINE_S} s içinde, state running, Location başlığı',
           status == 202 and took < ACCEPT_DEADLINE_S and doc['state'] == 'running'
           and headers.get('Location') == f'/api/jobs/{jid}', f'(durum {status}, {took:.2f} s)')
    steps, elapsed = set(), []
    for _ in range(10):
        d = job(api, jid)
        if d:
            steps.add(d['progress']['step'])
            elapsed.append(d['elapsed_s'])
        time.sleep(0.5)
    record('GET /api/jobs/<id>: state running, ilerleme (progress.step) ve elapsed_s artıyor',
           bool(away) and len(steps - {None}) >= 1 and elapsed[-1] > elapsed[0],
           f'(adımlar {sorted(s for s in steps if s)}, elapsed {elapsed[0]}..{elapsed[-1]})')
    top = api.http.state()
    record('/api/state: robot.state busy ve current_job.id aynı iş',
           top['robot']['state'] == 'busy' and top['robot']['current_job']['id'] == jid,
           f"({top['robot']['current_job']})")
    s2, _, b2 = api.post('/api/jobs', {'command': 'home'})
    s3, _, b3 = api.command('home')
    record('çalışırken ikinci iş ve /api/command: 409, çalışan işin kimliğiyle',
           s2 == 409 and b2.get('job_id') == jid and s3 == 409 and b3.get('job_id') == jid,
           f'({s2} {b2}; {s3})')
    record('geçersiz istekler: kötü komut 400, kötü timeout_s 400, bilinmeyen iş 404',
           api.post('/api/jobs', {'command': 'goto 1 2 3'})[0] == 400
           and api.post('/api/jobs', {'command': 'home', 'timeout_s': 0})[0] == 400
           and api.http.request('GET', '/api/jobs/j99999')[0] == 404)

    # 2. cancel in the middle of the motion -------------------------------------------------
    t_cancel = time.monotonic()
    status, _, body = api.post(f'/api/jobs/{jid}/cancel', {}, timeout=30.0)
    t_back = time.monotonic()
    print(f'       cancel: durum {status}, iş {body and body["job"]["state"]}, '
          f'{t_back - t_cancel:.2f} s sürdü')
    record('cancel -> 200, iş cancelled (cancelled_by user), result.ok false',
           status == 200 and body['job']['state'] == 'cancelled'
           and body['job']['cancelled_by'] == 'user' and body['job']['result']['ok'] is False)
    time.sleep(2.0)
    last = sampler.last_moving_time(t_cancel, t_back + 1.9)
    quiet = 0.0 if last is None else last - t_cancel
    record(f'KENDİ ÖLÇÜMÜM: iptalden sonra eklem hızı <= {CANCEL_DEADLINE_S} s içinde durdu',
           quiet <= CANCEL_DEADLINE_S, f'(son hareket iptalden {quiet:.2f} s sonra)')
    creep = sampler.displacement(t_back + 0.5, t_back + 1.9)
    record(f'iptalden sonra kol sürünmüyor (<= {CREEP_RAD} rad)', creep <= CREEP_RAD,
           f'({creep:.4f} rad)')
    tool = tool_from_tf(buffer, tip)
    record('iş tamamlanmadı: kol home dışında kaldı',
           bool(tool) and math.dist(tool, home_ref) > 0.05,
           f"(home'a {math.dist(tool, home_ref) * 100:.1f} cm)" if tool else '')
    robot = api.robot()
    record('iptal hiçbir şeyi KİLİTLEMEDİ: state idle, estop kapalı, moving false',
           robot['state'] == 'idle' and not robot['estop']['tripped'] and robot['moving'] is False,
           f'({robot["state"]}, {robot["estop"]})')
    status, _, body = api.post('/api/jobs', {'command': 'home'})
    done = wait_job(api, body['id'], 'done', 120.0) if status == 202 else None
    time.sleep(1.5)
    tool = tool_from_tf(buffer, tip)
    record('iptalden hemen sonra (reset olmadan) yeni iş çalıştı ve TF ile home\'da',
           status == 202 and bool(done) and tool and math.dist(tool, home_ref) <= HOME_TOLERANCE,
           f'(durum {status})')
    status, _, body = api.post(f'/api/jobs/{jid}/cancel', {})
    record('bitmiş işi tekrar iptal: 409', status == 409 and body['job']['state'] == 'cancelled')

    # 3. timeout ------------------------------------------------------------------------------
    (status, _, doc), took, away = start_far(api, buffer, tip, home_ref, timeout_s=TIMEOUT_S)
    jid = doc['id'] if doc else None
    failed = wait_job(api, jid, 'failed', 60.0) if jid else None
    t_failed = time.monotonic()
    record(f'timeout_s={TIMEOUT_S}: iş failed, timed_out true, neden "zaman aşımı"',
           status == 202 and bool(failed) and failed['timed_out'] and 'zaman aşımı'
           in failed['error'], f'({failed and failed["error"]})')
    time.sleep(2.0)
    last = sampler.last_moving_time(t_failed - 0.5, t_failed + 1.9)
    quiet = 0.0 if last is None else last - t_failed
    record(f'zaman aşımından sonra kol {CANCEL_DEADLINE_S} s içinde durdu (kendi ölçümüm)',
           quiet <= CANCEL_DEADLINE_S, f'({quiet:.2f} s)')
    record('zaman aşımı da kilitlemedi: state idle', api.robot()['state'] == 'idle')

    # 4. e-stop during a job -------------------------------------------------------------------
    api.post('/api/jobs', {'command': 'home'})
    time.sleep(0.5)
    wait_for(lambda: api.robot()['state'] == 'idle', 120.0, 0.2)
    (status, _, doc), took, away = start_far(api, buffer, tip, home_ref)
    jid = doc['id']
    status, _, stop = api.post('/api/stop', {'reason': 'jobs_check'}, timeout=30.0)
    cancelled = wait_job(api, jid, 'cancelled', 30.0)
    record('acil dur çalışan işi bitirdi: cancelled, cancelled_by estop; kol durdu',
           status == 200 and stop['stopped'] and bool(cancelled)
           and cancelled['cancelled_by'] == 'estop', f'({cancelled and cancelled["cancelled_by"]})')
    status, _, body = api.post('/api/jobs', {'command': 'home'})
    record('kilitliyken yeni iş 423', status == 423 and body['state'] == 'estopped')
    time.sleep(1.0)
    status, _, body = api.post('/api/reset', {})
    record('reset sonrası iş kabul ediliyor', status == 200
           and api.post('/api/jobs', {'command': 'home'})[0] == 202)
    wait_for(lambda: api.robot()['state'] == 'idle', 120.0, 0.2)

    # 5. history, deprecation, safety ----------------------------------------------------------
    listing = api.http.request('GET', '/api/jobs')
    jobs = json.loads(listing[2])['jobs'] if listing[0] == 200 else []
    states = {j['state'] for j in jobs}
    record('GET /api/jobs: son işler, en yeni önce; done/failed/cancelled hepsi görünüyor',
           listing[0] == 200 and {'done', 'failed', 'cancelled'} <= states
           and jobs[0]['created_at'] >= jobs[-1]['created_at'], f'({sorted(states)})')
    status, headers, body = api.command('home')
    record('eski POST /api/command hâlâ çalışıyor ve kullanımdan kaldırıldı işaretli',
           status == 200 and body['ok'] and headers.get('Deprecation') == 'true'
           and 'deprecated' in body)
    safety = api.robot()['safety']
    box = safety['zone']['box']
    record('/api/state robot.safety: kutu, hız tavanı ve gerçek ölçek (min(istenen, tavan))',
           safety['zone']['frame'] == 'world' and set(box) == {'x', 'y', 'z'}
           and 0 < safety['max_velocity_scale'] <= 1
           and safety['velocity_scale'] <= safety['max_velocity_scale'], f'({safety})')
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('jobs_check_node')
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
    sys.exit(0 if results and passed == len(results) and len(results) >= 22 else 1)


if __name__ == '__main__':
    main()
