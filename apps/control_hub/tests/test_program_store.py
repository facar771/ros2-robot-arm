"""P2 program modeli: katı doğrulama, JSON içe aktarma, dosya adı güvenliği, atomik kayıt, kuru çalıştırma."""
import hashlib
import json
import os

import pytest

from control_hub.hub_core import program_store as ps
from control_hub.hub_core.program_runner import dry_run, iter_actions, move_body


def prog(**kw):
    base = {'version': 1, 'name': 'Montaj 1', 'waypoints': [
        {'name': 'A', 'joints_deg': [0, 10, 20, 0, 0, 0], 'pos_m': [0.3, 0.0, 0.4], 'rpy_deg': [0, 0, 0]}],
        'steps': [{'kind': 'move_j', 'waypoint': 'A'}]}
    base.update(kw)
    return base


def paths(errs):
    return {e['path'] for e in errs}


@pytest.fixture
def store(tmp_path):
    return ps.ProgramStore(tmp_path)


# --- doğrulama -----------------------------------------------------------------------------------
def test_valid_program_round_trips_every_step_kind():
    steps = [
        {'kind': 'move_j', 'joints_deg': [0, 1, 2, 3, 4, 5]},
        {'kind': 'move_j', 'waypoint': 'A'},
        {'kind': 'move_l', 'pos_m': [0.3, 0, 0.4], 'rpy_deg': [0, 0, 0]},
        {'kind': 'move_l', 'waypoint': 'A', 'offset_m': [0, 0, 0.05]},
        {'kind': 'wait', 's': 1.5}, {'kind': 'tool', 'open_pct': 40}, {'kind': 'comment', 'text': 'not'},
        {'kind': 'loop', 'count': 3, 'steps': [{'kind': 'tool', 'open_pct': 0}, {'kind': 'wait', 's': 0}]},
    ]
    clean, errs = ps.validate_program(prog(steps=steps))
    assert errs == [] and clean['steps'][3]['offset_m'] == [0.0, 0.0, 0.05]
    assert ps.validate_program(clean)[0] == clean                      # kararlı (idempotent)


@pytest.mark.parametrize('bad_step', [
    {'kind': 'fly'}, {'kind': 'move_j'}, {'kind': 'move_j', 'joints_deg': [0] * 9},
    {'kind': 'move_j', 'joints_deg': [0, 'x']}, {'kind': 'move_j', 'joints_deg': [721]},
    {'kind': 'move_j', 'joints_deg': [0], 'waypoint': 'A'}, {'kind': 'move_j', 'waypoint': '../x'},
    {'kind': 'move_l', 'pos_m': [0, 0, 11], 'rpy_deg': [0, 0, 0]}, {'kind': 'move_l', 'pos_m': [0, 0, 0]},
    {'kind': 'move_l', 'waypoint': 'A', 'pos_m': [0, 0, 0], 'rpy_deg': [0, 0, 0]},
    {'kind': 'wait', 's': -1}, {'kind': 'wait', 's': 3601}, {'kind': 'wait', 's': True}, {'kind': 'wait'},
    {'kind': 'tool', 'open_pct': 101}, {'kind': 'tool', 'open_pct': None},
    {'kind': 'comment', 'text': 'x' * 201}, {'kind': 'comment', 'text': 'a\x00b'},
    {'kind': 'wait', 's': 1, 'extra': 1}, 'wait', 5, None,
    {'kind': 'loop', 'count': 0, 'steps': [{'kind': 'wait', 's': 1}]},
    {'kind': 'loop', 'count': 1.5, 'steps': [{'kind': 'wait', 's': 1}]},
    {'kind': 'loop', 'count': 1001, 'steps': [{'kind': 'wait', 's': 1}]},
    {'kind': 'loop', 'count': 2, 'steps': []},
    {'kind': 'loop', 'count': 2,
     'steps': [{'kind': 'loop', 'count': 2, 'steps': [{'kind': 'wait', 's': 1}]}]},
])
def test_bad_steps_are_rejected(bad_step):
    clean, errs = ps.validate_program(prog(steps=[bad_step]))
    assert clean is None and errs and all(e['message'] for e in errs)


def test_nan_and_infinity_never_pass():
    for text in ('{"kind":"wait","s":NaN}', '{"kind":"wait","s":Infinity}', '{"kind":"wait","s":-Infinity}'):
        doc = prog(steps=['@'])
        raw = json.dumps(doc).replace('"@"', text)
        with pytest.raises(ps.ProgramError) as exc:
            ps.parse_text(raw)
        assert exc.value.code == 'bad_json'
    clean, errs = ps.validate_program(prog(steps=[{'kind': 'wait', 's': float('nan')}]))     # Python tarafı
    assert clean is None and 'steps[0].s' in paths(errs)
    assert ps.validate_program(prog(steps=[{'kind': 'wait', 's': 10 ** 400}]))[0] is None     # taşma


def test_step_and_waypoint_limits():
    ok = [{'kind': 'comment', 'text': ''}] * ps.MAX_STEPS
    assert ps.validate_program(prog(steps=ok))[0] is not None
    assert 'steps' in paths(ps.validate_program(prog(steps=ok + [{'kind': 'comment'}]))[1])
    loop_heavy = [{'kind': 'loop', 'count': 1000, 'steps': [{'kind': 'wait', 's': 0}] * 30}] * 2  # 60 000
    assert 'steps' in paths(ps.validate_program(prog(steps=loop_heavy))[1])
    many = [{'name': f'p{i}', 'joints_deg': [0]} for i in range(ps.MAX_WAYPOINTS + 1)]
    assert 'waypoints' in paths(ps.validate_program(prog(waypoints=many))[1])


def test_waypoints_names_and_shape():
    dup = [{'name': 'A', 'joints_deg': [0]}, {'name': 'A', 'joints_deg': [1]}]
    assert 'waypoints' in paths(ps.validate_program(prog(waypoints=dup))[1])
    for bad in ({'name': 'A'}, {'name': 'A', 'pos_m': [0, 0, 0]}, {'name': '', 'joints_deg': [0]},
                {'name': 'A', 'joints_deg': [0], 'x': 1}, {'name': '../A', 'joints_deg': [0]}):
        assert ps.validate_program(prog(waypoints=[bad]))[0] is None, bad


def test_top_level_shape():
    assert ps.validate_program([])[0] is None and ps.validate_program(None)[0] is None
    for patch in ({'version': 2}, {'version': True}, {'name': ''}, {'name': 'a/b'}, {'name': '..'},
                  {'name': 'x' * 41}, {'steps': 'x'}, {'extra': 1}):
        assert ps.validate_program(prog(**patch))[0] is None, patch
    assert ps.validate_program(prog(name='Çıkış Pozu-2'))[0] is not None      # Türkçe harf serbest


def test_dangling_waypoint_is_saveable_but_flagged_by_dry_run():
    clean, errs = ps.validate_program(prog(steps=[{'kind': 'move_j', 'waypoint': 'Silinmiş'}]))
    assert errs == []
    res = dry_run(clean, lambda b: {'ok': True, 'violations': []}, 20)
    assert not res['ok'] and res['rows'][0]['violations'][0]['kind'] == 'missing_waypoint'


# --- içe aktarma ---------------------------------------------------------------------------------
def test_parse_text_rejects_garbage():
    big = 'x' * (ps.MAX_JSON_BYTES + 1)
    for text, code in (('', 'bad_json'), ('{', 'bad_json'), ('[]', 'invalid_program'),
                       ('{}', 'invalid_program'), (big, 'too_large'), ('[' * 5000, 'bad_json')):
        with pytest.raises(ps.ProgramError) as exc:
            ps.parse_text(text)
        assert exc.value.code == code, text[:20]
    assert ps.parse_text(json.dumps(prog()))['name'] == 'Montaj 1'


# --- ad / dosya yolu güvenliği -------------------------------------------------------------------
def test_slugify_is_ascii_and_never_a_path():
    assert ps.slugify('Çıkış Pozu 2') == 'cikis-pozu-2' and ps.slugify('İş Şöyle') == 'is-soyle'
    assert ps.slugify('../../etc/passwd') == 'etc-passwd'
    assert ps.slugify('...') == '' and ps.slugify('a' * 90) == 'a' * 40


@pytest.mark.parametrize('slug', ['../x', '..', 'a/b', 'A', '.hidden', '', 'a' * 41, 'x.json', '/etc/passwd',
                                  'a\\b', None, 5])
def test_bad_slugs_never_reach_the_filesystem(store, slug):
    with pytest.raises(ps.ProgramError) as exc:
        store.path_for(slug)
    assert exc.value.status == 400


def test_symlink_escape_is_blocked(store, tmp_path):
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside' / 'x.json').write_text('{}')
    store.root.mkdir(parents=True)
    try:
        os.symlink(tmp_path / 'outside', store.root / 'evil.json')   # evil.json -> dizin
    except OSError:
        pytest.skip('sembolik bağ kurulamadı')
    with pytest.raises(ps.ProgramError):
        store.path_for('evil')


# --- depo ----------------------------------------------------------------------------------------
def test_save_load_list_delete(store):
    slug = store.save(ps.validate_program(prog())[0])
    assert slug == 'montaj-1' and (store.root / 'montaj-1.json').is_file()
    assert store.load(slug)['name'] == 'Montaj 1'
    assert store.list() == [{'slug': 'montaj-1', 'name': 'Montaj 1', 'steps': 1, 'waypoints': 1,
                             'modified': pytest.approx((store.root / 'montaj-1.json').stat().st_mtime)}]
    assert store.save(ps.validate_program(prog(steps=[]))[0]) == slug          # aynı ad üzerine yazar
    assert store.load(slug)['steps'] == []
    store.delete(slug)
    assert store.list() == []
    for fn in (lambda: store.load(slug), lambda: store.delete(slug)):
        with pytest.raises(ps.ProgramError) as exc:
            fn()
        assert exc.value.status == 404


def test_write_is_atomic_and_leaves_no_temp_files(store, monkeypatch):
    good = ps.validate_program(prog())[0]
    store.save(good)
    before = (store.root / 'montaj-1.json').read_bytes()

    def boom(src, dst):
        raise OSError('disk dolu')
    monkeypatch.setattr(ps.os, 'replace', boom)
    with pytest.raises(ps.ProgramError):
        store.save(ps.validate_program(prog(steps=[]))[0])
    monkeypatch.undo()
    assert (store.root / 'montaj-1.json').read_bytes() == before                # eski dosya bozulmadı
    assert [p.name for p in store.root.iterdir()] == ['montaj-1.json']           # geçici dosya kalmadı


def test_save_never_touches_other_files(store, tmp_path):
    repo_like = tmp_path / 'repo.yaml'
    repo_like.write_text('a: 1')
    digest = hashlib.sha256(repo_like.read_bytes()).hexdigest()
    store.save(ps.validate_program(prog())[0])
    assert hashlib.sha256(repo_like.read_bytes()).hexdigest() == digest
    assert {p.name for p in tmp_path.iterdir()} == {'programs', 'repo.yaml'}


def test_corrupt_and_hand_edited_files(store):
    store.root.mkdir(parents=True)
    (store.root / 'bozuk.json').write_text('{"version": 1, "name": "x", "steps": [{"kind": "fly"}]}')
    (store.root / 'nan.json').write_text('{"version":1,"name":"n","steps":[{"kind":"wait","s":NaN}]}')
    (store.root / 'Ignored.json').write_text('{}')                                # geçersiz ad: listelenmez
    (store.root / '.tmp-1.part').write_text('x')
    assert {d['slug']: d.get('corrupt') for d in store.list()} == {'bozuk': True, 'nan': True}
    with pytest.raises(ps.ProgramError) as exc:
        store.load('bozuk')
    assert exc.value.code == 'corrupt' and exc.value.extra['errors']


def test_program_count_limit(store, monkeypatch):
    monkeypatch.setattr(ps, 'MAX_PROGRAMS', 2)
    for n in ('a', 'b'):
        store.save(ps.validate_program(prog(name=n))[0])
    with pytest.raises(ps.ProgramError) as exc:
        store.save(ps.validate_program(prog(name='c'))[0])
    assert exc.value.code == 'too_many'
    store.save(ps.validate_program(prog(name='a'))[0])                            # varolanı ezmek serbest


def test_data_dir_prefers_env(tmp_path):
    assert ps.data_dir({'HUB_DATA_DIR': str(tmp_path)}) == tmp_path
    assert ps.data_dir({}, user_dir=lambda: tmp_path).name == 'data'


def test_data_dir_takes_the_user_dir_from_outside_and_only_when_needed(tmp_path):
    """R7: hub_core ``paths``'i bilmez; kullanıcı klasörünü çağıran verir, ``HUB_DATA_DIR`` varsa sorulmaz."""
    asked = []

    def user_dir():
        asked.append(1)
        return tmp_path / 'kullanici'

    assert ps.data_dir({}, user_dir=user_dir) == tmp_path / 'kullanici' / 'data'
    assert asked == [1]
    assert ps.data_dir({'HUB_DATA_DIR': str(tmp_path / 'v')}, user_dir=user_dir) == tmp_path / 'v'
    assert asked == [1]                                         # env varken kullanıcı klasörü sorulmadı
    with pytest.raises(ValueError):                             # ne env ne enjeksiyon: sessiz tahmin YOK
        ps.data_dir({})


def test_store_without_base_uses_env_then_the_injected_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('HUB_DATA_DIR', str(tmp_path / 'env'))
    assert ps.ProgramStore().root == tmp_path / 'env' / 'programs'
    monkeypatch.delenv('HUB_DATA_DIR')
    assert ps.ProgramStore(user_dir=lambda: tmp_path / 'u').root == tmp_path / 'u' / 'data' / 'programs'
    assert ps.ProgramStore(tmp_path / 'acik').root == tmp_path / 'acik' / 'programs'   # base hepsinden önce


# --- yürütme modeli --------------------------------------------------------------------------------
def test_actions_expand_loops_and_skip_comments():
    clean, _ = ps.validate_program(prog(steps=[
        {'kind': 'comment', 'text': 'x'}, {'kind': 'tool', 'open_pct': 5},
        {'kind': 'loop', 'count': 2, 'steps': [{'kind': 'wait', 's': 1}, {'kind': 'comment'},
                                               {'kind': 'tool', 'open_pct': 9}]}]))
    got = [(a.line, a.sub, a.iteration, a.step['kind']) for a in iter_actions(clean)]
    assert got == [(1, None, None, 'tool'), (2, 0, 1, 'wait'), (2, 2, 1, 'tool'),
                   (2, 0, 2, 'wait'), (2, 2, 2, 'tool')]
    assert ps.count_actions(clean) == len(got)


def test_move_body_resolves_waypoints_and_offsets():
    wps = {'A': prog()['waypoints'][0], 'J': {'name': 'J', 'joints_deg': [1]}, 'P': {
        'name': 'P', 'pos_m': [0, 0, 0], 'rpy_deg': [0, 0, 0]}}
    assert move_body({'kind': 'move_j', 'waypoint': 'A'}, wps, 30)[0] == {
        'type': 'joint', 'joints_deg': [0, 10, 20, 0, 0, 0], 'speed_pct': 30}
    body = move_body({'kind': 'move_l', 'waypoint': 'A', 'offset_m': [0, 0, 0.05]}, wps, 30)[0]
    assert body == {'type': 'linear', 'pos_m': [0.3, 0.0, 0.45], 'rpy_deg': [0, 0, 0], 'speed_pct': 30}
    assert move_body({'kind': 'move_l', 'waypoint': 'J'}, wps, 30)[1]['kind'] == 'waypoint_no_pose'
    assert move_body({'kind': 'move_j', 'waypoint': 'P'}, wps, 30)[1]['kind'] == 'waypoint_no_joints'
    assert move_body({'kind': 'move_j', 'waypoint': 'Z'}, wps, 30)[1]['kind'] == 'missing_waypoint'
    assert move_body({'kind': 'tool', 'open_pct': 7}, wps, 30)[0] == {
        'type': 'tool', 'open_pct': 7, 'speed_pct': 30}


def test_dry_run_collects_row_by_row_and_survives_a_crashing_check():
    clean, _ = ps.validate_program(prog(steps=[
        {'kind': 'move_j', 'waypoint': 'A'}, {'kind': 'wait', 's': 1},
        {'kind': 'loop', 'count': 50,
         'steps': [{'kind': 'tool', 'open_pct': 1}, {'kind': 'tool', 'open_pct': 2}]}]))
    seen = []

    def check(body):
        seen.append(body['type'])
        if body['type'] == 'joint':
            return {'ok': False, 'violations': [{'kind': 'joint_limit', 'at_pct': None, 'message': 'x'}]}
        if body.get('open_pct') == 2:
            raise RuntimeError('çöktü')
        return {'ok': True, 'violations': []}
    res = dry_run(clean, check, 20)
    rows = [(r['line'], r['sub'], r['ok']) for r in res['rows']]
    assert rows == [(0, None, False), (2, 0, True), (2, 1, False)]
    assert res['violations'] == 2 and not res['ok'] and len(seen) == 3          # döngü gövdesi bir kez
    assert res['rows'][2]['violations'][0]['kind'] == 'check_failed'
    assert dry_run(clean, lambda b: {'ok': False}, 20)['rows'][0]['violations'][0]['kind'] == 'rejected'


# --- orientation:'free' (S1.1 / #108): yalnız konum hedefi ---------------------------------------------
FREE = {'kind': 'move_l', 'pos_m': [0.1, 0.0, 0.08], 'orientation': 'free'}


def test_move_l_with_free_orientation_is_valid_and_round_trips_without_rpy():
    clean, errs = ps.validate_program(prog(steps=[FREE]))
    assert errs == [] and clean['steps'] == [FREE]
    again, errs2 = ps.validate_program(clean)
    assert errs2 == [] and again == clean


def test_explicit_fixed_orientation_is_the_default_and_is_not_stored():
    step = {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'rpy_deg': [0, 0, 0], 'orientation': 'fixed'}
    clean, errs = ps.validate_program(prog(steps=[step]))
    assert errs == [] and 'orientation' not in clean['steps'][0] and clean['steps'][0]['rpy_deg'] == [0, 0, 0]


# sırayla: rpy ile birlikte olmaz, sabitte rpy şart, rpy yok ve serbest de değil, konum yok, kötü değer
@pytest.mark.parametrize('bad', [
    {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'rpy_deg': [0, 0, 0], 'orientation': 'free'},
    {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'orientation': 'fixed'},
    {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1]},
    {'kind': 'move_l', 'orientation': 'free'},
    {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'orientation': 'ters'},
    {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'orientation': True},
    {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'orientation': ['free']},
    {'kind': 'move_l', 'pos_m': [float('nan'), 0, 0.1], 'orientation': 'free'},
    {'kind': 'move_l', 'pos_m': [0.1, 0], 'orientation': 'free'},
    {'kind': 'move_l', 'pos_m': [99, 0, 0.1], 'orientation': 'free'},
    {'kind': 'move_j', 'joints_deg': [0, 0, 0], 'orientation': 'free'}])
def test_bad_orientation_combinations_are_rejected(bad):
    clean, errs = ps.validate_program(prog(steps=[bad]))
    assert clean is None and errs and all(e['message'] for e in errs)


def test_free_orientation_works_with_a_waypoint_and_offset():
    step = {'kind': 'move_l', 'waypoint': 'A', 'offset_m': [0, 0, 0.05], 'orientation': 'free'}
    clean, errs = ps.validate_program(prog(steps=[step]))
    assert errs == [] and clean['steps'][0]['orientation'] == 'free'
    wps = {'A': prog()['waypoints'][0]}
    body = move_body(clean['steps'][0], wps, 30)[0]
    assert body == {'type': 'linear', 'pos_m': [0.3, 0.0, 0.45], 'orientation': 'free', 'speed_pct': 30}
    assert 'rpy_deg' not in body                       # S1: orientation:'free' ile rpy_deg birlikte 400


def test_move_body_for_a_value_step_sends_orientation_free_and_never_rpy():
    assert move_body(FREE, {}, 40)[0] == {'type': 'linear', 'pos_m': [0.1, 0.0, 0.08],
                                          'orientation': 'free', 'speed_pct': 40}
    fixed = {'kind': 'move_l', 'pos_m': [0.1, 0, 0.1], 'rpy_deg': [1, 2, 3]}
    assert move_body(fixed, {}, 40)[0] == {'type': 'linear', 'pos_m': [0.1, 0, 0.1], 'rpy_deg': [1, 2, 3],
                                           'speed_pct': 40}      # eski davranış değişmedi


def test_the_mock_backend_understands_free_orientation_and_still_refuses_junk():
    from control_hub.hub_core.program_move import SimMoveClient
    m = SimMoveClient()
    assert m.check({'type': 'linear', 'pos_m': [0.4, 0.3, 0.1], 'orientation': 'free', 'speed_pct': 50})['ok']
    both = m.check({'type': 'linear', 'pos_m': [0.4, 0.3, 0.1], 'rpy_deg': [0, 0, 0], 'orientation': 'free',
                    'speed_pct': 50})
    assert not both['ok'] and both['violations'][0]['kind'] == 'bad_request'      # S1 ile aynı kural
    none = m.check({'type': 'linear', 'pos_m': [0.4, 0.3, 0.1], 'speed_pct': 50})
    assert not none['ok'] and none['violations'][0]['kind'] == 'bad_request'
    before = m.pose()['rpy_deg']
    job = m.start({'type': 'linear', 'pos_m': [0.4, 0.3, 0.1], 'orientation': 'free', 'speed_pct': 50})
    assert job.startswith('mock-') and m.pose()['rpy_deg'] == before              # yönelim değişmez, çökmez
