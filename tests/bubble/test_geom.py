import numpy as np
import pytest

from bubble.geom import Camera, Grid, dem_normals, project_points, visibility_mask, grid_xy


def _cam(**kwargs):
    base = dict(name="c", role="fit", xyz=(0.0, 0.0, 0.0), heading_deg=0.0,
                pitch_deg=0.0, fov_deg=90.0, width=100, height=80, image=None)
    base.update(kwargs)
    return Camera(**base)


def test_optical_axis_and_right_and_nadir():
    cam = _cam()
    u, v, depth, valid = project_points(cam, 0, 10, 0)
    assert bool(valid)
    assert float(depth) == pytest.approx(10)
    assert float(u) == pytest.approx(50)
    assert float(v) == pytest.approx(40)
    u, v, depth, valid = project_points(cam, 2, 10, 0)
    assert float(u) == pytest.approx(60)

    nadir = _cam(xyz=(0, 0, 10), pitch_deg=-90)
    u, v, depth, valid = project_points(nadir, 0, 0, 0)
    assert float(u) == pytest.approx(50)
    assert float(v) == pytest.approx(40)
    assert float(depth) == pytest.approx(10)
    u, v, depth, valid = project_points(nadir, 0, 2, 0)
    assert float(v) == pytest.approx(30)


def test_normals_flat_and_east_slope():
    nx, ny, nz = dem_normals(np.zeros((5, 5)), 1.0)
    assert np.allclose(nx, 0) and np.allclose(ny, 0) and np.allclose(nz, 1)
    slope = np.broadcast_to(0.5 * np.arange(7), (5, 7)).copy()
    nx, ny, nz = dem_normals(slope, 1.0)
    expect = -0.5 / np.hypot(0.5, 1.0)
    assert nx[2, 3] == pytest.approx(expect)
    assert ny[2, 3] == pytest.approx(0)


def test_grid_xy_is_dense_and_north_is_row_zero():
    grid = Grid(x0=-16, y0=16, res_m=1)
    x, y = grid_xy(grid, 33, 33)
    assert x.shape == (33, 33) and y.shape == (33, 33)
    assert x[0, 0] == pytest.approx(-16)
    assert y[0, 0] == pytest.approx(16)
    assert x[14, 20] == pytest.approx(4)
    assert y[14, 20] == pytest.approx(2)


def test_wall_occludes_far_texel_only():
    dem = np.zeros((21, 21))
    dem[:, 7:10] = 10
    grid = Grid(x0=-10, y0=10, res_m=1)
    x, y = grid_xy(grid, 21, 21)
    cam = _cam(xyz=(-8, 0, 1), heading_deg=90, pitch_deg=-5, fov_deg=40, width=32, height=32)
    vis = visibility_mask(cam, x, y, dem, dem, grid)
    assert bool(vis[10, 2]) is True
    assert bool(vis[10, 14]) is False
