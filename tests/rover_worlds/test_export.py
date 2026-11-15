import json
from pathlib import Path

import numpy as np
from PIL import Image

from export_rover_worlds import decode_rgb, export_worlds

REPO = Path(__file__).resolve().parents[2]


def _center(world: Path) -> float:
    rgb = np.array(Image.open(world / "tiles" / "height" / "0" / "0" / "0.png"))
    return float(decode_rgb(rgb)[128, 128])


def test_each_world_keeps_its_own_centre_height(tmp_path: Path):
    export_worlds(REPO, tmp_path)
    dem = np.load(REPO / "contracts" / "fixtures" / "jezero_sol9" / "dem.npy")
    header = json.loads((REPO / "contracts" / "fixtures" / "jezero_sol9_vision" / "drive" / "height.json").read_text())
    raw = np.fromfile(REPO / "contracts" / "fixtures" / "jezero_sol9_vision" / "drive" / "height.u16", dtype="<u2")
    vision = header["base"] + 0.01 * raw.reshape(header["height"], header["width"])
    assert abs(_center(tmp_path / "projection") - float(dem[40, 40])) < 0.01
    assert abs(_center(tmp_path / "vision") - float(vision[40, 40])) < 0.01
    proj = np.array(Image.open(tmp_path / "projection" / "tiles" / "ortho" / "0" / "0" / "0.webp"))
    vis = np.array(Image.open(tmp_path / "vision" / "tiles" / "ortho" / "0" / "0" / "0.webp"))
    assert proj.shape == (512, 512, 3)
    assert vis.shape == (512, 512, 3)
    assert not np.array_equal(proj, vis)
