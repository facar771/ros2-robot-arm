"""Import a plugin's entry class from its own directory (and nowhere else)."""
from __future__ import annotations

import importlib.util
import sys
from typing import Type

from robot_plugins.context import PluginBase
from robot_plugins.manifest import Manifest, ManifestError


def load_plugin_class(manifest: Manifest) -> Type[PluginBase]:
    module_name, class_name = manifest.entry.split(':')
    directory = manifest.directory.resolve()
    path = (directory / f'{module_name}.py').resolve()
    if directory not in path.parents or not path.is_file():
        raise ManifestError(f'entry: {module_name}.py plugin dizininde bulunamadı')
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))               # lets a plugin import its own helpers
    spec = importlib.util.spec_from_file_location(
        f'robot_plugin_{manifest.name}_{module_name}', path)
    if spec is None or spec.loader is None:
        raise ManifestError(f'entry: {path} yüklenemedi')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, class_name, None)
    if not isinstance(cls, type) or not issubclass(cls, PluginBase):
        raise ManifestError(f'entry: {class_name} robot_plugins.PluginBase alt sınıfı olmalı')
    return cls
