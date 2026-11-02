"""Çift tıklamayla açılan .exe'nin konsol penceresini gizler; PowerShell'den açılırsa DOKUNMAZ."""
from __future__ import annotations

import sys
from typing import Optional


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
