"""R10: gui.py ve agent/service.py bölündü. Bu testler Tk'siz koşar (Linux CI'da da): bölmenin korunmasını
(K12 satır sınırı, eski içe aktarma yolları, pencere bölüm sırası, .exe içeri alma listesi) kilitler."""
import ast
from pathlib import Path

from control_hub.agent import config, errors, guards, service

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'control_hub'

# bölünen dosyalar + yeni modüller: hepsi K12 sınırının (400 satır) altında kalmalı
SPLIT_FILES = ('desktop/gui.py', 'desktop/gui_sections.py', 'agent/service.py', 'agent/errors.py',
               'agent/config.py', 'agent/guards.py')

# ekrandaki (= pack = Tab) sıra; değişirse pencere yerleşimi değişir
SECTION_ORDER = ['_build_settings', '_build_bridge', '_build_agent', '_build_actions',
                 '_build_connection', '_build_firewall', '_build_events']


def test_split_files_stay_under_400_lines():
    for rel in SPLIT_FILES:
        n = len((PKG / rel).read_text(encoding='utf-8').splitlines())
        assert n < 400, f'{rel}: {n} satır (K12: <400)'


def test_old_service_import_paths_still_work():
    """agent_routes.py ve dış kod ``control_hub.agent.service``'ten alıyordu; aynı nesneler olmalı."""
    assert service.AgentError is errors.AgentError
    assert service.AgentConfig is config.AgentConfig
    assert service.UsageCounter is guards.UsageCounter
    assert service.ALL_PROVIDERS is config.ALL_PROVIDERS
    import control_hub.agent as pkg
    assert pkg.AgentConfig is config.AgentConfig and pkg.AgentError is errors.AgentError
    assert pkg.AgentService is service.AgentService


def test_guard_exceptions_are_plain_exceptions():
    """İptal/sınır istisnaları AgentError DEĞİL (service._run ikisini ayrı yakalar)."""
    for exc in (errors.Cancelled, errors.LimitReached):
        assert issubclass(exc, Exception) and not issubclass(exc, errors.AgentError)


def _class_methods(path: Path, cls: str):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}


def test_window_build_calls_sections_in_screen_order():
    """gui*.py Tk'yi içe aktardığı için AST ile okunur: ``_build`` bölümleri bu sırayla çağırır."""
    build = _class_methods(PKG / 'desktop/gui.py', 'HubWindow')['_build']
    called = [n.attr for n in ast.walk(build)
              if isinstance(n, ast.Attribute) and n.attr.startswith('_build_')]
    assert called == SECTION_ORDER
    sections = _class_methods(PKG / 'desktop/gui_sections.py', 'SectionsMixin')
    assert [name for name in sections if name.startswith('_build_')] == SECTION_ORDER


def test_gui_window_uses_the_sections_mixin():
    tree = ast.parse((PKG / 'desktop/gui.py').read_text(encoding='utf-8'))
    window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'HubWindow')
    assert [b.id for b in window.bases if isinstance(b, ast.Name)] == ['SectionsMixin']


def test_exe_import_list_covers_new_modules():
    """hub.spec R10'da DEĞİŞMEZ: yeni modüller ``collect_submodules('control_hub')`` ile otomatik girer."""
    spec = (ROOT / 'hub.spec').read_text(encoding='utf-8')
    assert "collect_submodules('control_hub')" in spec
    assert all(m in spec for m in ("'tkinter'", "'tkinter.ttk'", "'tkinter.messagebox'"))
    for rel in ('desktop/gui_sections.py', 'agent/errors.py', 'agent/config.py', 'agent/guards.py'):
        assert (PKG / rel).is_file()                         # paketin içinde: spec kapsar
