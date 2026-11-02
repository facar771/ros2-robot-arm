"""Image-processing core: detectors, depth geometry, tracker (pure numpy, no ROS)."""
from robot_vision.detectors import ColorDetector, Detector
from robot_vision.frame import Frame, Segment
from robot_vision.geometry import GeometryConfig, Grasp, ObjectMeasure, measure, segment_points
from robot_vision.tracker import Observation, Track, Tracker, TrackerConfig

__all__ = [
    'ColorDetector', 'Detector', 'Frame', 'Segment', 'GeometryConfig', 'Grasp', 'ObjectMeasure',
    'measure', 'segment_points', 'Observation', 'Track', 'Tracker', 'TrackerConfig',
]
