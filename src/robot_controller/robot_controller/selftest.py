"""End-to-end smoke test. Run while `ros2 launch robot_moveit_config demo.launch.py` is up:

    ros2 run robot_controller selftest

Checks, in order: joint_states flowing, MoveIt plans+executes, the joints really moved (measured
from /joint_states, not from MoveIt's own report), and that obstacles are honoured by collision
checking. Exit code 0 only if everything passed.
"""
import sys
import time

import rclpy
from rclpy.node import Node

from robot_controller.adapters import (
    BackgroundSpinner,
    JointStateCache,
    MoveItMotion,
    PlanningSceneAdapter,
)
from robot_controller.config_loader import load_robot_config
from robot_core import BoxObstacle, JointTarget
from robot_data import close_sink, event_sink_from_env


def max_error(state, joint_names, target):
    positions = state.joint_positions()
    if not positions or not all(j in positions for j in joint_names):
        return None
    return max(abs(positions[j] - t) for j, t in zip(joint_names, target))


def why(result):
    return result.code_name or result.message or 'bilinmeyen neden'


def run(config, state, scene, motion, events=None):
    results = []

    def record(name, ok, detail=''):
        results.append(ok)
        if events is not None:
            events.emit('selftest_check', {'name': name, 'ok': bool(ok), 'detail': detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    home = JointTarget(config.poses['home'], 'home')
    cnc = JointTarget(config.poses['cnc'], 'cnc')
    names = config.joint_names

    start = time.monotonic()
    while max_error(state, names, home.values) is None and time.monotonic() - start < 10:
        time.sleep(0.1)
    if not record('joint_states akıyor', max_error(state, names, home.values) is not None,
                  '(joint_state_broadcaster aktif olmalı)'):
        return results

    result = motion.move_to(home)
    record("MoveIt home'a planlayıp gitti", result.ok, '' if result.ok else f'-> {why(result)}')
    if not result.ok:
        print("   İpucu: 'ros2 param get /move_group planning_pipelines' ve move_group "
              'loglarına bak.')
        return results

    result = motion.move_to(cnc)
    time.sleep(0.5)
    error = max_error(state, names, cnc.values)
    record("MoveIt cnc'ye planlayıp gitti", result.ok, '' if result.ok else f'-> {why(result)}')
    record('Robot gerçekten hareket etti (/joint_states)', error is not None and error < 0.05,
           f'(maks sapma {error:.3f} rad)' if error is not None else '')

    # A box overlapping the vertical arm at home -> the start state is in collision.
    motion.move_to(home)
    box = BoxObstacle('selftest_box', 0.0, 0.0, 0.5, 0.3, 0.3, 0.3)
    added = scene.add_box(box)
    record('Engel sahneye eklendi', added)
    time.sleep(0.5)
    result = motion.move_to(cnc)
    # MoveIt must have *answered* with an error code; a timeout/missing server is not a detection.
    record('Kolun içine konan engel çarpışma olarak algılandı (plan REDDEDİLDİ)',
           not result.ok and result.code is not None,
           f'-> {why(result)}' if not result.ok
           else '-> plan engele rağmen BAŞARILI: çarpışma kontrolü çalışmıyor')

    scene.remove_box(box.name)
    time.sleep(0.5)
    result = motion.move_to(cnc)
    record('Engel silinince plan tekrar başarılı', result.ok,
           '' if result.ok else f'-> {why(result)}')
    motion.move_to(home)
    return results


def main(args=None):
    rclpy.init(args=args)
    node = Node('selftest_node')
    config = load_robot_config()
    state = JointStateCache(node)
    scene = PlanningSceneAdapter(node)
    motion = MoveItMotion(node, config)
    events = event_sink_from_env('selftest')
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(config, state, scene, motion, events)
    finally:
        close_sink(events)
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) else 1)


if __name__ == '__main__':
    main()
