"""Sphere ("dome camera") frames, persisted next to ``frames.yaml`` (L8, ROS-free).

``FrameStore`` (frames.py, Robot_2) only knows three-point user frames and refuses any other key in
``frames.yaml``; it is not touched here. Sphere frames therefore live in their OWN file,
``spheres.yaml`` (same data directory, same rules: atomic write, strict schema, version, validated
on load). ``frames.yaml`` stays byte-for-byte what an old robot/Hub expects.

File: ``{spheres_version: 1, spheres: [{type: sphere, name, center: [x,y,z], radius, tool_axis}]}``.
An entry with a different ``type`` is a clear error (never silently skipped: a skipped entry would
be deleted by the next write).
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Union

import yaml

from robot_core.frames import FrameError, SphereFrame, sphere_from_dict

SPHERES_VERSION = 1
TYPES = ('user', 'sphere')           # what /api/frames supports (GET ... "types")
MAX_SPHERES = 10


class SphereStore:
    """Sphere frames in a YAML file; every change is written atomically (tmp + rename)."""

    def __init__(self, path: Union[str, Path], max_spheres: int = MAX_SPHERES) -> None:
        self.path = Path(path)
        self.max_spheres = max_spheres
        self._spheres: Dict[str, SphereFrame] = {}
        self._lock = threading.RLock()
        self.load()

    def names(self) -> List[str]:
        with self._lock:
            return list(self._spheres)

    def get(self, name: str) -> SphereFrame:
        with self._lock:
            if name not in self._spheres:
                raise FrameError(f'küre çerçevesi yok: {name!r}')
            return self._spheres[name]

    def all(self) -> List[SphereFrame]:
        with self._lock:
            return list(self._spheres.values())

    def __len__(self) -> int:
        return len(self._spheres)

    def load(self) -> 'SphereStore':
        with self._lock:
            if not self.path.exists():
                self._spheres = {}
                return self
            try:
                data = yaml.safe_load(self.path.read_text(encoding='utf-8'))
            except (OSError, yaml.YAMLError) as exc:
                raise FrameError(f'{self.path}: okunamadı/geçerli YAML değil ({exc})') from exc
            try:
                self._spheres = self._parse(data)
            except FrameError as exc:
                raise FrameError(f'{self.path}: {exc}') from exc
            return self

    def _parse(self, data: object) -> Dict[str, SphereFrame]:
        if not isinstance(data, dict) or set(data) != {'spheres_version', 'spheres'}:
            raise FrameError('dosya {spheres_version, spheres} olmalı')
        if data['spheres_version'] != SPHERES_VERSION:
            raise FrameError(f"spheres_version {SPHERES_VERSION} olmalı, bulunan: "
                             f"{data['spheres_version']!r}")
        entries = data['spheres']
        if not isinstance(entries, list):
            raise FrameError('spheres: liste olmalı')
        if len(entries) > self.max_spheres:
            raise FrameError(f'en çok {self.max_spheres} küre çerçevesi; dosyada {len(entries)}')
        out: Dict[str, SphereFrame] = {}
        for i, entry in enumerate(entries):
            kind = entry.get('type') if isinstance(entry, dict) else None
            if kind != 'sphere':
                raise FrameError(f'spheres[{i}]: bilinmeyen çerçeve türü {kind!r} '
                                 f'(desteklenen: {", ".join(TYPES)}); atlanmaz, dosya yüklenmez')
            try:
                sphere = sphere_from_dict(entry)
            except FrameError as exc:
                raise FrameError(f'spheres[{i}]: {exc}') from exc
            if sphere.name.lower() in {n.lower() for n in out}:
                raise FrameError(f'spheres[{i}]: ad tekrar ediyor: {sphere.name!r}')
            out[sphere.name] = sphere
        return out

    def replace_all(self, spheres: Iterable[SphereFrame]) -> List[SphereFrame]:
        """Replace the WHOLE list (validated first; file and store untouched on error)."""
        new: Dict[str, SphereFrame] = {}
        with self._lock:
            for i, sphere in enumerate(spheres):
                if sphere.name.lower() in {n.lower() for n in new}:
                    raise FrameError(f'spheres[{i}]: ad tekrar ediyor: {sphere.name!r}')
                new[sphere.name] = sphere
            if len(new) > self.max_spheres:
                raise FrameError(f'en çok {self.max_spheres} küre çerçevesi')
            self._save(new)
            self._spheres = new
            return list(new.values())

    def _save(self, spheres: Dict[str, SphereFrame]) -> None:
        doc = {'spheres_version': SPHERES_VERSION,
               'spheres': [s.as_dict() for s in spheres.values()]}
        text = ('# Sphere frames (robot_core.spheres): centre C and radius r in the base frame '
                '(metres). Do not edit by hand.\n' + yaml.safe_dump(doc, sort_keys=False))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
