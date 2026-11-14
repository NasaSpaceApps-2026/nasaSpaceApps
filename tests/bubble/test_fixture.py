from pathlib import Path

import numpy as np
import pytest

from bubble.bundle import load_bundle
from bubble.fixture import PEAK_COL, PEAK_ROW, make_fixture
from bubble.geom import project_points


def test_mound_cameras_see_the_peak(tmp_path: Path):
    out = make_fixture(tmp_path / "bundle")
    bundle = load_bundle(out)
    assert bundle.bubble_id == "fixture_mound"
    assert bundle.requires_human is False
    assert bundle.poses_trusted is True
    assert (out / "albedo.npy").exists()
    peak_z = float(bundle.dem[PEAK_ROW, PEAK_COL])
    assert peak_z == pytest.approx(1.2159, abs=1e-3)
    x = bundle.grid.x0 + PEAK_COL * bundle.grid.res_m
    y = bundle.grid.y0 - PEAK_ROW * bundle.grid.res_m
    for cam in bundle.cameras:
        if cam.role == "nadir":
            continue
        u, v, _depth, valid = project_points(cam, x, y, peak_z)
        assert bool(valid)
        assert 0 <= float(u) < cam.width
        assert 0 <= float(v) < cam.height
    ortho = bundle.ortho.astype(float)
    albedo = np.load(out / "albedo.npy")
    # The blurred ortho is far from the checker at the peak.
    assert np.linalg.norm(ortho[PEAK_ROW, PEAK_COL] - albedo[PEAK_ROW, PEAK_COL]) > 80
