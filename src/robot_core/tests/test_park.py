"""F4a-2 park exemption: ready <-> home only, verified joint values, separate non-generated file."""
import copy

import numpy as np
import pytest
import yaml
from robot_core.jog import DlsJogEngine
from robot_core.kinematics import Chain
from robot_core.pathcheck import PathError, check_park_move, check_path, transit_extent
from robot_core.profile import load_profile, parse_profile
from robot_core.profile_gen import PROFILE_PATH, generate, output_paths
from robot_core.safezone import (
    PARK_MARGIN,
    ParkConfigError,
    ParkZone,
    SafetyConfig,
    SafetyConfigError,
    load_park,
    load_safety,
    parse_park,
)

from tests.conftest import CONFIG_DIR, SRC

PARK_FILE = CONFIG_DIR / 'safe_zone.park.yaml'


@pytest.fixture(scope='module')
def profile():
    return load_profile(PROFILE_PATH)


@pytest.fixture(scope='module')
def park():
    return load_park(PARK_FILE)


@pytest.fixture(scope='module')
def raw_park():
    return yaml.safe_load(PARK_FILE.read_text(encoding='utf-8'))


# ---- the move itself ----------------------------------------------------------------------------
def test_ready_to_home_passes_and_home_to_ready_passes(profile, park):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    out = check_park_move([ready, home], profile, park)
    assert out.ok and out.samples > 100
    assert check_park_move([home, ready], profile, park).ok
    assert not check_path([ready, home], profile).ok               # the inner box still refuses it
    near = list(ready)
    near[0] += 0.015                                               # starts 0.015 rad off ready
    assert check_park_move([near, home], profile, park).ok


def test_random_point_to_home_is_rejected(profile, park):
    home = profile.named_poses['home']
    rng = np.random.default_rng(4)
    for _ in range(30):
        start = np.array(profile.named_poses['ready']) + rng.uniform(-1.0, 1.0, 6)
        out = check_park_move([start, home], profile, park)
        assert not out.ok and "bir ucu 'ready'" in out.reason
    assert not check_park_move([np.zeros(6), home], profile, park).ok          # home -> home
    far = list(profile.named_poses['ready'])
    far[0] += 0.05                                                  # 0.05 rad > 0.02 tolerance
    assert not check_park_move([far, home], profile, park).ok


def test_only_the_exact_named_pose_is_a_goal(profile, park):
    ready, home = np.array(profile.named_poses['ready']), np.array(profile.named_poses['home'])
    almost_home = home + np.array([0.0, 0.0, 0.001, 0.0, 0.0, 0.0])
    assert not check_park_move([ready, almost_home], profile, park).ok
    for other in ('cnc', 'pc', 'dik'):                              # only `home` is parkable
        out = check_park_move([ready, profile.named_poses[other]], profile, park, pose=other)
        assert not out.ok and 'park listesinde değil' in out.reason
    with pytest.raises(PathError):
        check_park_move([ready, home, ready], profile, park)        # exactly two points


def test_a_changed_home_or_ready_in_the_profile_is_not_exempt(profile, park):
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding='utf-8'))
    for pose in ('home', 'ready'):
        data = copy.deepcopy(raw)
        values = list(data['moveit']['named_poses'][pose]) if isinstance(
            data['moveit']['named_poses'][pose], list) else None
        if values is None:                                          # {joint: value} mapping
            data['moveit']['named_poses'][pose]['joint_5'] += 0.01
        else:
            values[4] += 0.01
            data['moveit']['named_poses'][pose] = values
        changed = parse_profile(data)
        ready, home = changed.named_poses['ready'], changed.named_poses['home']
        out = check_park_move([ready, home], changed, park)
        assert not out.ok and pose in out.reason
        assert 'doğrulanmış eklem değerinden farklı' in out.reason


def test_park_envelope_is_the_measured_transit_plus_exactly_5_cm(profile, park):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    assert PARK_MARGIN == 0.05
    measured = transit_extent([ready, home], profile)               # re-derived, not trusted
    assert tuple(map(tuple, park.measured)) == measured
    assert park.zone.lower == tuple(round(lo - 0.05, 4) for lo, _ in measured)
    assert park.zone.upper == tuple(round(hi + 0.05, 4) for _, hi in measured)
    assert park.max_velocity_fraction == 0.3 and park.ready_tolerance == 0.02
    assert set(park.poses) == {'home'}
    assert park.poses['home'] == tuple(home) and park.ready_joints == tuple(ready)
    assert not check_park_move([ready, home], profile, park, max_step=0.005).violation


def test_envelope_is_not_wide_enough_for_arbitrary_detours(profile, park):
    """A path that wanders far from the verified transit leaves the envelope even if its ends are
    right: here ready -> (detour) is not even a valid park move, and the envelope excludes the
    table-level region the inner box is for."""
    z = park.zone
    assert z.lower[2] > 0.4 and z.upper[0] < 0.7                  # nothing like the inner box
    assert z.lower[1] == pytest.approx(-0.05) and z.upper[1] == pytest.approx(0.05)


# ---- the file is strict -------------------------------------------------------------------------
def test_park_file_schema_is_strict(raw_park):
    parse_park(raw_park)
    cases = {
        'margin': lambda d: d['transit'].__setitem__('margin', 0.06),
        'tolerance': lambda d: d.__setitem__('ready_tolerance', 0.05),
        'speed': lambda d: d.__setitem__('max_velocity_fraction', 0.5),
        'extra pose': lambda d: d['poses'].__setitem__('cnc', {'joints': [0.0] * 6}),
        'no home': lambda d: d['poses'].clear(),
        'extra key': lambda d: d.__setitem__('tolerance', 0.1),
        'missing key': lambda d: d.pop('transit'),
        'pose key': lambda d: d['poses']['home'].__setitem__('box', 1),
        'version': lambda d: d.__setitem__('park_version', 2),
        'nan': lambda d: d['ready_joints'].__setitem__(0, float('nan')),
        'joint count': lambda d: d['poses']['home'].__setitem__('joints', [0.0] * 5),
    }
    for name, mutate in cases.items():
        data = copy.deepcopy(raw_park)
        mutate(data)
        with pytest.raises(ParkConfigError):
            parse_park(data)
        assert name


# ---- nothing else can use the park envelope -----------------------------------------------------
def test_jog_waypoint_and_goto_paths_cannot_use_the_park_envelope(profile, park):
    ready, home = profile.named_poses['ready'], profile.named_poses['home']
    assert isinstance(park.zone, ParkZone)
    with pytest.raises(PathError):                                  # check_path refuses ParkZone
        check_path([ready, home], profile, box=park.zone)
    with pytest.raises(PathError):                                  # nor any non-SafeZone object
        check_path([ready, home], profile, box=park)
    with pytest.raises(SafetyConfigError):                          # the jog / goal safety config
        SafetyConfig(park.zone, 0.5)
    with pytest.raises(SafetyConfigError):
        load_safety(PARK_FILE)                                      # not a safe_zone file
    with pytest.raises(ParkConfigError):
        load_park(CONFIG_DIR / 'safe_zone.yaml')                    # and vice versa
    engine_safety = profile.safety
    assert not isinstance(engine_safety.zone, ParkZone)
    DlsJogEngine(Chain(profile), engine_safety)                     # normal construction works


def test_profile_gen_neither_produces_nor_changes_the_park_file(profile):
    before = PARK_FILE.read_bytes()
    assert PARK_FILE.name not in ' '.join(output_paths('six_axis_robot').values())
    produced = generate(profile)
    assert all('park' not in text.lower() for text in produced.values())
    from robot_core.profile_gen import main
    assert main(['--check']) == 0
    assert PARK_FILE.read_bytes() == before


def test_hub_and_profile_path_cannot_write_the_exemption():
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding='utf-8'))
    for key in ('park', 'park_exemption', 'safe_zone_park'):
        data = copy.deepcopy(raw)
        data[key] = {'home': {'joints': [0.0] * 6}}
        with pytest.raises(Exception, match='bilinmeyen'):
            parse_profile(data)
    data = copy.deepcopy(raw)
    data['safety'] = {**data['safety'], 'park': {'home': [0.0] * 6}}
    with pytest.raises(Exception, match='bilinmeyen|park'):
        parse_profile(data)
    for module in ('profile.py', 'profile_gen.py', 'jog.py', 'frames.py', 'hardware_gen.py'):
        text = (SRC / 'robot_core' / 'robot_core' / module).read_text(encoding='utf-8')
        assert 'safe_zone.park' not in text and 'load_park' not in text and 'ParkConfig' not in text
    assert 'park' not in PROFILE_PATH.read_text(encoding='utf-8').lower()
