import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import JointConstraint, Constraints, CollisionObject
from moveit_msgs.srv import ApplyPlanningScene
from moveit_msgs.msg import PlanningScene
from shape_msgs.msg import SolidPrimitive
from geometry_msgs.msg import Pose

class MoveItCLI(Node):
    def __init__(self):
        super().__init__('cli_moveit_node')
        self._action_client = ActionClient(self, MoveGroup, 'move_action')
        self.scene_client = self.create_client(ApplyPlanningScene, '/apply_planning_scene')
        self.get_logger().info("T2.4 Servis Tabanlı Senkron Engel CLI Hazır.")

    def add_obstacle(self, name, x, y, z, sx, sy, sz):
        if not self.scene_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().error("Planlama sahnesi servisi bulunamadı!")
            return

        co = CollisionObject()
        co.id = name
        co.header.frame_id = "base_link"

        box = SolidPrimitive()
        box.type = SolidPrimitive.BOX
        box.dimensions = [float(sx), float(sy), float(sz)]

        pose = Pose()
        pose.position.x = float(x)
        pose.position.y = float(y)
        pose.position.z = float(z)
        pose.orientation.w = 1.0

        co.primitives.append(box)
        co.primitive_poses.append(pose)
        co.operation = CollisionObject.ADD

        scene = PlanningScene()
        scene.is_diff = True
        scene.world.collision_objects.append(co)

        req = ApplyPlanningScene.Request()
        req.scene = scene

        future = self.scene_client.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        if future.result() and future.result().success:
            print(f"[+] Engel sahneye başarıyla işlendi: {name}")
        else:
            print(f"[-] Engel eklenemedi!")

    def remove_obstacle(self, name):
        if not self.scene_client.wait_for_service(timeout_sec=2.0):
            return

        co = CollisionObject()
        co.id = name
        co.operation = CollisionObject.REMOVE

        scene = PlanningScene()
        scene.is_diff = True
        scene.world.collision_objects.append(co)

        req = ApplyPlanningScene.Request()
        req.scene = scene

        future = self.scene_client.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        print(f"[-] Engel silindi: {name}")

    def send_goal(self, target_joints):
        if not self._action_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().error("MoveIt Action sunucusu bulunamadı!")
            return False

        goal_msg = MoveGroup.Goal()
        goal_msg.request.group_name = "robot_arm"
        goal_msg.request.allowed_planning_time = 5.0
        goal_msg.request.num_planning_attempts = 10

        constraints = Constraints()
        for i, val in enumerate(target_joints):
            jc = JointConstraint()
            jc.joint_name = f'joint_{i+1}'
            jc.position = val
            jc.tolerance_above = 0.01
            jc.tolerance_below = 0.01
            jc.weight = 1.0
            constraints.joint_constraints.append(jc)

        goal_msg.request.goal_constraints.append(constraints)
        
        self.get_logger().info(f"Hedef gönderiliyor: {target_joints}")
        self._send_goal_future = self._action_client.send_goal_async(goal_msg)
        self._send_goal_future.add_done_callback(self.goal_response_callback)
        return True

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().error("[-] Planlama reddedildi (Engel nedeniyle rota bulunamadı)!")
            return

        self.get_logger().info("[+] Planlama kabul edildi, yürütülüyor...")
        self._get_result_future = goal_handle.get_result_async()
        self._get_result_future.add_done_callback(self.get_result_callback)

    def get_result_callback(self, future):
        result = future.result().result
        if result.error_code.val == 1:
            self.get_logger().info("[+] Engelin etrafından dolanılarak başarıyla ulaşıldı!")
        else:
            self.get_logger().error(f"[-] Planlama başarısız! Hata kodu: {result.error_code.val}")

def main(args=None):
    rclpy.init(args=args)
    node = MoveItCLI()

    print("\n[FAZ 2.4] Senkron Engel Yönetimi Aktif")
    
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            cmd_line = input("moveit_cli> ").strip()
            if not cmd_line:
                continue
            if cmd_line.lower() == 'exit':
                break
            
            parts = cmd_line.split()
            if parts[0].lower() == 'move' and len(parts) == 7:
                try:
                    vals = [float(x) for x in parts[1:7]]
                    node.send_goal(vals)
                except ValueError:
                    print("Hatalı sayı formatı!")
            elif parts[0].lower() == 'addbox' and len(parts) == 8:
                node.add_obstacle(parts[1], parts[2], parts[3], parts[4], parts[5], parts[6], parts[7])
            elif parts[0].lower() == 'rmbox' and len(parts) == 2:
                node.remove_obstacle(parts[1])
            else:
                print("Geçersiz komut!")
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
