#!/usr/bin/env bash
# Runs the LLM tool layer (robot_agent) inside the running demo container.
#   Terminal 1:  docker compose up demo
#   Terminal 2:  ./docker/agent.sh --offline --dry-run "yeşil nesnenin üstüne git"
#                ./docker/agent.sh --offline --yes "yeşil nesnenin üstüne git"
#                ANTHROPIC_API_KEY=... ./docker/agent.sh "kırmızı kutunun üstüne git"   (real LLM)
#                OPENAI_API_KEY=... ROBOT_AGENT_OPENAI_MODEL=<model> \
#                  ROBOT_AGENT_OPENAI_BASE_URL=<https://.../v1> \
#                  ./docker/agent.sh --provider openai "kırmızı kutunun üstüne git"
# Arguments are passed to `robot_agent` as they are (see: ./docker/agent.sh --help).
set -e
cd "$(dirname "$0")/.."
if ! docker compose ps --status running --services 2>/dev/null | grep -qx demo; then
    echo "[-] 'demo' servisi çalışmıyor. Başka bir terminalde önce: docker compose up demo"
    exit 1
fi
# The key (if set on the host) is handed over by name only, never written to a file or image.
exec docker compose exec -e ANTHROPIC_API_KEY -e OPENAI_API_KEY -e ROBOT_AGENT_OPENAI_MODEL \
    -e ROBOT_AGENT_OPENAI_BASE_URL demo bash -c \
    '. /opt/ros/${ROS_DISTRO}/setup.bash && . /workspace/install/setup.bash && exec ros2 run robot_agent robot_agent "$@"' \
    _ "$@"
