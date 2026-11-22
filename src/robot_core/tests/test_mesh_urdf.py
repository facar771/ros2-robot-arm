"""C: CAD meshes in the URDF / RViz (karma_robot).

The URDF's visual = the link-frame CAD mesh (mesh_import), collision = one box per CAD part.
Everything else (inertial, limits, SRDF, safe box, view model) stays profile-only."""
import ast
import copy
import re
import xml.etree.ElementTree as ET

import pytest
import yaml
from robot_core.profile import ProfileError, load_profile
from robot_core.profile_gen import (
    PROFILE_PATH,
    generate,
    load_mesh_report,
    mesh_report_path,
    output_paths,
)

from tests.conftest import SRC

KARMA = SRC / 'robot_description' / 'profile' / 'karma_robot' / 'karma_robot.yaml'
PKG = SRC / 'robot_description'


@pytest.fixture(scope='module')
def profile():
    return load_profile(KARMA)


@pytest.fixture(scope='module')
def meshes(profile):
    return load_mesh_report(profile)


@pytest.fixture(scope='module')
def urdf(profile):
    return ET.fromstring((SRC / output_paths('karma_robot')['urdf']).read_text(encoding='utf-8'))


def test_every_link_wears_its_cad_mesh(profile, urdf):
    for link in profile.links:
        el = urdf.find(f"link[@name='{link.name}']")
        mesh = el.find('visual/geometry/mesh')
        assert mesh is not None, link.name
        uri = mesh.get('filename')
        assert uri == f'package://robot_description/meshes/karma_robot/link/{link.name}.stl'
        assert (PKG / uri.removeprefix('package://robot_description/')).is_file()


def test_collision_is_one_box_per_cad_part(profile, urdf):
    report = yaml.safe_load((SRC / mesh_report_path('karma_robot')).read_text(encoding='utf-8'))
    for link in profile.links:
        cols = urdf.findall(f"link[@name='{link.name}']/collision")
        parts = report['links'][link.name]['collision_boxes']
        assert len(cols) == len(parts) >= 1
        assert all(c.find('geometry/box') is not None for c in cols)
        assert all(c.find('geometry/mesh') is None for c in cols)     # never the heavy mesh


def test_inertial_still_comes_from_the_profile(profile, urdf):
    for link in profile.links:
        mass = urdf.find(f"link[@name='{link.name}']/inertial/mass")
        assert float(mass.get('value')) == pytest.approx(link.mass)


def test_meshes_change_only_the_urdf(profile, meshes):
    """A mesh is a picture: limits, SRDF, safe box, controllers and the view model ignore it."""
    plain, dressed = generate(profile), generate(profile, meshes)
    assert {k for k in plain if plain[k] != dressed[k]} == {'urdf'}


def test_six_axis_robot_has_no_mesh_report_and_is_unchanged():
    six = load_profile(PROFILE_PATH)
    assert load_mesh_report(six) is None
    assert generate(six, None) == generate(six)


# ---- the report is read fail-closed -----------------------------------------------------------
@pytest.fixture()
def fake_src(tmp_path):
    """A copy of the karma report + meshes under a temporary src/."""
    src = tmp_path / 'src'
    rel = mesh_report_path('karma_robot')
    (src / rel).parent.mkdir(parents=True)
    (src / rel).write_text((SRC / rel).read_text(encoding='utf-8'), encoding='utf-8')
    link_dir = src / 'robot_description' / 'meshes' / 'karma_robot' / 'link'
    link_dir.mkdir(parents=True)
    for p in (PKG / 'meshes' / 'karma_robot' / 'link').glob('*.stl'):
        (link_dir / p.name).write_bytes(p.read_bytes())
    return src


def _rewrite(src, mutate):
    path = src / mesh_report_path('karma_robot')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    mutate(data)
    path.write_text(yaml.safe_dump(data), encoding='utf-8')


def test_fake_src_copy_loads(profile, fake_src, meshes):
    assert load_mesh_report(profile, fake_src) == meshes


@pytest.mark.parametrize('mutate, match', [
    (lambda d: d.update(robot='six_axis_robot'), 'için değil'),
    (lambda d: d['links'].update(link_9=d['links']['link_1']), 'links.link_9'),
    (lambda d: d['links']['link_3'].update(mesh='meshes/karma_robot/link/yok.stl'), 'dosya yok'),
    (lambda d: d['links']['link_3'].update(mesh='../../../etc/passwd'), 'dosya yok'),
    (lambda d: d['links']['link_3'].update(collision_boxes=[]), 'çarpışma kutusu yok'),
    (lambda d: d['links']['link_3']['collision_boxes'][0].update(size=[0.1, 0, 0.1]),
     'bozuk kutu'),
    (lambda d: d['links']['link_3']['collision_boxes'][0].update(origin=[0, 0]), 'bozuk kutu'),
])
def test_bad_report_is_refused(profile, fake_src, mutate, match):
    _rewrite(fake_src, mutate)
    with pytest.raises(ProfileError, match=match):
        load_mesh_report(profile, fake_src)


def test_missing_mesh_file_is_refused(profile, fake_src):
    (fake_src / 'robot_description' / 'meshes' / 'karma_robot' / 'link' / 'link_4.stl').unlink()
    with pytest.raises(ProfileError, match='links.link_4.mesh'):
        load_mesh_report(profile, fake_src)


# ---- packaging: what the launch needs is installed --------------------------------------------
def test_cmake_installs_the_karma_urdf_home_meshes_and_rviz():
    cmake = (PKG / 'CMakeLists.txt').read_text(encoding='utf-8')
    assert re.search(r'install\(DIRECTORY urdf launch rviz ', cmake)
    assert 'profile/generated/karma_robot/robot.urdf' in cmake
    assert 'profile/generated/karma_robot/initial_positions.yaml' in cmake
    assert 'install(DIRECTORY meshes/karma_robot/link' in cmake
    assert 'meshes/karma_robot/cad' not in cmake.split('not installed')[-1]


def test_display_launch_has_a_robot_argument_defaulting_to_the_six_axis_robot():
    src = (PKG / 'launch' / 'display.launch.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    consts = {n.targets[0].id: n.value.value for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)}
    assert consts['DEFAULT_ROBOT'] == 'six_axis_robot'
    assert "DeclareLaunchArgument('robot'" in src and "'zeros'" in src


def test_rviz_config_shows_the_robot_model_in_base_link():
    cfg = yaml.safe_load((PKG / 'rviz' / 'karma_robot.rviz').read_text(encoding='utf-8'))
    vm = cfg['Visualization Manager']
    assert vm['Global Options']['Fixed Frame'] == 'base_link'
    model = next(d for d in vm['Displays'] if d['Class'] == 'rviz_default_plugins/RobotModel')
    assert model['Description Topic']['Value'] == '/robot_description' and model['Enabled']


def test_home_pose_for_the_sliders_is_inside_the_limits(profile):
    data = yaml.safe_load((SRC / output_paths('karma_robot')['initial_positions'])
                          .read_text(encoding='utf-8'))['initial_positions']
    for j in profile.joints:
        assert j.lower <= data[j.name] <= j.upper, j.name


def test_report_copy_is_not_mutated_by_tests(meshes):
    assert copy.deepcopy(meshes) == meshes
