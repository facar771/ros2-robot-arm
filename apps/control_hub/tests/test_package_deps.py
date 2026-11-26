"""R7: paket bağımlılık YÖNÜ. ``hub_core`` ve ``agent`` üst katman olan ``desktop``'u içe aktarmaz
(docs/MIMARI.md §3). İşlev içindeki tembel içe aktarmalar da sayılır (statik analizden kaçan bağ buydu)."""
import ast
from pathlib import Path
from typing import Iterator, List, Tuple

PKG = Path(__file__).resolve().parents[1] / 'control_hub'
ROOT = 'control_hub'

# paket -> içe aktaramayacağı paketler. ``desktop`` en üst katmandır (pencere, launcher, .exe);
# alttakiler onu bilmez.
FORBIDDEN = {'hub_core': ('desktop',), 'agent': ('desktop',)}


def _absolute(pkg_parts: List[str], level: int, module: str) -> str:
    """Göreli içe aktarmayı (``from ..x import y``) mutlak ada çevirir; ``pkg_parts`` dosyanın paketi."""
    if not level:
        return module
    base = pkg_parts[:len(pkg_parts) - (level - 1)]
    return '.'.join(base + ([module] if module else []))


def imports_of(source: str, pkg_parts: List[str]) -> Iterator[Tuple[int, str]]:
    """Kaynaktaki TÜM içe aktarmalar (işlev içindekiler dahil): ``(satır, mutlak modül adı)``.

    ``from control_hub import desktop`` → ``control_hub.desktop``; ``importlib.import_module('x.y')`` ve
    ``__import__('x.y')`` çağrılarındaki düz metin adlar da sayılır."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(pkg_parts, node.level, node.module or '')
            yield node.lineno, base
            for alias in node.names:
                yield node.lineno, f'{base}.{alias.name}'
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, 'attr', '')
            if name in ('import_module', '__import__') and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                yield node.lineno, node.args[0].value


def _is_under(module: str, package: str) -> bool:
    target = f'{ROOT}.{package}'
    return module == target or module.startswith(target + '.')


def scan(package: str) -> Iterator[Tuple[str, int, str]]:
    """``control_hub/<package>/**.py`` içindeki her içe aktarma: ``(göreli dosya, satır, modül)``."""
    for path in sorted((PKG / package).rglob('*.py')):
        rel = path.relative_to(PKG)
        pkg_parts = [ROOT, *rel.parts[:-1]]
        for line, module in imports_of(path.read_text(encoding='utf-8'), pkg_parts):
            yield rel.as_posix(), line, module


def test_hub_core_and_agent_do_not_import_desktop():
    bad = [f'{f}:{n} -> {m}' for pkg, banned in FORBIDDEN.items()
           for f, n, m in scan(pkg) if any(_is_under(m, b) for b in banned)]
    assert not bad, 'yukarı bağ (alt katman üst katmanı içe aktarıyor):\n' + '\n'.join(sorted(set(bad)))


def test_program_store_no_longer_imports_paths():
    """R7'nin ikinci yarısı: kullanıcı klasörü ``ProgramStore``'a dışarıdan verilir (test_program_store)."""
    bad = [f'{f}:{n} -> {m}' for f, n, m in scan('hub_core')
           if f == 'hub_core/program_store.py' and _is_under(m, 'paths')]
    assert not bad, '\n'.join(bad)


def test_the_scan_is_not_vacuous():
    """Tarama gerçek dosyaları görüyor (yol hatası testi sessizce boşaltmasın) ve izinli yönü de buluyor."""
    scanned = {f for pkg in FORBIDDEN for f, _, _ in scan(pkg)}
    assert {'hub_core/program_store.py', 'agent/service.py', 'agent/guards.py'} <= scanned
    desktop_to_agent = [m for _, _, m in scan('desktop') if _is_under(m, 'agent')]
    assert desktop_to_agent, 'desktop → agent (izinli yön) bulunamadı: tarayıcı bozuk olabilir'
    assert (PKG / 'desktop' / 'selftest.py').is_file() and not (PKG / 'agent' / 'selftest.py').exists()


def test_scanner_catches_lazy_relative_from_package_and_string_imports():
    """Tarayıcının kendisi: gizli bağlar (işlev içi, göreli, ``from paket import alt``, metin) yakalanır."""
    src = '''
def lazy():
    from control_hub.desktop.service import HubService
def rel():
    from ..desktop import settings
def sub():
    from control_hub import desktop
def txt():
    import importlib
    importlib.import_module('control_hub.desktop.gui')
    __import__('control_hub.desktop')
import control_hub.desktop.launcher
'''
    found = {m for _, m in imports_of(src, [ROOT, 'agent'])}
    hits = {m for m in found if _is_under(m, 'desktop')}
    assert hits >= {'control_hub.desktop.service', 'control_hub.desktop.settings', 'control_hub.desktop',
                    'control_hub.desktop.gui', 'control_hub.desktop.launcher'}
    clean = {m for _, m in imports_of('from control_hub.hub_core import hub\nimport os\n', [ROOT, 'agent'])}
    assert not any(_is_under(m, 'desktop') for m in clean)
