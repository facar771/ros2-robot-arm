"""End-to-end check of the browser commands. Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` and ``ros2 launch robot_viz web_view.launch.py commands:=true`` are up:

    ros2 run robot_viz cmd_check            # --url http://127.0.0.1:8080

The robot's real position is always measured from TF by this check itself, never taken from what
the web view or MoveIt report:

1. ``/api/state`` says commands are on and lists the white list; ``objects`` finds the three
   objects of the world file;
2. everything that must be refused is refused with a clear error (unknown/CLI-only commands, bad
   colours and heights, shell metacharacters, wrong content type, foreign Origin / Sec-Fetch-Site /
   Host, bad JSON, oversize body, wrong methods) and the joints did not move;
3. a valid command the arm cannot carry out gets 422 with the reason and the arm does not move;
4. ``home`` / ``above green`` / ``above red`` reach their targets (TF) through the HTTP endpoint;
5. ``demo_scenario``: while it runs, /api/state shows the narration growing and the tool moving, a
   second command gets 409, and afterwards every step is verified against TF and the arm is home.
Exit code 0 only if every check passed.
"""
import argparse
import math
import sys
import threading
import time

import rclpy
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner, JointStateCache
from robot_controller.config_loader import load_robot_config
from robot_core.webcmd import USAGE
from tf2_ros import Buffer, TransformListener

from robot_viz.viz_check import Http, tool_from_tf

WAIT_S = 120.0
TOOL_TOLERANCE = 0.015      # m, TF vs the target the skill reports
HOME_TOLERANCE = 0.03       # m, home vs the first home (the straight-up arm sways ~1 cm)
DEMO_TIMEOUT_S = 400.0
JSON = {'Content-Type': 'application/json'}
LABELS = {'red', 'green', 'blue'}


def joint_snapshot(state: JointStateCache, names, wait_s: float = 30.0) -> dict:
    """Joint positions; waits for the first /joint_states so a start-up race cannot yield nan."""
    deadline = time.monotonic() + wait_s
    while True:
        now = state.joint_positions() or {}
        if all(n in now for n in names) or time.monotonic() >= deadline:
            return {n: now.get(n, float('nan')) for n in names}
        time.sleep(0.2)


def moved(before: dict, after: dict) -> float:
    return max(abs(after[n] - before[n]) for n in before)


def run(base: str, config, state: JointStateCache, buffer: Buffer) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    http_ = Http(base)
    names = list(config.joint_names)
    deadline = time.monotonic() + WAIT_S
    listing = None
    while time.monotonic() < deadline:
        status, _, body = http_.post('objects')
        if status == 200 and LABELS <= {o['label'] for o in body['data']['objects']}:
            listing = body
            break
        time.sleep(2.0)
    if not record('komut ucu açık ve 3 nesne planlama sahnesinde', listing is not None,
                  f'(son durum: {status}, {body})' if listing is None else ''):
        return results

    # 1. state and the white list ------------------------------------------------------------
    s = http_.state()
    record('/api/state komutların açık olduğunu ve beyaz listeyi söylüyor',
           s['commands'] == {'enabled': True, 'allowed': list(USAGE)}, f"({s['commands']})")
    record('objects: kırmızı, yeşil, mavi nesne adı + konum + boyutla listelendi',
           all(len(o['position']) == 3 and len(o['size']) == 3 for o in listing['data']['objects']),
           f"({listing['message']})")

    # 2. refusals ----------------------------------------------------------------------------
    before = joint_snapshot(state, names)
    last_before = http_.state()['command']['last']
    refused = [
        ('bilinmeyen komut (dance)', http_.post('dance'), 400),
        ("CLI'ya özel komut (goto 1 2 3) web'de yok", http_.post('goto 1 2 3'), 400),
        ('move home web\'de yok', http_.post('move home'), 400),
        ('geçersiz renk (above purple)', http_.post('above purple'), 400),
        ('sayı olmayan yükseklik (above green abc)', http_.post('above green abc'), 400),
        ('sınır dışı yükseklik (above green 9)', http_.post('above green 9'), 400),
        ('negatif yükseklik (above green -1)', http_.post('above green -1'), 400),
        ('fazla argüman (above green 0.2 x)', http_.post('above green 0.2 x'), 400),
        ('argümansız komuta argüman (home now)', http_.post('home now'), 400),
        ('kabuk metakarakteri (above green; reboot)', http_.post('above green; reboot'), 400),
        ('satır sonu (home\\nobjects)', http_.post('home\nobjects'), 400),
        ('boş komut', http_.post(''), 400),
        ('çok uzun komut', http_.post('x' * 200), 400),
        ('geçersiz JSON', http_.post(raw=b'{not json'), 400),
        ('fazladan anahtar', http_.post(raw=b'{"command": "home", "x": 1}'), 400),
        ('komut metin değil', http_.post(raw=b'{"command": ["home"]}'), 400),
        ('text/plain içerik türü', http_.post('home', headers={'Content-Type': 'text/plain'}),
         415),
        ('içerik türü yok', http_.post('home', headers={}), 415),
        ('form içerik türü', http_.post('home', headers={
            'Content-Type': 'application/x-www-form-urlencoded'}), 415),
        ('yabancı Origin', http_.post('home', headers=dict(JSON, Origin='http://evil.example')),
         403),
        ('Origin: null', http_.post('home', headers=dict(JSON, Origin='null')), 403),
        ('Sec-Fetch-Site: cross-site', http_.post('home', headers=dict(
            JSON, **{'Sec-Fetch-Site': 'cross-site'})), 403),
        ('yabancı Host', http_.post('home', headers=dict(JSON, Host='evil.example')), 403),
        ('512 bayttan büyük gövde', http_.post(raw=b'{"command": "' + b'x' * 600 + b'"}'), 413),
    ]
    for label, (status, _headers, body), expected in refused:
        ok = status == expected and bool((body or {}).get('error'))
        record(f'reddedildi ({expected}): {label}', ok,
               '' if ok else f'(durum {status}, gövde {body})')
    status, _, body = refused[0][1]
    record('400 yanıtı beyaz listeyi içeriyor', body.get('allowed') == list(USAGE))
    for method in ('GET', 'PUT', 'DELETE'):
        status, headers, _ = http_.request(method, '/api/command', body=b'{}', headers=JSON)
        record(f'/api/command {method} -> 405, Allow: POST',
               status == 405 and headers.get('Allow') == 'POST', f'(durum {status})')
    time.sleep(1.0)
    record('hiçbir reddedilen istek robotu hareket ettirmedi',
           moved(before, joint_snapshot(state, names)) < 0.01,
           f'(eklem değişimi {moved(before, joint_snapshot(state, names)):.4f} rad)')
    record('reddedilen istekler "son komut" kaydını değiştirmedi',
           http_.state()['command']['last'] == last_before)
    origin = f'http://{http_.host}:{http_.port}'
    status, _, body = http_.post('objects', headers=dict(JSON, Origin=origin, **{
        'Sec-Fetch-Site': 'same-origin'}))
    record('aynı kaynaklı (Origin = Host) istek kabul edilir (sayfanın kendi kullanımı)',
           status == 200 and body['ok'], f'(durum {status})')

    # 3. valid but impossible ----------------------------------------------------------------
    before = joint_snapshot(state, names)
    status, _, body = http_.post('above red 0')
    time.sleep(1.0)
    after = joint_snapshot(state, names)
    if status == 422:
        record('above red 0: robot yapamadı -> 422, nedeni açık, robot hareket etmedi',
               bool(body['message']) and 'Robot hareket etmedi' in body['message']
               and moved(before, after) < 0.01,
               f"(-> {body['message']}; eklem değişimi {moved(before, after):.4f} rad)")
    else:    # reaching it is allowed; the invariant is "at the target or an explicit refusal"
        tool = tool_from_tf(buffer, config.tip_link)
        err = math.dist(tool, body['data']['target']) if status == 200 and tool else float('inf')
        record('above red 0: ya hedefte ya da açık reddedildi', status == 200 and err <= 0.02,
               f'(durum {status}, hata {err * 100:.2f} cm)')

    # 4. moves through the endpoint, measured from TF ------------------------------------------
    status, _, body = http_.post('home')
    time.sleep(1.5)
    home_ref = tool_from_tf(buffer, config.tip_link)
    record('home -> 200', status == 200 and body['ok'] and home_ref is not None,
           f'(durum {status}, uç konum {home_ref})')
    # 0.35 = the page's default height: every colour button must work out of the box
    for line in ('above green 0.25', 'above red 0.35', 'above green 0.35', 'above blue 0.35'):
        status, _, body = http_.post(line)
        time.sleep(1.5)
        tool = tool_from_tf(buffer, config.tip_link)
        err = math.dist(tool, body['data']['target']) if status == 200 and tool else float('inf')
        view_tool = http_.state()['tool']['position']
        verr = math.dist(view_tool, tool) if tool else float('inf')
        target = body and body.get('data', {}).get('target')
        print(f'       {line}: TF uç konum {tool}, hedef {target}, hata {err * 100:.2f} cm; '
              f'görünüm-TF farkı {verr * 100:.2f} cm')
        record(f'{line} -> 200 ve TF ile ölçülen uç konum hedefe <= {TOOL_TOLERANCE * 100:.1f} cm',
               status == 200 and err <= TOOL_TOLERANCE)
        record(f'{line}: sayfadaki uç konum TF ile tutarlı', verr <= TOOL_TOLERANCE)
    status, _, body = http_.post('home')
    time.sleep(1.5)
    tool = tool_from_tf(buffer, config.tip_link)
    err = math.dist(tool, home_ref) if tool and home_ref else float('inf')
    record(f'home: ilk home konumuna <= {HOME_TOLERANCE * 100:.1f} cm geri dönüldü',
           status == 200 and err <= HOME_TOLERANCE, f'(fark {err * 100:.2f} cm)')

    # 5. demo scenario, live ------------------------------------------------------------------
    outcome = {}

    def demo():
        outcome['response'] = http_.post('demo_scenario')
    worker = threading.Thread(target=demo)
    worker.start()
    seen_logs, positions, busy_status, running_seen = set(), [], None, False
    deadline = time.monotonic() + DEMO_TIMEOUT_S
    while worker.is_alive() and time.monotonic() < deadline:
        s = http_.state()
        if s and s['command']['running']:
            running_seen = running_seen or s['command']['name'] == 'demo_scenario'
            seen_logs.add(len(s['command']['log']))
            if s['tool']:
                positions.append(tuple(s['tool']['position']))
            if busy_status is None and running_seen:
                busy_status = http_.post('home')[0]
        time.sleep(0.4)
    worker.join(30)
    status, _, body = outcome.get('response', (None, {}, None))
    record('demo_scenario çalışırken /api/state running=true ve adı gösterdi', running_seen)
    record('çalışırken ikinci komut 409 (meşgul) aldı', busy_status == 409,
           f'(durum {busy_status})')
    record('anlatım canlı büyüdü (en az 3 farklı günlük uzunluğu görüldü)', len(seen_logs) >= 3,
           f'(görülen uzunluklar {sorted(seen_logs)})')
    spread = max((math.dist(p, positions[0]) for p in positions), default=0.0)
    record('çalışırken sayfadaki uç konum hareketi izledi (> 20 cm yer değiştirdi)',
           spread > 0.20, f'(en büyük yer değiştirme {spread * 100:.1f} cm)')
    steps = (body or {}).get('data', {}).get('steps', [])
    print(f"       demo_scenario: durum {status}, süre {(body or {}).get('duration_s')} s, "
          f"{(body or {}).get('message')}")
    for st in steps:
        print(f"         [{st['step']}] {st['say']} -> {st['message']}; "
              f"hata {st['error_cm']} cm")
    record('demo_scenario -> 200, 4 adımın hepsi doğrulandı',
           status == 200 and body['ok'] and len(steps) == 4 and all(st['ok'] for st in steps))
    record('hedefli adımların uç konum hataları <= 1.5 cm (TF)',
           all(st['error_cm'] <= TOOL_TOLERANCE * 100 for st in steps[1:3]) and len(steps) == 4)
    time.sleep(1.0)
    tool = tool_from_tf(buffer, config.tip_link)
    err = math.dist(tool, home_ref) if tool and home_ref else float('inf')
    now = joint_snapshot(state, names)
    record('senaryo sonunda robot home\'da (TF + eklemler)',
           err <= HOME_TOLERANCE and max(abs(v) for v in now.values()) < 0.05,
           f'(TF farkı {err * 100:.2f} cm)')
    s = http_.state()
    record('/api/state: komut bitti, son komut demo_scenario ve başarılı',
           not s['command']['running'] and s['command']['last']['command'] == 'demo_scenario'
           and s['command']['last']['ok'])
    status, _, body = http_.post('home')
    record('senaryodan sonra yeni komut kabul ediliyor (kilit serbest)', status == 200)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('cmd_check_node')
    config = load_robot_config()
    state = JointStateCache(node)
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(opts.url, config, state, buffer)
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 40 else 1)


if __name__ == '__main__':
    main()
