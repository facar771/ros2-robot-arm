"""Aşama 0 iskeleti: otomatik sayfa/rota keşfi, 3B mesh kapısı, yerel Three.js, CDN yokluğu, öz denetim."""
import hashlib
import re
import shutil
import struct
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi import Request

from control_hub.hub_core.meshes import MeshCatalog, MeshError, check_binary_stl, mesh_root_candidates
from control_hub.server import registry
from control_hub.server.registry import (
    CORE_PAGES,
    CORE_ROUTES,
    RouteContext,
    install_routes,
    page_files,
    route_module_names,
    selfcheck,
)

APP = Path(__file__).resolve().parents[1]
WEB = APP / 'control_hub' / 'web'
REPO = APP.parents[1]


def stl(n=2):
    """Geçerli ikili STL (n üçgen)."""
    return b'\0' * 80 + struct.pack('<I', n) + b''.join(
        struct.pack('<12fH', 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0) for _ in range(n))


# --- otomatik keşif ---------------------------------------------------------------------------------
def test_core_routes_and_pages_are_discovered():
    assert set(CORE_ROUTES) <= set(route_module_names())
    assert set(CORE_PAGES) <= set(page_files(WEB))
    assert selfcheck(WEB) == []


def test_selfcheck_reports_every_missing_piece(tmp_path):
    problems = selfcheck(tmp_path)
    assert any('pages/robot.js' in p for p in problems) and any('three.module.js' in p for p in problems)


def test_pages_endpoint_lists_page_files_and_needs_login_in_pin_mode(make_client):
    c, _ = make_client('open')
    ids = [p['id'] for p in c.get('/api/pages').json()['pages']]
    assert ids == sorted(ids) and {'robot', 'jog', 'agent', 'bench'} <= set(ids)
    assert c.get('/pages/robot.js').status_code == 200
    pin, _ = make_client('pin')
    assert pin.get('/api/pages').status_code == 401


def test_page_file_names_are_validated(tmp_path):
    (tmp_path / 'pages').mkdir()
    for name in ('ok_page.js', 'Bad.js', '1x.js', 'a-b.js', 'x.txt', '.hidden.js'):
        (tmp_path / 'pages' / name).write_text('//')
    assert page_files(tmp_path) == ['ok_page.js']


def test_new_routes_module_is_installed_without_touching_shared_files(make_client, monkeypatch):
    """server/<ad>_routes.py + install(app, ctx): ortak dosyaya dokunmadan kurulur ve ctx'i alır."""
    mod = types.ModuleType('control_hub.server.zz_routes')

    def install(app, ctx):
        @app.get('/api/zz')
        async def zz(request: Request):
            ctx.need_view(request)
            return {'ok': True, 'backend': ctx.hub.backend.name}
    mod.install = install
    monkeypatch.setitem(sys.modules, 'control_hub.server.zz_routes', mod)
    monkeypatch.setattr(registry, 'route_module_names', lambda: ['zz_routes'])
    c, _ = make_client('open')
    assert c.get('/api/zz').json() == {'ok': True, 'backend': 'fake'}


def test_routes_module_without_install_fails_loudly(monkeypatch):
    name = 'control_hub.server.nn_routes'
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setattr(registry, 'route_module_names', lambda: ['nn_routes'])
    ctx = RouteContext(hub=None, need_view=None, need_control=None, token_of=None, api_error=None)
    with pytest.raises(RuntimeError, match='install'):
        install_routes(object(), ctx)


# --- mesh kapısı ------------------------------------------------------------------------------------
def test_binary_stl_check():
    assert check_binary_stl(stl(3)) == 3
    for bad in (b'', b'solid x\n' + b'a' * 100, stl(2)[:-1], b'\0' * 80 + struct.pack('<I', 0)):
        with pytest.raises(MeshError):
            check_binary_stl(bad)


def test_catalog_reads_only_validated_names(tmp_path):
    (tmp_path / 'bot' / 'link').mkdir(parents=True)
    (tmp_path / 'bot' / 'link' / 'link_1.stl').write_bytes(stl())
    (tmp_path / 'bot' / 'link' / 'bad.stl').write_bytes(b'solid ascii')
    cat = MeshCatalog(tmp_path)
    assert cat.read('bot', 'link_1') == stl()
    assert cat.available('bot', ['link_1', 'bad', 'nope']) == {'link_1': len(stl())}
    bad = (('bot', '../x'), ('..', 'link_1'), ('bot', 'a/b'), ('bot', '.x'), ('bot', ''), ('', 'link_1'))
    for robot, link in bad:
        with pytest.raises(MeshError):
            cat.read(robot, link)
    with pytest.raises(MeshError):
        MeshCatalog(None).read('bot', 'link_1')


def test_mesh_root_candidates_order_and_shallow_install():
    here = REPO / 'apps' / 'control_hub' / 'control_hub' / 'hub_core' / 'meshes.py'
    env = {'HUB_MESH_DIR': '/x/meshes'}
    c = mesh_root_candidates(here, False, env)
    assert c[0] == Path('/x/meshes') and c[-1] == REPO / 'src' / 'robot_description' / 'meshes'
    assert mesh_root_candidates(here, False, {}) == [REPO / 'src' / 'robot_description' / 'meshes']
    docker = Path('/app/control_hub/hub_core/meshes.py')
    assert mesh_root_candidates(docker, False, {}) == []       # Docker: depo yok, çökmez


def test_meshes_routes_gate_on_the_model(make_client, monkeypatch, tmp_path):
    (tmp_path / 'six_axis_robot' / 'link').mkdir(parents=True)
    (tmp_path / 'six_axis_robot' / 'link' / 'link_1.stl').write_bytes(stl())
    (tmp_path / 'six_axis_robot' / 'link' / 'ghost.stl').write_bytes(stl())      # modelde olmayan link
    monkeypatch.setenv('HUB_MESH_DIR', str(tmp_path))
    c, _ = make_client('open')
    doc = c.get('/api/meshes').json()
    assert doc['robot'] == 'six_axis_robot' and list(doc['meshes']) == ['link_1'] and doc['reason'] is None
    r = c.get('/api/meshes/link_1.stl')
    assert r.status_code == 200 and r.content == stl() and r.headers['content-type'] == 'model/stl'
    assert c.get('/api/meshes/ghost.stl').status_code == 404            # model kapısı: listede yoksa sunulmaz
    assert c.get('/api/meshes/link_2.stl').status_code == 404           # dosya yok
    assert c.get('/api/meshes/..%2Fx.stl').status_code == 404
    assert c.post('/api/meshes/link_1.stl', json={}).status_code in (404, 405)   # salt okunur


def test_meshes_absent_is_not_an_error(make_client, monkeypatch, tmp_path):
    monkeypatch.setenv('HUB_MESH_DIR', str(tmp_path / 'yok'))
    monkeypatch.setattr('control_hub.hub_core.meshes.mesh_root_candidates', lambda *a, **k: [])
    c, _ = make_client('open')
    doc = c.get('/api/meshes').json()
    assert doc['ok'] and doc['meshes'] == {} and doc['reason']


def test_meshes_need_login_in_pin_mode(make_client):
    c, _ = make_client('pin')
    assert c.get('/api/meshes').status_code == 401 and c.get('/api/meshes/link_1.stl').status_code == 401


@pytest.mark.skipif(not (REPO / 'src' / 'robot_description' / 'meshes' / 'karma_robot' / 'link').is_dir(),
                    reason='depo mesh\'leri yok')
def test_repo_profile_meshes_are_valid_binary_stl():
    import json
    path = REPO / 'src' / 'robot_description' / 'profile' / 'generated' / 'karma_robot' / 'view_model.json'
    model = json.loads(path.read_text())
    cat = MeshCatalog(REPO / 'src' / 'robot_description' / 'meshes')
    found = cat.available('karma_robot', [k['name'] for k in model['links']])
    assert set(found) == {k['name'] for k in model['links']}


# --- yerel Three.js, CDN yok, güvenlik --------------------------------------------------------------
def test_three_is_vendored_with_pinned_hashes(make_client):
    readme = (WEB / 'vendor' / 'three' / 'README.md').read_text(encoding='utf-8')
    for name in ('three.module.js', 'three.core.js'):
        digest = hashlib.sha256((WEB / 'vendor' / 'three' / name).read_bytes()).hexdigest()
        assert digest in readme, f'{name}: README\'deki SHA-256 dosyayla uyuşmuyor'
    assert (WEB / 'vendor' / 'three' / 'LICENSE').is_file()
    c, _ = make_client('open')
    r = c.get('/vendor/three/three.module.js')
    assert r.status_code == 200
    assert r.headers['content-type'].startswith('text/javascript')     # ES modülü için şart
    assert "default-src 'self'" in r.headers['content-security-policy']


def test_no_cdn_and_no_inline_script_in_our_web_files():
    """CSP script-src 'self': satır içi betik/stil ve dış adres yok (vendor/ hariç)."""
    for f in [WEB / 'index.html', WEB / 'style.css', *WEB.glob('*.js'), *(WEB / 'pages').glob('*.js')]:
        text = f.read_text(encoding='utf-8')
        code = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        external = r"""(?:src|href)=["']https?://|url\(\s*["']?https?://|from\s+["']https?://"""
        assert not re.search(external, code), f
        assert 'innerHTML' not in code, f
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    assert not re.search(r'<script(?![^>]*\bsrc=)[^>]*>', html)
    assert ' onclick=' not in html and 'style="' not in html


def test_every_page_file_registers_its_own_id():
    for f in sorted((WEB / 'pages').glob('*.js')):
        pattern = r"HubPages\.register\(\{\s*id:\s*'%s'" % re.escape(f.stem)
        assert re.search(pattern, f.read_text(encoding='utf-8')), f


def test_scenario_buttons_and_named_poses_are_gone_from_the_ui(ui):
    assert ui['layout']['sections'] == []
    pages_js = ''.join(p.read_text(encoding='utf-8') for p in (WEB / 'pages').glob('*.js'))
    shipped = pages_js + ''.join((WEB / n).read_text(encoding='utf-8')
                                 for n in ('app.js', 'jog.js', 'index.html'))
    for banned in ('above_green', 'Hazır pozuna', "class: 'scn'", 'onScenario', 'jg-ready'):
        assert banned not in shipped, banned


def test_topbar_has_the_fixed_controls():
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    top = html[html.index('<header'):html.index('</header>')]
    for needed in ('id="btn-estop"', 'id="pill-conn"', 'id="pill-mode"', 'id="pill-access"'):
        assert needed in top, needed


def test_hub_spec_bundles_meshes_and_loud_when_missing():
    spec = (APP / 'hub.spec').read_text(encoding='utf-8')
    assert 'robot_description' in spec and 'MESH_DATAS' in spec and 'SystemExit' in spec
    assert "('control_hub/web', 'control_hub/web')" in spec


# --- node: gerçek JS dosyaları sahte DOM üzerinde ---------------------------------------------------------
JS = Path(__file__).parent / 'js'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node yok: arayüz mantığı testi atlandı')


def run_node(script, *args):
    cmd = ['node', str(JS / script), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@needs_node
def test_scene_and_shell_logic_in_node():
    assert 'SCENE_LOGIC_JS_OK' in run_node('scene_logic_check.js', WEB)


@needs_node
def test_hubscene_skeleton_in_node():
    assert 'HUBSCENE_OK' in run_node('hubscene_check.js', WEB)


@needs_node
def test_page_registry_in_node():
    assert 'PAGES_JS_OK' in run_node('pages_check.js', WEB)


@needs_node
def test_all_web_scripts_are_syntactically_valid(tmp_path):
    for f in [*WEB.glob('*.js'), *(WEB / 'pages').glob('*.js')]:
        target = f
        if f.name == 'scene3d.js':                   # ES modülü: node --check yalnız .mjs'i modül sayar
            target = tmp_path / 'scene3d.mjs'
            target.write_text(f.read_text(encoding='utf-8'), encoding='utf-8')
        r = subprocess.run(['node', '--check', str(target)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f'{f.name}: {r.stderr}'
    bad = tmp_path / 'bad.js'
    bad.write_text('const = ;', encoding='utf-8')     # denetimin kendisi çalışıyor mu
    assert subprocess.run(['node', '--check', str(bad)], capture_output=True, timeout=60).returncode != 0


# --- docs/UI_CONTRACT.md şablonları GERÇEKTEN çalışır -----------------------------------------------------
def template(kind):
    doc = (APP / 'docs' / 'UI_CONTRACT.md').read_text(encoding='utf-8')
    m = re.search(r'```(?:python|js) template:%s\n(.*?)```' % kind, doc, flags=re.S)
    assert m, f'UI_CONTRACT.md: template:{kind} bloğu yok'
    return m.group(1)


def test_contract_route_and_test_templates_work(make_client, monkeypatch):
    mod = types.ModuleType('control_hub.server.hello_routes')
    exec(compile(template('routes'), 'hello_routes.py', 'exec'), mod.__dict__)        # noqa: S102
    monkeypatch.setitem(sys.modules, 'control_hub.server.hello_routes', mod)
    monkeypatch.setattr(registry, 'route_module_names', lambda: ['hello_routes'])
    monkeypatch.setattr('control_hub.server.app.page_files', lambda web: ['hello.js'])
    ns = {}
    exec(compile(template('test'), 'test_hello_page.py', 'exec'), ns)                  # noqa: S102
    ns['test_hello_route_is_auto_installed'](make_client)


@needs_node
def test_contract_page_template_works(tmp_path):
    f = tmp_path / 'hello.js'
    f.write_text(template('page'), encoding='utf-8')
    assert 'PAGE_TEMPLATE_OK' in run_node('page_template_check.js', f)


# --- S1-0: sayfalar arası sözleşme (UI_CONTRACT §9) -----------------------------------------------------
def test_snapshot_carries_caps_list(make_client):
    """snap.caps: sayfa `requires` için; arka uç tanımlamıyorsa boş liste (sahte robot SİM iddia etmez)."""
    c, hub = make_client('open')
    assert hub.snapshot()['caps'] == []
    hub.backend.caps = ('sim',)                  # S1'in SimBackend'i bunu sınıf özniteliği olarak taşır
    assert hub.snapshot()['caps'] == ['sim']


def test_data_dir_env_and_subdirs(tmp_path):
    from control_hub import paths
    env = {'HUB_DATA_DIR': str(tmp_path / 'veri')}
    assert paths.data_dir(env) == tmp_path / 'veri'
    for name in paths.DATA_SUBDIRS:
        p = paths.data_subdir(name, env)
        assert p == tmp_path / 'veri' / name and p.is_dir()
    for bad in ('../x', 'secrets', 'parts/../x', ''):
        with pytest.raises(ValueError):
            paths.data_subdir(bad, env)
    for bad in ('göreli/yol', str(tmp_path / '..' / 'x')):
        with pytest.raises(ValueError):
            paths.data_dir({'HUB_DATA_DIR': bad})
    default = paths.data_dir({}, platform='linux')               # ortam değişkeni yoksa kullanıcı klasörü
    assert default.name == 'data' and default.parent.name == 'control_hub'
    assert paths.data_dir({'APPDATA': r'C:\Users\x\AppData\Roaming'}, platform='win32').name == 'data'


def test_contract_section9_documents_every_interface():
    """§9, S1/S2/P2/P4'ün kodladığı arayüzlerin hepsini adıyla taşır (biri silinirse kırılır)."""
    text = (APP / 'docs' / 'UI_CONTRACT.md').read_text(encoding='utf-8')
    section = text[text.index('## 9. Sayfalar arası arayüzler'):]
    needles = (
        '9-A', '9-B', '9-C', '9-D', 'snap.caps', 'snap.estop.latched', 'pos_m', 'rpy_deg', 'near_limit',
        '/api/move', '/api/move/check', '/api/move/stop', '/api/sim/reset', 'outside_safe_zone',
        'joint_limit', 'path_blocked', 'unreachable', 'estop_latched', 'HubPages.scene()', 'addObject',
        'onPick', 'enableGizmo', 'onGizmoChange', 'cameraPreset', 'setTrail', 'HUB_DATA_DIR',
        'data_subdir', 'safebox', 'styles', 'requires')
    for needle in needles:
        assert needle in section or needle in text, needle
    assert text.count('requires') >= 3 and "styles: ['/pages/<ad>.css']" in text
