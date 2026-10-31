#!/usr/bin/env bash
# Starts MoveIt headless (no RViz), waits until it is really ready, runs the selftest, cleans up.
# Works inside the `ci` image (workspace already built) and in `dev` after `colcon build`.
# NOTE: no `set -u`: ROS setup scripts reference unset variables (e.g. COLCON_TRACE).
set -o pipefail

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /workspace/install/setup.bash

LOG=/tmp/demo_launch.log
# setsid: own process group, so cleanup can stop launch *and* all nodes it started.
setsid ros2 launch robot_moveit_config demo.launch.py use_rviz:=false >"$LOG" 2>&1 &
LAUNCH_PID=$!
cleanup() {
    kill -INT -- "-$LAUNCH_PID" 2>/dev/null   # graceful: launch shuts its nodes down
    for _ in $(seq 1 15); do kill -0 "$LAUNCH_PID" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$LAUNCH_PID" 2>/dev/null || true
}
trap cleanup EXIT

wait_for() {  # wait_for <description> <timeout_s> <command...>
    local what=$1 timeout=$2; shift 2
    local deadline=$((SECONDS + timeout))
    until "$@" >/dev/null 2>&1; do
        if ! kill -0 "$LAUNCH_PID" 2>/dev/null; then
            echo "[!] launch process died while waiting for: $what"; tail -n 60 "$LOG"; exit 2
        fi
        if (( SECONDS > deadline )); then
            echo "[!] timeout waiting for: $what"; tail -n 60 "$LOG"; exit 2
        fi
        sleep 1
    done
    echo "[ok] $what"
}

controllers_active() {
    [ "$(ros2 control list_controllers 2>/dev/null | grep -c 'active')" -ge 2 ]
}

wait_for "move_group node" 90 bash -c 'ros2 node list 2>/dev/null | grep -q move_group'
wait_for "controllers active" 60 controllers_active

timeout 180 ros2 run robot_controller selftest
STATUS=$?
if [ $STATUS -ne 0 ]; then
    echo "----- last launch log lines -----"; tail -n 80 "$LOG"
fi
exit $STATUS
