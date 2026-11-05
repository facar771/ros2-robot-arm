#!/usr/bin/env bash
# Starts the simulation headless, waits until it is really ready, runs the selftest, cleans up.
#   run_selftest [mock|gazebo|gazebo-camera|gazebo-perception|gazebo-scene|gazebo-skills|gazebo-viz|gazebo-cmd|gazebo-stop|gazebo-jobs|gazebo-agent|gazebo-integration|gazebo-tracking|hw|jog|gazebo-jog]
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
# gazebo-agent  = same stack + the browser with commands enabled; runs only agent_check (robot_agent: valid /
#                 invalid / unsafe / e-stop requests through a scripted LLM, the real CLI incl. Ctrl-C)
# gazebo-integration = same stack + the browser with commands enabled; runs only integration_check: the Hub
#                 core (apps/control_hub) -> Robot API -> perception -> motion, with the live-camera vision
#                 plugin and the agent layer on the same arm. Needs the repo's apps/control_hub and plugins/
#                 inside the image (they are not baked in): mount them, see compose.yaml / ci.yml.
# gazebo-tracking = NO robot arm: robot_sim/worlds/tracking.sdf (3-DoF camera platform + scripted red target)
#                 and robot_vision_ros' tracking node; runs only tracking_check (lock behaviour on four
#                 scenarios, thresholds shared with the unit simulation). Slow: ~5 min (sim runs at 0.4x).
# hw            = NO simulator: robot_hw_bridge's hw_stack (robot_state_publisher + move_group + hw_bridge_node)
#                 against the ESP32 SIMULATOR (Esp32Sim, served over TCP by the check on 127.0.0.1:8771, the
#                 way the Hub bridge will serve the real board) + the browser with commands in `mode: real`;
#                 runs only hw_check (unhomed boot, refusal before homing, homing, measured motion vs.
#                 hardware.py, Robot API e-stop, MCU e-stop, lost link, frozen node -> MCU watchdog).
#                 No physics: this proves the bridge logic, NOT a real motor or the real firmware.
# jog           = mock_components + the browser with commands (mode:=mock); runs only jog_check (J2): POST /api/jog
#                 lease/TTL dead-man, incremental + hold-to-run, e-stop mid-jog, dropped connection, box
#                 ceiling, wrist singularity, taught frames (frames.yaml in a temp ROBOT_DATA_DIR)
# gazebo-jog    = same checks on Gazebo physics (gazebo.launch.py + the browser)
# Works inside the `ci` image (workspace already built) and in `dev` after `colcon build`.
# NOTE: no `set -u`: ROS setup scripts reference unset variables (e.g. COLCON_TRACE).
set -o pipefail
export PYTHONUNBUFFERED=1     # a check killed by `timeout` must not lose its buffered output

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
    gazebo-agent) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true); CTRL_TIMEOUT=180 ;;
    gazebo-integration) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py world:=workspace perception:=true perception_scene:=true)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true); CTRL_TIMEOUT=180 ;;
    gazebo-tracking) LAUNCH=(ros2 launch robot_vision_ros tracking.launch.py); CTRL_TIMEOUT=240 ;;
    jog) LAUNCH=(ros2 launch robot_moveit_config demo.launch.py use_rviz:=false)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true mode:=mock use_sim_time:=false); CTRL_TIMEOUT=60 ;;
    gazebo-jog) LAUNCH=(ros2 launch robot_moveit_config gazebo.launch.py)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true mode:=gazebo); CTRL_TIMEOUT=120 ;;
    hw) LAUNCH=(ros2 launch robot_hw_bridge hw_stack.launch.py bridge_host:=127.0.0.1 bridge_port:=8771 loop_stall_ms:=300 robot_api_url:=http://127.0.0.1:8080)
                EXTRA=(ros2 launch robot_viz web_view.launch.py commands:=true mode:=real use_sim_time:=false); CTRL_TIMEOUT=60 ;;
    *) echo "usage: run_selftest [mock|gazebo|gazebo-camera|gazebo-perception|gazebo-scene|gazebo-skills|gazebo-viz|gazebo-cmd|gazebo-stop|gazebo-jobs|gazebo-agent|gazebo-integration|gazebo-tracking|hw|jog|gazebo-jog]"; exit 64 ;;
esac
echo "[mode] $MODE"

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /workspace/install/setup.bash

# jog modes keep the taught frames in a throw-away data directory (never in the repo)
case "$MODE" in jog|gazebo-jog) export ROBOT_DATA_DIR=$(mktemp -d /tmp/jog_data.XXXXXX) ;; esac

LOG=/tmp/sim_launch.log
CHECK_LOG=/tmp/selftest_check.log      # what the check printed (the annotation reads this)
: >"$CHECK_LOG"

# On failure, also write GitHub annotations: people who cannot open the raw CI log can still read
# these (red boxes on the run page / PR checks). `::error` lines on stdout are picked up by the
# Actions runner even from inside `docker run`. Harmless anywhere else (just a few extra lines).
annotate_failure() {  # annotate_failure <headline> <exit code>
    local headline=$1 code=$2
    local tool=/usr/local/bin/annotate; [ -x "$tool" ] || tool=/workspace/docker/annotate.py
    python3 "$tool" --level error --title "selftest $MODE: $headline (çıkış kodu $code)" \
        --grep '(\[FAIL\]|\[!\]|Traceback|Error|=== )' --last 30 "$CHECK_LOG" || true
    python3 "$tool" --level error --title "selftest $MODE: kontrol çıktısının sonu" --last 25 \
        "$CHECK_LOG" || true
    python3 "$tool" --level warning --title "selftest $MODE: simülasyon (launch) günlüğünün sonu" \
        --last 20 "$LOG" || true
    if [ -n "$EXTRA_PID" ]; then
        python3 "$tool" --level warning --title "selftest $MODE: web_view günlüğünün sonu" \
            --last 12 "$EXTRA_LOG" || true
    fi
}
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
            echo "[!] launch process died while waiting for: $what" | tee -a "$CHECK_LOG"
            tail -n 60 "$LOG"; annotate_failure "launch süreci ölmüş: $what" 2; exit 2
        fi
        if (( SECONDS > deadline )); then
            echo "[!] timeout waiting for: $what" | tee -a "$CHECK_LOG"
            tail -n 60 "$LOG"; annotate_failure "zaman aşımı: $what" 2; exit 2
        fi
        sleep 1
    done
    echo "[ok] $what"
}

controllers_active() {
    [ "$(ros2 control list_controllers 2>/dev/null | grep -c 'active')" -ge 2 ]
}

camera_and_joints_ready() {
    timeout 20 ros2 topic list 2>/dev/null | grep -q '^/camera/image$' \
        && timeout 20 ros2 topic list 2>/dev/null | grep -q '^/platform/joint_states$'
}

hw_ready() {
    timeout 20 ros2 action list 2>/dev/null | grep -q '^/arm_controller/follow_joint_trajectory$'
}

if [ "$MODE" = "hw" ]; then
    # no ros2_control here: the bridge node IS the controller. The ESP32 simulator is started by hw_check.
    wait_for "move_group node" 90 bash -c 'ros2 node list 2>/dev/null | grep -q move_group'
    wait_for "hw_bridge action server" "$CTRL_TIMEOUT" hw_ready
elif [ "$MODE" = "gazebo-tracking" ]; then
    wait_for "camera + platform joint states" "$CTRL_TIMEOUT" camera_and_joints_ready
else
    wait_for "move_group node" 90 bash -c 'ros2 node list 2>/dev/null | grep -q move_group'
    wait_for "controllers active" "$CTRL_TIMEOUT" controllers_active
fi

STATUS=0
if [ "$MODE" = "gazebo-perception" ]; then
    timeout 180 ros2 run robot_perception perception_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-scene" ]; then
    timeout 400 ros2 run robot_controller perception_scene_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-skills" ]; then
    timeout 500 ros2 run robot_controller pose_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-viz" ]; then
    timeout 500 ros2 run robot_viz viz_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-cmd" ]; then
    timeout 900 ros2 run robot_viz cmd_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-stop" ]; then
    timeout 900 ros2 run robot_viz stop_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-jobs" ]; then
    timeout 900 ros2 run robot_viz jobs_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-agent" ]; then
    timeout 900 ros2 run robot_viz agent_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-integration" ]; then
    timeout 1500 ros2 run robot_viz integration_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "jog" ] || [ "$MODE" = "gazebo-jog" ]; then
    PLANT=mock; [ "$MODE" = "gazebo-jog" ] && PLANT=gazebo
    timeout 900 ros2 run robot_viz jog_check --plant "$PLANT" 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "hw" ]; then
    timeout 600 ros2 run robot_viz hw_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
elif [ "$MODE" = "gazebo-tracking" ]; then
    timeout 1500 ros2 run robot_vision_ros tracking_check --out /tmp/tracking 2>&1 | tee /tmp/tracking_check.log
    STATUS=${PIPESTATUS[0]}
    # CI logs cannot be read through the API, annotations can: publish the outcome as annotations
    annotate() {  # annotate <level> <title> <file> <lines>
        local body
        body=$(tail -n "$4" "$3" 2>/dev/null | tr -d '\r' | head -c 3000 | sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')
        echo "::$1 title=$2::$body"
    }
    annotate "$([ $STATUS -eq 0 ] && echo notice || echo error)" "tracking_check exit=$STATUS" /tmp/tracking_check.log 40
    if [ $STATUS -ne 0 ]; then
        annotate error "tracking launch log (tail)" "$LOG" 40
        annotate error "gz / bridge processes and topics" /dev/null 1
        { ps -eo pid,args | grep -E "gz sim|parameter_bridge|tracking_node" | grep -v grep | cut -c1-160; \
          timeout 20 ros2 topic list 2>&1 | head -30; } > /tmp/diag.txt 2>&1
        annotate error "diag: processes + topics" /tmp/diag.txt 40
    fi
else
    timeout 180 ros2 run robot_controller selftest 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
fi
if [ "$MODE" = "gazebo-camera" ]; then
    timeout 120 ros2 run robot_controller sensor_check 2>&1 | tee -a "$CHECK_LOG" || STATUS=$?
fi
if [ $STATUS -ne 0 ]; then
    echo "----- last launch log lines -----"; tail -n 80 "$LOG"
    if [ -n "$EXTRA_PID" ]; then echo "----- last web_view log lines -----"; tail -n 40 "$EXTRA_LOG"; fi
    annotate_failure "başarısız" "$STATUS"
fi
exit $STATUS
