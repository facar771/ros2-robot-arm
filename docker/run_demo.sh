#!/usr/bin/env bash
# Interactive demo: headless Gazebo + MoveIt + perception + planning scene, plus the read-only
# browser view (robot_viz). Stays up until it is stopped; Ctrl-C (SIGINT) and SIGTERM shut every
# process down in order.
#
#   Terminal 1:  docker compose up demo      (this script; Ctrl-C stops it)
#   Terminal 2:  ./docker/cli.sh             (command line: `help`, `objects`, `above red 0.3`, ...)
#   Browser:     http://localhost:8080
#
# The web view listens on 0.0.0.0 *inside* the container; compose.yaml publishes the port on the
# host's 127.0.0.1 only, so it is not reachable from other machines.
# NOTE: no `set -u`: ROS setup scripts reference unset variables (e.g. COLCON_TRACE).
set -o pipefail

PORT=${VIEW_PORT:-8080}
CTRL_TIMEOUT=${CTRL_TIMEOUT:-240}

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /workspace/install/setup.bash

SIM_LOG=/tmp/sim_launch.log
VIEW_LOG=/tmp/web_view.log
# setsid: each launch gets its own process group, so it can be stopped together with its nodes.
setsid ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true \
    perception_scene:=true >"$SIM_LOG" 2>&1 &
SIM_PID=$!
setsid ros2 launch robot_viz web_view.launch.py host:=0.0.0.0 port:="$PORT" >"$VIEW_LOG" 2>&1 &
VIEW_PID=$!

cleanup() {
    trap - EXIT
    echo "[*] Kapatılıyor..."
    kill -INT -- "-$VIEW_PID" "-$SIM_PID" 2>/dev/null     # graceful: launch stops its nodes
    for _ in $(seq 1 20); do
        kill -0 "$SIM_PID" 2>/dev/null || kill -0 "$VIEW_PID" 2>/dev/null || break
        sleep 1
    done
    kill -KILL -- "-$VIEW_PID" "-$SIM_PID" 2>/dev/null || true
    echo "[*] Kapatıldı."
}
trap cleanup EXIT
trap 'echo; exit 0' INT TERM      # run the EXIT trap, exit code 0: stopping the demo is not an error

fail() {  # fail <message> <log>
    echo "[!] $1"; tail -n 60 "$2"; exit 2
}

wait_for() {  # wait_for <description> <timeout_s> <command...>
    local what=$1 timeout=$2; shift 2
    local deadline=$((SECONDS + timeout))
    until "$@" >/dev/null 2>&1; do
        kill -0 "$SIM_PID" 2>/dev/null || fail "simülasyon süreci öldü (bekleniyordu: $what)" "$SIM_LOG"
        kill -0 "$VIEW_PID" 2>/dev/null || fail "web_view süreci öldü (bekleniyordu: $what)" "$VIEW_LOG"
        (( SECONDS > deadline )) && fail "zaman aşımı: $what" "$SIM_LOG"
        sleep 1 & wait $!
    done
    echo "[ok] $what"
}

controllers_active() {
    [ "$(ros2 control list_controllers 2>/dev/null | grep -c 'active')" -ge 2 ]
}
view_answers() {
    python3 -c "import urllib.request as u; u.urlopen('http://127.0.0.1:${PORT}/api/state', timeout=3)"
}

echo "[*] Simülasyon başlatılıyor (ilk açılış 1-3 dakika sürebilir)..."
wait_for "move_group" 120 bash -c 'ros2 node list 2>/dev/null | grep -q move_group'
wait_for "kontrolcüler aktif" "$CTRL_TIMEOUT" controllers_active
wait_for "web_view yanıt veriyor" 60 view_answers

cat <<BANNER

==============================================================================
  Hazır.
  Tarayıcı (Windows'ta, WSL'dekiyle aynı localhost):  http://localhost:8080
  Komut satırı, İKİNCİ terminalde (WSL'de):   ./docker/cli.sh
  Durdurmak için bu terminalde Ctrl-C.
==============================================================================

BANNER

while true; do
    kill -0 "$SIM_PID" 2>/dev/null || fail "simülasyon süreci beklenmedik şekilde bitti" "$SIM_LOG"
    kill -0 "$VIEW_PID" 2>/dev/null || fail "web_view süreci beklenmedik şekilde bitti" "$VIEW_LOG"
    sleep 2 & wait $!
done
