"""STEP/IGES -> üçgen dizisi (mm), ALT SÜREÇTE (S2b, UI_EMIRLER K5/K6) — Hub (ebeveyn) tarafı.

* Kütüphane: ``cascadio==0.1.1`` (MIT; içindeki OpenCASCADE DLL'leri LGPL-2.1 + Open CASCADE istisnası,
  bkz. docs/THIRD_PARTY.md). Hub süreci cascadio'yu HİÇ yüklemez (``available`` yalnız ``find_spec``
  bakar); dönüşüm ``step_child.py``'de, ayrı süreçte çalışır.
* Neden alt süreç: yerli (C++) ayrıştırıcı güvenilmeyen dosya okur; bozuk/kötü niyetli dosya süreci
  düşürebilir ya da dakikalarca dönebilir. ZAMAN AŞIMINDA alt süreç AĞAÇ olarak öldürülür (Windows tek
  dosya .exe'de önyükleyici + Python = 2 süreç); çocuk ayrıca kendi saatini kurar (``faulthandler``).
* Giriş dosyası ``<geçici dizin>/in.bin`` adıyla yazılır: kullanıcı dosya adı ASLA yol olmaz (K6).
  Çocuğun çıktısı ham float32 (**milimetre**); bu dosya onu boyut/biçim açısından doğrular.
* Birim tuzağı ve GLB okuyucu ``step_child.py``'de; burada yeniden dışa aktarılır (``sc.GLB_TO_MM`` vb.).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
from array import array
from typing import List, Optional

from control_hub.hub_core import step_child
from control_hub.hub_core.step_child import (  # noqa: F401 (yeniden dışa aktarım)
    EXIT_BAD_OUTPUT,
    EXIT_FAILED,
    EXIT_NO_MODULE,
    EXIT_OK,
    EXIT_TOO_MANY,
    EXIT_USAGE,
    EXIT_VERSION,
    FLOATS_PER_TRI,
    GLB_TO_MM,
    PINNED_VERSION,
    TOL_ANGULAR,
    TOL_LINEAR,
    TOO_MANY,
    GlbError,
    StepError,
    convert_main,
    glb_to_triangles,
)

TIMEOUT_S = 20.0                             # tüm dönüşüm (alt süreç başlatma dahil)
FORMAT_OF = {'.step': 'step', '.stp': 'step', '.iges': 'iges', '.igs': 'iges'}
LICENSE_NOTICE = 'OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna)'


def available() -> bool:
    """Dönüştürücü kurulu mu? cascadio'yu İÇE AKTARMAZ (yalnız ``find_spec``): Hub yerli kod yüklemez."""
    try:
        return importlib.util.find_spec('cascadio') is not None
    except (ImportError, ValueError):
        return False


def sniff(fmt: str, data: bytes) -> bool:
    """Ucuz içerik kontrolü (alt süreç açmadan): STEP ``ISO-10303-21`` ile başlar; IGES'in ilk satırı
    80 sütunlu ve 73. sütunu ``S`` (başlangıç bölümü) olur."""
    if fmt == 'step':
        return data.lstrip(b'\xef\xbb\xbf \t\r\n')[:12].upper() == b'ISO-10303-21'
    first = data.split(b'\n', 1)[0].rstrip(b'\r')
    return len(first) >= 73 and first[72:73] == b'S'


def child_command(fmt: str, src: str, dst: str, max_triangles: int, timeout_s: float) -> List[str]:
    """Alt sürecin komutu. Tek dosya .exe'de aynı .exe ``--convert`` ile açılır (exe_entry.py); aksi halde
    ``step_child.py`` betik olarak, ``-I`` (yalıtılmış: PYTHONPATH/cwd yok sayılır) ile."""
    args = [fmt, src, dst, str(max_triangles), repr(float(timeout_s))]
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--convert', *args]
    return [sys.executable, '-I', os.path.abspath(step_child.__file__), *args]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=10, check=False)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:                                                           # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:                                                           # noqa: BLE001
        pass


_FALLBACK = "CAD programında STL'e çevirip yükle."
_MESSAGES = {
    EXIT_NO_MODULE: ('step_unsupported', f'STEP/IGES için dönüştürücü kurulu değil: {_FALLBACK}'),
    EXIT_VERSION: ('step_unsupported', f'STEP/IGES dönüştürücü sürümü uyumsuz (cascadio=={PINNED_VERSION} '
                                       'gerekir).'),
    EXIT_FAILED: ('step_failed', f'Dosya STEP/IGES olarak okunamadı (bozuk/desteklenmeyen). {_FALLBACK}'),
    EXIT_TOO_MANY: ('too_many_triangles', TOO_MANY.format(n='500 000')),
    EXIT_BAD_OUTPUT: ('step_failed', f'Dönüştürücü geçerli bir mesh üretmedi. {_FALLBACK}'),
}


def sweep(workdir: str, max_age_s: float = 600.0) -> int:
    """Hub çökmesinden kalmış eski ``step-*`` geçici dizinlerini siler (yalnız ``workdir`` içinde)."""
    if not os.path.isdir(workdir):
        return 0
    gone, now = 0, time.time()
    for name in os.listdir(workdir):
        path = os.path.join(workdir, name)
        try:
            if name.startswith('step-') and os.path.isdir(path) and not os.path.islink(path) \
                    and now - os.path.getmtime(path) > max_age_s:
                shutil.rmtree(path, ignore_errors=True)
                gone += 1
        except OSError:
            continue
    return gone


def _run_child(cmd: List[str], timeout_s: float) -> int:
    kwargs: dict = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if os.name == 'nt':
        kwargs['creationflags'] = 0x08000000                                    # CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True                                      # killpg için kendi grubu
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.wait(timeout=10)                                               # zombi bırakma
        except subprocess.TimeoutExpired:
            pass
        raise StepError('step_timeout', f'Dönüşüm {timeout_s:g} sn içinde bitmedi (parça çok karmaşık ya da '
                                        f'dosya bozuk). {_FALLBACK}') from None


def convert(ext: str, data: bytes, *, max_triangles: int, timeout_s: Optional[float] = None,
            workdir: Optional[str] = None, command=None) -> array:
    """STEP/IGES baytları -> düz float32 üçgen dizisi (**mm**). Hatada ``StepError``; Hub süreci ASLA
    düşmez/asılmaz. ``timeout_s``: varsayılan ``TIMEOUT_S`` (20 sn). ``workdir``: geçici dizinin kökü
    (``HUB_DATA_DIR`` altı, K6). ``command``: alt süreç komutu üreticisi (yalnız testler)."""
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
    command = command or child_command
    fmt = FORMAT_OF.get(ext)
    if fmt is None:
        raise StepError('bad_extension', f'STEP/IGES uzantısı değil: {ext}')
    if not available():
        raise StepError(*_MESSAGES[EXIT_NO_MODULE])
    if not sniff(fmt, data):
        raise StepError('step_failed', 'Dosya içeriği STEP/IGES gibi görünmüyor (başlık yanlış).')
    if workdir:
        os.makedirs(workdir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='step-', dir=workdir or None)
    src, dst = os.path.join(tmp, 'in.bin'), os.path.join(tmp, 'out.f32')
    try:
        with open(src, 'wb') as fh:
            fh.write(data)
        rc = _run_child(command(fmt, src, dst, max_triangles, timeout_s), timeout_s)
        if rc != EXIT_OK:
            if rc in _MESSAGES:
                raise StepError(*_MESSAGES[rc])
            raise StepError('step_crashed', 'Dönüştürücü beklenmedik biçimde kapandı (dosya bozuk olabilir); '
                                            f'Hub çalışıyor. {_FALLBACK}')
        size = os.path.getsize(dst) if os.path.isfile(dst) else 0
        if size == 0 or size % (4 * FLOATS_PER_TRI) or size > max_triangles * FLOATS_PER_TRI * 4:
            raise StepError('step_failed', 'Dönüştürücü çıktısı geçersiz.')
        tris = array('f')
        with open(dst, 'rb') as fh:
            tris.frombytes(fh.read())
        if sys.byteorder == 'big':
            tris.byteswap()
        return tris
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
