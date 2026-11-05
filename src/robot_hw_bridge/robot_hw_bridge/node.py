"""ROS 2 düğümü: gerçek robotun (ESP32) FollowJointTrajectory sunucusu ve /joint_states yayıncısı.

    ros2 run robot_hw_bridge hw_bridge_node --ros-args -p bridge_host:=host.docker.internal

* ``/arm_controller/follow_joint_trajectory``  MoveIt'in beklediği action (moveit_controllers.yaml).
* ``/joint_states``                            ESP32 STATUS'tan, ``robot_core.hardware`` dönüşümüyle.
* ``~/home``, ``~/clear`` (std_srvs/Trigger)   homing ve MCU acil dur kilidini açma. Açılış: UNHOMED.
* ``~/state`` (std_msgs/String, JSON)          hat/MCU/homed/uyarılar.
* Robot API (``robot_api_url``, yalnızca 127.0.0.1) ile iki yönlü acil dur: bkz. ``robot_api.py``.

Hub köprüsüne TCP ile bağlanır (``host.docker.internal:8770``); koparsa 1 sn aralıkla yeniden dener ve
her yeniden bağlanmada yeniden homing ister. **Yazılım acil durdurması donanım acil durdurmanın yerine
geçmez** (docs/HARDWARE.md §6, §11).
"""
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from robot_core.hardware import load_hardware
from robot_core.profile import load_profile
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import Trigger
from trajectory_msgs.msg import JointTrajectory

from robot_hw_bridge import core
from robot_hw_bridge.host import HwLink
from robot_hw_bridge.robot_api import EstopRelay, RobotApiClient
from robot_hw_bridge.transport import SocketTransport

ENV_PROFILE = 'ROBOT_HARDWARE_PROFILE'
ENV_TOKEN = 'HUB_BRIDGE_TOKEN'
PROFILE_REL = Path('src') / 'robot_description' / 'profile' / 'hardware.profile.yaml'


def find_profile(explicit: str = '') -> Path:
    """Parametre > ortam değişkeni > depo içinde (robot_core'dan yukarı doğru) arama."""
    for candidate in (explicit, os.environ.get(ENV_PROFILE, '')):
        if candidate:
            return Path(candidate)
    here = Path(core.__file__).resolve()
    for parent in here.parents:
        if (parent / PROFILE_REL).is_file():
            return parent / PROFILE_REL
    for root in ('/workspace',):
        if (Path(root) / PROFILE_REL).is_file():
            return Path(root) / PROFILE_REL
    raise FileNotFoundError('hardware.profile.yaml bulunamadı: hardware_profile parametresi '
                            f'ya da {ENV_PROFILE} verin')


class HwBridgeNode(Node):
    def __init__(self):
        super().__init__('hw_bridge')
        self.declare_parameter('hardware_profile', '')
        self.declare_parameter('bridge_host', 'host.docker.internal')
        self.declare_parameter('bridge_port', 8770)
        self.declare_parameter('bridge_token', '')        # ya da ortam değişkeni HUB_BRIDGE_TOKEN
        self.declare_parameter('watchdog_ms', 300)          # 200-500
        self.declare_parameter('jog_topic', '/arm_controller/joint_trajectory')
        self.declare_parameter('loop_stall_ms', 250)        # 50..watchdog_ms; dar = daha güvenli
        self.declare_parameter('robot_api_url', 'http://127.0.0.1:8080')    # '' = bağ yok
        self.declare_parameter('joint_states_hz', 20.0)
        self.declare_parameter('action_name', 'arm_controller/follow_joint_trajectory')
        path = find_profile(str(self.get_parameter('hardware_profile').value))
        self.hw = load_hardware(path)
        self.robot = load_profile(path.with_name(f'{self.hw.robot}.yaml'))
        for warning in self.hw.warnings:
            self.get_logger().warning(warning)
        cfg = core.BridgeConfig(watchdog_ms=int(self.get_parameter('watchdog_ms').value),
                                loop_stall_s=int(self.get_parameter('loop_stall_ms').value) / 1000)
        self.relay: Optional[EstopRelay] = None
        self.bridge = core.HwBridge(self.hw, self.robot, cfg, on_estop=self._report_estop)
        url = str(self.get_parameter('robot_api_url').value)
        if url:
            self.relay = EstopRelay(RobotApiClient(url), hw_estop=self.bridge.estop,
                                    hw_stop=self.bridge.stop,
                                    moving=lambda: self.bridge.executing).start()
        ticks = MutuallyExclusiveCallbackGroup()
        work = ReentrantCallbackGroup()
        jog_group = MutuallyExclusiveCallbackGroup()
        self._pub = self.create_publisher(JointState, '/joint_states', 10)
        self._state_pub = self.create_publisher(String, '~/state', 10)
        hz = max(1.0, float(self.get_parameter('joint_states_hz').value))
        self.create_timer(1.0 / hz, self._publish, callback_group=ticks)
        self.create_timer(0.02, self.bridge.tick, callback_group=ticks)
        self.create_timer(0.5, self._publish_state, callback_group=ticks)
        self.create_service(Trigger, '~/home', self._srv_home, callback_group=work)
        self.create_service(Trigger, '~/clear', self._srv_clear, callback_group=work)
        self.create_subscription(String, '/trajectory_execution_event', self._on_event, 10,
                                 callback_group=ticks)
        # jog: kısa ufuklu setpoint akışı (robot_viz web_view); topic adı ros2_control'ün varsayılanıdır
        self.create_subscription(JointTrajectory, str(self.get_parameter('jog_topic').value),
                                 self._on_jog, 1, callback_group=jog_group)
        self._server = ActionServer(
            self, FollowJointTrajectory, str(self.get_parameter('action_name').value),
            execute_callback=self._execute, goal_callback=lambda goal: GoalResponse.ACCEPT,
            cancel_callback=lambda goal: CancelResponse.ACCEPT, callback_group=work)
        self._stop = threading.Event()
        self._conn = threading.Thread(target=self._connect_loop, name='hw-connect', daemon=True)
        self._conn.start()
        self.get_logger().info('hw_bridge hazır: açılış durumu UNHOMED; ~/home ile homing yapın')

    # ---- ESP32 -> Robot API -----------------------------------------------------------------
    def _report_estop(self, reason: str) -> None:
        self.get_logger().error(f'ACİL DUR bildirimi: {reason}')
        if self.relay is not None:
            self.relay.report(reason)

    # ---- bağlantı yönetimi ------------------------------------------------------------------
    def _connect_loop(self) -> None:
        host = str(self.get_parameter('bridge_host').value)
        port = int(self.get_parameter('bridge_port').value)
        token = str(self.get_parameter('bridge_token').value) or os.environ.get(ENV_TOKEN) or None
        wd = self.bridge.cfg.watchdog_ms / 1000
        seq = 0
        while not self._stop.is_set():
            try:
                transport = SocketTransport(host, port, auth_token=token)
            except OSError as exc:
                self.get_logger().warning(f'köprüye bağlanılamadı ({host}:{port}): {type(exc).__name__}',
                                          throttle_duration_sec=5.0)
                self._stop.wait(1.0)
                continue
            link = HwLink(transport, link_timeout_s=wd, on_status=self.bridge.on_status,
                          on_fault=self.bridge.on_fault, on_link_lost=self.bridge.on_link_lost,
                          on_link_up=self.bridge.on_link_up, initial_seq=seq)
            self.bridge.attach(link)
            link.start()
            ok, msg = self.bridge.configure(wait_s=5.0)
            if ok:
                self.get_logger().info(f'ESP32: {msg}')
            else:
                self.get_logger().warning(f'ESP32: {msg}')
            while not self._stop.wait(0.2):
                if link.closed_reason or self.bridge._stalled:
                    break
            self.get_logger().warning(f'hat kapandı: {link.closed_reason or "döngü takıldı"}; '
                                      'yeniden bağlanılıyor (yeniden homing gerekir)')
            link.stop()
            # MCU eski (geri giden) seq'li çerçeveleri atar: bir sonraki bağlantı kaldığı yerden sürer
            seq = link.next_seq
            self._stop.wait(1.0)

    # ---- yayınlar ---------------------------------------------------------------------------
    def _publish(self) -> None:
        q = self.bridge.positions()
        if q is None:
            return
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = list(self.hw.joint_names)
        msg.position = [float(v) for v in q]
        msg.velocity = [float(v) for v in self.bridge.velocities()]
        self._pub.publish(msg)

    def _publish_state(self) -> None:
        self._state_pub.publish(String(data=json.dumps(self.bridge.state_doc())))

    def _on_event(self, msg: String) -> None:
        if msg.data == 'stop':
            self.bridge.stop('MoveIt/robot API durdur')

    def _on_jog(self, msg: JointTrajectory) -> None:
        points = [core.Point(p.time_from_start.sec + p.time_from_start.nanosec * 1e-9,
                             tuple(p.positions)) for p in msg.points]
        outcome = self.bridge.setpoint(list(msg.joint_names), points)
        if not outcome.ok:
            self.get_logger().warning(f'jog setpoint reddedildi: {outcome.message}',
                                      throttle_duration_sec=2.0)

    # ---- servisler --------------------------------------------------------------------------
    def _srv_home(self, request, response):
        response.success, response.message = self.bridge.home()
        return response

    def _srv_clear(self, request, response):
        response.success, response.message = self.bridge.clear()
        return response

    # ---- action ---------------------------------------------------------------------------
    def _execute(self, goal_handle):
        traj = goal_handle.request.trajectory
        points = [core.Point(p.time_from_start.sec + p.time_from_start.nanosec * 1e-9,
                             tuple(p.positions)) for p in traj.points]
        result = FollowJointTrajectory.Result()
        outcome = self.bridge.execute(list(traj.joint_names), points,
                                      cancelled=lambda: goal_handle.is_cancel_requested)
        result.error_code = outcome.code
        result.error_string = outcome.message
        if outcome.ok:
            goal_handle.succeed()
        elif goal_handle.is_cancel_requested:
            goal_handle.canceled()
        else:
            goal_handle.abort()
            self.get_logger().warning(f'yörünge reddedildi/kesildi: {outcome.message}')
        return result

    def shutdown(self) -> None:
        self._stop.set()
        if self.relay is not None:
            self.relay.close()
        self.bridge.close()


def main(args=None):
    rclpy.init(args=args)
    node = HwBridgeNode()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
        time.sleep(0.1)


if __name__ == '__main__':
    main()
