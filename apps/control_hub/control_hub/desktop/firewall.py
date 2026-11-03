"""Windows güvenlik duvarı: telefondan bağlanmak için Hub portuna GELEN bağlantı izni.

**Bu modül kendiliğinden hiçbir kural eklemez.** Yalnızca (1) komutları üretir (belge/PowerShell için),
(2) kullanıcı bir iletişim kutusunda açıkça onaylarsa ``run_elevated`` ile Windows'un kendi UAC
yükseltme istemini açar. Kural: yalnızca TCP, yalnızca Hub portu, yalnızca **Özel** ağ profili,
adı sabit (kaldırması kolay).

Mantık saf fonksiyonlardır (Linux'ta test edilir); Windows'a özgü ctypes/subprocess çağrıları
``sys.platform`` ile korunur ve dışarıdan enjekte edilebilir.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Callable, List, Optional, Sequence

RULE_NAME = 'Robot Kontrol Merkezi (Hub)'
PROFILES = ('private', 'domain', 'public', 'any')


class FirewallError(ValueError):
    pass


def _check_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise FirewallError('port 1024-65535 arasında olmalı')
    return port


def _check_name(name: str) -> str:
    if not name or any(c in name for c in '"\'`$;&|<>%\r\n') or len(name) > 80:
        raise FirewallError('kural adı güvenli olmayan karakter içeriyor')
    return name


def add_rule_args(port: int, name: str = RULE_NAME, profile: str = 'private') -> List[str]:
    """``netsh`` argümanları (program adı hariç)."""
    if profile not in PROFILES:
        raise FirewallError(f'profil {PROFILES} içinden olmalı')
    return ['advfirewall', 'firewall', 'add', 'rule', f'name={_check_name(name)}', 'dir=in',
            'action=allow', 'protocol=TCP', f'localport={_check_port(port)}', f'profile={profile}']


def delete_rule_args(name: str = RULE_NAME) -> List[str]:
    return ['advfirewall', 'firewall', 'delete', 'rule', f'name={_check_name(name)}']


def show_rule_args(name: str = RULE_NAME) -> List[str]:
    return ['advfirewall', 'firewall', 'show', 'rule', f'name={_check_name(name)}']


def netsh_command_line(args: Sequence[str]) -> str:
    """Yönetici komut isteminde/PowerShell'de çalıştırılacak tam satır (adı çift tırnaklı)."""
    quoted = [subprocess.list2cmdline([a]) for a in args]
    return 'netsh ' + ' '.join(quoted)


def powershell_add(port: int, name: str = RULE_NAME, profile: str = 'private') -> str:
    prof = {'private': 'Private', 'domain': 'Domain', 'public': 'Public', 'any': 'Any'}[profile]
    return (f'New-NetFirewallRule -DisplayName "{_check_name(name)}" -Direction Inbound -Action Allow '
            f'-Protocol TCP -LocalPort {_check_port(port)} -Profile {prof}')


def powershell_remove(name: str = RULE_NAME) -> str:
    return f'Remove-NetFirewallRule -DisplayName "{_check_name(name)}"'


def powershell_show(name: str = RULE_NAME) -> str:
    return (f'Get-NetFirewallRule -DisplayName "{_check_name(name)}" | '
            'Format-List DisplayName,Enabled,Profile,Action')


def confirmation_text(port: int, name: str = RULE_NAME, profile: str = 'private') -> str:
    """Onay iletişim kutusunda gösterilen, kullanıcının ne onayladığını tam söyleyen metin."""
    where = {'private': 'yalnızca "Özel" (ev) ağlarda',
             'any': 'TÜM ağ profillerinde (genel/halka açık dahil!)',
             'domain': 'yalnızca etki alanı ağlarında',
             'public': 'yalnızca "Genel" ağlarda'}[profile]
    return (f'Windows güvenlik duvarına şu kural eklenecek:\n\n'
            f'  Ad: {name}\n  Yön: gelen (inbound), TCP, port {port}\n  Profil: {where}\n\n'
            'Amaç: aynı Wi-Fi\'daki telefonun bu bilgisayardaki Hub\'a bağlanabilmesi.\n'
            'Windows yönetici izni (UAC) isteyecek. Kuralı istediğin zaman kaldırabilirsin.\n\n'
            'Devam edilsin mi?')


# --- Windows'a özgü (enjekte edilebilir) ---------------------------------------------------------
def is_windows() -> bool:
    return sys.platform == 'win32'


def rule_exists(name: str = RULE_NAME,
                runner: Callable[..., 'subprocess.CompletedProcess'] = subprocess.run) -> Optional[bool]:
    """Kural var mı? (yönetici gerekmez). Windows değilse/çalıştırılamazsa ``None``."""
    if not is_windows():
        return None
    try:
        r = runner(['netsh', *show_rule_args(name)], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def run_elevated(args: Sequence[str], shell_execute: Optional[Callable[..., int]] = None) -> bool:
    """``netsh <args>`` komutunu UAC yükseltmesiyle çalıştırır. UAC reddedilirse/Windows değilse
    ``False``. (Komutun başarısını bu çağrı bilmez: ``rule_exists`` ile doğrula.)"""
    if shell_execute is None:
        if not is_windows():
            return False
        import ctypes
        shell_execute = ctypes.windll.shell32.ShellExecuteW      # type: ignore[attr-defined]
    params = subprocess.list2cmdline(list(args))
    rc = shell_execute(None, 'runas', 'netsh.exe', params, None, 0)    # 0 = pencere gizli
    return int(rc) > 32
