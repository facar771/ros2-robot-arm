"""Çift tıklamayla açılan .exe'nin konsol penceresini gizler; PowerShell'den açılırsa DOKUNMAZ."""
from __future__ import annotations

import sys
from typing import Optional


def ensure_utf8_output(platform: Optional[str] = None) -> None:
    """Türkçe karakterler Windows konsolunda (cp1252/cp437) ``UnicodeEncodeError`` ile ÇÖKMESİN.

    Konsol kod sayfasını UTF-8'e alır ve stdout/stderr'i UTF-8 + ``errors='replace'`` yapar (yazılamayan
    karakter en kötü ``?`` olur, program düşmez). Akış yoksa (pencereli kip) sessizce geçer."""
    if (platform or sys.platform) == 'win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)        # type: ignore[attr-defined]
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def hide_console_if_owned(platform: Optional[str] = None) -> bool:
    if (platform or sys.platform) != 'win32':
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32                        # type: ignore[attr-defined]
        user32 = ctypes.windll.user32                            # type: ignore[attr-defined]
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return False
        pids = (ctypes.c_ulong * 4)()
        count = kernel32.GetConsoleProcessList(pids, 4)
        if count != 1:                  # başka süreç de bağlı: kullanıcının terminali
            return False
        user32.ShowWindow(hwnd, 0)      # SW_HIDE
        return True
    except Exception:                   # gizleyememek ölümcül değil
        return False
