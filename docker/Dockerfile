# syntax=docker/dockerfile:1
# One image definition for CI and local development. Switch distro with --build-arg ROS_DISTRO=humble.
ARG ROS_DISTRO=jazzy

# ---- base: ROS 2 + MoveIt + ros2_control + Gazebo (server), no GUI (used by CI) ------------------------------
FROM ros:${ROS_DISTRO}-ros-base AS base
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
        ros-${ROS_DISTRO}-robot-state-publisher \
        ros-${ROS_DISTRO}-xacro \
        ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
        python3-pytest python3-yaml \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

# ---- ci: build the workspace and bake in the selftest runner -----------------------------------
FROM base AS ci
COPY src /workspace/src
COPY docker/run_selftest.sh /usr/local/bin/run_selftest
RUN chmod +x /usr/local/bin/run_selftest \
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
        python3-pip git nano \
    && rm -rf /var/lib/apt/lists/*
RUN echo ". /opt/ros/${ROS_DISTRO}/setup.bash" >> /root/.bashrc \
    && echo "[ -f /workspace/install/setup.bash ] && . /workspace/install/setup.bash" >> /root/.bashrc
CMD ["bash"]
