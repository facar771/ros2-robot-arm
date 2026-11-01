from robot_core import CommandInterpreter, MotionResult
from tests.fakes import FakeMotion, FakeScene, FakeState, RecordingSink


def make(config, motion=None, scene=None, state=None, sink=None):
    out = []
    interp = CommandInterpreter(config, motion or FakeMotion(), scene or FakeScene(),
                                state or FakeState(), out=out.append, events=sink)
    return interp, out


def test_empty_line_and_exit(config):
    interp, out = make(config)
    assert interp.execute('   ') is True
    assert interp.execute('exit') is False
    assert out == []


def test_unknown_command(config):
    interp, out = make(config)
    assert interp.execute('fly') is True
    assert 'Geçersiz komut' in out[0]


def test_move_named_pose_calls_motion(config):
    motion = FakeMotion()
    interp, out = make(config, motion=motion)
    interp.execute('move home')
    assert motion.targets[0].label == 'home'
    assert any('Hedefe ulaşıldı' in line for line in out)


def test_move_invalid_never_reaches_motion(config):
    motion = FakeMotion()
    interp, out = make(config, motion=motion)
    interp.execute('move 0 9 0 0 0 0')
    assert motion.targets == []
    assert 'limit dışı' in out[0]


def test_failed_motion_prints_code_and_hint(config):
    result = MotionResult(ok=False, code=99999, code_name='FAILURE')
    interp, out = make(config, motion=FakeMotion(result))
    interp.execute('move cnc')
    assert any('FAILURE (99999)' in line for line in out)
    assert any('İpucu' in line for line in out)


def test_addbox_list_rmbox_clear(config):
    scene = FakeScene()
    interp, out = make(config, scene=scene)
    interp.execute('addbox a 0.3 0 0.3 0.1 0.1 0.1')
    interp.execute('addbox b 0 0 1 0.1 0.1 0.1')
    assert interp.obstacles == {'a', 'b'}
    assert scene.added[0].frame == 'base_link'
    interp.execute('rmbox a')
    assert interp.obstacles == {'b'}
    interp.execute('clear')
    assert interp.obstacles == set()
    assert scene.removed == ['a', 'b']


def test_addbox_validation(config):
    scene = FakeScene()
    interp, out = make(config, scene=scene)
    interp.execute('addbox a 0 0 0 0 1 1')      # zero size
    interp.execute('addbox a 0 0 0 x 1 1')      # not a number
    interp.execute('addbox a 0 0 0')            # wrong arity
    assert scene.added == []
    assert len(out) == 3 and all(line.startswith('[-]') for line in out)


def test_addbox_scene_failure_is_not_recorded(config):
    interp, out = make(config, scene=FakeScene(ok=False))
    interp.execute('addbox a 0 0 0 1 1 1')
    assert interp.obstacles == set()
    assert 'eklenemedi' in out[0]


def test_status(config):
    interp, out = make(config)
    interp.execute('status')
    assert 'Henüz' in out[0]
    positions = {name: 0.1234567 for name in config.joint_names}
    interp, out = make(config, state=FakeState(positions))
    interp.execute('status')
    assert '0.123' in out[0]


def test_events_are_emitted(config):
    sink = RecordingSink()
    interp, _ = make(config, sink=sink)
    interp.execute('addbox a 0 0 0 1 1 1')
    interp.execute('move home')
    interp.execute('bogus')
    kinds = [kind for kind, _ in sink.events]
    assert kinds == ['obstacle_added', 'motion_result', 'command_rejected']


def test_repl_stops_on_exit_and_eof(config):
    interp, out = make(config)
    lines = iter(['list', 'exit', 'list'])
    interp.repl(read=lambda prompt: next(lines))
    assert sum('Engeller' in line for line in out) == 1

    def eof(prompt):
        raise EOFError
    interp.repl(read=eof)


# ---- skills commands -----------------------------------------------------------------------
def _with_skills(config):
    from robot_core.models import ObjectInfo
    from robot_core.skills import Skills

    from tests.fakes import FakeObjects, FakePose
    red = ObjectInfo('obj_red_1', 'red', (0.35, 0.15, 0.03), (0.07, 0.07, 0.07))
    pose = FakePose()
    skills = Skills(config, FakeMotion(), pose, FakeObjects({red.name: red}))
    out = []
    return CommandInterpreter(config, FakeMotion(), FakeScene(), FakeState(), out=out.append,
                              skills=skills), pose, out


def test_objects_command_lists_detected_objects(config):
    cli, _, out = _with_skills(config)
    cli.execute('objects')
    assert any('obj_red_1' in line and '0.350' in line for line in out)


def test_above_and_goto_commands_call_the_skills(config):
    cli, pose, out = _with_skills(config)
    cli.execute('above red 0.15')
    cli.execute('goto 0.3 0.0 0.25')
    assert [round(t.z, 3) for t in pose.targets] == [0.03 + 0.035 + 0.15, 0.25]
    assert any(line.startswith('[+]') for line in out)


def test_skill_commands_validate_their_arguments(config):
    cli, pose, out = _with_skills(config)
    cli.execute('above')
    cli.execute('above red tall')
    cli.execute('goto 1 2')
    cli.execute('goto a b c')
    assert pose.targets == [] and sum(line.startswith('[-]') for line in out) == 4


def test_skill_commands_without_a_skills_layer_say_so(config):
    out = []
    cli = CommandInterpreter(config, FakeMotion(), FakeScene(), FakeState(), out=out.append)
    cli.execute('above red')
    assert 'Beceri katmanı bağlı değil' in out[0]
