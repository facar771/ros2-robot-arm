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
# Aşama 0 iskelet: web/pages/*.js ve server/*_routes.py otomatik keşfedilir (server/registry.py); .exe'de keşif
# `--check-config` ile doğrulanır (cli.py: selfcheck). Three.js yerel (web/vendor/three), CDN yok.
# KM3 Tezgâh: tools/bench/bench_core.py (veri olarak) ve robot_hw_bridge (ROS'suz host/protocol/transport) gömülür.
AGENT_PATHS = ['../../src/robot_agent', '../../src/robot_core', '../../src/robot_hw_bridge']
CORE_MODULES = ['config', 'errors', 'frames', 'interpreter', 'kinematics', 'models', 'ports', 'profile',
                'safezone', 'scenario', 'se3', 'skills', 'supervisor', 'targets', 'waypointapi', 'waypoints',
                'webcmd']

# robot_hw_bridge/__init__ bunları içe aktarır (ROS'suz). collect_submodules derleme anında pakete erişemediği için sayılır.
HW_MODULES = ['codec', 'crc', 'host', 'protocol', 'sim', 'transport']

# 3B görünüm: profildeki link mesh'leri (ham CAD'ler `cad/` DEĞİL, yalnız `link/`; ~1 MB/robot). Hub bunları
# _MEIPASS/robot_description/meshes/<robot>/link altında arar (hub_core/meshes.py). Dizin yoksa .exe sessizce
# mesh'siz çıkmasın diye derleme DURUR (primitif çizim yine çalışırdı ama bu bir paketleme hatasıdır).
import os  # noqa: E402

_MESHES = os.path.normpath(os.path.join(SPECPATH, '..', '..', 'src', 'robot_description', 'meshes'))
MESH_DATAS = [(os.path.join(_MESHES, r, 'link'), os.path.join('robot_description', 'meshes', r, 'link'))
              for r in sorted(os.listdir(_MESHES)) if os.path.isdir(os.path.join(_MESHES, r, 'link'))] \
    if os.path.isdir(_MESHES) else []
if not MESH_DATAS:
    raise SystemExit('hub.spec: src/robot_description/meshes/<robot>/link bulunamadı; 3B görünüm mesh\'siz '
                     'paketlenirdi. Depoyu eksiksiz çıkar (git lfs/sığ klon değil).')

# server/*_routes.py -> routes.manifest (registry.py: .exe'de pkgutil PYZ'yi listeleyemez). Elle liste YOK: dosyalar taranır.
_SERVER = os.path.join(SPECPATH, 'control_hub', 'server')
_ROUTES = sorted(f[:-3] for f in os.listdir(_SERVER) if f.endswith('_routes.py') and not f.startswith('_'))
from PyInstaller.config import CONF  # noqa: E402

_WORK = CONF['workpath']
os.makedirs(_WORK, exist_ok=True)
_MANIFEST = os.path.join(_WORK, 'routes.manifest')
with open(_MANIFEST, 'w', encoding='utf-8') as _f:
    _f.write('\n'.join(_ROUTES) + '\n')
if not _ROUTES:
    raise SystemExit('hub.spec: server/*_routes.py bulunamadı')

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
    + ['control_hub.server.' + r for r in _ROUTES]      # dinamik içe aktarılır (registry.py): statik analiz görmez
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
    datas=[('control_hub/web', 'control_hub/web'), ('config', 'config'), ('../../tools/bench/bench_core.py', 'tools/bench')]
          + MESH_DATAS + [(_MANIFEST, 'control_hub/server')] + copy_metadata('keyring'),
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
