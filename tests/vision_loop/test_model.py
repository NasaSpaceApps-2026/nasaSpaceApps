import numpy as np
import pytest

from bubble.geom import Grid
from vision_loop.errors import VisionError
from vision_loop.model import apply_ops, pop_last


def grid():
    return Grid(x0=-4, y0=4, res_m=1, center_x=0, center_y=0)


def test_bump_raises_the_center_and_leaves_the_corner():
    dem = np.zeros((9, 9), np.float32)
    texture = np.full((9, 9, 3), 100, np.uint8)
    height, color = apply_ops(dem, texture, grid(), [
        {"op": "bump", "x": 0, "y": 0, "radius_m": 2, "height_m": 0.5},
    ])
    assert height[4, 4] == pytest.approx(0.5, abs=1e-5)
    assert height[0, 0] == pytest.approx(0.0, abs=1e-5)
    assert np.array_equal(color, texture)


def test_paint_changes_color_and_not_height():
    dem = np.full((9, 9), 3, np.float32)
    texture = np.full((9, 9, 3), 20, np.uint8)
    height, color = apply_ops(dem, texture, grid(), [
        {"op": "paint", "x": 0, "y": 0, "radius_m": 1.5, "rgb": [255, 0, 0], "strength": 1},
    ])
    assert np.allclose(height, dem)
    assert color[4, 4].tolist() == [255, 0, 0]
    assert color[0, 0].tolist() == [20, 20, 20]


def test_oversized_bump_is_rejected():
    dem = np.zeros((5, 5), np.float32)
    texture = np.zeros((5, 5, 3), np.uint8)
    with pytest.raises(VisionError, match="height_m"):
        apply_ops(dem, texture, Grid(0, 4, 1), [
            {"op": "bump", "x": 0, "y": 0, "radius_m": 2, "height_m": 5},
        ])


def test_pop_last_removes_one_op():
    model = pop_last({"ops": [{"op": "bump"}, {"op": "paint"}]})
    assert model["ops"] == [{"op": "bump"}]
