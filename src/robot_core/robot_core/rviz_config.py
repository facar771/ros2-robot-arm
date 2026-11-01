"""RViz layout files (``*.rviz``): finding one by name and reading what it depends on.

Pure Python (PyYAML), no ROS: the launch file uses :func:`resolve_rviz_config`, and a unit test uses
:func:`read_rviz_config` to check that every topic and frame a layout names is really published by
this project, so a layout cannot silently go stale.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import yaml

PathLike = Union[str, Path]
IMAGE, MARKER_ARRAY, TF, MOTION_PLANNING = (
    'rviz_default_plugins/Image', 'rviz_default_plugins/MarkerArray', 'rviz_default_plugins/TF',
    'moveit_rviz_plugin/MotionPlanning')


@dataclass(frozen=True)
class RvizConfig:
    fixed_frame: str
    topics: Dict[str, str]                 # display name -> topic (Image, MarkerArray, ...)
    marker_namespaces: Dict[str, Tuple[str, ...]]   # MarkerArray display name -> namespaces
    planning_scene_topic: Optional[str]    # MotionPlanning display
    tf_frames: Tuple[str, ...]             # frames the TF display lists explicitly
    classes: Tuple[str, ...]               # display classes, in order


def available_rviz_configs(config_dir: PathLike) -> List[str]:
    return sorted(p.name for p in Path(config_dir).glob('*.rviz'))


def resolve_rviz_config(value: str, config_dir: PathLike) -> Path:
    """``value`` is an absolute path, or a file name inside ``config_dir`` (the ``.rviz`` suffix is
    optional: ``robot_demo`` = ``robot_demo.rviz``). Raises FileNotFoundError listing what
    exists."""
    text = str(value).strip()
    if not text:
        raise FileNotFoundError('rviz_config boş')
    path = Path(text)
    if not path.is_absolute():
        path = Path(config_dir) / (text if path.suffix == '.rviz' else text + '.rviz')
    if not path.is_file():
        raise FileNotFoundError(
            f"RViz yapılandırması bulunamadı: {path} (rviz_config:='{text}'). "
            f"{config_dir} içindekiler: {', '.join(available_rviz_configs(config_dir)) or 'yok'}")
    return path


def read_rviz_config(path: PathLike) -> RvizConfig:
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    manager = data['Visualization Manager']
    topics: Dict[str, str] = {}
    namespaces: Dict[str, Tuple[str, ...]] = {}
    scene_topic: Optional[str] = None
    frames: Tuple[str, ...] = ()
    classes = []
    for display in manager.get('Displays', []):
        cls = display.get('Class', '')
        classes.append(cls)
        if cls in (IMAGE, MARKER_ARRAY):
            topics[display['Name']] = display['Topic']['Value']
            if cls == MARKER_ARRAY:
                namespaces[display['Name']] = tuple(display.get('Namespaces', {}))
        elif cls == MOTION_PLANNING:
            scene_topic = display.get('Planning Scene Topic')
        elif cls == TF:
            frames = tuple(k for k, v in display.get('Frames', {}).items()
                           if isinstance(v, dict) and v.get('Value'))
    return RvizConfig(fixed_frame=manager['Global Options']['Fixed Frame'], topics=topics,
                      marker_namespaces=namespaces, planning_scene_topic=scene_topic,
                      tf_frames=frames, classes=tuple(classes))
