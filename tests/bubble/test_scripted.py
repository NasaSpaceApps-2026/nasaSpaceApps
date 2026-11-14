# tests/bubble/test_scripted.py
from pathlib import Path
import json

import numpy as np
import pytest
from PIL import Image

from bubble.errors import BubbleError
from bubble.fixture import PEAK_COL, PEAK_ROW, make_fixture
from bubble.gltf import position_delta
from bubble.scripted import apply_judge, run_scripted


def test_nested_work_dir_is_refused(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(BubbleError, match="separate"):
        run_scripted(bundle, bundle / "out")
    assert bundle.is_dir()


def test_scripted_fixture_beats_the_ortho(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    work = tmp_path / "work"
    metrics = run_scripted(bundle, work)
    assert metrics["numeric_pass"] is True
    assert metrics["pass"] is True
    assert metrics["active_recommendation"] == "refined"
    assert metrics["human_verdict"] == "auto_gate"
    assert metrics["geometry_delta_m_max"] == 0.0
    assert metrics["outside_unchanged"] is True
    assert metrics["lpips"] is None
    assert metrics["threshold_ssim"] == 0.75
    assert len(metrics["iterations"]) == 3
    assert metrics["iterations"][1]["operator"] == "enable_frame"
    assert metrics["iterations"][1]["result"] == "better"
    assert position_delta(work / "baseline" / "mesh.glb", work / "refined" / "mesh.glb") == 0.0
    albedo = np.load(bundle / "albedo.npy")
    ortho = np.asarray(Image.open(bundle / "ortho.png").convert("RGB"))
    refined = np.asarray(Image.open(work / "refined" / "texture.png").convert("RGB"))
    peak = (PEAK_ROW, PEAK_COL)
    d_ref = np.linalg.norm(refined[peak].astype(float) - albedo[peak].astype(float))
    d_ort = np.linalg.norm(ortho[peak].astype(float) - albedo[peak].astype(float))
    assert d_ref + 40 <= d_ort
    assert np.array_equal(refined[0, 0], ortho[0, 0])
    block = json.loads((work / "bubble_block.json").read_text())
    assert block["active"] == "refined"
    assert block["baseline_mesh"] == "/bubble/baseline/mesh.glb"
    assert "enable_frame" in (work / "LOOP_STATE.md").read_text()
    for name in ("nadir", "fit_0", "holdout_0"):
        assert (work / "compare" / f"{name}.png").exists()


def test_human_gate_and_untrusted_poses(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    raw = json.loads((bundle / "viewpoints.json").read_text())
    raw["requires_human"] = True
    (bundle / "viewpoints.json").write_text(json.dumps(raw))
    work = tmp_path / "work"
    metrics = run_scripted(bundle, work)
    assert metrics["numeric_pass"] is True
    assert metrics["pass"] is False
    assert metrics["active_recommendation"] == "baseline"
    assert metrics["awaiting_human"] is True
    accepted = apply_judge(work, "accept")
    assert accepted["pass"] is True
    assert accepted["active_recommendation"] == "refined"
    assert json.loads((work / "bubble_block.json").read_text())["active"] == "refined"
    rejected = apply_judge(work, "reject")
    assert rejected["active_recommendation"] == "baseline"
    assert rejected["pass"] is False

    raw["poses_trusted"] = False
    raw["requires_human"] = False
    (bundle / "viewpoints.json").write_text(json.dumps(raw))
    work2 = tmp_path / "work2"
    metrics = run_scripted(bundle, work2)
    assert metrics["iterations"] == []
    assert metrics["pass"] is False
    assert "refusing to project" in (work2 / "LOOP_STATE.md").read_text()
    assert (work2 / "compare" / "fit_0.png").exists()
