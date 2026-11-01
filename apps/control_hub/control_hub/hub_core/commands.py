"""Robotun tarayıcı-komut beyaz listesinin Hub tarafındaki aynası.

Robot tarafı (``docs/INTERFACES.md``, ``POST /api/command``) yalnızca şunları kabul eder:
``home``, ``objects``, ``above <red|green|blue> [yükseklik_m]``. Hub aynı kuralı **yapılandırma
yüklenirken** uygular: scenarios.yaml'daki hatalı bir komut Hub açılırken yakalanır, robota
gidince ``400`` olarak değil. ROS'a bağımlılık yoktur (Windows .exe'ye ROS girmez), bu yüzden kural
burada kopyalanmıştır; iki tarafı aynı tutan şey testlerdir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

COLORS = {
    'red': 'red', 'green': 'green', 'blue': 'blue',
    'kirmizi': 'red', 'kırmızı': 'red', 'yesil': 'green', 'yeşil': 'green', 'mavi': 'blue',
}
MAX_CLEARANCE_M = 0.5
MAX_LEN = 64
_CHARS = re.compile(r'^[\w ,.]+$')


class CommandError(ValueError):
    """Komut beyaz listeye uymuyor."""


@dataclass(frozen=True)
class RobotCommand:
    name: str                                # home | objects | above
    color: Optional[str] = None              # above
    clearance_m: Optional[float] = None      # above (None -> robotun varsayılanı)

    def text(self) -> str:
        """Robota gönderilen normalleştirilmiş metin."""
        if self.name != 'above':
            return self.name
        if self.clearance_m is None:
            return f'above {self.color}'
        h = f'{self.clearance_m:.3f}'.rstrip('0').rstrip('.') or '0'
        return f'above {self.color} {h}'


def parse_robot_command(text: str) -> RobotCommand:
    if not isinstance(text, str):
        raise CommandError('komut metin olmalı')
    raw = text.strip().lower()
    if not raw:
        raise CommandError('komut boş')
    if len(raw) > MAX_LEN or not _CHARS.match(raw):
        raise CommandError(f'geçersiz komut: en çok {MAX_LEN} karakter; harf, rakam, boşluk, nokta, '
                           'virgül ve alt çizgi dışında karakter olmaz')
    tokens = raw.split()
    head, args = tokens[0], tokens[1:]
    if head in ('home', 'objects'):
        if args:
            raise CommandError(f"'{head}' argüman almaz")
        return RobotCommand(head)
    if head == 'above':
        if not 1 <= len(args) <= 2:
            raise CommandError("kullanım: above <red|green|blue> [yükseklik_m]")
        color = COLORS.get(args[0])
        if color is None:
            raise CommandError(f"bilinmeyen renk '{args[0]}' (red, green, blue)")
        clearance = None
        if len(args) == 2:
            try:
                clearance = float(args[1].replace(',', '.'))
            except ValueError:
                raise CommandError(f"yükseklik sayı olmalı: '{args[1]}'") from None
            if not 0.0 <= clearance <= MAX_CLEARANCE_M:    # NaN da burada elenir
                raise CommandError(f'yükseklik 0-{MAX_CLEARANCE_M} m aralığında olmalı')
        return RobotCommand('above', color, clearance)
    raise CommandError(f"izin verilmeyen komut '{head}' (home, objects, above)")
