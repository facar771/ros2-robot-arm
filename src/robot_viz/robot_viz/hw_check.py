"""End-to-end check of the hardware bridge node with the ESP32 SIMULATOR (B2). Run via
``run_selftest hw`` (ros2 launch robot_hw_bridge hw_stack.launch.py bridge_host:=127.0.0.1
bridge_port:=8771, plus ``web_view.launch.py commands:=true mode:=real use_sim_time:=false``):

    ros2 run robot_viz hw_check            # --url http://127.0.0.1:8080 --sim-port 8771

The check plays the Hub bridge: it serves ``Esp32Sim`` over TCP (``SimServer``) and the bridge node
connects like it will connect to the Hub (``host.docker.internal:8770``). The arm is judged by the
simulated MCU's *physical* position (``Esp32Sim.true_position``), not by what the node reports.

 1. start-up: Robot API says ``mode: real``; bridge connected but UNHOMED; /joint_states flows
 2. before homing a FollowJointTrajectory goal is refused (nothing moves, no MOVE sent)
 3. ``~/home``: homed; MCU counters equal the hardware.py conversion of the profile home position
 4. a trajectory through the action server arrives (physical position == hardware.py steps)
 5. MoveIt-planned Robot API job ``home`` runs through the bridge and arrives
 6. Robot API emergency stop mid-motion: MCU ESTOPPED/HOST_CMD, arm stops (measured), further
    jobs ``423``; after ``/api/reset`` the bridge still refuses (unhomed) until clear + home
 7. MCU emergency stop (NC button) mid-motion: Robot API ``estopped`` (hw_bridge), job cancelled
 8. ESP32 telemetry lost (TCP dropped) mid-motion: MCU watchdog stops the arm, Robot API
    ``estopped``; the node reconnects by itself and is UNHOMED again
 9. node process frozen (SIGSTOP) mid-motion: the MCU's own watchdog stops the arm unaided
Exit code 0 only if every check passed.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time

import rclpy
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient
from rclpy.node import Node
from robot_controller.adapters import BackgroundSpinner
from robot_core.hardware import load_hardware
from robot_core.jogapi import READY_POSE
from robot_hw_bridge.node import find_profile
from robot_hw_bridge.protocol import EstopReason, State
from robot_hw_bridge.sim import SimAxis
from std_msgs.msg import String
from std_srvs.srv import Trigger
from trajectory_msgs.msg import JointTrajectoryPoint

from robot_hw_bridge import Esp32Sim, SimServer
from robot_viz.jobs_check import wait_job
from robot_viz.stop_check import Api, wait_for

JOINTS = [f"joint_{i}" for i in range(1, 7)]
FAR = [1.0, -0.8, 0.8, 0.0, 0.6, 0.0]  # inside the placeholder profile's limits
QUIET_STEPS_PER_S = 20.0  # physical speed below this = standing still
STOP_DEADLINE_S = 2.0
WAIT_S = 120.0


class Plant:
    """Samples the simulated MCU's physical axis positions (ground truth) and their speed."""

    def __init__(self, sim: Esp32Sim):
        self.sim, self.samples = sim, []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(2.0)

    def _run(self):
        while not self._stop.is_set():
            self.samples.append((time.monotonic(), [self.sim.true_position(i) for i in range(6)]))
            time.sleep(0.01)

    def position(self):
        return [self.sim.true_position(i) for i in range(6)]

    def speed(self, window: float = 0.15) -> float:
        now = time.monotonic()
        recent = [s for s in self.samples if now - window <= s[0]]
        if len(recent) < 2:
            return float("inf")
        dt = recent[-1][0] - recent[0][0]
        return max(abs(a - b) for a, b in zip(recent[-1][1], recent[0][1])) / max(dt, 1e-3)

    def quiet_since(self, start: float):
        """Time of the last sample at/after ``start`` that was faster than the quiet threshold."""
        last = None
        data = [s for s in self.samples if s[0] >= start - 0.2]
        for a, b in zip(data, data[1:]):
            dt = b[0] - a[0]
            if (
                dt > 0
                and max(abs(x - y) for x, y in zip(a[1], b[1])) / dt > QUIET_STEPS_PER_S
                and b[0] >= start
            ):
                last = b[0]
        return last


class Rig(Node):
    def __init__(self):
        super().__init__("hw_check")
        self.state = {}
        self.joints = None
        self.create_subscription(String, "/hw_bridge/state", self._on_state, 10)
        from sensor_msgs.msg import JointState

        self.create_subscription(JointState, "/joint_states", self._on_joints, 10)
        self.action = ActionClient(
            self, FollowJointTrajectory, "/arm_controller/follow_joint_trajectory"
        )
        self.home_srv = self.create_client(Trigger, "/hw_bridge/home")
        self.clear_srv = self.create_client(Trigger, "/hw_bridge/clear")

    def _on_state(self, msg):
        self.state = json.loads(msg.data)

    def _on_joints(self, msg):
        self.joints = dict(zip(msg.name, msg.position))

    def trigger(self, client, timeout=90.0):
        if not client.wait_for_service(timeout_sec=10.0):
            return False, "servis yok"
        future = client.call_async(Trigger.Request())
        end = time.monotonic() + timeout
        while not future.done() and time.monotonic() < end:
            time.sleep(0.02)
        r = future.result()
        return (r.success, r.message) if r else (False, "yanıt yok")

    def recover(self, timeout=25.0):
        """~/clear (retried: just after a dropped link the node is still reconnecting), ~/home."""
        end = time.monotonic() + timeout
        ok, msg = False, ""
        while time.monotonic() < end:
            ok, msg = self.trigger(self.clear_srv)
            if ok:
                break
            time.sleep(0.5)
        if not ok:
            return False, f"clear: {msg}"
        ok2, msg2 = self.trigger(self.home_srv)
        return ok2 and bool(
            wait_for(lambda: self.state.get("homed") is True, 3.0)
        ), f"{msg} | {msg2}"

    def goal(self, positions, seconds, start=None):
        g = FollowJointTrajectory.Goal()
        g.trajectory.joint_names = JOINTS
        for t, q in ((0.0, start if start is not None else [0.0] * 6), (seconds, positions)):
            p = JointTrajectoryPoint()
            p.positions = [float(v) for v in q]
            p.time_from_start.sec, p.time_from_start.nanosec = int(t), int((t % 1) * 1e9)
            g.trajectory.points.append(p)
        return g

    def send(self, goal, wait=True, timeout=60.0):
        """Returns (accepted, error_code, error_string, handle)."""
        if not self.action.wait_for_server(timeout_sec=10.0):
            return False, None, "action sunucusu yok", None
        future = self.action.send_goal_async(goal)
        wait_for(future.done, 10.0, 0.01)
        handle = future.result()
        if handle is None or not handle.accepted:
            return False, None, "goal kabul edilmedi", handle
        if not wait:
            return True, None, "", handle
        res = handle.get_result_async()
        wait_for(res.done, timeout, 0.02)
        r = res.result()
        return (
            True,
            (r.result.error_code if r else None),
            (r.result.error_string if r else ""),
            handle,
        )


def make_sim() -> Esp32Sim:
    axes = [SimAxis(phys_min=-5000.0, phys_max=20000.0) for _ in range(6)]
    return Esp32Sim(axes)


def run(base: str, port: int) -> list:
    results = []

    def record(name, ok, detail=""):
        results.append(bool(ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
        return bool(ok)

    hw = load_hardware(find_profile())
    sim = make_sim()
    server = SimServer(sim, "127.0.0.1", port).start()
    plant = Plant(sim)
    plant.start()
    rclpy.init()
    node = Rig()
    spinner = BackgroundSpinner(node)
    spinner.start()
    api = Api(base)
    try:
        # ---- 1. start-up
        up = wait_for(
            lambda: node.state.get("link_ok") and node.state.get("configured"), WAIT_S, 0.2
        )
        robot = wait_for(api.robot, 60.0, 0.5)
        record(
            "1 bridge ESP32 simülatörüne bağlandı, yapılandırıldı (watchdog+limitler)",
            up,
            f"watchdog_ms={sim.watchdog_ms}",
        )
        if not up or not robot:
            return results
        record(
            "1 Robot API mode: real, durum idle",
            robot["mode"] == "real" and robot["state"] == "idle",
            f"mode={robot['mode']} state={robot['state']}",
        )
        record(
            "1 açılış durumu UNHOMED (köprü ve MCU)",
            node.state.get("homed") is False and sim.state == State.UNHOMED,
            str(node.state.get("mcu_state")),
        )
        record(
            "1 /joint_states akıyor, 6 eklem",
            bool(wait_for(lambda: node.joints and len(node.joints) == 6, 10.0)),
        )
        record(
            "1 açık döngü uyarısı köprü durumunda görünüyor",
            bool(node.state.get("warnings")) and len(node.state.get("open_loop_axes", [])) == 6,
        )

        # ---- 2. before homing
        before = plant.position()
        ok_goal, code, text, _ = node.send(node.goal(FAR, 2.0))
        record(
            "2 homing öncesi hedef reddedildi, hiçbir şey hareket etmedi",
            ok_goal
            and code != 0
            and "homing" in text
            and plant.position() == before
            and sim.state == State.UNHOMED,
            f"code={code} {text}",
        )

        # ---- 3. home
        success, msg = node.trigger(node.home_srv)
        expect = [a.to_steps_int(a.homing.position) for a in hw.axes]
        counters = [round(a.cmd) for a in sim.axes]
        record(
            "3 ~/home başarılı, köprü homed",
            success and bool(wait_for(lambda: node.state.get("homed") is True, 3.0)),
            msg,
        )
        record(
            "3 MCU sayaçları hardware.py dönüşümüne eşit (home konumu)",
            counters == expect,
            f"sayaç={counters} beklenen={expect}",
        )

        # ---- 4. trajectory through the action server
        ok_goal, code, text, _ = node.send(node.goal(FAR, 3.0))
        steps = hw.joints_to_steps(FAR)
        phys = [round(p) for p in plant.position()]
        record("4 yörünge action sunucusundan geçti ve SUCCESS döndü", ok_goal and code == 0, text)
        record(
            "4 fiziksel konum == hardware.py adımları (±3 adım)",
            all(abs(a - b) <= 3 for a, b in zip(phys, steps)),
            f"fiziksel={phys} beklenen={steps}",
        )
        record(
            "4 /joint_states hedefi gösteriyor (±0.03 rad)",
            bool(
                wait_for(
                    lambda: (
                        node.joints
                        and all(abs(node.joints[n] - q) < 0.03 for n, q in zip(JOINTS, FAR))
                    ),
                    3.0,
                )
            ),
        )

        # ---- 5. MoveIt-planned Robot API job
        # F4b (B'): a task motion starts INSIDE the box (or at home). FAR is outside the box and
        # not a park pose, so stand at 'ready' first (raw bridge goal, below the API guard);
        # 'home' then runs ready -> home as the verified park transit through the same bridge.
        ok_goal, code, text, _ = node.send(node.goal(READY_POSE, 4.0, start=FAR))
        record("5 ön koşul: kol ready'ye (kutu içi) getirildi (ham köprü hedefi)",
               ok_goal and code == 0, text)
        status, _, body = api.post("/api/jobs", {"command": "home"}, timeout=30.0)
        done = wait_job(api, body["id"], "done", 90.0) if status == 202 else None
        time.sleep(0.5)
        phys = [round(p) for p in plant.position()]
        record(
            "5 Robot API 'home' işi MoveIt planıyla köprüden geçip bitti",
            bool(done) and done["result"]["ok"],
            str(done and done.get("result", {}).get("message")),
        )
        record(
            "5 fiziksel konum home adımlarında",
            all(abs(a - b) <= 6 for a, b in zip(phys, expect)),
            f"fiziksel={phys}",
        )

        # ---- 6. Robot API emergency stop mid-motion
        ok_goal, _, _, handle = node.send(node.goal(FAR, 5.0), wait=False)
        away = wait_for(
            lambda: plant.speed() > 40 and abs(plant.position()[0] - expect[0]) > 60, 10.0, 0.02
        )
        t_stop = time.monotonic()
        status, _, stop = api.post("/api/stop", {"reason": "hw_check"})
        quiet = wait_for(lambda: plant.speed() < QUIET_STEPS_PER_S, 5.0, 0.01)
        took = time.monotonic() - t_stop
        freeze = plant.position()
        time.sleep(0.6)
        creep = max(abs(a - b) for a, b in zip(plant.position(), freeze))
        record(
            "6 acil dur: MCU ESTOPPED/HOST_CMD (sürücüler kesik)",
            bool(
                wait_for(
                    lambda: sim.state == State.ESTOPPED and sim.reason == EstopReason.HOST_CMD, 3.0
                )
            ),
            f"{sim.state.name}/{sim.reason.name}",
        )
        record(
            f"6 kol ölçülerek durdu (<= {STOP_DEADLINE_S} s) ve sürünmedi",
            bool(away) and status == 200 and bool(quiet) and took <= STOP_DEADLINE_S and creep < 3,
            f"durma={took:.2f}s sürünme={creep:.1f} adım",
        )
        record("6 durmuş robotta yeni iş 423", api.post("/api/jobs", {"command": "home"})[0] == 423)
        record(
            "6 köprü homed bilgisini kaybetti",
            wait_for(lambda: node.state.get("homed") is False, 3.0),
        )
        status, _, body = api.post("/api/reset", {})
        record(
            "6 /api/reset yazılım kilidini açtı (donanım hâlâ kilitli)",
            status == 200 and sim.state == State.ESTOPPED,
            f"http={status}",
        )
        ok_goal, code, text, _ = node.send(node.goal(FAR, 2.0, start=[p / 509.0 for p in [0] * 6]))
        record(
            "6 reset sonrası köprü hareket vermiyor (ESP32 acil dur + unhomed)",
            code != 0 and plant.speed() < 5,
            text,
        )
        ready, msg = node.recover()
        record("6 ~/clear + ~/home sonrası yeniden hazır", ready, msg)

        # ---- 7. MCU emergency stop (NC button)
        ok_goal, _, _, handle = node.send(
            node.goal(FAR, 5.0, start=[a.homing.position for a in hw.axes]), wait=False
        )
        wait_for(lambda: plant.speed() > 40, 10.0, 0.02)
        status, _, body = api.post(
            "/api/jobs", {"command": "home"}, timeout=30.0
        )  # busy with the action?
        sim.press_estop()
        tripped = wait_for(lambda: (api.robot() or {}).get("state") == "estopped", 4.0, 0.05)
        reason = ((api.robot() or {}).get("estop") or {}).get("reason") or ""
        record(
            "7 NC buton: Robot API estopped (hw_bridge nedeniyle)",
            bool(tripped) and "hw_bridge" in reason,
            reason,
        )
        record("7 kol durdu", bool(wait_for(lambda: plant.speed() < QUIET_STEPS_PER_S, 3.0, 0.01)))
        listing = api.http.request("GET", "/api/jobs")[2]
        running = [j for j in json.loads(listing)["jobs"] if j["state"] in ("running", "queued")]
        record("7 çalışan iş kalmadı", not running, str([j["state"] for j in running]))
        sim.release_estop()
        api.post("/api/reset", {})
        ready, msg = node.recover()
        record("7 buton bırakıldı: clear + home ile yeniden hazır", ready, msg)

        # ---- 8. telemetry lost: TCP dropped mid-motion
        node.send(node.goal(FAR, 5.0, start=[a.homing.position for a in hw.axes]), wait=False)
        moving = wait_for(lambda: plant.speed() > 40, 10.0, 0.02)
        # N6: an idle MCU (drivers off) is not latched by the watchdog, so a drop without motion
        # says nothing; fail with the real reason instead of a misleading '3.02 s'.
        record("8 ön koşul: kol hareket ediyordu (hat koparılmadan önce)", bool(moving))
        t_drop = time.monotonic()
        server.drop_client()
        mcu_stop = wait_for(
            lambda: sim.state == State.ESTOPPED and sim.reason == EstopReason.WATCHDOG, 3.0, 0.02
        )
        record(
            "8 hat koptu: MCU kendi watchdog'uyla durdu (<= 0.8 s)",
            bool(mcu_stop) and time.monotonic() - t_drop <= 0.8,
            f"{time.monotonic() - t_drop:.2f}s",
        )
        record(
            "8 kol fiziksel olarak durdu",
            bool(wait_for(lambda: plant.speed() < QUIET_STEPS_PER_S, 2.0, 0.01)),
        )
        tripped = wait_for(lambda: (api.robot() or {}).get("state") == "estopped", 4.0, 0.05)
        record("8 Robot API estopped (ESP32 telemetrisi kesildi)", bool(tripped))
        back = wait_for(
            lambda: node.state.get("link_ok") and node.state.get("homed") is False, 15.0, 0.2
        )
        record("8 düğüm kendiliğinden yeniden bağlandı ve UNHOMED", bool(back))
        api.post("/api/reset", {})
        ready, msg = node.recover()
        record("8 clear + home ile yeniden hazır", ready, msg)

        # ---- 9. node process frozen
        pids = subprocess.run(
            ["pgrep", "-f", "lib/robot_hw_bridge/hw_bridge_node"], capture_output=True, text=True
        ).stdout.split()
        pid = int(pids[0]) if pids else None
        record("9 hw_bridge_node süreci bulundu", pid is not None, str(pids))
        if pid is not None:
            node.send(node.goal(FAR, 6.0, start=[a.homing.position for a in hw.axes]), wait=False)
            wait_for(lambda: plant.speed() > 40, 10.0, 0.02)
            t_freeze = time.monotonic()
            os.kill(pid, signal.SIGSTOP)
            try:
                froze = wait_for(
                    lambda: sim.state == State.ESTOPPED and sim.reason == EstopReason.WATCHDOG,
                    3.0,
                    0.02,
                )
                took = time.monotonic() - t_freeze
                stopped = wait_for(lambda: plant.speed() < QUIET_STEPS_PER_S, 2.0, 0.01)
            finally:
                os.kill(pid, signal.SIGCONT)
            record(
                "9 düğüm donduruldu: MCU watchdog'u kendiliğinden durdurdu (<= 0.8 s)",
                bool(froze) and bool(stopped) and took <= 0.8,
                f"{took:.2f}s",
            )
            tripped = wait_for(lambda: (api.robot() or {}).get("state") == "estopped", 6.0, 0.1)
            record("9 düğüm uyanınca Robot API estopped", bool(tripped))
    finally:
        plant.close()
        spinner.stop()
        node.destroy_node()
        rclpy.try_shutdown()
        server.stop()
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--sim-port", type=int, default=8771)
    args = parser.parse_args(argv)
    results = run(args.url, args.sim_port)
    passed = sum(results)
    print(f"=== {passed}/{len(results)} test geçti ===", flush=True)
    return 0 if results and passed == len(results) and len(results) >= 25 else 1


if __name__ == "__main__":
    sys.exit(main())
