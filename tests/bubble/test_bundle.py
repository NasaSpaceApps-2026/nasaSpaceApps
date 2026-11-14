from pathlib import Path
import json

import numpy as np
import pytest
from PIL import Image

from bubble.bundle import load_bundle
from bubble.errors import BubbleError


def _write(path: Path, cameras: list[dict]) -> None:
    path.mkdir(parents=True)
    np.save(path / "dem.npy", np.zeros((4, 4), np.float32))
    Image.fromarray(np.full((4, 4, 3), 80, np.uint8)).save(path / "ortho.png")
    (path / "grid.json").write_text(json.dumps({
        "x0": -1.5, "y0": 1.5, "res_m": 1, "center_x": 0, "center_y": 0
    }))
    (path / "refs").mkdir()
    Image.fromarray(np.full((8, 8, 3), 10, np.uint8)).save(path / "refs" / "fit_0.png")
    (path / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": False,
        "registration_rms_m": 0.0,
        "bubble_id": "tiny",
        "cameras": cameras,
    }))


def _fit():
    return {
        "name": "fit_0", "role": "fit", "xyz": [0, 0, 2], "heading_deg": 0,
        "pitch_deg": -20, "fov_deg": 50, "width": 8, "height": 8, "image": "refs/fit_0.png",
    }


def _nadir():
    return {
        "name": "nadir", "role": "nadir", "xyz": [0, 0, 20], "heading_deg": 0,
        "pitch_deg": -90, "fov_deg": 40, "width": 8, "height": 8, "image": None,
    }


def test_loads_tiny_bundle(tmp_path: Path):
    bundle_dir = tmp_path / "b"
    _write(bundle_dir, [_nadir(), _fit()])
    bundle = load_bundle(bundle_dir)
    assert bundle.dem.shape == (4, 4)
    assert bundle.size_m == 3
    assert bundle.poses_trusted is True
    assert bundle.images["fit_0"].shape == (8, 8, 3)
    assert [cam.role for cam in bundle.cameras] == ["nadir", "fit"]


def test_requires_one_fit_camera(tmp_path: Path):
    bundle_dir = tmp_path / "b"
    _write(bundle_dir, [_nadir()])
    with pytest.raises(BubbleError, match="fit"):
        load_bundle(bundle_dir)
