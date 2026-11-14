from dataclasses import dataclass

import numpy as np

from bubble.compose import bake_texture
from bubble.errors import BubbleError
from bubble.geom import Camera, Grid


@dataclass(frozen=True)
class P:
    opacity: float = 1.0
    fade_start_m: float = 30.0
    fade_end_m: float = 40.0
    angle_power: float = 0.0
    luminance_gain: float = 1.0
    luminance_bias: float = 0.0
    enabled_frames: tuple = ("fit_0",)


def test_nadir_photo_paints_center_and_leaves_corner():
    dem = np.zeros((9, 9), np.float32)
    grid = Grid(-4, 4, 1, 0, 0)
    ortho = np.full((9, 9, 3), 100, np.uint8)
    cam = Camera("fit_0", "fit", (0, 0, 20), 0, -90, 20, 32, 32, "refs/fit_0.png")
    red = np.zeros((32, 32, 3), np.uint8)
    red[:] = (255, 0, 0)
    tex, touched = bake_texture(dem, ortho, grid, (cam,), {"fit_0": red}, P(), True)
    assert tex[4, 4].tolist() == [255, 0, 0]
    assert tex[0, 0].tolist() == [100, 100, 100]
    assert bool(touched[4, 4]) is True
    assert bool(touched[0, 0]) is False


def test_camera_under_the_mesh_paints_nothing():
    dem = np.zeros((9, 9), np.float32)
    grid = Grid(-4, 4, 1, 0, 0)
    ortho = np.full((9, 9, 3), 100, np.uint8)
    cam = Camera("fit_0", "fit", (0, 0, -5), 0, 90, 40, 32, 32, "x")
    red = np.zeros((32, 32, 3), np.uint8)
    red[:] = (255, 0, 0)
    params = P(fade_start_m=0.1, fade_end_m=30)
    tex, touched = bake_texture(dem, ortho, grid, (cam,), {"fit_0": red}, params, True)
    assert int(touched.sum()) == 0
    assert np.array_equal(tex, ortho)


def test_disabled_frames_match_ortho_bytes():
    dem = np.zeros((4, 4), np.float32)
    grid = Grid(0, 3, 1)
    ortho = np.full((4, 4, 3), 40, np.uint8)
    tex, touched = bake_texture(dem, ortho, grid, (), {}, P(enabled_frames=()), True)
    assert int(touched.sum()) == 0
    assert np.array_equal(tex, ortho)


def test_untrusted_poses_refuse_to_project():
    dem = np.zeros((4, 4), np.float32)
    grid = Grid(0, 3, 1)
    ortho = np.full((4, 4, 3), 40, np.uint8)
    cam = Camera("fit_0", "fit", (0, 0, 10), 0, -90, 40, 8, 8, "x")
    try:
        bake_texture(dem, ortho, grid, (cam,), {"fit_0": np.zeros((8, 8, 3), np.uint8)}, P(), False)
        raised = False
    except BubbleError as exc:
        raised = True
        assert "poses_trusted" in str(exc)
    assert raised
