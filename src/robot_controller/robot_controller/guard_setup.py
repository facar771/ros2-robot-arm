"""Builds the F4b path guard the same way for every process that executes motions."""
import os
from pathlib import Path
from typing import Callable, Optional

from ament_index_python.packages import get_package_share_directory
from robot_core.pathguard import PathGuard
from robot_core.profile import load_profile
from robot_core.profile_gen import PROFILE_PATH
from robot_core.safezone import PARK_FILE, ParkConfig, effective_velocity_scale, load_park


def load_park_config() -> ParkConfig:
    """The hand-reviewed park file (robot_moveit_config/config/safe_zone.park.yaml); the path can
    be overridden with ROBOT_PARK. Raises on a missing or malformed file."""
    path = os.environ.get('ROBOT_PARK') or (
        Path(get_package_share_directory('robot_moveit_config')) / 'config' / PARK_FILE)
    return load_park(path)


def build_path_guard(planner, state, config, safety, events=None,
                     log_error: Optional[Callable[[str], None]] = None) -> PathGuard:
    """``planner``: MoveItMotion (PlanningPort), ``state``: the joint-state cache, ``config``:
    RobotConfig, ``safety``: SafetyConfig. A missing/broken park file does NOT stop the box
    check: it only switches the park move (and with it "Eve dön") off, which then says why.
    A profile that does not match the robot's joints is an error (no unguarded fallback)."""
    profile = load_profile(os.environ.get('ROBOT_PROFILE') or PROFILE_PATH)
    if tuple(profile.joint_names) != tuple(config.joint_names):
        raise ValueError(f'profil eklemleri {profile.joint_names} robotun eklemlerinden '
                         f'{config.joint_names} farklı; yol denetimi kurulamaz')
    park = None
    try:
        park = load_park_config()
    except Exception as exc:    # noqa: BLE001 - park off, box check stays on
        if log_error is not None:
            log_error(f'park dosyası yüklenemedi, park geçişi ve "Eve dön" kapalı: {exc}')
    # the park transit obeys the SAME global speed cap as every other motion (read per move)
    return PathGuard(planner, state, config.joint_names, profile, safety.zone, park,
                     poses=config.poses, events=events,
                     velocity_scale=lambda: effective_velocity_scale(
                         planner.velocity_scale, safety.max_velocity_scale))
