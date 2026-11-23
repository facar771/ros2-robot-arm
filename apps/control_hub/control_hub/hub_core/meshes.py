"""Robot mesh'leri (yalnız GÖRSEL): profilin ``meshes/<robot>/link/<link>.stl`` dosyalarını bulur ve doğrular.

* Kaynak: ``robot_core.mesh_import`` çıktısı (metre, link çerçevesinde, ikili STL). Hub bu dosyalara yalnız
  OKUR; ``robot_core``/Robot API değişmez. Mesh ölçüm değildir: kalite, güvenli kutu ve eklem limitleri
  mesh'ten gelmez.
* Aranan yerler (ilk bulunan): ``HUB_MESH_DIR`` ortam değişkeni, ``.exe`` içinde ``_MEIPASS/robot_description/
  meshes``, depo kaynağı ``src/robot_description/meshes`` (sığ kurulumda, ör. Docker ``/app``, depo yoktur:
  mesh de yoktur ve görünüm primitiflerle çizilir; Hub çökmez).
* Ad doğrulaması: robot ve link adı yalnız ``[A-Za-z][A-Za-z0-9_]*``; yol ayırıcı/nokta kabul edilmez.
"""
from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional

MESH_DIR_ENV = 'HUB_MESH_DIR'
NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
MAX_BYTES = 16 * 1024 * 1024
HEADER = 84                     # 80 B başlık + uint32 üçgen sayısı
TRI = 50                        # normal(12) + 3 köşe(36) + öznitelik(2)


class MeshError(Exception):
    """Mesh sunulamaz; ``str(exc)`` güvenle gösterilebilir (dosya yolu içermez)."""


def mesh_root_candidates(here: Path, frozen: bool, env: Optional[Mapping[str, str]] = None) -> List[Path]:
    """Mesh kökü adayları. ``here``: bu dosyanın yolu. Sığ kurulumda (``len(parents) <= 4``) depo yok."""
    env = os.environ if env is None else env
    out: List[Path] = []
    if env.get(MESH_DIR_ENV):
        out.append(Path(env[MESH_DIR_ENV]))
    meipass = getattr(sys, '_MEIPASS', None)
    if frozen and meipass:
        out.append(Path(meipass) / 'robot_description' / 'meshes')
    parents = here.resolve().parents
    if len(parents) > 4:        # <depo>/apps/control_hub/control_hub/hub_core/meshes.py
        out.append(parents[4] / 'src' / 'robot_description' / 'meshes')
    return out


def find_mesh_root(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    for c in mesh_root_candidates(Path(__file__), bool(getattr(sys, 'frozen', False)), env):
        if c.is_dir():
            return c
    return None


def check_binary_stl(data: bytes) -> int:
    """İkili STL'nin boyut tutarlılığını denetler; üçgen sayısını döndürür. Aksi halde ``MeshError``."""
    if len(data) < HEADER:
        raise MeshError('STL çok kısa')
    n = struct.unpack_from('<I', data, 80)[0]
    if n == 0 or HEADER + n * TRI != len(data):
        raise MeshError('STL ikili biçimde değil ya da boyutu başlıkla uyuşmuyor')
    return n


class MeshCatalog:
    def __init__(self, root: Optional[Path]):
        self.root = Path(root) if root is not None else None

    @classmethod
    def discover(cls, env: Optional[Mapping[str, str]] = None) -> 'MeshCatalog':
        return cls(find_mesh_root(env))

    def _path(self, robot: str, link: str) -> Path:
        if self.root is None:
            raise MeshError('mesh dizini bulunamadı')
        if not NAME_RE.match(robot or '') or not NAME_RE.match(link or ''):
            raise MeshError('geçersiz robot/link adı')
        return self.root / robot / 'link' / f'{link}.stl'

    def read(self, robot: str, link: str) -> bytes:
        p = self._path(robot, link)
        try:
            if p.stat().st_size > MAX_BYTES:
                raise MeshError('mesh çok büyük')
            data = p.read_bytes()
        except FileNotFoundError:
            raise MeshError('mesh yok') from None
        except OSError as exc:
            why = os.strerror(exc.errno) if exc.errno else type(exc).__name__
            raise MeshError(f'mesh okunamadı: {why}') from None
        check_binary_stl(data)
        return data

    def available(self, robot: str, links: List[str]) -> Dict[str, int]:
        """Modeldeki link adlarından geçerli mesh'i olanlar -> bayt sayısı."""
        out: Dict[str, int] = {}
        for link in links:
            try:
                out[link] = len(self.read(robot, link))
            except MeshError:
                continue
        return out
