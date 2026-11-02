"""Frame -> JSON-able object descriptions; the shared body of the first plugins (find_object,
follow_color, scan_extent). Pure functions on ``robot_vision`` types."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from robot_core.perception import ColorClass
from robot_vision.detectors import ColorDetector
from robot_vision.frame import Frame
from robot_vision.geometry import GeometryConfig, ObjectMeasure, measure

COLORS = ('red', 'green', 'blue')


def _r(values, nd: int = 4):
    return [round(float(v), nd) for v in values]


def describe_measure(m: ObjectMeasure) -> Dict[str, Any]:
    """The measurements an LLM or a grasp planner needs, rounded to 0.1 mm (world frame, metres)."""
    return {
        'shape': m.shape, 'center': _r(m.center), 'size': _r(m.size), 'height': round(m.height, 4),
        'radius': None if m.radius is None else round(m.radius, 4),
        'top_center': _r(m.top_center),
        'oriented_box': {'center': _r(m.obb_center), 'size': _r(m.obb_size),
                         'yaw_rad': round(m.obb_yaw, 4)},
        'nearest_visible_point': {'point': _r(m.nearest), 'distance_to_camera': round(
            m.nearest_distance, 4)},
        'edge_points': {k: _r(v) for k, v in m.edge_points.items()},
        'grasps': [{'kind': g.kind, 'position': _r(g.position), 'approach': _r(g.approach),
                    'yaw_rad': round(g.yaw, 4), 'width': round(g.width, 4), 'fits': g.fits}
                   for g in m.grasps],
    }


def find_objects(frame: Frame, colors: Optional[Sequence[str]] = None,
                 max_aperture: float = 0.08) -> List[Dict[str, Any]]:
    """Every coloured object in ``frame`` (largest first) with its 3-D measurements."""
    wanted = [c for c in (colors or COLORS)]
    palette = [c for c in _palette() if c.name in wanted]
    detector = ColorDetector(palette)
    cfg = GeometryConfig(max_aperture=max_aperture)
    out: List[Dict[str, Any]] = []
    for seg in sorted(detector.detect(frame), key=lambda s: -s.pixels):
        m = measure(frame, seg, cfg)
        if m is None:
            continue
        u, v = seg.centroid
        out.append({'color': seg.label, 'pixels': seg.pixels,
                    'pixel_center': [round(u, 1), round(v, 1)], **describe_measure(m)})
    return out


def _palette() -> Sequence[ColorClass]:
    from robot_core.perception import DEFAULT_COLORS
    return DEFAULT_COLORS
