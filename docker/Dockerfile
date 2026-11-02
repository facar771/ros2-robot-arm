# syntax=docker/dockerfile:1
# One image definition for CI and local development.
ARG ROS_DISTRO=jazzy
# The base image is pinned by DIGEST (the multi-arch manifest list published for the tag below), so a
# rebuild next month starts from the very same bytes. To move to a newer base on purpose:
#   docker buildx imagetools inspect ros:jazzy-ros-base   (copy the "Digest:" line)
# and change the tag + digest below together (docs/ARIZA_GIDERME.md, "Taban imajı güncelleme").
# Another distro: --build-arg ROS_DISTRO=humble --build-arg ROS_BASE_IMAGE=ros:humble-ros-base@sha256:...
# NOT pinned: the apt packages installed below (ROS/MoveIt/Gazebo move with the apt archive); the
# nightly workflow (.github/workflows/nightly.yml) exists to notice when that drifts.
ARG ROS_BASE_IMAGE=ros:jazzy-ros-base@sha256:8eed0a4669bbf43ee331878a4c39bd8ebddea8e3312a58b4fae8edb14526b617

# ---- base: ROS 2 + MoveIt + ros2_control + Gazebo (server), no GUI (used by CI) ------------------------------
FROM ${ROS_BASE_IMAGE} AS base
ARG ROS_DISTRO
ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=${ROS_DISTRO} \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-moveit-ros-move-group \
        ros-${ROS_DISTRO}-moveit-planners \
        ros-${ROS_DISTRO}-moveit-simple-controller-manager \
        ros-${ROS_DISTRO}-moveit-kinematics \
        ros-${ROS_DISTRO}-moveit-configs-utils \
        ros-${ROS_DISTRO}-moveit-ros-planning-interface \
        ros-${ROS_DISTRO}-ros2-control \
        ros-${ROS_DISTRO}-ros2-controllers \
        ros-${ROS_DISTRO}-ros-gz-sim \
        ros-${ROS_DISTRO}-ros-gz-bridge \
        ros-${ROS_DISTRO}-gz-ros2-control \
        ros-${ROS_DISTRO}-tf2-ros \
        ros-${ROS_DISTRO}-robot-state-publisher \
        ros-${ROS_DISTRO}-xacro \
        ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
        python3-pytest python3-yaml python3-numpy python3-pip \
        libgl1-mesa-dri libegl1 libegl-mesa0 libglx-mesa0 libgles2 libopengl0 \
    && rm -rf /var/lib/apt/lists/*
# Data layer (robot_data). pip, not apt: Ubuntu's python3-sqlalchemy is 1.4, we need 2.x.
# Exact versions + hashes (requirements/docker.in -> docker.txt, see docs/ARIZA_GIDERME.md).
COPY requirements/docker.txt /tmp/requirements-docker.txt
RUN pip3 install --no-cache-dir --break-system-packages --require-hashes \
        -r /tmp/requirements-docker.txt
WORKDIR /workspace

# ---- ci: build the workspace and bake in the selftest runner -----------------------------------
FROM base AS ci
# No GPU in CI: render the simulated camera in software (Mesa llvmpipe).
ENV LIBGL_ALWAYS_SOFTWARE=1
COPY src /workspace/src
COPY docker/run_selftest.sh /usr/local/bin/run_selftest
COPY docker/run_demo.sh /usr/local/bin/run_demo
COPY docker/annotate.py /usr/local/bin/annotate
RUN chmod +x /usr/local/bin/run_selftest /usr/local/bin/run_demo /usr/local/bin/annotate \
    && . /opt/ros/${ROS_DISTRO}/setup.sh \
    && colcon build --symlink-install --event-handlers console_direct+ \
    && echo ". /workspace/install/setup.bash" >> /root/.bashrc
CMD ["run_selftest"]

# ---- dev: adds RViz and GUI tools; mount the repo at /workspace (see compose.yaml) ---------------
FROM base AS dev
ARG ROS_DISTRO
RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-${ROS_DISTRO}-moveit-ros-visualization \
        ros-${ROS_DISTRO}-moveit-setup-assistant \
        ros-${ROS_DISTRO}-rviz2 \
        ros-${ROS_DISTRO}-joint-state-publisher-gui \
        git nano \
    && rm -rf /var/lib/apt/lists/*
RUN echo ". /opt/ros/${ROS_DISTRO}/setup.bash" >> /root/.bashrc \
    && echo "[ -f /workspace/install/setup.bash ] && . /workspace/install/setup.bash" >> /root/.bashrc
CMD ["bash"]
