import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import JointConstraint, Constraints

class MoveItCLI(Node):
    def __init__(self):
        super().__init__('cli_moveit_node')
        self._action_client = ActionClient(self, MoveGroup, 'move_action')
        self.get_logger().info("T1.4 MoveIt CLI İstemcisi Hazır.")

    def send_goal(self, target_joints):
        if not self._action_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().error("MoveIt Action sunucusu (move_action) bulunamadı!")
            return False

        goal_msg = MoveGroup.Goal()
        goal_msg.request.group_name = "robot_arm" # Doğru grup adı
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
            self.get_logger().error("[-] Planlama reddedildi!")
            return

        self.get_logger().info("[+] Planlama kabul edildi, yürütülüyor...")
        self._get_result_future = goal_handle.get_result_async()
        self._get_result_future.add_done_callback(self.get_result_callback)

    def get_result_callback(self, future):
        result = future.result().result
        if result.error_code.val == 1:
            self.get_logger().info("[+] Başarıyla planlandı ve yürütüldü!")
        else:
            self.get_logger().error(f"[-] Planlama başarısız! Hata kodu: {result.error_code.val}")

def main(args=None):
    rclpy.init(args=args)
    node = MoveItCLI()

    print("\n[FAZ 1.4] MoveIt Planlama CLI Aktif")
    print("Kullanım: move j1 j2 j3 j4 j5 j6 (Örn: move 0.2 -0.2 0.2 0 0 0)")
    
    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            cmd_line = input("moveit_cli> ").strip()
            if cmd_line.lower() == 'exit':
                break
            
            parts = cmd_line.split()
            if parts and parts[0].lower() == 'move' and len(parts) == 7:
                try:
                    vals = [float(x) for x in parts[1:7]]
                    node.send_goal(vals)
                except ValueError:
                    print("Hatalı sayı formatı!")
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
