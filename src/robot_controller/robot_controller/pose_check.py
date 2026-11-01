"""End-to-end check of Cartesian goals and the Skills layer. Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` is up:

    ros2 run robot_controller pose_check

The tool position is measured from TF (robot_state_publisher on the *simulated* joint states), not
from MoveIt's own report:

1. ``go_above`` each detected object: the link_6 origin ends up within POSITION_TOLERANCE of the
   target (object top + clearance) and of the same point computed from the world file, with the
   tool pointing down;
2. an unreachable goal gives an explicit error and the joints do not move;
3. an unknown object gives an error that lists the known ones, without any motion.
Exit code 0 only if every check passed.

Reachability is a property of the arm, not of the code: with the tool pointing straight down,
joint_3 (+-2.0 rad) and joint_5 (+-1.57 rad) only allow a *window* of clearances per object, and
none at all for a target ~0.30 m from the base (the blue ball used to sit there; it is now at
x=0.45). So the clearance is chosen per object, inside its window (see tests/test_reach.py).
"""
import math
import sys
import time

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from robot_core.perception import classify_color
from robot_core.skills import Skills
from robot_core.workspace import load_workspace
from tf2_ros import Buffer, TransformException, TransformListener

from robot_controller.adapters import (
    BackgroundSpinner,
    JointStateCache,
    MoveItMotion,
    PlanningSceneReader,
)
from robot_controller.config_loader import load_robot_config

WAIT_S = 90.0
# Clearance above the object top (m) and whether this arm must reach it (see module docstring).
CASES = (('green', 0.25, True), ('red', 0.30, True), ('blue', 0.20, True))
POSITION_TOLERANCE = 0.01  # m, tool vs target (planner tolerance is 5 mm)
TRUTH_TOLERANCE = 0.02     # m, tool vs the same point from the world file (perception error)
TOOL_DOWN_MIN_DOT = 0.97   # cos of ~14 degrees; the planner tolerance is 0.15 rad


def tool_pose(buffer: Buffer, link: str):
    """(position, tool z axis in world) of ``link`` from TF, or None."""
    try:
        t = buffer.lookup_transform('world', link, rclpy.time.Time(),
                                    rclpy.duration.Duration(seconds=5.0)).transform
    except TransformException:
        return None
    q = t.rotation
    z_axis = (2 * (q.x * q.z + q.y * q.w), 2 * (q.y * q.z - q.x * q.w),
              1 - 2 * (q.x * q.x + q.y * q.y))
    return (t.translation.x, t.translation.y, t.translation.z), z_axis


def run(config, ws, skills: Skills, state: JointStateCache, buffer: Buffer) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    truth = {classify_color(s.color): s for s in ws.shapes}
    deadline = time.monotonic() + WAIT_S
    names = set()
    while time.monotonic() < deadline:
        names = {o.label for o in skills.list_objects()}
        if names >= set(truth):
            break
        time.sleep(1.0)
    if not record('Skills.list_objects algılanan tüm nesneleri veriyor', names >= set(truth),
                  f'(görünen: {sorted(names)}; perception_scene:=true ile başlattın mı?)'):
        return results

    skills.go_to_named_pose('home')
    time.sleep(1.0)
    for label, clearance, must_reach in CASES:
        shape = truth[label]
        before = state.joint_positions()
        result = skills.go_above(label, clearance)
        time.sleep(1.0)    # let the controller settle; TF follows /joint_states
        if not result.ok and not must_reach:
            after = state.joint_positions()
            moved = max(abs(after[j] - before[j]) for j in config.joint_names)
            record(f"go_above('{label}', {clearance}): erişilemedi, açık hatayla reddedildi, "
                   'hareket yok', bool(result.message) and moved < 0.01,
                   f'(-> {result.message}; eklem değişimi {moved:.4f} rad)')
            continue
        if not record(f"go_above('{label}', {clearance}) başarılı", result.ok,
                      '' if result.ok else f'-> {result.message}'):
            continue
        measured = tool_pose(buffer, config.tip_link)
        if measured is None:
            record(f'{config.tip_link} TF ile ölçülebildi', False)
            continue
        (px, py, pz), z_axis = measured
        target = tuple(result.data['target'])
        true_target = (shape.center[0], shape.center[1],
                       shape.center[2] + shape.height / 2 + clearance)
        e_target = math.dist((px, py, pz), target)
        e_truth = math.dist((px, py, pz), true_target)
        pointing_down = -z_axis[2]
        print(f'       ölçülen ({px:.3f}, {py:.3f}, {pz:.3f})  hedef ({target[0]:.3f}, '
              f'{target[1]:.3f}, {target[2]:.3f})  hata {e_target * 100:.2f} cm; '
              f'dünya dosyasındaki noktaya hata {e_truth * 100:.2f} cm; '
              f'alet aşağı: {pointing_down:.3f}')
        record(f'{label}: uç işlevci hedefe <= {POSITION_TOLERANCE * 100:.0f} cm yaklaştı '
               '(TF ölçümü)', e_target <= POSITION_TOLERANCE)
        record(f'{label}: algılanan nesnenin gerçek üstüne <= {TRUTH_TOLERANCE * 100:.0f} cm',
               e_truth <= TRUTH_TOLERANCE)
        record(f'{label}: alet aşağı bakıyor', pointing_down >= TOOL_DOWN_MIN_DOT)

    skills.go_to_named_pose('home')
    time.sleep(1.0)
    before = state.joint_positions()
    result = skills.go_to_position(2.0, 0.0, 0.5)
    time.sleep(1.0)
    after = state.joint_positions()
    moved = max(abs(after[j] - before[j]) for j in config.joint_names)
    record('ulaşılamaz hedef (2.0, 0, 0.5) açık hatayla reddedildi, robot hareket etmedi',
           not result.ok and 'erişim alanı dışında' in result.message and moved < 0.01,
           f'(-> {result.message}; eklem değişimi {moved:.4f} rad)')

    # Inside the nominal radius but beyond the arm: MoveIt itself must say no, and clearly.
    result = skills.go_to_position(1.25, 0.0, 0.3)
    time.sleep(1.0)
    after = state.joint_positions()
    moved = max(abs(after[j] - before[j]) for j in config.joint_names)
    record('kola göre uzak ama yarıçap içindeki hedefi MoveIt reddetti, mesaj açık',
           not result.ok and bool(result.code_name) and 'Robot hareket etmedi' in result.message
           and moved < 0.01, f'(-> {result.message}; eklem değişimi {moved:.4f} rad)')

    result = skills.go_above('mor_inek')
    record('bilinmeyen nesne: bilinenleri sayan hata, hareket yok',
           not result.ok and 'obj_red_1' in result.message, f'(-> {result.message})')
    skills.go_to_named_pose('home')
    return results


def main(args=None):
    rclpy.init(args=args)
    node = Node('pose_check_node')
    config = load_robot_config()
    ws = load_workspace(f"{get_package_share_directory('robot_sim')}/worlds/workspace.sdf")
    state = JointStateCache(node)
    motion = MoveItMotion(node, config)
    skills = Skills(config, motion, motion, PlanningSceneReader(node))
    buffer = Buffer()
    TransformListener(buffer, node)
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(config, ws, skills, state, buffer)
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 12 else 1)


if __name__ == '__main__':
    main()
