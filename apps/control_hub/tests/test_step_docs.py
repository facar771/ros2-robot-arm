"""S2b (#109): LGPL bildirimi, üçüncü taraf belgesi ve paketleme/CI bağlantıları (bağımlılık gerekmez)."""
import re
from pathlib import Path

from control_hub.hub_core import step_convert as sc

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
NOTICE = 'OpenCASCADE Technology kullanılıyor (LGPL-2.1 + istisna)'


def read(p: Path) -> str:
    return p.read_text(encoding='utf-8')


def test_notice_text_is_the_same_everywhere():
    assert sc.LICENSE_NOTICE == NOTICE
    assert NOTICE in read(ROOT / 'docs' / 'THIRD_PARTY.md')
    parts_logic = read(ROOT / 'control_hub' / 'web' / 'parts_logic.js')
    assert NOTICE in parts_logic             # sunucu bildirmezse yedek metin (R12: mantık dosyasında)


def test_third_party_doc_has_license_source_and_version():
    doc = read(ROOT / 'docs' / 'THIRD_PARTY.md')
    for needle in ('LGPL-2.1', 'Open CASCADE', '8.0.1', 'cascadio==0.1.1', 'MIT', 'third_party/LGPL-2.1.txt',
                   'github.com/Open-Cascade-SAS/OCCT', 'github.com/trimesh/cascadio'):
        assert needle in doc, needle
    lic = read(ROOT / 'docs' / 'third_party' / 'LGPL-2.1.txt')
    assert 'GNU LESSER GENERAL PUBLIC LICENSE' in lic and 'Version 2.1, February 1999' in lic
    assert len(lic) > 25000


def test_requirements_pin_only_cascadio_plus_its_numpy():
    text = read(ROOT / 'requirements-step.txt')
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith('#')]
    assert lines == ['cascadio==0.1.1', 'numpy']


def test_exe_entry_change_is_minimal():
    text = read(ROOT / 'exe_entry.py')
    assert text.count('--convert') == 1 and 'convert_main' in text and len(text.splitlines()) <= 12
    assert 'from control_hub.desktop.launcher import main' in text and 'raise SystemExit(main())' in text


def test_spec_embeds_cascadio_with_its_dll_folder_and_can_be_made_fatal():
    spec = read(ROOT / 'hub.spec')
    for needle in ("collect_all('cascadio')", "copy_metadata('cascadio')", 'cascadio.libs',
                   'HUB_REQUIRE_STEP', 'binaries=STEP_BINARIES'):
        assert needle in spec, needle


def test_workflows_install_the_pin_cache_wheels_and_prove_it_inside_the_exe():
    exe = read(REPO / '.github' / 'workflows' / 'hub-exe.yml')
    assert 'requirements-step.txt' in exe and 'cache: pip' in exe and "HUB_REQUIRE_STEP: '1'" in exe
    assert exe.count('tools.step_exe_check') == 1 and 'Açılış testi 6' in exe
    step = exe[exe.index('Açılış testi 6'):exe.index("ControlHub.exe'yi artifact")]
    assert 'dist/ControlHub.exe' in step and 'continue-on-error' not in step   # zorunlu: gevşetilemez
    ci = read(REPO / '.github' / 'workflows' / 'ci.yml')
    assert 'requirements-step.txt' in ci and re.search(r'cache: pip', ci)


def test_exe_check_tool_covers_both_formats_and_all_units():
    tool = read(ROOT / 'tools' / 'step_exe_check.py')
    for needle in ('step_box', 'iges_box', "'mm', 'in', 'm'", 'step_failed', 'step.available'):
        assert needle in tool, needle


def test_s2b_source_files_stay_under_400_lines():
    """UI_EMIRLER K12: her dosya < 400 satır (aşarsa böl)."""
    pkg = ROOT / 'control_hub'
    files = [pkg / 'hub_core' / n for n in ('step_child.py', 'step_convert.py', 'mesh_tools.py')]
    files += [pkg / 'server' / 'parts_routes.py', pkg / 'web' / 'pages' / 'parts.js']
    files += [pkg / 'web' / 'parts_logic.js']
    files += [ROOT / 'tools' / 'step_exe_check.py', ROOT / 'tests' / 'test_step_convert.py']
    for f in files:
        assert len(f.read_text(encoding='utf-8').splitlines()) < 400, f.name
