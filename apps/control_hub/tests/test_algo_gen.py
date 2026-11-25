"""A1 Algoritmalar üreteçleri (saf işlevler): adım sayısı, koordinatlar, doğrulama, 500 adım sınırı.

Sayılar elle hesaplanır (üreteç mantığını yeniden yazmaz): beklenen değerler testte açıkça yazılıdır."""
import json
import math

import pytest

from control_hub.hub_core.algo_gen import SPECS, AlgoError, clean_params, generate, kinds_doc
from control_hub.hub_core.program_store import MAX_STEPS, validate_program

NAN, INF = float('nan'), float('inf')


def moves(g):
    return [s for s in g.steps if s['kind'] == 'move_l']


def pos_mm(step):
    return [round(v * 1000, 3) for v in step['pos_m']]


def is_valid_program(steps):
    clean, errs = validate_program({'version': 1, 'name': 'A', 'waypoints': [], 'steps': steps})
    return clean is not None, errs


# --- ızgara / palet pick-place ---------------------------------------------------------------------
def test_grid_step_counts_for_2_by_3():
    g = generate('grid', {'rows': 2, 'cols': 3})
    kinds = [s['kind'] for s in g.steps]
    assert len(g.steps) == 1 + 9 * 6 == 55                   # başta alet açma + hücre başına 9 adım
    assert kinds.count('move_l') == 6 * 6 and kinds.count('tool') == 1 + 2 * 6 and kinds.count('comment') == 6


def test_grid_single_cell_is_exactly_the_pick_and_place_sequence():
    g = generate('grid', {'rows': 1, 'cols': 1, 'pitch_mm': 20, 'approach_mm': 30,
                          'pick_x_mm': 120, 'pick_y_mm': -60, 'pick_z_mm': 50,
                          'place_x_mm': 100, 'place_y_mm': 0, 'place_z_mm': 50,
                          'open_pct': 100, 'grip_pct': 30})
    free = {'orientation': 'free'}
    assert g.steps == [
        {'kind': 'tool', 'open_pct': 100.0},
        {'kind': 'comment', 'text': 'Hücre 1: satır 1, sütun 1'},
        {'kind': 'move_l', 'pos_m': [0.12, -0.06, 0.08], **free},        # alma noktasının üstü
        {'kind': 'move_l', 'pos_m': [0.12, -0.06, 0.05], **free},        # alma
        {'kind': 'tool', 'open_pct': 30.0},                              # tut
        {'kind': 'move_l', 'pos_m': [0.12, -0.06, 0.08], **free},        # yukarı
        {'kind': 'move_l', 'pos_m': [0.10, 0.0, 0.08], **free},          # hücrenin üstü
        {'kind': 'move_l', 'pos_m': [0.10, 0.0, 0.05], **free},          # bırakma
        {'kind': 'tool', 'open_pct': 100.0},                             # bırak
        {'kind': 'move_l', 'pos_m': [0.10, 0.0, 0.08], **free},          # yukarı
    ]


def test_grid_cells_follow_rows_along_x_and_columns_along_y():
    g = generate('grid', {'rows': 2, 'cols': 3, 'pitch_mm': 25, 'place_x_mm': 100, 'place_y_mm': -20,
                          'place_z_mm': 40, 'approach_mm': 15})
    places = [pos_mm(g.steps[m['i']]) for m in g.marks if m['role'] == 'place']
    assert places == [[100, -20, 40], [100, 5, 40], [100, 30, 40],       # satır 1: y artar
                      [125, -20, 40], [125, 5, 40], [125, 30, 40]]       # satır 2: x + aralık
    assert [m['n'] for m in g.marks if m['role'] == 'place'] == [1, 2, 3, 4, 5, 6]


def test_grid_approach_height_is_added_to_z_on_every_approach_move():
    g = generate('grid', {'rows': 1, 'cols': 2, 'approach_mm': 45, 'pick_z_mm': 30, 'place_z_mm': 60})
    zs = sorted({pos_mm(s)[2] for s in moves(g)})
    assert zs == [30, 60, 75, 105]                                   # alma, bırakma ve üstleri (z + 45)


def test_grid_marks_the_pick_point_once_and_every_place_point():
    g = generate('grid', {'rows': 2, 'cols': 2})
    roles = [m['role'] for m in g.marks]
    assert roles.count('pick') == 1 and roles.count('place') == 4
    assert all(g.steps[m['i']]['kind'] == 'move_l' for m in g.marks)


def test_grid_tool_steps_use_the_given_percentages():
    g = generate('grid', {'rows': 1, 'cols': 1, 'open_pct': 80, 'grip_pct': 15})
    assert [s['open_pct'] for s in g.steps if s['kind'] == 'tool'] == [80.0, 15.0, 80.0]


# --- daire / yay -----------------------------------------------------------------------------------
@pytest.mark.parametrize('plane, axes', [('xy', (0, 1)), ('xz', (0, 2)), ('yz', (1, 2))])
def test_full_circle_points_lie_on_the_circle_and_close(plane, axes):
    g = generate('arc', {'plane': plane, 'center_x_mm': 140, 'center_y_mm': 10, 'center_z_mm': 90,
                         'radius_mm': 35, 'segments': 12, 'sweep_deg': 360})
    pts = [pos_mm(s) for s in g.steps]
    assert len(pts) == 13                                            # 12 parça = 13 nokta (kapanış dahil)
    c = [140, 10, 90]
    u, v = axes
    for p in pts:
        assert math.hypot(p[u] - c[u], p[v] - c[v]) == pytest.approx(35, abs=1e-3)
        w = ({0, 1, 2} - {u, v}).pop()
        assert p[w] == c[w]                                          # düzlem dışı eksen sabit
    assert pts[0] == pts[-1]                                         # tam daire ilk noktada kapanır


def test_quarter_arc_endpoints_and_direction():
    ccw = generate('arc', {'plane': 'xy', 'center_x_mm': 100, 'center_y_mm': 0, 'center_z_mm': 80,
                           'radius_mm': 40, 'start_deg': 0, 'sweep_deg': 90, 'segments': 3})
    assert [pos_mm(s) for s in ccw.steps][0] == [140, 0, 80]
    assert [pos_mm(s) for s in ccw.steps][-1] == [100, 40, 80]       # +90°: merkezin +y'sinde
    cw = generate('arc', {'plane': 'xy', 'center_x_mm': 100, 'center_y_mm': 0, 'center_z_mm': 80,
                          'radius_mm': 40, 'start_deg': 0, 'sweep_deg': -90, 'segments': 3})
    assert [pos_mm(s) for s in cw.steps][-1] == [100, -40, 80]       # −90°: ters yön
    assert [m['role'] for m in ccw.marks] == ['start', 'end']       # yayın iki ucu işaretlenir


def test_arc_start_angle_rotates_the_first_point():
    g = generate('arc', {'plane': 'xy', 'center_x_mm': 100, 'center_y_mm': 0, 'center_z_mm': 80,
                         'radius_mm': 40, 'start_deg': 90, 'sweep_deg': 180, 'segments': 6})
    assert pos_mm(g.steps[0]) == [100, 40, 80] and pos_mm(g.steps[-1]) == [100, -40, 80]   # 90° -> 270°


def test_arc_segment_count_sets_the_step_count():
    for n in (3, 7, 90, 180):
        assert len(generate('arc', {'segments': n}).steps) == n + 1


# --- zigzag tarama ---------------------------------------------------------------------------------
def test_zigzag_default_area_has_5_lines_and_10_moves_in_alternating_direction():
    g = generate('zigzag', {'axis': 'x', 'x0_mm': 100, 'y0_mm': -40, 'z_mm': 80, 'size_x_mm': 60,
                            'size_y_mm': 80, 'pitch_mm': 20})
    assert [pos_mm(s) for s in g.steps] == [
        [100, -40, 80], [160, -40, 80], [160, -20, 80], [100, -20, 80], [100, 0, 80],
        [160, 0, 80], [160, 20, 80], [100, 20, 80], [100, 40, 80], [160, 40, 80]]
    assert len(g.steps) == 2 * 5


def test_zigzag_last_line_never_leaves_the_area_when_pitch_does_not_divide():
    g = generate('zigzag', {'y0_mm': 0, 'size_y_mm': 70, 'pitch_mm': 20})
    ys = sorted({pos_mm(s)[1] for s in g.steps})
    assert ys == [0, 20, 40, 60] and max(ys) <= 70                  # 4 çizgi, son çizgi y=60


def test_zigzag_along_y_swaps_the_axes():
    g = generate('zigzag', {'axis': 'y', 'x0_mm': 100, 'y0_mm': -40, 'z_mm': 80, 'size_x_mm': 40,
                            'size_y_mm': 80, 'pitch_mm': 20})
    assert [pos_mm(s) for s in g.steps] == [
        [100, -40, 80], [100, 40, 80], [120, 40, 80], [120, -40, 80], [140, -40, 80], [140, 40, 80]]


def test_zigzag_pitch_larger_than_area_gives_a_single_line():
    g = generate('zigzag', {'size_x_mm': 30, 'size_y_mm': 10, 'pitch_mm': 50})
    assert len(g.steps) == 2 and pos_mm(g.steps[0])[1] == pos_mm(g.steps[1])[1]


def test_zigzag_marks_every_line_start():
    g = generate('zigzag', {})
    assert [m['n'] for m in g.marks] == [1, 2, 3, 4, 5] and all(m['role'] == 'line' for m in g.marks)


# --- ortak kurallar --------------------------------------------------------------------------------
@pytest.mark.parametrize('kind', list(SPECS))
def test_every_step_is_a_linear_move_with_free_orientation_and_no_rpy(kind):
    for s in moves(generate(kind, {})):
        assert s['orientation'] == 'free' and 'rpy_deg' not in s and len(s['pos_m']) == 3


@pytest.mark.parametrize('kind', list(SPECS))
def test_defaults_are_valid_and_json_serializable(kind):
    g = generate(kind, None)
    ok, errs = is_valid_program(g.steps)
    assert ok, errs
    json.dumps(g.steps), json.dumps(g.marks), json.dumps(g.points)


@pytest.mark.parametrize('kind, params', [
    ('grid', {'rows': 7, 'cols': 7, 'pitch_mm': 500}), ('grid', {'rows': 1, 'cols': 1}),
    ('arc', {'segments': 180, 'sweep_deg': -360, 'radius_mm': 500}), ('arc', {'plane': 'yz', 'sweep_deg': 1}),
    ('zigzag', {'size_x_mm': 1000, 'size_y_mm': 1000, 'pitch_mm': 10}), ('zigzag', {'axis': 'y'})])
def test_extreme_but_legal_parameters_still_produce_valid_program_steps(kind, params):
    g = generate(kind, params)
    ok, errs = is_valid_program(g.steps)
    assert ok, errs
    assert 0 < len(g.steps) <= MAX_STEPS


def test_coordinates_are_rounded_to_a_micrometre_and_never_negative_zero():
    g = generate('arc', {'center_x_mm': 0, 'center_y_mm': 0, 'center_z_mm': 0, 'radius_mm': 33.3333333,
                         'segments': 7})
    for s in g.steps:
        for v in s['pos_m']:
            assert v == round(v, 6) and not (v == 0 and math.copysign(1, v) < 0)


def test_points_list_every_move_step_by_index():
    g = generate('grid', {'rows': 1, 'cols': 1})
    assert [p['i'] for p in g.points] == [i for i, s in enumerate(g.steps) if s['kind'] == 'move_l']
    assert all(g.points[k]['pos_m'] == g.steps[p['i']]['pos_m'] for k, p in enumerate(g.points))


def test_generation_is_pure_the_input_is_not_changed_and_output_is_repeatable():
    params = {'rows': 2, 'cols': 2}
    before = json.dumps(params)
    a, b = generate('grid', params), generate('grid', params)
    assert json.dumps(params) == before and a.steps == b.steps and a.marks == b.marks
    a.steps[2]['pos_m'][0] = 9.0                                    # sonuç değişirse sonrakini etkilemez
    assert generate('grid', params).steps == b.steps


# --- doğrulama -------------------------------------------------------------------------------------
def err_fields(kind, params):
    with pytest.raises(AlgoError) as exc:
        generate(kind, params)
    return {e['field'] for e in exc.value.errors}, exc.value


@pytest.mark.parametrize('kind, field, bad', [
    ('grid', 'rows', 0), ('grid', 'rows', 21), ('grid', 'cols', 2.5), ('grid', 'pitch_mm', 0),
    ('grid', 'approach_mm', -1), ('grid', 'open_pct', 101), ('grid', 'pick_x_mm', 1001),
    ('arc', 'radius_mm', 0), ('arc', 'segments', 2), ('arc', 'segments', 181), ('arc', 'start_deg', 361),
    ('arc', 'sweep_deg', 0), ('arc', 'sweep_deg', 361), ('arc', 'plane', 'ab'), ('arc', 'plane', 1),
    ('zigzag', 'size_x_mm', 0), ('zigzag', 'pitch_mm', 501), ('zigzag', 'axis', 'z'),
    ('zigzag', 'z_mm', -1001)])
def test_out_of_range_or_wrong_type_is_rejected_with_the_field_name(kind, field, bad):
    fields, _ = err_fields(kind, {field: bad})
    assert fields == {field}


@pytest.mark.parametrize('bad', [NAN, INF, -INF, True, '5', None, [1], {'a': 1}])
def test_non_numbers_nan_and_infinity_are_rejected(bad):
    fields, _ = err_fields('grid', {'pitch_mm': bad})
    assert fields == {'pitch_mm'}


def test_unknown_fields_and_wrong_shapes_are_rejected():
    assert err_fields('grid', {'colz': 3})[0] == {'colz'}
    assert err_fields('grid', [1, 2])[0] == {'params'}
    assert err_fields('torus', {})[0] == {'kind'}


def test_all_errors_are_reported_together_not_just_the_first():
    fields, exc = err_fields('grid', {'rows': 0, 'cols': 99, 'pitch_mm': NAN})
    assert fields == {'rows', 'cols', 'pitch_mm'} and 'rows' in str(exc)


def test_missing_fields_take_the_documented_defaults():
    p = clean_params('grid', {'rows': 4})
    spec = {f.key: f.default for f in SPECS['grid']['fields'] if f.key != 'rows'}
    assert p['rows'] == 4 and {k: v for k, v in p.items() if k != 'rows'} == spec


def test_too_many_steps_is_refused_and_says_what_to_reduce():
    fields, exc = err_fields('grid', {'rows': 20, 'cols': 20})       # 1 + 9·400 adım
    assert fields == {''} and '3601' in str(exc) and 'satır/sütun' in str(exc)
    assert err_fields('zigzag', {'size_y_mm': 1000, 'pitch_mm': 1})[0] == {''}      # 1001 çizgi
    assert len(generate('grid', {'rows': 7, 'cols': 7}).steps) == 1 + 9 * 49 <= MAX_STEPS    # 442: içeride
    assert err_fields('grid', {'rows': 8, 'cols': 7})[0] == {''}     # 505 > 500


def test_kinds_doc_describes_every_field_with_a_valid_default():
    doc = kinds_doc()
    assert [k['id'] for k in doc] == ['grid', 'arc', 'zigzag']
    for k in doc:
        keys = [f['key'] for f in k['fields']]
        assert len(keys) == len(set(keys)) and k['title'] and k['about']
        clean_params(k['id'], {f['key']: f['default'] for f in k['fields']})      # varsayılanlar geçerli
        for f in k['fields']:
            assert f['label'] and ('options' in f or f['min'] <= f['default'] <= f['max'])
    json.dumps(doc)
