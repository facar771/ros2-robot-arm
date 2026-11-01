"""End-to-end check of the browser view. Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` and ``ros2 launch robot_viz web_view.launch.py`` are up:

    ros2 run robot_viz viz_check            # --url http://127.0.0.1:8080

Everything the page shows is compared with an independent source, not with itself:

1. page, script and style are served; ``/api/state`` has the documented keys;
2. detected objects match the world file (position), and the pixel boxes in the JSON match
   ``robot_core.workspace.project`` of the true object centres;
3. ``/frame/rgb.png`` is the camera image (compared with a frame this check subscribed to itself)
   plus the overlay; the overlay outlines sit exactly where the JSON says, and their centres sit
   at the true object pixels;
4. ``/frame/depth.png`` is the colour map of the camera's depth image, and at probe pixels it has
   the colour of the depth ray-cast from the world file;
5. joint angles equal /joint_states, the tool position equals TF world <- tip link;
6. nothing can be changed through it (POST/PUT/DELETE: 405) and foreign Host headers get 403;
7. the view follows the robot: after ``go_above('green')`` the reported tool position is at the
   target, equals TF, and the joints changed.
Exit code 0 only if every check passed.
"""
import argparse
import http.client
import json
import math
import sys
import time
from urllib.parse import urlsplit

import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from robot_controller.adapters import (
    BackgroundSpinner,
    JointStateCache,
    MoveItMotion,
    PlanningSceneReader,
)
from robot_controller.config_loader import load_robot_config
from robot_core.perception import classify_color
from robot_core.skills import Skills
from robot_core.viz import OVERLAY_COLOR, decode_png, depth_to_rgb
from robot_core.workspace import load_workspace, project, stable_probes
from robot_perception.images import depth_array, rgb_array
from sensor_msgs.msg import Image
from tf2_ros import Buffer, TransformException, TransformListener

WAIT_S = 90.0
POSITION_TOLERANCE = 0.025     # m, reported object centre vs world file (perception is <= 2 cm)
PIXEL_TOLERANCE = 5.0          # px, reported/drawn object centre vs true projection (2 cm ~ 5 px)
EDGE_FRACTION = 0.98           # share of an outline's pixels that must carry the overlay colour
UNCHANGED_FRACTION = 0.999     # share of non-overlay pixels equal to the camera image
JOINT_TOLERANCE = 0.01         # rad
TOOL_TOLERANCE = 0.015         # m
TARGET_TOLERANCE = 0.02        # m
MOVE_CASE = ('green', 0.25)    # reached reliably by this arm (see pose_check)
STATE_KEYS = {'time', 'camera', 'objects', 'objects_age_s', 'joints', 'tool'}


class Http:
    def __init__(self, base: str):
        parts = urlsplit(base)
        self.host, self.port = parts.hostname, parts.port or 80

    def request(self, method: str, path: str, headers=None, body=None):
        """(status, headers, body); (None, {}, b'') if the server cannot be reached."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        except OSError:
            return None, {}, b''
        finally:
            conn.close()

    def state(self):
        status, _, body = self.request('GET', '/api/state')
        return json.loads(body) if status == 200 else None


class CameraTap:
    """Keeps the latest raw camera frames (the reference the PNGs are compared with)."""

    def __init__(self, node: Node):
        self.rgb = self.depth = None
        node.create_subscription(Image, '/camera/image', self._on_rgb, qos_profile_sensor_data)
        node.create_subscription(Image, '/camera/depth_image', self._on_depth,
                                 qos_profile_sensor_data)

    def _on_rgb(self, msg):
        self.rgb = np.array(rgb_array(msg))

    def _on_depth(self, msg):
        self.depth = np.array(depth_array(msg))


def tool_from_tf(buffer: Buffer, link: str):
    try:
        t = buffer.lookup_transform('world', link, rclpy.time.Time(),
                                    rclpy.duration.Duration(seconds=5.0)).transform.translation
    except TransformException:
        return None
    return (t.x, t.y, t.z)


def edge_pixels(img: np.ndarray, rect):
    u0, v0, u1, v1 = rect
    h, w = img.shape[:2]
    u0, u1, v0, v1 = max(u0, 0), min(u1, w - 1), max(v0, 0), min(v1, h - 1)
    return np.concatenate([img[v0, u0:u1 + 1], img[v1, u0:u1 + 1],
                           img[v0:v1 + 1, u0], img[v0:v1 + 1, u1]])


def run(base: str, config, ws, skills: Skills, state: JointStateCache, buffer: Buffer,
        tap: CameraTap) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    http_ = Http(base)
    truth = {classify_color(s.color): s for s in ws.shapes}
    deadline = time.monotonic() + WAIT_S
    s = None
    while time.monotonic() < deadline:
        s = http_.state()
        fresh = s and s['camera']['rgb_age_s'] is not None and s['camera']['rgb_age_s'] < 2.0
        if fresh and {o['label'] for o in s['objects']} >= set(truth) and tap.rgb is not None \
                and tap.depth is not None:
            break
        time.sleep(1.0)
    else:
        record('web_view yanıt veriyor, kamera akıyor ve 3 nesne görünüyor', False,
               f'(son durum: {json.dumps(s)[:300] if s else "sunucuya ulaşılamadı: " + base})')
        return results
    record('web_view yanıt veriyor, kamera akıyor ve 3 nesne görünüyor', True)

    # 1. page and state document --------------------------------------------------------------
    status, headers, body = http_.request('GET', '/')
    html = body.decode(errors='replace')
    record('/ HTML sayfasını veriyor (kamera, derinlik, nesne ve eklem alanları)',
           status == 200 and headers.get('Content-Type', '').startswith('text/html')
           and all(k in html for k in ('id="rgb"', 'id="depth"', 'id="objects"', 'id="joints"',
                                       'id="tool"')))
    record('/app.js ve /style.css sunuluyor',
           http_.request('GET', '/app.js')[0] == 200
           and http_.request('GET', '/style.css')[0] == 200)
    record('/api/state belgelenen alanlara sahip', set(s) == STATE_KEYS,
           f'(alanlar: {sorted(s)})')
    cam_size = (ws.camera.width, ws.camera.height)
    record('kamera boyutu dünya dosyasıyla aynı',
           (s['camera']['width'], s['camera']['height']) == cam_size, f'({s["camera"]})')

    # 2. objects: world truth and pixel boxes --------------------------------------------------
    by_label = {o['label']: o for o in s['objects']}
    for label, shape in sorted(truth.items()):
        obj = by_label[label]
        err = math.dist(obj['position'], shape.center)
        u, v, _ = project(ws, shape.center)
        pixel = obj['pixel']
        perr = math.dist(pixel['center'], (u, v)) if pixel else float('inf')
        print(f'       {label}: konum hatası {err * 100:.2f} cm; piksel merkez '
              f'{pixel and pixel["center"]} gerçek ({u:.1f}, {v:.1f}) hata {perr:.2f} px')
        record(f'{label}: bildirilen konum dünya dosyasındakine '
               f'<= {POSITION_TOLERANCE * 100:.1f} cm', err <= POSITION_TOLERANCE)
        record(f'{label}: JSON piksel kutusu gerçek nesne pikseline <= {PIXEL_TOLERANCE:.0f} px',
               perr <= PIXEL_TOLERANCE)

    # 3. RGB PNG ------------------------------------------------------------------------------
    status, headers, body = http_.request('GET', '/frame/rgb.png')
    rgb = decode_png(body) if status == 200 else None
    record('/frame/rgb.png PNG, kamera boyutunda, no-store',
           rgb is not None and rgb.shape == (cam_size[1], cam_size[0], 3)
           and headers.get('Content-Type') == 'image/png'
           and 'no-store' in headers.get('Cache-Control', ''))
    if rgb is not None and rgb.shape[:2] == (cam_size[1], cam_size[0]):
        s = http_.state()         # static scene: same content as when the PNG was taken
        raw = tap.rgb
        covered = np.zeros(rgb.shape[:2], dtype=bool)
        for obj in s['objects']:
            u0, v0, u1, v1 = obj['pixel']['rect']
            covered[max(v0 - 12, 0):v1 + 13, max(u0 - 2, 0):u1 + 45] = True   # box + label band
        same = np.all(rgb == raw, axis=2)
        share = same[~covered].mean()
        record(f'RGB PNG, kamera görüntüsüyle bindirme dışında aynı (>= {UNCHANGED_FRACTION:.1%})',
               share >= UNCHANGED_FRACTION, f'(eşleşme {share:.2%})')
        for obj in s['objects']:
            edge = edge_pixels(rgb, obj['pixel']['rect'])
            frac = np.all(edge == OVERLAY_COLOR, axis=1).mean()
            u, v, _ = project(ws, truth[obj['label']].center)
            cu, cv = (int(round(x)) for x in obj['pixel']['center'])
            u0, v0, u1, v1 = obj['pixel']['rect']
            drawn_center = ((u0 + u1) / 2, (v0 + v1) / 2)
            derr = math.dist(drawn_center, (u, v))
            print(f"       {obj['label']}: çerçeve piksellerinin {frac:.1%}'i bindirme rengi; "
                  f'çerçeve merkezi gerçek nesne pikselinden {derr:.2f} px')
            record(f"{obj['label']}: bindirme çerçevesi PNG'de JSON'daki yerde çizili",
                   frac >= EDGE_FRACTION and tuple(rgb[cv, cu]) == OVERLAY_COLOR)
            record(f"{obj['label']}: çizilen çerçevenin merkezi gerçek nesne pikseline "
                   f'<= {PIXEL_TOLERANCE:.0f} px', derr <= PIXEL_TOLERANCE)

    # 4. depth PNG ----------------------------------------------------------------------------
    status, headers, body = http_.request('GET', '/frame/depth.png')
    dpng = decode_png(body) if status == 200 else None
    record('/frame/depth.png PNG, kamera boyutunda',
           dpng is not None and dpng.shape == (cam_size[1], cam_size[0], 3)
           and headers.get('Content-Type') == 'image/png')
    if dpng is not None and dpng.shape[:2] == (cam_size[1], cam_size[0]):
        near, far = 0.5, 1.7       # web_view defaults (depth_near / depth_far)
        expected = depth_to_rgb(tap.depth, near, far)
        share = np.all(dpng == expected, axis=2).mean()
        record('derinlik PNG, kameranın derinlik görüntüsünün renk haritasıyla aynı (>= 99%)',
               share >= 0.99, f'(eşleşme {share:.2%})')
        bad = []
        for name, (u, v, depth) in stable_probes(ws).items():
            want = depth_to_rgb(np.array([[depth]], dtype=np.float32), near, far)[0, 0].astype(int)
            got = dpng[v, u].astype(int)
            if np.abs(want - got).max() > 20:
                bad.append((name, tuple(got), tuple(want)))
        record("derinlik PNG'si, dünya dosyasından ışın izlemeyle hesaplanan derinlik renginde "
               '(her nesne ve zemin)', not bad, f'(uyuşmayanlar: {bad})' if bad else '')

    # 5. joints and tool ----------------------------------------------------------------------
    s = http_.state()
    names = list(config.joint_names)
    reported = {j['name']: j['rad'] for j in (s['joints'] or [])}
    record('eklem adları robot tanımıyla aynı sırada',
           [j['name'] for j in (s['joints'] or [])] == names)
    now = state.joint_positions()
    err = max((abs(reported.get(n, 1e9) - now[n]) for n in names), default=1e9)
    record(f'eklem açıları /joint_states ile <= {JOINT_TOLERANCE} rad', err <= JOINT_TOLERANCE,
           f'(en büyük fark {err:.4f} rad)')
    tf_tool = tool_from_tf(buffer, config.tip_link)
    tool = s['tool']
    terr = math.dist(tool['position'], tf_tool) if tool and tf_tool else float('inf')
    record(f'uç işlevci konumu TF ile <= {TOOL_TOLERANCE * 100:.1f} cm ve bağlantı adı doğru',
           tool is not None and tool['link'] == config.tip_link and terr <= TOOL_TOLERANCE,
           f'(fark {terr * 100:.2f} cm)')

    # 6. read-only and host guard ------------------------------------------------------------
    for method in ('POST', 'PUT', 'DELETE'):
        status, headers, _ = http_.request(method, '/api/state', body=b'{}',
                                           headers={'Content-Type': 'application/json'})
        record(f'{method} reddedildi (405, yalnızca GET/HEAD)',
               status == 405 and headers.get('Allow') == 'GET, HEAD', f'(durum {status})')
    status, _, _ = http_.request('GET', '/api/state', headers={'Host': 'evil.example.com'})
    record('yabancı Host başlığı 403', status == 403, f'(durum {status})')
    record('bilinmeyen yol 404', http_.request('GET', '/api/command')[0] == 404)

    # 7. the view follows the robot ----------------------------------------------------------
    label, clearance = MOVE_CASE
    before = {j['name']: j['rad'] for j in http_.state()['joints']}
    result = skills.go_above(label, clearance)
    time.sleep(1.5)          # controller settles; TF and /joint_states follow
    if record(f"go_above('{label}', {clearance}) başarılı", result.ok,
              '' if result.ok else f'-> {result.message}'):
        target = tuple(result.data['target'])
        s = http_.state()
        tool = s['tool']
        tf_tool = tool_from_tf(buffer, config.tip_link)
        e_target = math.dist(tool['position'], target)
        e_tf = math.dist(tool['position'], tf_tool)
        moved = max(abs(j['rad'] - before[j['name']]) for j in s['joints'])
        print(f'       görünümdeki uç konum {tool["position"]}, '
              f'hedef {[round(x, 3) for x in target]}, hata {e_target * 100:.2f} cm; '
              f'TF farkı {e_tf * 100:.2f} cm; en büyük eklem değişimi {moved:.3f} rad')
        record(f'görünümdeki uç işlevci konumu hedefe <= {TARGET_TOLERANCE * 100:.0f} cm yaklaştı',
               e_target <= TARGET_TOLERANCE)
        record('görünümdeki uç işlevci konumu TF ile tutarlı', e_tf <= TOOL_TOLERANCE)
        record('görünümdeki eklem açıları hareketi yansıtıyor (> 0.1 rad değişti)', moved > 0.1)
    skills.go_to_named_pose('home')
    time.sleep(1.0)
    return results


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    opts, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = Node('viz_check_node')
    config = load_robot_config()
    ws = load_workspace(f"{get_package_share_directory('robot_sim')}/worlds/workspace.sdf")
    state = JointStateCache(node)
    tap = CameraTap(node)
    motion = MoveItMotion(node, config)
    skills = Skills(config, motion, motion, PlanningSceneReader(node))
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(opts.url, config, ws, skills, state, buffer, tap)
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 25 else 1)


if __name__ == '__main__':
    main()
