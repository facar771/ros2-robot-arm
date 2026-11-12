"""One end-to-end check of the whole chain: Hub -> Robot API -> perception -> motion, with the
vision plugins and the agent layer on the same robot. Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` and ``ros2 launch robot_viz web_view.launch.py commands:=true`` are up:

    ros2 run robot_viz integration_check  # --url http://127.0.0.1:8080 --hub-dir <apps/control_hub>

Each link is measured by this check itself (own /joint_states and TF), never taken from what a
layer reports about itself:

1. **Hub** (``control_hub.hub_core``: RobotApiBackend + scenario runner, in-process) connects to the
   Robot API v1; the objects it shows come from **perception** (planning scene);
2. **vision**: the agent's ``run_plugin find_object`` reads the LIVE Gazebo camera (a separate
   plugin process); its object centres must agree with the world file and with perception;
3. **motion**: the Hub scenario ``above_green`` moves the arm; the tool position (TF) must be where
   the *camera-derived* target says (object top + clearance), i.e. camera, perception and arm agree;
4. the agent's arm plugin (``follow_color``) moves the same arm through the same API, and the Hub
   *sees* that job (state busy / moving);
5. while the Hub runs a long scenario the agent is refused a second motion (one job at a time across
   clients) but may still LOOK; the Hub's **emergency stop** stops the arm (measured), the agent
   refuses every tool (it cannot unlock anything), the Hub reset unlocks and everything works again;
6. the agent's ``emergency_stop`` tool and ``request_stop`` (Ctrl-C path) lock the robot and the Hub
   sees the lock; the Hub refuses scenarios until it is reset.
Exit code 0 only if every check passed.

Not covered here (the CI image has no FastAPI): the Hub's HTTP/PIN layer.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from robot_agent.client import HttpRobot
from robot_agent.executor import Policy, ToolExecutor
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from robot_core.ports import NullEventSink
from robot_core.workspace import load_workspace
from tf2_ros import Buffer, TransformListener

from robot_viz.stop_check import (
    CREEP_RAD,
    HOME_TOLERANCE,
    WAIT_S,
    Api,
    Sampler,
    far_and_inside,
    wait_for,
)
from robot_viz.viz_check import tool_from_tf

STOP_DEADLINE_S = 2.5
VISION_TOLERANCE = 0.02        # m: camera vs world file / perception (measured: ~1 mm)
TARGET_TOLERANCE = 0.025       # m: TF tool vs camera-derived target
COLORS = ('red', 'green', 'blue')


def load_hub(hub_dir: str):
    """Import the Hub core from ``hub_dir`` (not part of the CI image: mounted into it)."""
    path = Path(hub_dir)
    if not (path / 'control_hub' / 'hub_core').is_dir():
        raise FileNotFoundError(
            f'Hub bulunamadı: {path}/control_hub/hub_core yok. Docker içinde: '
            '-v "$PWD/apps/control_hub:/workspace/apps/control_hub:ro" ile bağla')
    sys.path.insert(0, str(path))
    from control_hub.hub_core import (
        AccessControl,
        EstopLatchedError,
        Hub,
        RobotApiBackend,
        load_scenarios,
        load_ui_config,
    )
    config = path / 'config'
    scenarios = load_scenarios(config / 'scenarios.yaml')
    ui = load_ui_config(config / 'ui.config.json', scenarios)
    return Hub, RobotApiBackend, AccessControl, EstopLatchedError, scenarios, ui


def job_count(api: Api) -> int:
    status, _, body = api.http.request('GET', '/api/jobs')
    return len(json.loads(body)['jobs']) if status == 200 else -1


def agent_for(base: str, confirm=True):
    return ToolExecutor(HttpRobot(base, timeout=30.0), Policy(), NullEventSink(),
                        (lambda req: True) if confirm else None, plugin_frames='ros')


def find(executor, color='any'):
    return executor.execute('run_plugin', {'plugin': 'find_object', 'tool': 'find_object',
                                           'arguments': {'color': color}})


def run(base: str, hub_dir: str, config, state: JointStateCache, buffer: Buffer,
        sampler: Sampler) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    try:
        Hub, RobotApiBackend, AccessControl, EstopLatchedError, scenarios, ui = load_hub(hub_dir)
    except Exception as exc:    # noqa: BLE001 - a clear FAIL, not a traceback
        record('Hub çekirdeği içe aktarılabiliyor', False, f'({type(exc).__name__}: {exc})')
        return results
    api, tip = Api(base), config.tip_link
    deadline = time.monotonic() + WAIT_S
    ready = False
    while time.monotonic() < deadline and not ready:
        status, _, body = api.command('objects')
        ready = status == 200 and set(COLORS) <= {o['label'] for o in body['data']['objects']}
        if not ready:
            time.sleep(2.0)
    if not record('Robot API açık ve 3 nesne planlama sahnesinde (algı)', ready):
        return results
    world = load_workspace(os.path.join(get_package_share_directory('robot_sim'), 'worlds',
                                        'workspace.sdf'))
    truth = {s.name.split('_')[0]: s for s in world.shapes}

    hub = Hub(RobotApiBackend(base), scenarios, ui, AccessControl('open'))
    backend = hub.backend
    agent = agent_for(base)
    try:
        # 1. Hub <-> Robot API <-> perception ------------------------------------------
        backend.wait_first_poll(10)
        snap = backend.snapshot()
        record('Hub robota bağlı ve Robot API v1 ile uyumlu',
               snap['connected'] and snap['compatible'], f"({snap['problem']})")
        hub_objects = {o['label']: o for o in snap['objects']}
        record('Hub\'ın gösterdiği nesneler (algı -> API -> Hub): kırmızı, yeşil, mavi',
               set(COLORS) <= set(hub_objects), f'({sorted(hub_objects)})')
        hub.run_scenario('home')
        hub.runner.wait_idle(120)
        time.sleep(1.5)
        home_ref = tool_from_tf(buffer, tip)
        record('Hub senaryosu home bitti (robot API işi)', hub.runner.status()['last']['ok'])

        # 2. vision (live camera) vs world and perception ------------------------------
        t0 = time.monotonic()
        seen = find(agent)
        objects = {o['color']: o for o in (seen.data.get('plugin_data') or {}).get('objects', [])}
        record('ajan run_plugin find_object (canlı Gazebo kamerası, ayrı süreç): ok, 3 renk, '
               'onaysız (yalnızca bakar)',
               seen.ok and seen.risk == 'read' and set(COLORS) <= set(objects),
               f'({seen.status}: {seen.message}; {time.monotonic() - t0:.1f} s)')
        worst_truth = worst_hub = 0.0
        for color in COLORS:
            if color in objects:
                c, t = objects[color]['center'], truth[color].center
                worst_truth = max(worst_truth, math.dist(c, t))
                worst_hub = max(worst_hub, math.dist(c, hub_objects[color]['position'])
                                if color in hub_objects else 9.9)
        record(f'kamera nesne merkezleri dünya dosyasına <= {VISION_TOLERANCE * 100:.0f} cm',
               worst_truth <= VISION_TOLERANCE, f'(en kötü {worst_truth * 100:.2f} cm)')
        record(f'kamera ile algı düğümünün (Hub\'daki) nesneleri uyuşuyor <= '
               f'{VISION_TOLERANCE * 100:.0f} cm', worst_hub <= VISION_TOLERANCE,
               f'(en kötü {worst_hub * 100:.2f} cm)')

        # 3. Hub scenario -> arm at the CAMERA-derived target --------------------------
        moving_seen = threading.Event()
        stop_watch = threading.Event()

        def watch():
            while not stop_watch.is_set():
                if backend.snapshot().get('moving') is True:
                    moving_seen.set()
                time.sleep(0.1)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        hub.run_scenario('above_green')
        hub.runner.wait_idle(180)
        time.sleep(1.5)
        stop_watch.set()
        last = hub.runner.status()['last']
        tool = tool_from_tf(buffer, tip)
        green = objects.get('green')
        target = None if not green else (green['top_center'][0], green['top_center'][1],
                                         green['top_center'][2] + 0.25)
        err = math.dist(tool, target) if tool and target else float('inf')
        record('Hub senaryosu above_green bitti ve hareket sırasında Hub "moving" gördü',
               last['ok'] and moving_seen.is_set(), f"({last['message']})")
        record(f'UÇTAN UCA: TF uç konumu, KAMERADAN türetilen hedefe (üst+25 cm) <= '
               f'{TARGET_TOLERANCE * 100:.1f} cm', err <= TARGET_TOLERANCE,
               f'(hata {err * 100:.2f} cm)')

        # 4. agent's arm plugin on the same arm; the Hub sees its job ------------------
        stop_watch.clear()
        busy_seen = threading.Event()

        def watch_busy():
            while not stop_watch.is_set():
                if backend.snapshot().get('state') == 'busy':
                    busy_seen.set()
                time.sleep(0.1)

        watcher = threading.Thread(target=watch_busy, daemon=True)
        watcher.start()
        red = (find(agent, 'red').data.get('plugin_data') or {}).get('objects', [{}])[0]
        followed = agent.execute('run_plugin', {'plugin': 'follow_color', 'tool': 'follow_color',
                                                'arguments': {'color': 'red', 'duration_s': 3}})
        time.sleep(1.5)
        stop_watch.set()
        tool = tool_from_tf(buffer, tip)
        target = (red['top_center'][0], red['top_center'][1], red['top_center'][2] + 0.30) \
            if red else None
        err = math.dist(tool, target) if tool and target else float('inf')
        print(f'       follow_color: {followed.status}, {followed.message}, '
              f'{(followed.data.get("plugin_data") or {})}')
        record('ajan run_plugin follow_color (kolu oynatan eklenti; onayla): ok, neden "duration"',
               followed.ok and followed.risk == 'long'
               and (followed.data.get('plugin_data') or {}).get('reason') == 'duration')
        record('eklentinin işini Hub gördü (state busy)', busy_seen.is_set())
        record(f'eklenti kolu kamera hedefine (kırmızı üst+30 cm) taşıdı <= '
               f'{TARGET_TOLERANCE * 100:.1f} cm (TF)', err <= TARGET_TOLERANCE,
               f'(hata {err * 100:.2f} cm)')

        # 5. one job at a time across clients; looking is allowed; Hub e-stop ----------
        # N8: the plugin job of section 4 may still be releasing the arm when it returns. A scenario
        # started in that instant fails its first step ("robot başka bir işle meşgul") at once, and
        # every check below would then run against a scenario that is already dead (the arm still
        # stands far from home from section 4, which is all `far_and_inside` can see). So: wait for
        # idle, start the scenario, and only go on once it is PROVEN running (runner running AND the
        # robot busy with its job); the checks below also require it to still run after the agent
        # call.
        wait_for(lambda: backend.snapshot().get('state') == 'idle', 30.0, 0.1)
        hub.run_scenario('demo_tour')
        alive = wait_for(lambda: hub.runner.running and backend.snapshot().get('state') == 'busy',
                         30.0, 0.05)
        away = bool(alive) and wait_for(lambda: far_and_inside(api, buffer, tip, home_ref),
                                        120.0, 0.1)
        busy = agent.execute('run_plugin', {'plugin': 'follow_color', 'tool': 'follow_color',
                                            'arguments': {'color': 'red', 'duration_s': 3}})
        looking = find(agent, 'green')
        # Between two Hub steps the robot may read "idle" for an instant: then the agent's own
        # object lookup meets the server's 409 instead of the "robot meşgul" gate. Same safe result.
        still_running = hub.runner.running
        record('Hub senaryosu sürerken ajanın ikinci hareketi reddedildi (tek iş: ajan kapısı ya '
               'da sunucu 409)', bool(alive) and bool(away) and still_running
               and busy.status == 'refused'
               and ('meşgul' in busy.message or 'başka bir iş çalışıyor' in busy.message),
               f'({busy.status}: {busy.message[:60]}; senaryo başladı: {bool(alive)}, '
               f'çağrıdan sonra sürüyor: {still_running})')
        record('...ama ajan bakabiliyor (find_object hareket sırasında ok)',
               looking.ok and (looking.data.get('plugin_data') or {}).get('count', 0) >= 1)
        t_stop = time.monotonic()
        hub.estop('integration_check')
        report = hub.wait_estop_report(15)
        t_back = time.monotonic()
        hub.runner.wait_idle(30)
        time.sleep(1.5)
        last_moving = sampler.last_moving_time(t_stop, t_back + 1.4)
        quiet = 0.0 if last_moving is None else last_moving - t_stop
        record('Hub ACİL DUR: robotun durduğu doğrulandı ve senaryo iptal',
               bool(report and report.stopped) and hub.runner.status()['last']['cancelled'],
               f'({report and report.message})')
        record(f'KENDİ ÖLÇÜMÜM: Hub acil durunda kol {STOP_DEADLINE_S} s içinde durdu, sürünmüyor',
               quiet <= STOP_DEADLINE_S and sampler.displacement(t_back + 0.3, t_back + 1.4)
               <= CREEP_RAD, f'(son hareket {quiet:.2f} s sonra)')
        n_jobs = job_count(api)
        stopped = [agent.execute('run_plugin', {'plugin': 'follow_color', 'tool': 'follow_color',
                                                'arguments': {'color': 'red'}}),
                   agent.execute('run_plugin', {'plugin': 'find_object', 'tool': 'find_object',
                                                'arguments': {'color': 'red'}}),
                   agent.execute('go_above_object', {'color': 'red'}),
                   agent.execute('go_home', {})]
        record('Hub acil durundan sonra ajan HER aracı reddetti (estopped) ve yeni iş açılmadı',
               all(r.status == 'estopped' and r.halts for r in stopped)
               and job_count(api) == n_jobs,
               f'({[r.status for r in stopped]})')
        try:
            hub.run_scenario('home')
            record('kilitliyken Hub senaryosu da reddedildi', False)
        except EstopLatchedError:
            record('kilitliyken Hub senaryosu da reddedildi', True)
        time.sleep(1.0)
        hub.reset_estop('integration_check')
        agent = agent_for(base)                       # a new run: the halted executor stays halted
        record('Hub reset sonrası ajan yine bakabiliyor ve hareket ettirebiliyor',
               find(agent, 'green').ok and agent.execute('go_home', {}).ok)
        time.sleep(1.5)
        tool = tool_from_tf(buffer, tip)
        record('reset sonrası kol home\'da (TF)', bool(tool) and bool(home_ref)
               and math.dist(tool, home_ref) <= HOME_TOLERANCE)

        # 6. the agent locks the robot; the Hub sees it --------------------------------
        stop = agent.execute('emergency_stop', {'reason': 'integration_check ajan'})
        time.sleep(0.8)
        hub_view = hub.snapshot()
        record('ajanın emergency_stop aracı robotu kilitledi (durma doğrulandı), Hub kilidi gördü',
               stop.ok and stop.halts and hub_view['estop']['latched']
               and hub_view['estop']['by_robot_only'], f'({stop.message[:70]})')
        try:
            hub.run_scenario('home')
            record('ajanın kilidindeyken Hub senaryosu reddedildi', False)
        except EstopLatchedError:
            record('ajanın kilidindeyken Hub senaryosu reddedildi', True)
        time.sleep(1.0)
        hub.reset_estop('integration_check')
        record('Hub kilidi açtı (reset)', not hub.estopped)

        agent = agent_for(base)
        box = []
        worker = threading.Thread(target=lambda: box.append(agent.execute('run_plugin', {
            'plugin': 'follow_color', 'tool': 'follow_color',
            'arguments': {'color': 'red', 'duration_s': 60}})))
        worker.start()
        moving = wait_for(lambda: backend.snapshot().get('moving') is True, 60.0, 0.1)
        # F4b (B'): stop once the tool is inside the box (the park transit's first part is outside;
        # an e-stop there needs the F4c recovery before the next task motion)
        wait_for(lambda: far_and_inside(api, buffer, tip, home_ref), 60.0, 0.1)
        time.sleep(0.3)
        t_stop = time.monotonic()
        agent.request_stop('integration Ctrl-C')
        t_back = time.monotonic()
        worker.join(60)
        time.sleep(1.5)
        last_moving = sampler.last_moving_time(t_stop, t_back + 1.4)
        quiet = 0.0 if last_moving is None else last_moving - t_stop
        result = box[0] if box else None
        record('Ctrl-C yolu (request_stop) çalışan kol eklentisini ve robotu durdurdu; '
               'araç sonucu estopped', bool(moving) and bool(result) and result.status == 'estopped'
               and result.halts and hub.snapshot()['estop']['latched'],
               f'({result and result.status})')
        record(f'KENDİ ÖLÇÜMÜM: kol {STOP_DEADLINE_S} s içinde durdu, sürünmüyor',
               quiet <= STOP_DEADLINE_S and sampler.displacement(t_back + 0.3, t_back + 1.4)
               <= CREEP_RAD, f'(son hareket {quiet:.2f} s sonra)')
        time.sleep(1.0)
        hub.reset_estop('integration_check')
        hub.run_scenario('home')
        hub.runner.wait_idle(120)
        time.sleep(1.5)
        tool = tool_from_tf(buffer, tip)
        record('sonunda Hub home senaryosu çalıştı ve kol home\'da (TF)',
               hub.runner.status()['last']['ok'] and bool(tool) and bool(home_ref)
               and math.dist(tool, home_ref) <= HOME_TOLERANCE)
    finally:
        hub.close()
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--hub-dir',
                        default=os.environ.get('HUB_DIR', '/workspace/apps/control_hub'))
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('integration_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    sampler = Sampler(state, config.joint_names)
    sampler.start()
    try:
        results = run(opts.url, opts.hub_dir, config, state, buffer, sampler)
    finally:
        sampler.close()
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 25 else 1)


if __name__ == '__main__':
    main()
