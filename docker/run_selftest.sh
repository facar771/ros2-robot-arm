#!/usr/bin/env bash
# Starts the simulation headless, waits until it is really ready, runs the selftest, cleans up.
#   run_selftest [mock|gazebo|gazebo-camera|gazebo-perception|gazebo-scene|gazebo-skills|gazebo-viz|gazebo-cmd|gazebo-stop|gazebo-jobs]
#   (default: mock; or set SIM_MODE)
# mock          = ros2_control mock_components (fast, no physics)
# gazebo        = Gazebo physics server (gz_ros2_control), no GUI/GPU
# gazebo-camera = gazebo + workspace world with RGB-D camera (software rendering without a GPU);
#                 also runs sensor_check
# gazebo-perception = gazebo-camera + colour-object detector (perception:=true); runs only
#                 perception_check (detected positions vs. the world file)
# gazebo-scene  = gazebo-perception + detections mirrored into MoveIt's planning scene
#                 (perception_scene:=true); runs only perception_scene_check (objects appear, a
#                 plan through one is rejected, moving/removing it in Gazebo updates the scene)
# gazebo-skills = same stack; runs only pose_check (Skills.go_above etc.: tool position measured
#                 from TF reaches the target, unreachable goals are refused with an explicit error)
# gazebo-viz    = same stack + the read-only browser view (robot_viz web_view on 127.0.0.1:8080);
#                 runs only viz_check (page, JSON, PNGs and overlay vs. the world file, joints/tool
#                 vs. /joint_states and TF, read-only guard, view follows a go_above move)
# gazebo-cmd    = same stack + the browser with commands enabled (web_view commands:=true); runs only
#                 cmd_check (white-listed POST /api/command: refusals, TF-measured moves, the demo
#                 scenario with live narration, 409 while busy)
# gazebo-stop   = same stack + the browser with commands enabled; runs only stop_check (POST /api/stop in
#                 the middle of a motion: the arm is measured to stop, 423 while latched, reset rules)
# gazebo-jobs   = same stack + the browser with commands enabled; runs only jobs_check (POST /api/jobs:
#                 202 + progress, cancel/timeout/e-stop mid-motion with the arm measured to stop)
# Works inside the `ci` image (workspace already built) and in `dev` after `colcon build`.
# NOTE: no `set -u`: ROS setup scripts reference unset variables (e.g. COLCON_TRACE).
set -o pipefail

MODE=${1:-${SIM_MODE:-mock}}
EXTRA=()    # a second launch that runs next to the simulation (browser view)
case "$MODE" in
    mock)   LAUNCH=(ros2 launch robot_moveit_config demo.launch.py use_rviz:=false); CTRL_TIMEOUT=60 ;;
    gazebo) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py); CTRL_TIMEOUT=120 ;;
    gazebo-camera) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace); CTRL_TIMEOUT=180 ;;
    gazebo-perception) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true); CTRL_TIMEOUT=180 ;;
    gazebo-scene) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true); CTRL_TIMEOUT=180 ;;
    gazebo-skills) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true); CTRL_TIMEOUT=180 ;;
    gazebo-viz) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true)
                EXTRA=(ros2 launch robot_viz web_view.launch.py); CTRL_TIMEOUT=180 ;;
    gazebo-cmd) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true); CTRL_TIMEOUT=180 ;;
    gazebo-stop) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true); CTRL_TIMEOUT=180 ;;
    gazebo-jobs) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true); CTRL_TIMEOUT=180 ;;
    *) echo "usage: run_selftest [mock|gazebo|gazebo-camera|gazebo-perception|gazebo-scene|gazebo-skills|gazebo-viz|gazebo-cmd|gazebo-stop]"; exit 64 ;;
esac
echo "[mode] $MODE"

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /workspace/install/setup.bash

LOG=/tmp/sim_launch.log
# setsid: own process group, so cleanup can stop launch *and* all nodes it started.
setsid "${LAUNCH[@]}" >"$LOG" 2>&1 &
LAUNCH_PID=$!
EXTRA_PID=
EXTRA_LOG=/tmp/extra_launch.log
if [ ${#EXTRA[@]} -gt 0 ]; then
    setsid "${EXTRA[@]}" >"$EXTRA_LOG" 2>&1 &
    EXTRA_PID=$!
fi
cleanup() {
    for pid in $EXTRA_PID $LAUNCH_PID; do
        kill -INT -- "-$pid" 2>/dev/null   # graceful: launch shuts its nodes down
    done
    for _ in $(seq 1 15); do
        alive=0
        for pid in $EXTRA_PID $LAUNCH_PID; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ $alive -eq 0 ] && break
        sleep 1
    done
    for pid in $EXTRA_PID $LAUNCH_PID; do kill -KILL -- "-$pid" 2>/dev/null || true; done
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
wait_for "controllers active" "$CTRL_TIMEOUT" controllers_active

STATUS=0
if [ "$MODE" = "gazebo-perception" ]; then
    timeout 180 ros2 run robot_perception perception_check || STATUS=$?
elif [ "$MODE" = "gazebo-scene" ]; then
    timeout 400 ros2 run robot_controller perception_scene_check || STATUS=$?
elif [ "$MODE" = "gazebo-skills" ]; then
    timeout 500 ros2 run robot_controller pose_check || STATUS=$?
elif [ "$MODE" = "gazebo-viz" ]; then
    timeout 500 ros2 run robot_viz viz_check || STATUS=$?
elif [ "$MODE" = "gazebo-cmd" ]; then
    timeout 900 ros2 run robot_viz cmd_check || STATUS=$?
elif [ "$MODE" = "gazebo-stop" ]; then
    timeout 900 ros2 run robot_viz stop_check || STATUS=$?
elif [ "$MODE" = "gazebo-jobs" ]; then
    timeout 900 ros2 run robot_viz jobs_check || STATUS=$?
else
    timeout 180 ros2 run robot_controller selftest || STATUS=$?
fi
if [ "$MODE" = "gazebo-camera" ]; then
    timeout 120 ros2 run robot_controller sensor_check || STATUS=$?
fi
if [ $STATUS -ne 0 ]; then
    echo "----- last launch log lines -----"; tail -n 80 "$LOG"
    if [ -n "$EXTRA_PID" ]; then echo "----- last web_view log lines -----"; tail -n 40 "$EXTRA_LOG"; fi
fi
exit $STATUS
