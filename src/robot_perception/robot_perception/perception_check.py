"""Perception check. Run while ``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace
perception:=true`` is up:

    ros2 run robot_perception perception_check

Compares what the perception node publishes with the ground truth in the world file
(workspace.sdf): every object must be detected exactly once, with the right colour label, within
POSITION_TOLERANCE of its true centre and with roughly the right height, and the estimate must be
stable over several frames. Exit code 0 only if every check passed.
"""
import math
import statistics
import sys
import threading
import time

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from robot_core.perception import classify_color
from robot_core.workspace import load_workspace
from visualization_msgs.msg import MarkerArray

from robot_perception.perception_node import TOPIC_OBJECTS
from robot_perception.spinner import BackgroundSpinner

WAIT_S = 90.0
SAMPLES = 5
POSITION_TOLERANCE = 0.02   # m, distance to the true object centre
HEIGHT_TOLERANCE = 0.015    # m
JITTER_TOLERANCE = 0.005    # m, spread of the estimate over the samples (static scene)


class MarkerListener:
    def __init__(self, node: Node):
        self._lock = threading.Lock()
        self.frames = []          # each: {(label, index): (x, y, z, height)}
        self.frame_ids = set()
        node.create_subscription(MarkerArray, TOPIC_OBJECTS, self._on_message, 10)

    def _on_message(self, msg: MarkerArray) -> None:
        frame = {}
        for m in msg.markers:
            frame[(m.ns, m.id)] = (m.pose.position.x, m.pose.position.y, m.pose.position.z,
                                   m.scale.z)
            self.frame_ids.add(m.header.frame_id)
        with self._lock:
            self.frames.append(frame)

    def wait_for(self, labels, count: int, timeout: float):
        """Wait for ``count`` frames that contain every label; returns them (or fewer)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                good = [f for f in self.frames if {k[0] for k in f} >= set(labels)]
            if len(good) >= count:
                return good[-count:]
            time.sleep(0.2)
        with self._lock:
            return [f for f in self.frames if {k[0] for k in f} >= set(labels)]


def run(ws, listener: MarkerListener) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    expected = {}
    for shape in ws.shapes:
        label = classify_color(shape.color) if shape.color else None
        if label is not None:
            expected[label] = shape
    record('dünya dosyasındaki her nesnenin renk sınıfı var',
           len(expected) == len(ws.shapes) >= 3, f'({sorted(expected)})')

    frames = listener.wait_for(expected, SAMPLES, WAIT_S)
    if not record(f'/perception/objects yayında ve tüm nesneler görünüyor ({SAMPLES} kare)',
                  len(frames) >= SAMPLES,
                  '(world:=workspace perception:=true ile başlattın mı?)'):
        return results
    record("işaretçiler 'world' çerçevesinde", listener.frame_ids == {'world'},
           f'({sorted(listener.frame_ids)})')
    record('her nesne tam bir kez algılanıyor, fazladan nesne yok',
           all(set(f) == {(label, 0) for label in expected} for f in frames),
           f'({[sorted(f) for f in frames[-1:]]})')

    pos_ok = height_ok = jitter_ok = True
    for label, shape in sorted(expected.items()):
        samples = [f[(label, 0)] for f in frames if (label, 0) in f]
        med = [statistics.median(s[i] for s in samples) for i in range(4)]
        err = math.dist(med[:3], shape.center)
        herr = abs(med[3] - shape.height)
        jitter = max(max(s[i] for s in samples) - min(s[i] for s in samples) for i in range(3))
        ok = err <= POSITION_TOLERANCE
        pos_ok &= ok
        height_ok &= herr <= HEIGHT_TOLERANCE
        jitter_ok &= jitter <= JITTER_TOLERANCE
        print(f"       {'ok ' if ok else 'BAD'} {shape.name:15s} [{label:5s}] "
              f'konum hatası {err * 100:.2f} cm, yükseklik hatası {herr * 100:.2f} cm, '
              f'kareler arası oynama {jitter * 1000:.1f} mm')
    record(f'konum hatası <= {POSITION_TOLERANCE * 100:.0f} cm (her nesne)', pos_ok)
    record(f'yükseklik hatası <= {HEIGHT_TOLERANCE * 100:.1f} cm (her nesne)', height_ok)
    record(f'tahmin kareler arasında kararlı (<= {JITTER_TOLERANCE * 1000:.0f} mm)', jitter_ok)
    return results


def main(args=None):
    rclpy.init(args=args)
    node = Node('perception_check_node')
    ws = load_workspace(f"{get_package_share_directory('robot_sim')}/worlds/workspace.sdf")
    listener = MarkerListener(node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(ws, listener)
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 7 else 1)


if __name__ == '__main__':
    main()
