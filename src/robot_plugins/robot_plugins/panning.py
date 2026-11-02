"""Camera panning for plugins: the abstract ``robot_vision.scan.Panner`` plus ROS-free sources."""
from __future__ import annotations

from typing import Optional, Tuple

from robot_vision.frame import Frame
from robot_vision.scan import Panner, SyntheticPanner, ring_world


class NoPanner(Panner):
    """A camera that cannot turn (and has no frames)."""

    @property
    def limits(self) -> Tuple[float, float]:
        return (0.0, 0.0)

    def move_to(self, yaw: float) -> None:
        raise RuntimeError('bu ortamda kamera döndürülemez')

    def frame(self) -> Optional[Frame]:
        return None


def make_panner(spec: str) -> Panner:
    """``none`` | ``synthetic`` (the six-object ring world of ``robot_vision.scan``)."""
    if spec in ('', 'none'):
        return NoPanner()
    if spec == 'synthetic':
        camera, shapes = ring_world()
        return SyntheticPanner(camera, shapes)
    raise ValueError(f'bilinmeyen döndürücü {spec!r}; none | synthetic')
