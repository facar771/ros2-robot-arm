"""Generated files: up to date with the profile, identical to the original hand-written robot."""
import copy
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from robot_core.profile import load_profile, parse_profile
from robot_core.profile_gen import PROFILE_PATH, generate, main, output_paths

from robot_core import RobotConfig
from tests.conftest import CONFIG_DIR, SRC
from tests.robot_snapshot import snapshot

GOLDEN = Path(__file__).parent / 'golden' / 'today_robot.json'


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def generated(profile):
    return generate(profile)


def test_generated_files_are_up_to_date(profile, generated):
    """Fails when the profile or the generator changed without regenerating (or a generated file
    was edited by hand). Fix: python3 -m robot_core.profile_gen"""
    for key, rel in output_paths(profile.robot).items():
        assert (SRC / rel).read_text(encoding='utf-8') == generated[key], (
            f'{rel} güncel değil: python3 -m robot_core.profile_gen')


def test_every_generated_file_says_generated_do_not_edit(profile, generated):
    for key, text in generated.items():
        head = '\n'.join(text.splitlines()[:3])
        assert 'ÜRETİLDİ' in head and 'ELLE DÜZENLEME' in head, key


def test_check_mode_passes_on_the_committed_tree_and_fails_on_a_stale_one(tmp_path, capsys):
    assert main(['--check']) == 0
    stale = tmp_path / 'src'
    for rel in output_paths('six_axis_robot').values():
        target = stale / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((SRC / rel).read_text(encoding='utf-8'), encoding='utf-8')
    (stale / output_paths('six_axis_robot')['joint_limits']).write_text('edited by hand\n')
    assert main(['--check', '--src', str(stale)]) == 1
    assert 'ESKİ' in capsys.readouterr().out
    assert main(['--src', str(stale)]) == 0                  # writing repairs it
    assert main(['--check', '--src', str(stale)]) == 0


def test_cli_runs_as_a_module():
    out = subprocess.run([sys.executable, '-m', 'robot_core.profile_gen', '--check'],
                         capture_output=True, text=True, cwd=SRC / 'robot_core')
    assert out.returncode == 0, out.stdout + out.stderr


def test_generation_is_deterministic(profile):
    assert generate(profile) == generate(profile)


# ---- the proof: same robot as the original hand-written files --------------------------------
def test_generated_robot_equals_the_original_hand_written_robot():
    """Joints, limits, link geometry/inertia, forward kinematics at 11 configurations, collision
    matrix, poses, safe zone, initial positions and controller lists, compared with the snapshot
    taken from the hand-written files before they were replaced by generated ones."""
    now = snapshot(SRC / 'robot_description' / 'urdf' / 'robot.urdf',
                   CONFIG_DIR / 'six_axis_robot.srdf', CONFIG_DIR)
    golden = json.loads(GOLDEN.read_text(encoding='utf-8'))
    fk_now, fk_gold = now.pop('fk'), golden.pop('fk')
    assert now == golden                       # exact: same limits, offsets, ACM, scalings...
    assert len(fk_now) == len(fk_gold) == 11
    for a, b in zip(fk_now, fk_gold):
        assert a['q'] == b['q'] and a['frames'].keys() == b['frames'].keys()
        for link in a['frames']:
            assert a['frames'][link] == pytest.approx(b['frames'][link], abs=1e-12), link


def test_robot_config_reads_the_generated_files_like_before():
    cfg = RobotConfig.from_files(SRC / 'robot_description' / 'urdf' / 'robot.urdf',
                                 CONFIG_DIR / 'six_axis_robot.srdf')
    assert cfg.joint_names == tuple(f'joint_{i}' for i in range(1, 7))
    assert (cfg.base_frame, cfg.tip_link, cfg.planning_group) == ('base_link', 'link_6',
                                                                 'robot_arm')
    assert set(cfg.poses) == {'home', 'cnc', 'pc', 'dik'}
    assert cfg.limits['joint_3'].lower == -2.0 and cfg.limits['joint_3'].velocity == 1.0


def test_hand_written_wrapper_still_fits_the_generated_files(profile):
    wrapper = (CONFIG_DIR / f'{profile.robot}.urdf.xacro').read_text(encoding='utf-8')
    assert f'name="{profile.robot}"' in wrapper
    assert f'xacro:{profile.robot}_ros2_control' in wrapper
    assert f'{profile.robot}.ros2_control.xacro' in wrapper
    assert f'<joint name="{profile.mount[0]}" type="fixed">' in wrapper
    assert ET.parse(CONFIG_DIR / f'{profile.robot}.srdf').getroot().get('name') == profile.robot


# ---- a different robot really produces a different (and consistent) set of files --------------
def _changed(profile_path, mutate):
    data = yaml.safe_load(profile_path.read_text(encoding='utf-8'))
    mutate(copy.deepcopy(data))
    data = copy.deepcopy(data)
    mutate(data)
    return parse_profile(data)


def test_editing_a_link_length_moves_the_generated_robot(tmp_path):
    longer = _changed(PROFILE_PATH, lambda d: d['links']['link_3'].update(length=0.40))
    text = generate(longer)['urdf']
    root = ET.fromstring(text.split('?>', 1)[1])
    joint_4 = next(j for j in root.findall('joint') if j.get('name') == 'joint_4')
    assert joint_4.find('origin').get('xyz') == '0.0 0.0 0.4'
    link_3 = next(link for link in root.findall('link') if link.get('name') == 'link_3')
    assert link_3.find('collision/geometry/box').get('size') == '0.05 0.05 0.4'
    assert link_3.find('collision/origin').get('xyz') == '0.0 0.0 0.2'
    assert generate(longer)['urdf'] != generate(load_profile(PROFILE_PATH))['urdf']


def test_changed_axis_limit_pose_and_extra_pair_reach_every_generated_file():
    def mutate(d):
        d['joints']['joint_5']['axis'] = [1, 0, 0]
        d['joints']['joint_3']['limits'].update(position=[-1.9, 1.9])
        d['joints']['joint_2']['limits'].update(velocity=0.8)
        d['moveit']['named_poses']['up'] = [0, 0.5, 0, 0, 0, 0]
        d['moveit']['collision']['allow_extra'] = [['link_1', 'link_4', 'never touch']]
        d['safety']['max_velocity_scale'] = 0.25
    files = generate(_changed(PROFILE_PATH, mutate))
    assert 'axis xyz="1.0 0.0 0.0"' in files['urdf']
    assert 'lower="-1.9" upper="1.9"' in files['urdf']
    assert 'max_velocity: 0.8' in files['joint_limits']
    assert '<group_state name="up"' in files['srdf']
    assert 'link1="link_1" link2="link_4" reason="never touch"' in files['srdf']
    assert 'max_velocity_scale: 0.25' in files['safe_zone']
    cfg_srdf = ET.fromstring(files['srdf'].split('?>', 1)[1])
    assert len(cfg_srdf.findall('disable_collisions')) == 7


def test_generated_yaml_parses_and_lists_every_joint(profile, generated):
    for key in ('joint_limits', 'safe_zone', 'initial_positions', 'ros2_controllers',
                'moveit_controllers'):
        yaml.safe_load(generated[key])
    ctrl = yaml.safe_load(generated['ros2_controllers'])
    assert tuple(ctrl['arm_controller']['ros__parameters']['joints']) == profile.joint_names
    limits = yaml.safe_load(generated['joint_limits'])['joint_limits']
    assert all(isinstance(v['max_velocity'], float) and isinstance(v['max_acceleration'], float)
               for v in limits.values())
