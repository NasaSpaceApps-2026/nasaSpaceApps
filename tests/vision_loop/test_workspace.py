import json
from pathlib import Path

import numpy as np

from bubble.geom import Grid
from vision_loop.coverage import inferred_ops_on_high_coverage
from vision_loop.model import load_model, save_model
from vision_loop.workspace import init_workspace
from vision_loop.__main__ import check_inferred, gap_report, render_camera


def test_inferred_guess_cannot_sit_on_a_two_view_texel():
    dem = np.zeros((5, 5), np.float32)
    grid = Grid(x0=0, y0=4, res_m=1)
    count = np.zeros((5, 5), np.uint8)
    count[0, 0] = 2
    ops = [{
        "op": "paint", "x": 0, "y": 4, "radius_m": 1,
        "rgb": [1, 2, 3], "strength": 1, "inferred": True,
    }]
    problems = inferred_ops_on_high_coverage(ops, dem, grid, count)
    assert problems
    count[0, 0] = 1
    assert inferred_ops_on_high_coverage(ops, dem, grid, count) == []


def test_init_renders_the_same_fixture_the_projection_loop_uses(tmp_path: Path):
    work = tmp_path / "work"
    summary = init_workspace(Path("contracts/fixtures/bubble_source"), work)
    assert summary["sets"][0]["cameras"]
    assert "fit_0" in summary["sets"][0]["focus_order"]
    render_camera(work, "fit_0")
    compare = work / "compare" / "fit_0.png"
    assert compare.exists()
    assert (work / "mesh.glb").exists()
    model = load_model(work / "model.json")
    model["ops"].append({
        "op": "bump", "x": 4, "y": 2, "radius_m": 3, "height_m": 0.4, "inferred": False,
    })
    save_model(work / "model.json", model)
    render_camera(work, "fit_0")
    sets = json.loads((work / "sets.json").read_text())
    assert sets["max_images"] == 4
    assert sets["max_direction_deg"] == 35
    report = gap_report(work)
    assert 0.0 <= report["low_fraction"] <= 1.0
    assert (work / "compare" / "gap_north.png").exists()
    assert check_inferred(work) == []
