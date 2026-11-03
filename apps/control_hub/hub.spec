# -*- mode: python ; coding: utf-8 -*-
# PyInstaller (tek dosya):   pyinstaller hub.spec --noconfirm        (apps/control_hub içinde)
# Çıktı: dist/ControlHub(.exe).  Tek dosya, ROS yok; web arayüzü ve varsayılan yapılandırma gömülü.
#
# console=True: çift tıkta Windows bir konsol açar, uygulama onu GİZLER (desktop/winconsole.py);
# PowerShell'den bayrakla açılınca çıktı (--check-config, --help ...) normal görünür.
import sys

from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules('uvicorn')            # protokol/loop modülleri dinamik import edilir
    + collect_submodules('websockets')       # /ws için (yoksa arayüz yoklamaya düşer)
    + collect_submodules('serial')           # ESP32 köprüsü: pyserial (Windows COM, socket:// vb. işleyiciler)
    + collect_submodules('control_hub')
    + ['yaml', 'segno', 'h11', 'anyio._backends._asyncio', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox']
)

a = Analysis(
    ['exe_entry.py'],
    pathex=['.'],
    datas=[('control_hub/web', 'control_hub/web'), ('config', 'config')],
    hiddenimports=hidden,
    excludes=['numpy', 'PIL', 'pytest', 'robot_core', 'matplotlib', 'IPython'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='ControlHub',
    icon='assets/hub.ico' if sys.platform == 'win32' else None,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None, console=True,
)
