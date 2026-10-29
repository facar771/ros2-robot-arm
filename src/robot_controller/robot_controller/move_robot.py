import rclpy
from rclpy.node import Node
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from sensor_msgs.msg import JointState
import json
import os
import time

class AdvancedRobotCLI(Node):
    def __init__(self):
        super().__init__('advanced_robot_cli')
        
        # Publisher ve Subscriber
        self.publisher_ = self.create_publisher(
            JointTrajectory, 
            '/arm_controller/joint_trajectory', 
            10
        )
        self.subscription = self.create_subscription(
            JointState,
            '/joint_states',
            self.joint_state_callback,
            10
        )
        
        self.latest_positions = []
        
        # Dahili pozisyon hafızası
        self.poses = {
            "home": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "cnc":  [0.5, -0.4, 0.4, 0.0, 0.5, 0.0],
            "pc":   [-0.5, -0.3, 0.3, -0.2, 0.5, 0.0],
            "dik":  [0.0, -1.57, 1.57, 0.0, 0.0, 0.0]
        }
        
        self.route_file = os.path.expanduser('~/ros2_ws/routes.json')
        self.create_default_route_file()

    def joint_state_callback(self, msg):
        self.latest_positions = list(msg.position)

    def create_default_route_file(self):
        if not os.path.exists(self.route_file):
            default_data = {
                "rutin_1": [
                    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    [0.2, -0.2, 0.2, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                ]
            }
            with open(self.route_file, 'w') as f:
                json.dump(default_data, f, indent=4)

    def send_trajectory(self, positions, duration_sec=2.0):
        traj = JointTrajectory()
        traj.joint_names = ['joint_1', 'joint_2', 'joint_3', 'joint_4', 'joint_5', 'joint_6']
        
        point = JointTrajectoryPoint()
        point.positions = positions
        point.time_from_start.sec = int(duration_sec)
        point.time_from_start.nanosec = int((duration_sec % 1) * 1e9)
        
        traj.points.append(point)
        self.publisher_.publish(traj)

    def run_command(self, cmd_line):
        parts = cmd_line.split()
        if not parts:
            return
        
        cmd = parts[0].lower()

        # 1. Durum Takibi (status)
        if cmd == 'status':
            if self.latest_positions:
                print(f"[Anlık Açılar]: {[round(p, 3) for p in self.latest_positions]}")
            else:
                print("[-] Henüz /joint_states verisi alınamadı.")

        # 2. Serbest Açı Girişi (move j1 j2 j3 j4 j5 j6 [süre])
        elif cmd == 'move':
            if len(parts) < 7:
                print("[-] Eksik parametre! Örnek: move 0.0 -0.5 0.5 0.0 0.0 0.0 [süre]")
                return
            try:
                vals = [float(x) for x in parts[1:7]]
                duration = float(parts[7]) if len(parts) > 7 else 2.0
                self.send_trajectory(vals, duration)
                print(f"[+] Özel açılara hareket gönderildi: {vals} ({duration}s)")
            except ValueError:
                print("[-] Hatalı sayı formatı!")

        # 3. Hazır Pozisyon veya Süreli Çağrı (home / cnc [süre])
        elif cmd in self.poses:
            duration = float(parts[1]) if len(parts) > 1 else 2.0
            self.send_trajectory(self.poses[cmd], duration)
            print(f"[+] '{cmd}' konumuna gidiliyor ({duration}s)")

        # 4. JSON Dosyasından Rota Okuma (load <rota_adi>)
        elif cmd == 'load':
            if len(parts) < 2:
                print("[-] Rota adı belirtmediniz! Örnek: load rutin_1")
                return
            route_name = parts[1]
            if os.path.exists(self.route_file):
                with open(self.route_file, 'r') as f:
                    data = json.load(f)
                    if route_name in data:
                        print(f"[+] '{route_name}' rotası başlatılıyor...")
                        for step_idx, pos in enumerate(data[route_name]):
                            self.send_trajectory(pos, duration_sec=2.0)
                            print(f" -> Adım {step_idx + 1}: {pos}")
                            time.sleep(2.2)
                        print("[+] Rota başarıyla tamamlandı.")
                    else:
                        print(f"[-] '{route_name}' rotası JSON içinde bulunamadı.")

        # 5. Döngüsel Test Modu (loop <komut> <tekrar_sayisi>)
        elif cmd == 'loop':
            if len(parts) < 3:
                print("[-] Örnek kullanım: loop cnc 3")
                return
            target_pose = parts[1]
            try:
                count = int(parts[2])
            except ValueError:
                print("[-] Tekrar sayısı sayı olmalıdır!")
                return

            if target_pose not in self.poses:
                print(f"[-] Geçersiz pozisyon: {target_pose}")
                return

            print(f"[+] '{target_pose}' için {count} tur döngü başlatılıyor...")
            for i in range(count):
                print(f" -> Tur {i+1}/{count}")
                # Home ile hedef arasında gidip gelsin
                self.send_trajectory(self.poses["home"], 2.0)
                time.sleep(2.2)
                self.send_trajectory(self.poses[target_pose], 2.0)
                time.sleep(2.2)
            print("[+] Döngü bitti.")

        else:
            print(f"[-] Bilinmeyen komut: '{cmd}'. Yardım için listeye bakın.")

def main(args=None):
    rclpy.init(args=args)
    node = AdvancedRobotCLI()

    print("\n" + "="*50)
    print(" 🚀 GELİŞMİŞ ROBOT KONTROL MERKEZİ AKTİF")
    print("="*50)
    print(" Komutlar:")
    print("   - status                 : Anlık eklem açılarını göster")
    print("   - home / cnc / pc / dik  : Hazır pozisyonlar (İsteğe bağlı süre: cnc 4.0)")
    print("   - move j1 j2 j3 j4 j5 j6 : Serbest açı gönder (Örn: move 0.2 -0.2 0 0 0 0)")
    print("   - load rutin_1           : routes.json dosyasından rota çalıştır")
    print("   - loop cnc 3             : Belirtilen pozisyonu döngüye sok")
    print("   - exit                   : Çıkış")
    print("-"*50)

    try:
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.1)
            cmd_line = input("robot_cli> ").strip()
            if cmd_line.lower() == 'exit':
                break
            elif cmd_line:
                node.run_command(cmd_line)
    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
