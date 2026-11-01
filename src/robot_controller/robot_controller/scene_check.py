"""End-to-end check of perception -> planning scene. Run while
``ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true
perception_scene:=true`` is up:

    ros2 run robot_controller perception_scene_check

1. detected objects appear in MoveIt's scene (one box each, at the true position);
2. an unrelated motion still plans with the objects present;
3. a joint goal that puts link_6 *inside the red box* is REJECTED by the planner (the premise,
   that the goal really is inside the box, is verified with MoveIt's forward kinematics);
4. moving an object in Gazebo moves its collision box (same id, no duplicate);
5. removing the red box in Gazebo removes its collision object, and the same goal now succeeds.

Objects are moved/removed with ``gz service`` against the running world. Exit code 0 only if
every check passed.
"""
import math
import subprocess
import sys
import time

import rclpy
from ament_index_python.packages import get_package_share_directory
from moveit_msgs.msg import PlanningSceneComponents
from moveit_msgs.srv import GetPlanningScene, GetPositionFK
from rclpy.node import Node
from robot_core.perception import classify_color
from robot_core.workspace import load_workspace

from robot_controller.adapters import BackgroundSpinner, MoveItMotion, wait_future
from robot_controller.config_loader import load_robot_config
from robot_core import JointTarget

WAIT_S = 90.0
POSITION_TOLERANCE = 0.02   # m
# After a move the box settles within the synchroniser's hysteresis (move_threshold, 1 cm).
SETTLED_TOLERANCE = 0.012   # m
# Joint goal that puts link_6's origin at the red box centre (0.35, 0.15, 0.03); found with a
# forward-kinematics search over the URDF. The check verifies this premise with /compute_fk, so
# if the model or the world changes, the check fails loudly instead of passing for the wrong reason.
REACH_RED_BOX = (0.405, 0.55, 1.95, 0.0, 1.4, 0.0)
REACH_LINK = 'link_6'
WORLD = 'workspace'


def gz_service(service: str, reqtype: str, req: str) -> bool:
    out = subprocess.run(
        ['gz', 'service', '-s', f'/world/{WORLD}/{service}', '--reqtype', reqtype,
         '--reptype', 'gz.msgs.Boolean', '--timeout', '5000', '--req', req],
        capture_output=True, text=True, timeout=30)
    return out.returncode == 0 and 'true' in out.stdout


class SceneReader:
    def __init__(self, node: Node):
        self._client = node.create_client(GetPlanningScene, '/get_planning_scene')

    def objects(self, timeout: float = 5.0):
        """id -> (x, y, z, n_primitives, frame) of every collision object; None on failure."""
        if not self._client.wait_for_service(timeout_sec=timeout):
            return None
        req = GetPlanningScene.Request()
        req.components.components = PlanningSceneComponents.WORLD_OBJECT_GEOMETRY
        future = self._client.call_async(req)
        if not wait_future(future, timeout) or future.result() is None:
            return None
        out = {}
        for co in future.result().scene.world.collision_objects:
            if not co.primitive_poses:
                continue
            p, q = co.pose.position, co.primitive_poses[0].position   # identity orientations
            out[co.id] = (p.x + q.x, p.y + q.y, p.z + q.z, len(co.primitives), co.header.frame_id)
        return out


def fk_position(node: Node, client, joint_names, values, link: str):
    if not client.wait_for_service(timeout_sec=10.0):
        return None
    req = GetPositionFK.Request()
    req.header.frame_id = 'world'
    req.fk_link_names = [link]
    req.robot_state.joint_state.name = list(joint_names)
    req.robot_state.joint_state.position = [float(v) for v in values]
    future = client.call_async(req)
    if not wait_future(future, 10.0) or not future.result() or not future.result().pose_stamped:
        return None
    p = future.result().pose_stamped[0].pose.position
    return (p.x, p.y, p.z)


def wait_until(predicate, timeout: float, period: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(period)
    return predicate()


def why(result) -> str:
    return result.code_name or result.message or 'bilinmeyen neden'


def run(node: Node, config, ws, reader: SceneReader, motion: MoveItMotion) -> list:
    results = []

    def record(name, ok, detail=''):
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok

    truth = {f'obj_{classify_color(s.color)}_1': s for s in ws.shapes}
    red, green = 'obj_red_1', 'obj_green_1'
    home = JointTarget(config.poses['home'], 'home')
    cnc = JointTarget(config.poses['cnc'], 'cnc')
    reach = JointTarget(REACH_RED_BOX, 'red_box_içi')

    def scene():
        return reader.objects() or {}

    found = wait_until(lambda: set(truth) <= set(scene()), WAIT_S)
    if not record('algılanan nesneler planlama sahnesinde belirdi',
                  found, f'(beklenen {sorted(truth)}, sahnede {sorted(scene())}; '
                         'perception_scene:=true ile başlattın mı?)'):
        return results
    objs = scene()
    ok = True
    for oid, shape in sorted(truth.items()):
        x, y, z, n, frame = objs[oid]
        err = math.dist((x, y, z), shape.center)
        good = err <= POSITION_TOLERANCE and n == 1 and frame == 'world'
        ok &= good
        print(f"       {'ok ' if good else 'BAD'} {oid:15s} konum hatası {err * 100:.2f} cm, "
              f"{n} şekil, çerçeve '{frame}'")
    record('her nesne tek kutu, world çerçevesinde, gerçek konumunda (<= 2 cm)', ok)

    result = motion.move_to(home)
    result = motion.move_to(cnc) if result.ok else result
    record('nesneler sahnedeyken ilgisiz hareket (home -> cnc) planlanıyor', result.ok,
           '' if result.ok else f'-> {why(result)}')
    motion.move_to(home)

    fk = fk_position(node, node.create_client(GetPositionFK, '/compute_fk'),
                     config.joint_names, REACH_RED_BOX, REACH_LINK)
    box_center = truth[red].center
    inside = fk is not None and math.dist(fk, box_center) < 0.03
    if not record(f'önkoşul: hedefte {REACH_LINK} kırmızı kutunun içinde (MoveIt FK)', inside,
                  f'(FK {None if fk is None else tuple(round(v, 3) for v in fk)}, '
                  f'kutu {box_center})'):
        return results

    result = motion.move_to(reach)
    record('kutunun içinden geçen hedef için plan REDDEDİLDİ',
           not result.ok and result.code is not None,
           f'-> {why(result)}' if not result.ok
           else '-> plan nesneye rağmen BAŞARILI: algı çarpışma nesnesi üretmiyor')

    before = set(scene())
    gy = truth[green].center[1] - 0.05
    gz_ok = gz_service('set_pose', 'gz.msgs.Pose',
                       f'name: "green_cylinder" position {{x: {truth[green].center[0]} y: {gy} '
                       f'z: {truth[green].center[2]}}} orientation {{w: 1}}')
    moved = gz_ok and wait_until(
        lambda: green in scene() and abs(scene()[green][1] - gy) < SETTLED_TOLERANCE, 60.0)
    after = scene()
    record('Gazebo\'da taşınan nesnenin kutusu taşındı (aynı kimlik, yinelenen yok)',
           bool(moved) and set(after) == before and after[green][3] == 1,
           f'(y: {after.get(green, (0, math.nan))[1]:.3f}, beklenen {gy:.3f}, '
           f'kimlikler {sorted(after)})')

    removed = gz_service('remove', 'gz.msgs.Entity', 'name: "red_box" type: MODEL')
    gone = removed and wait_until(lambda: red not in scene(), 40.0)
    record('Gazebo\'dan kaldırılan nesnenin çarpışma nesnesi sahneden silindi', bool(gone),
           f'(sahnede {sorted(scene())})')

    result = motion.move_to(reach)
    record('nesne kalkınca aynı hedef için plan tekrar BAŞARILI', result.ok,
           '' if result.ok else f'-> {why(result)}')
    motion.move_to(home)
    return results


def main(args=None):
    rclpy.init(args=args)
    node = Node('perception_scene_check_node')
    config = load_robot_config()
    ws = load_workspace(f"{get_package_share_directory('robot_sim')}/worlds/workspace.sdf")
    reader = SceneReader(node)
    motion = MoveItMotion(node, config)
    spinner = BackgroundSpinner(node)
    spinner.start()
    try:
        results = run(node, config, ws, reader, motion)
    finally:
        spinner.stop()
        node.destroy_node()
        rclpy.shutdown()
    passed = sum(results)
    print(f'\n=== {passed}/{len(results)} test geçti ===')
    sys.exit(0 if results and passed == len(results) and len(results) >= 8 else 1)


if __name__ == '__main__':
    main()
