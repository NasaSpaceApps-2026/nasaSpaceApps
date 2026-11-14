import numpy as np

from bubble.geom import Camera, Grid
from bubble.render import MAGENTA, render_mesh


def test_nadir_center_reads_the_red_patch():
    dem = np.zeros((17, 17), np.float32)
    grid = Grid(-8, 8, 1)
    texture = np.zeros((17, 17, 3), np.uint8)
    texture[:] = (0, 255, 0)
    texture[6:11, 6:11] = (255, 0, 0)
    cam = Camera("n", "nadir", (0, 0, 30), 0, -90, 30, 48, 48, None)
    image = render_mesh(dem, texture, cam, grid)
    assert image[24, 24, 0] > 200
    assert image[24, 24, 1] < 40
