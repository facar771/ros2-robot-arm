from robot_core.workspace import Shape
from robot_vision.synth import render
from robot_vision.validate import main, run, to_markdown
from vision_support import WORLD, workspace

from robot_vision import ColorDetector, measure


def test_validation_report_passes_every_graded_row_and_writes_files(tmp_path):
    ws = workspace()
    extra = Shape('tall_box', 'box', (0.40, 0.05, 0.025), (0.08, 0.04, 0.05), (0.9, 0.1, 0.1))
    rows = run(ws, extra)
    graded = [r for r in rows if not r.informational]
    assert len(graded) >= 40
    assert [f'{r.scenario}/{r.obj}/{r.metric}' for r in graded if not r.ok] == []
    assert '| red_box |' in to_markdown(rows)
    assert main(['--out', str(tmp_path), '--world', str(WORLD)]) == 0
    assert (tmp_path / 'vision_v1_dogrulama.md').read_text(encoding='utf-8').startswith('# ')


def test_classification_is_stable_across_noise_seeds_up_to_3mm():
    ws = workspace()
    for seed in range(8):
        frame = render(ws.camera, ws.shapes, depth_noise=0.003, seed=seed)
        got = {s.label: measure(frame, s).shape for s in ColorDetector().detect(frame)}
        assert got == {'red': 'box', 'green': 'cylinder', 'blue': 'sphere'}, seed
