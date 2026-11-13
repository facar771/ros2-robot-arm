# -*- mode: python ; coding: utf-8 -*-
# PyInstaller (tek dosya):   pyinstaller hub.spec --noconfirm        (apps/control_hub içinde)
# Çıktı: dist/ControlHub(.exe).  Tek dosya, ROS yok; web arayüzü ve varsayılan yapılandırma gömülü.
#
# console=True: çift tıkta Windows bir konsol açar, uygulama onu GİZLER (desktop/winconsole.py);
# PowerShell'den bayrakla açılınca çıktı (--check-config, --help ...) normal görünür.
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

# LLM ajanı (A3): robot_agent + robot_core (ROS'suz alt küme) depo kaynağından gömülür. robot_plugins gömülmez
# (ajan eklenti köprüsü boş katalogla kapalı açılır).
# N7a: robot_core.webcmd artık robot_core.waypoints'i içe aktarıyor (O2) ve o numpy ister (frames/kinematics/se3
# de). Bu yüzden numpy GÖMÜLÜR (eskiden dışlanmıştı) ve zincirin tamamı CORE_MODULES'te açıkça sayılır. Yeni bir
# robot_core modülü webcmd zincirine girerse hub-exe.yml artık src/robot_core/** değişince de koşar ve ajan öz
# sınamasının "ajan etkin" adımında yakalar.
# KM3 Tezgâh: tools/bench/bench_core.py (veri olarak) ve robot_hw_bridge (ROS'suz host/protocol/transport) gömülür.
AGENT_PATHS = ['../../src/robot_agent', '../../src/robot_core', '../../src/robot_hw_bridge']
CORE_MODULES = ['config', 'errors', 'frames', 'interpreter', 'kinematics', 'models', 'ports', 'profile',
                'safezone', 'scenario', 'se3', 'skills', 'supervisor', 'targets', 'waypointapi', 'waypoints',
                'webcmd']

# robot_hw_bridge/__init__ bunları içe aktarır (ROS'suz). collect_submodules derleme anında pakete erişemediği için sayılır.
HW_MODULES = ['codec', 'crc', 'host', 'protocol', 'sim', 'transport']

try:
    import numpy  # noqa: F401  (derleme ortamında yoksa .exe sessizce eksik çıkmasın)
except ImportError as exc:
    raise SystemExit('hub.spec: numpy kurulu değil; .exe ajanı (robot_core.waypoints) numpy ister: '
                     'pip install numpy') from exc

hidden = (
    collect_submodules('uvicorn')            # protokol/loop modülleri dinamik import edilir
    + collect_submodules('websockets')       # /ws için (yoksa arayüz yoklamaya düşer)
    + collect_submodules('serial')           # ESP32 köprüsü: pyserial (Windows COM, socket:// vb. işleyiciler)
    + collect_submodules('control_hub')
    + collect_submodules('robot_agent')
    + ['robot_hw_bridge'] + ['robot_hw_bridge.' + m for m in HW_MODULES]      # KM3: tools/bench bunları içe aktarır
    + ['robot_core'] + ['robot_core.' + m for m in CORE_MODULES]
    # anahtar deposu: keyring arka uçlarını entry point (importlib.metadata) ile bulur -> dist-info gömülür,
    # Windows Credential Manager arka ucu (WinVaultKeyring) pywin32-ctypes (win32ctypes) ile çalışır
    + collect_submodules('keyring') + collect_submodules('win32ctypes')
    + ['numpy', 'yaml', 'segno', 'h11', 'anyio._backends._asyncio', 'tkinter', 'tkinter.ttk', 'tkinter.messagebox']
)

a = Analysis(
    ['exe_entry.py'],
    pathex=['.'] + AGENT_PATHS,
    datas=[('control_hub/web', 'control_hub/web'), ('config', 'config'), ('../../tools/bench/bench_core.py', 'tools/bench')] + copy_metadata('keyring'),
    hiddenimports=hidden,
    excludes=['PIL', 'pytest', 'matplotlib', 'IPython'],
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
