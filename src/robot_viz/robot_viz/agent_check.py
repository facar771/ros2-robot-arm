"""End-to-end check of the LLM tool layer (robot_agent) against the real stack. Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` and ``ros2 launch robot_viz web_view.launch.py commands:=true`` are up:

    ros2 run robot_viz agent_check            # --url http://127.0.0.1:8080

No API key and no network: the "LLM" is a script (or the built-in phrase parser). What is real is
everything after it: the ToolExecutor, the HTTP API, MoveIt and Gazebo. The arm is measured by this
check itself (own /joint_states and TF), never taken from what the API reports:

1. a valid request (phrase parser) moves the tool to the reported target (TF), as a job;
2. the real CLI (``ros2 run robot_agent robot_agent --offline --yes ...``) does the same, exit 0;
3. invalid tool calls (unknown tool, bad colour/height) are rejected and *nothing* is sent: no job,
   the joints do not change;
4. a target MoveIt cannot reach is refused by the robot, the arm stays put, the model is told;
5. a dry run sends no motion;
6. emergency stop has priority: a human stop in the middle of a tool call ends the run (the model
   gets no second turn) and the arm is measured to stop; the stopped executor refuses everything;
   an LLM-issued ``emergency_stop`` stops and verifies; Ctrl-C on the real CLI process stops the
   robot (exit 130) and the arm stops; after a human reset a fresh agent works again.
Exit code 0 only if every check passed.
"""
import argparse
import json
import math
import signal
import subprocess
import sys
import threading
import time

import rclpy
from rclpy.node import Node
from robot_agent.agent import AgentRunner
from robot_agent.client import HttpRobot
from robot_agent.executor import Policy, ToolExecutor
from robot_agent.llm import LLMResponse, RuleLLM, ScriptedLLM, call
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from robot_core.jogapi import READY_POSE
from robot_core.ports import NullEventSink
from tf2_ros import Buffer, TransformListener

from robot_viz.cmd_check import joint_snapshot, moved
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

STOP_DEADLINE_S = 2.5
TOOL_TOLERANCE = 0.02


def agent(robot, llm, *, confirm=True, dry_run=False):
    executor = ToolExecutor(robot, Policy(), NullEventSink(), (lambda req: True) if confirm
                            else None, dry_run=dry_run)
    return AgentRunner(llm, executor), executor


def job_count(api: Api) -> int:
    status, _, body = api.http.request('GET', '/api/jobs')
    return len(json.loads(body)['jobs']) if status == 200 else -1


def run(base: str, config, state: JointStateCache, buffer: Buffer, sampler: Sampler) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    api, robot, tip = Api(base), HttpRobot(base, timeout=30.0), config.tip_link
    names = list(config.joint_names)
    deadline = time.monotonic() + WAIT_S
    ready = False
    while time.monotonic() < deadline and not ready:
        status, _, body = api.command('objects')
        ready = status == 200 and LABELS <= {o['label'] for o in body['data']['objects']}
        if not ready:
            time.sleep(2.0)
    if not record('API açık ve 3 nesne planlama sahnesinde', ready):
        return results
    api.command('home')
    time.sleep(2.0)
    home_ref = tool_from_tf(buffer, tip)

    # 1. valid request through the phrase parser -------------------------------------------
    runner, _ = agent(robot, RuleLLM())
    out = runner.run('yeşil nesnenin üstüne git')
    time.sleep(1.5)
    tool = tool_from_tf(buffer, tip)
    result = out.calls[0][2] if out.calls else None
    target = (result.data or {}).get('target') if result else None
    err = math.dist(tool, target) if tool and target else float('inf')
    record('geçerli istek: araç ok, iş kimliği var, uç konum hedefe <= 2 cm (TF)',
           bool(result) and result.ok and result.job_id and err <= TOOL_TOLERANCE,
           f'({result and result.status}, hata {err * 100:.2f} cm, "{out.text}")')
    doc = json.loads(api.http.request('GET', f'/api/jobs/{result.job_id}')[2] or b'{}')
    record('iş API\'de done olarak görünüyor ve doğru komutla',
           doc.get('state') == 'done' and doc.get('command') == 'above green 0.35',
           f'({doc.get("command")})')

    # 2. the real CLI ----------------------------------------------------------------------
    proc = subprocess.run(['ros2', 'run', 'robot_agent', 'robot_agent', '--url', base, '--offline',
                           '--yes', 'kırmızı kutunun 35 cm üstüne git'],
                          capture_output=True, text=True, timeout=180)
    time.sleep(1.5)
    tool = tool_from_tf(buffer, tip)
    red_target = (0.35, 0.15, 0.06 + 0.35)
    err = math.dist(tool, red_target) if tool else float('inf')
    print('       CLI çıktısı:', proc.stdout.strip().replace('\n', ' | ')[:300])
    record('gerçek CLI: çıkış 0, "[+] go_above_object", uç konum kırmızının 35 cm üstünde (TF)',
           proc.returncode == 0 and '[+] go_above_object' in proc.stdout and err <= 0.03,
           f'(çıkış {proc.returncode}, hata {err * 100:.2f} cm)')

    # 3. invalid calls: nothing is sent ----------------------------------------------------
    before, jobs = joint_snapshot(state, names), job_count(api)
    bad_calls = [call('exec', {'cmd': 'ls'}, 'a'),
                 call('go_above_object', {'color': 'purple'}, 'b'),
                 call('go_above_object', {'color': 'red', 'height_m': 9}, 'c')]
    llm = ScriptedLLM(LLMResponse('', bad_calls), LLMResponse('', [call('reset', {}, 'd')]),
                      LLMResponse('Olmadı.'))
    runner, _ = agent(robot, llm)
    out = runner.run('bir şeyler yap')
    time.sleep(1.0)
    record('geçersiz/tehlikeli çağrılar reddedildi (4/4 rejected)',
           [r.status for _, _, r in out.calls] == ['rejected'] * 4,
           f'({[r.status for _, _, r in out.calls]})')
    record('hiçbir iş oluşmadı ve eklemler değişmedi',
           job_count(api) == jobs and moved(before, joint_snapshot(state, names)) < 0.01)

    # 4. reachable by the API's rules but not by the arm -----------------------------------
    unreachable = call('go_above_object', {'color': 'red', 'height_m': 0.25})
    llm = ScriptedLLM(LLMResponse('', [unreachable]), LLMResponse('Ulaşılamıyor.'))
    runner, _ = agent(robot, llm)
    before = joint_snapshot(state, names)
    out = runner.run('kırmızının 25 cm üstüne git')
    time.sleep(1.0)
    status = out.calls[0][2]
    # F4b (B'): from home the task motion is two stages; MoveIt refuses stage 2, so the arm either
    # did not move ("Robot hareket etmedi") or stands at 'ready' and the message says exactly that
    now = joint_snapshot(state, names)
    at_ready = max(abs(a - b) for a, b in zip(now, READY_POSE)) <= 0.02
    parked = "'ready' pozunda" in status.message and 'görev hareketi yapılmadı' in status.message
    record('MoveIt\'in ulaşamadığı hedef: araç failed, neden açık ("Robot hareket etmedi" ya da '
           "park geçişi sonrası 'ready' pozunda)",
           status.status == 'failed' and ('Robot hareket etmedi' in status.message
                                          or (parked and at_ready)),
           f'({status.status}: {status.message[:90]})')
    record('o hedefte kol oynamadı (ya da yalnız ready\'ye park etti), robot idle',
           (moved(before, now) < 0.01 or (parked and at_ready)) and api.robot()['state'] == 'idle')

    # 5. dry run ---------------------------------------------------------------------------
    before, jobs = joint_snapshot(state, names), job_count(api)
    runner, _ = agent(robot, ScriptedLLM(
        LLMResponse('', [call('go_above_object', {'color': 'blue', 'height_m': 0.3})]),
        LLMResponse('Kuru çalıştırma tamam.')), dry_run=True)
    out = runner.run('mavinin üstüne git')
    time.sleep(1.0)
    res = out.calls[0][2]
    record('kuru çalıştırma: dry_run, hedef hesaplandı, kutu içi, yalnızca 1 okuma işi (objects)',
           res.status == 'dry_run' and res.data.get('zone') == 'içeride'
           and job_count(api) == jobs + 1 and moved(before, joint_snapshot(state, names)) < 0.01,
           f'({res.data})')

    # 6a. a human stops the robot in the middle of a tool call -----------------------------
    api.command('home')
    time.sleep(2.0)
    llm = ScriptedLLM(LLMResponse('Demoyu başlatıyorum.', [call('run_demo_scenario')]),
                      LLMResponse('(buraya gelinmemeli)'))
    runner, executor = agent(robot, llm)
    box = []
    worker = threading.Thread(target=lambda: box.append(runner.run('demo çalıştır')))
    worker.start()
    away = wait_for(lambda: (lambda p: p and math.dist(p, home_ref) > FAR_FROM_HOME)(
        tool_from_tf(buffer, tip)), 120.0, 0.1)
    t_stop = time.monotonic()
    status, _, stop = api.post('/api/stop', {'reason': 'agent_check insan'}, timeout=30.0)
    t_back = time.monotonic()
    worker.join(60)
    time.sleep(1.5)
    out = box[0] if box else None
    record('insan acil durdu: ajan koşusu "acil dur" ile bitti, modele 2. tur verilmedi',
           bool(away) and bool(out) and out.halted == 'acil dur' and len(llm.calls) == 1
           and out.calls[0][2].status == 'estopped',
           f'({out and out.halted}, {len(llm.calls)} tur)')
    last = sampler.last_moving_time(t_stop, t_back + 1.4)
    quiet = 0.0 if last is None else last - t_stop
    record(f'KENDİ ÖLÇÜMÜM: kol {STOP_DEADLINE_S} s içinde durdu ve sürünmüyor',
           quiet <= STOP_DEADLINE_S and sampler.displacement(t_back + 0.5, t_back + 1.4)
           <= CREEP_RAD, f'(son hareket {quiet:.2f} s sonra)')
    record('kullanıcıya giden metin acil durdan söz ediyor', out and 'Acil dur etkin' in out.text)
    jobs = job_count(api)
    res = executor.execute('go_home', {})
    other, _ = agent(robot, ScriptedLLM(LLMResponse('', [call('go_home')])), confirm=True)
    res2 = other.run('home').calls[0][2]
    record('kilitliyken hem durmuş yürütücü hem yeni ajan hareketi reddetti, yeni iş açılmadı',
           res.status == 'estopped' and res2.status == 'estopped' and job_count(api) == jobs)
    record('zaten kilitliyken emergency_stop yine ok ve koşuyu bitirir (halts)',
           ToolExecutor(robot, events=NullEventSink()).execute('emergency_stop', {}).halts)
    time.sleep(1.0)
    status, _, body = api.post('/api/reset', {})
    record('insan sıfırladı (reset 200)', status == 200 and body['state'] == 'idle')

    # 6b. the model itself stops the robot -------------------------------------------------
    runner, _ = agent(robot, ScriptedLLM(
        LLMResponse('Şüpheli görünüyor.', [call('emergency_stop', {'reason': 'agent_check LLM'})]),
        LLMResponse('(buraya gelinmemeli)')))
    out = runner.run('her yere git')
    robot_doc = api.robot()
    record('LLM emergency_stop çağırdı: ok, durma doğrulandı, koşu bitti, robot estopped',
           out.halted == 'acil dur' and out.calls[0][2].ok and robot_doc['state'] == 'estopped'
           and 'agent_check LLM' in (robot_doc['estop']['reason'] or ''),
           f"({out.calls[0][2].message})")
    time.sleep(1.0)
    record('reset (insan)', api.post('/api/reset', {})[0] == 200)

    # 6c. Ctrl-C on the real CLI process --------------------------------------------------
    api.command('home')
    time.sleep(2.0)
    proc = subprocess.Popen([sys.executable, '-m', 'robot_agent.cli', '--url', base, '--offline',
                             '--yes', 'demo senaryosunu çalıştır'], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    away = wait_for(lambda: (lambda p: p and math.dist(p, home_ref) > FAR_FROM_HOME)(
        tool_from_tf(buffer, tip)), 120.0, 0.1)
    t_int = time.monotonic()
    proc.send_signal(signal.SIGINT)
    try:
        output, _ = proc.communicate(timeout=30)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        output, code = proc.communicate()[0], 'zaman aşımı'
    time.sleep(1.5)
    last = sampler.last_moving_time(t_int, time.monotonic())
    quiet = 0.0 if last is None else last - t_int
    robot_doc = api.robot()
    print('       CLI (Ctrl-C) çıktısı:', output.strip().replace('\n', ' | ')[-250:])
    record('Ctrl-C: CLI çıkış 130, robot estopped (neden Ctrl-C), kol durdu',
           bool(away) and code == 130 and robot_doc['state'] == 'estopped'
           and 'Ctrl-C' in (robot_doc['estop']['reason'] or '') and quiet <= STOP_DEADLINE_S + 1.0,
           f'(çıkış {code}, durum {robot_doc["state"]}, son hareket {quiet:.2f} s)')
    time.sleep(1.0)
    record('reset (insan)', api.post('/api/reset', {})[0] == 200)

    # 7. a fresh agent works after the human reset -----------------------------------------
    runner, _ = agent(robot, RuleLLM())
    out = runner.run('home')
    time.sleep(1.5)
    tool = tool_from_tf(buffer, tip)
    record('reset sonrası yeni ajan: home işi ok ve uç konum home\'da (TF)',
           out.all_ok and tool and math.dist(tool, home_ref) <= HOME_TOLERANCE)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('agent_check_node')
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
    sys.exit(0 if results and passed == len(results) and len(results) >= 20 else 1)


if __name__ == '__main__':
    main()
