#!/usr/bin/env bash
# Opens the interactive command line (cli_moveit) inside the running demo container.
#   Terminal 1:  docker compose up demo
#   Terminal 2:  ./docker/cli.sh
set -e
cd "$(dirname "$0")/.."
if ! docker compose ps --status running --services 2>/dev/null | grep -qx demo; then
    echo "[-] 'demo' servisi çalışmıyor. Başka bir terminalde önce: docker compose up demo"
    exit 1
fi
exec docker compose exec demo bash -c \
    '. /opt/ros/${ROS_DISTRO}/setup.bash && . /workspace/install/setup.bash && exec ros2 run robot_controller cli_moveit'
