# tests/bubble/test_tick.py
from pathlib import Path
import json

import pytest

from bubble.errors import BubbleError
from bubble.fixture import make_fixture
from bubble.params import load_params
from bubble.tick import format_tick, run_tick
from bubble.gates import Scores


def test_format_tick_names_the_edit():
    text = format_tick(2, "enable_frame", "fit_0", Scores(0.1, 0.2), Scores(0.8, 0.4), "better", False)
    assert "Tick 2" in text
    assert "enable_frame fit_0" in text
    assert "better" in text


def test_enable_improves_and_black_gain_reverts(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    work = tmp_path / "work"
    run_tick(bundle, work, "set_opacity", "1")
    metrics = run_tick(bundle, work, "enable_frame", "fit_0")
    assert metrics["iterations"][1]["result"] == "better"
    assert metrics["iterations"][1]["reverted"] is False
    assert metrics["fit_ssim"] > metrics["baseline_fit_ssim"] + 0.05
    kept = metrics["fit_ssim"]
    metrics = run_tick(bundle, work, "set_luminance_gain", "0")
    assert metrics["iterations"][-1]["reverted"] is True
    assert metrics["iterations"][-1]["result"] == "worse"
    assert load_params(work / "params.json").luminance_gain == 1.0
    assert metrics["fit_ssim"] == pytest.approx(kept)
    assert "reverted" in (work / "LOOP_STATE.md").read_text()


def test_sixth_tick_is_refused(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    work = tmp_path / "work"
    run_tick(bundle, work, "set_opacity", "1")
    metrics = json.loads((work / "metrics.json").read_text())
    metrics["iterations"] = [{"tick": i, "operator": "set_opacity", "value": "1",
                              "fit_ssim": 0, "holdout_ssim": 1, "result": "same", "reverted": False}
                             for i in range(1, 6)]
    (work / "metrics.json").write_text(json.dumps(metrics))
    with pytest.raises(BubbleError, match="tick cap"):
        run_tick(bundle, work, "set_opacity", "1")
