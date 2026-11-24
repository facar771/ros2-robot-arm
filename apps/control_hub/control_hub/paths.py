"""Kaynak ve kullanıcı yolları: geliştirmede, ``pip`` kurulumunda ve PyInstaller ``--onefile`` .exe'de aynı.

* PyInstaller onefile açılışta her şeyi geçici bir klasöre (``sys._MEIPASS``) açar; ``web/`` ve
  ``config/`` oraya gömülüdür (``hub.spec`` ``datas``).
* Kullanıcının düzenleyebileceği yapılandırma (``scenarios.yaml``, ``ui.config.json``) ve ayarlar
  ``%APPDATA%\\ControlHub`` altındadır (Windows dışında ``~/.config/control_hub``); .exe'nin içindeki
  varsayılanlar salt okunurdur.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

CONFIG_FILES = ('scenarios.yaml', 'ui.config.json')
APP_DIR_NAME = 'ControlHub'


def frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def bundle_root() -> Path:
    """Paketin kök klasörü: .exe'de ``_MEIPASS``, aksi halde ``apps/control_hub``."""
    if frozen():
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return bundle_root() / 'control_hub' / 'web'


def bundled_config_dir() -> Path:
    return bundle_root() / 'config'


def user_dir(env: Optional[dict] = None, platform: Optional[str] = None) -> Path:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform == 'win32':
        base = env.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
        return Path(base) / APP_DIR_NAME
    base = env.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'control_hub'


def user_config_dir() -> Path:
    return user_dir() / 'config'


def has_config(directory: Path) -> bool:
    return all((directory / name).is_file() for name in CONFIG_FILES)


def resolve_config_dir(explicit: Optional[os.PathLike] = None) -> Path:
    """Öncelik: açıkça verilen → kullanıcı klasörü (iki dosya da varsa) → gömülü varsayılan."""
    if explicit:
        return Path(explicit)
    user = user_config_dir()
    return user if has_config(user) else bundled_config_dir()


def export_default_config(dest: Optional[Path] = None, overwrite: bool = False) -> Path:
    """Gömülü varsayılan yapılandırmayı kullanıcı klasörüne kopyalar (varolanı ezmez)."""
    dest = Path(dest) if dest else user_config_dir()
    dest.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES:
        target = dest / name
        if overwrite or not target.exists():
            shutil.copyfile(bundled_config_dir() / name, target)
    return dest


# --- kullanıcı VERİSİ (parçalar, programlar, profil taslakları): HUB_DATA_DIR (UI_CONTRACT §9-D) ---------
DATA_ENV = 'HUB_DATA_DIR'
DATA_SUBDIRS = ('parts', 'programs', 'profiles')        # başka ad yok: sayfa kendi alt klasörünü uyduramaz


def data_dir(env: Optional[dict] = None, platform: Optional[str] = None) -> Path:
    """Kullanıcı verisinin kökü. ``HUB_DATA_DIR`` (mutlak yol) ya da ``<kullanıcı klasörü>/data``.

    Depo dosyalarına ASLA yazılmaz; ``config/`` ayardır, ``data/`` kullanıcının ürettiği veridir."""
    env = os.environ if env is None else env
    given = (env.get(DATA_ENV) or '').strip()
    if given:
        p = Path(given)
        if not p.is_absolute() or '..' in p.parts:
            raise ValueError(f'{DATA_ENV} mutlak yol olmalı ve ".." içermemeli: {given!r}')
        return p
    return user_dir(env, platform) / 'data'


def data_subdir(name: str, env: Optional[dict] = None, create: bool = True) -> Path:
    """``parts`` / ``programs`` / ``profiles`` alt klasörü (yoksa oluşturur)."""
    if name not in DATA_SUBDIRS:
        raise ValueError(f'bilinmeyen veri klasörü: {name!r} (izinli: {", ".join(DATA_SUBDIRS)})')
    p = data_dir(env) / name
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_robot_core_path() -> None:
    """Depo çıkışından çalışırken (``python -m control_hub``) ``src/robot_core``'u yola ekler; kurulu paket,
    PYTHONPATH ya da donmuş .exe (gömülü) varsa dokunmaz. SİM arka ucu ``robot_core``'u bununla bulur."""
    if frozen():
        return
    path = Path(__file__).resolve().parents[3] / 'src' / 'robot_core'
    if path.is_dir() and str(path) not in sys.path:
        sys.path.append(str(path))
