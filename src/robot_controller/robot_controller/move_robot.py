"""Direct trajectory CLI (bypasses MoveIt: publishes to the joint_trajectory_controller topic).

No collision checking here -- use cli_moveit for collision-aware motion.
Design notes (vs. the previous version): node spun in a background thread, motion completion is
detected from /joint_states instead of blind time.sleep(), targets validated against joint limits,
routes file location configurable via ROBOT_ROUTES.
"""
import json
import os
import threading
import time

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from robot_controller.config_loader import load_robot_config
from robot_core import TargetError, TargetParser

REACH_TOL = 0.02  # rad


class AdvancedRobotCLI(Node):
    def __init__(self):
        super().__init__('advanced_robot_cli')
        self.publisher_ = self.create_publisher(
            JointTrajectory, '/arm_controller/joint_trajectory', 10)
        self.create_subscription(JointState, '/joint_states', self.joint_state_callback, 10)
        self.config = load_robot_config()
        self.parser = TargetParser(self.config)
        self.joint_pos = {}
        self.poses = {k: list(v) for k, v in self.config.poses.items()}
        self.route_file = os.environ.get(
            'ROBOT_ROUTES', os.path.expanduser('~/ros2_ws/data/routes.json'))
        self.create_default_route_file()

    def joint_state_callback(self, msg):
        for n, p in zip(msg.name, msg.position):
            self.joint_pos[n] = p

    def current(self):
        if all(j in self.joint_pos for j in self.config.joint_names):
            return [self.joint_pos[j] for j in self.config.joint_names]
        return None

    def create_default_route_file(self):
        if not os.path.exists(self.route_file):
            os.makedirs(os.path.dirname(self.route_file), exist_ok=True)
            default_data = {"rutin_1": [
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.2, -0.2, 0.2, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]]}
            with open(self.route_file, 'w') as f:
                json.dump(default_data, f, indent=4)

    def send_trajectory(self, positions, duration_sec=2.0):
        traj = JointTrajectory()
        traj.joint_names = list(self.config.joint_names)
        point = JointTrajectoryPoint()
        point.positions = [float(p) for p in positions]
        point.time_from_start.sec = int(duration_sec)
        point.time_from_start.nanosec = int((duration_sec % 1) * 1e9)
        traj.points.append(point)
        self.publisher_.publish(traj)

    def go(self, positions, duration_sec=2.0):
        """Send a point and wait until /joint_states reaches it (or give up)."""
        errs = self.config.validate(positions)
        if errs:
            print("[-] " + "; ".join(errs))
            return False
        self.send_trajectory(positions, duration_sec)
        deadline = time.monotonic() + duration_sec + 3.0
        while time.monotonic() < deadline:
            cur = self.current()
            if cur and max(abs(a - b) for a, b in zip(cur, positions)) < REACH_TOL:
                return True
            time.sleep(0.05)
        print("[-] Hedefe ulaşılamadı / /joint_states doğrulanamadı (controller aktif mi?)")
        return False

    def run_command(self, cmd_line):
        parts = cmd_line.split()
        if not parts:
            return
        cmd = parts[0].lower()

        if cmd == 'status':
            cur = self.current()
            print(f"[Anlık Açılar]: {[round(p, 3) for p in cur]}" if cur
                  else "[-] Henüz /joint_states verisi alınamadı.")

        elif cmd == 'move':
            if len(parts) < 7:
                print("[-] Eksik parametre! Örnek: move 0.0 -0.5 0.5 0.0 0.0 0.0 [süre]")
                return
            try:
                duration = float(parts[7]) if len(parts) > 7 else 2.0
            except ValueError:
                print("[-] Hatalı süre!")
                return
            try:
                vals = list(self.parser.parse(parts[1:7]).values)
            except TargetError as exc:
                print(f"[-] {exc}")
                return
            ok = self.go(vals, duration)
            print(f"[{'+' if ok else '-'}] move {vals} ({duration}s)")

        elif cmd in self.poses:
            try:
                duration = float(parts[1]) if len(parts) > 1 else 2.0
            except ValueError:
                print("[-] Hatalı süre!")
                return
            ok = self.go(self.poses[cmd], duration)
            print(f"[{'+' if ok else '-'}] '{cmd}' ({duration}s)")

        elif cmd == 'load':
            if len(parts) < 2:
                print("[-] Rota adı belirtmediniz! Örnek: load rutin_1")
                return
            if not os.path.exists(self.route_file):
                print(f"[-] Rota dosyası yok: {self.route_file} "
                      "(ROBOT_ROUTES ile değiştirilebilir)")
                return
            with open(self.route_file, 'r') as f:
                data = json.load(f)
            if parts[1] not in data:
                print(f"[-] '{parts[1]}' rotası JSON içinde bulunamadı.")
                return
            print(f"[+] '{parts[1]}' rotası başlatılıyor...")
            for i, pos in enumerate(data[parts[1]], 1):
                print(f" -> Adım {i}: {pos}")
                if not self.go(pos, 2.0):
                    print("[-] Rota yarıda kesildi.")
                    return
            print("[+] Rota tamamlandı.")

        elif cmd == 'loop':
            if len(parts) < 3 or parts[1] not in self.poses:
                print(f"[-] Örnek: loop cnc 3  (pozisyonlar: {', '.join(self.poses)})")
                return
            try:
                count = int(parts[2])
            except ValueError:
                print("[-] Tekrar sayısı sayı olmalıdır!")
                return
            print(f"[+] '{parts[1]}' için {count} tur...")
            for i in range(count):
                print(f" -> Tur {i + 1}/{count}")
                if not (self.go(self.poses['home']) and self.go(self.poses[parts[1]])):
                    print("[-] Döngü yarıda kesildi.")
                    return
            print("[+] Döngü bitti.")
        else:
            print(f"[-] Bilinmeyen komut: '{cmd}'.")


def main(args=None):
    rclpy.init(args=args)
    node = AdvancedRobotCLI()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()

    print("\n" + "=" * 50)
    print(" ROBOT KONTROL MERKEZİ (doğrudan trajectory, çarpışma kontrolü YOK)")
    print("=" * 50)
    print("   status | <poz> [süre] | move j1..j6 [süre] | load <rota> | loop <poz> <n> | exit")
    try:
        while rclpy.ok():
            try:
                line = input("robot_cli> ").strip()
            except EOFError:
                break
            if line.lower() == 'exit':
                break
            if line:
                node.run_command(line)
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
