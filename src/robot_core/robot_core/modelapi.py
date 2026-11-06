"""``GET /api/model`` (L9b): the read-only view model (``view_model.json``, from L9a).

ROS-free. The file is read ONCE at start. A missing/corrupt file never stops the Robot API: the
door answers 503 with the reason and nothing is guessed or repaired. The served document is the
file minus its ``_``-prefixed notes (``_generated`` names a repository path); error texts never
contain a file path. Nothing here can command the robot: it is not a command door, so the
e-stop and 423 rules do not touch it (a latched e-stop must not hide the model).
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

SUPPORTED_VERSION = 1
REQUIRED = ('view_model_version', 'robot', 'units', 'root', 'joints', 'links', 'tcp', 'safe_box')


class ModelUnavailable(Exception):
    """The model cannot be served; ``str(exc)`` is the reason (no path, safe to show)."""


def _check(doc: Any, joint_names: Optional[Sequence[str]]) -> None:
    if not isinstance(doc, dict):
        raise ModelUnavailable('model dosyası bir JSON nesnesi değil')
    missing = [k for k in REQUIRED if k not in doc]
    if missing:
        raise ModelUnavailable(f'model dosyasında eksik alan: {", ".join(missing)}')
    version = doc['view_model_version']
    if isinstance(version, bool) or version != SUPPORTED_VERSION:
        raise ModelUnavailable(f'desteklenmeyen view_model_version: {version!r} '
                               f'(bu sürüm {SUPPORTED_VERSION} ister)')
    joints = doc['joints']
    if not isinstance(joints, list) or not joints or not all(
            isinstance(j, dict) and isinstance(j.get('name'), str) for j in joints):
        raise ModelUnavailable('model dosyasında joints[] geçersiz')
    if not isinstance(doc['links'], list):
        raise ModelUnavailable('model dosyasında links[] geçersiz')
    if joint_names is not None:
        names = [j['name'] for j in joints]
        if names != list(joint_names):
            raise ModelUnavailable(
                f'model eklem adları robotun eklem adlarıyla eşleşmiyor: model {names}, '
                f'robot {list(joint_names)} (üretilmiş dosya profille uyumsuz; yeniden üret)')


def load_model(path: Path, joint_names: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Read and validate; returns the public document. Raises :class:`ModelUnavailable`."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        raise ModelUnavailable('model dosyası bulunamadı') from None
    except (OSError, UnicodeDecodeError) as exc:       # strerror only: OSError.__str__ has the path
        reason = os.strerror(exc.errno) if getattr(exc, 'errno', None) else type(exc).__name__
        raise ModelUnavailable(f'model dosyası okunamadı: {reason}') from None
    try:
        doc = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise ModelUnavailable(f'model dosyası geçerli JSON değil: {exc}') from None
    _check(doc, joint_names)
    return {k: v for k, v in doc.items() if not k.startswith('_')}


def _reject_constant(name: str):
    raise ValueError(f'{name} sabiti JSON değil')


class ModelApi:
    """What the HTTP door calls. ``error`` set: the door answers 503 with it."""

    def __init__(self, document: Optional[Dict[str, Any]] = None, error: Optional[str] = None):
        self._body = None if document is None else json.dumps(document).encode()
        self.error = error if document is None else None
        if document is None and not error:
            self.error = 'model yüklenmedi'

    @classmethod
    def from_file(cls, path: Path, joint_names: Optional[Sequence[str]] = None) -> 'ModelApi':
        try:
            return cls(load_model(path, joint_names))
        except ModelUnavailable as exc:
            return cls(error=str(exc))

    def body(self) -> bytes:
        if self._body is None:
            raise ModelUnavailable(self.error or 'model yüklenmedi')
        return self._body
