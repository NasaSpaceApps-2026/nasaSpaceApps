from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from bubble.geom import Grid
from bubble.gltf import glb_png, glb_positions, position_delta, write_glb


def test_positions_are_y_up_around_the_bubble_center(tmp_path: Path):
    dem = np.full((2, 2), 5, np.float32)
    grid = Grid(x0=10, y0=20, res_m=1, center_x=10, center_y=20)
    texture = np.zeros((2, 2, 3), np.uint8)
    texture[0, 0] = (255, 0, 0)
    path = tmp_path / "mesh.glb"
    write_glb(path, dem, texture, grid)
    pos = glb_positions(path)
    assert np.allclose(pos[0], [0, 5, 0])
    assert np.allclose(pos[1], [1, 5, 0])
    assert np.allclose(pos[2], [0, 5, 1])
    assert np.allclose(pos[3], [1, 5, 1])
    image = np.asarray(Image.open(BytesIO(glb_png(path))).convert("RGB"))
    assert image[0, 0].tolist() == [255, 0, 0]
    assert path.read_bytes()[:4] == b"glTF"


def test_position_delta_is_zero_for_the_same_heights(tmp_path: Path):
    dem = np.arange(16, dtype=np.float32).reshape(4, 4)
    grid = Grid(0, 3, 1, 0, 0)
    gray = np.full((4, 4, 3), 8, np.uint8)
    red = gray.copy()
    red[:] = (200, 0, 0)
    a = tmp_path / "a.glb"
    b = tmp_path / "b.glb"
    write_glb(a, dem, gray, grid)
    write_glb(b, dem, red, grid)
    assert position_delta(a, b) == 0.0
    assert glb_png(a) != glb_png(b)
