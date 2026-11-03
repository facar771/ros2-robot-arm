import sys
from pathlib import Path

from control_hub import paths


def test_dev_layout_has_bundled_resources():
    assert (paths.web_dir() / 'index.html').is_file()
    assert paths.has_config(paths.bundled_config_dir())


def test_frozen_layout_uses_meipass(monkeypatch, tmp_path):
    (tmp_path / 'control_hub' / 'web').mkdir(parents=True)
    (tmp_path / 'config').mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    assert paths.frozen() and paths.bundle_root() == tmp_path
    assert paths.web_dir() == tmp_path / 'control_hub' / 'web'
    assert paths.bundled_config_dir() == tmp_path / 'config'


def test_user_dir_per_platform(tmp_path):
    assert paths.user_dir({'APPDATA': r'C:\Users\f\AppData\Roaming'}, 'win32') == \
        Path(r'C:\Users\f\AppData\Roaming') / 'ControlHub'
    assert paths.user_dir({'XDG_CONFIG_HOME': str(tmp_path)}, 'linux') == tmp_path / 'control_hub'


def test_config_resolution_order(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, 'user_dir', lambda *a, **k: tmp_path / 'u')
    assert paths.resolve_config_dir() == paths.bundled_config_dir()       # kullanıcı klasörü yok
    assert paths.resolve_config_dir(tmp_path / 'x') == tmp_path / 'x'     # açık yol kazanır
    dest = paths.export_default_config()
    assert dest == tmp_path / 'u' / 'config' and paths.has_config(dest)
    assert paths.resolve_config_dir() == dest                             # artık kullanıcınınki
    (dest / 'scenarios.yaml').write_text('# benim\nscenarios: []\n', encoding='utf-8')
    paths.export_default_config()                                         # varolanı ezmez
    assert (dest / 'scenarios.yaml').read_text(encoding='utf-8').startswith('# benim')
    paths.export_default_config(overwrite=True)
    assert 'scenarios:' in (dest / 'scenarios.yaml').read_text(encoding='utf-8')
    (dest / 'ui.config.json').unlink()                                    # yarım klasör: varsayılana dön
    assert paths.resolve_config_dir() == paths.bundled_config_dir()
