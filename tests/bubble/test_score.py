from pathlib import Path

import numpy as np
from PIL import Image

from bubble.geom import Camera
from bubble.score import score_views, ssim_masked, write_compare


def test_ssim_identical_is_one_and_black_white_is_low():
    rgb = np.zeros((32, 32, 3), np.uint8)
    rgb[:] = (20, 40, 60)
    assert ssim_masked(rgb, rgb.copy()) == 1.0
    white = np.full_like(rgb, 255)
    assert ssim_masked(np.zeros_like(rgb), white) < 0.1


def test_ssim_tiny_mask_is_zero():
    a = np.zeros((8, 8, 3), np.uint8)
    a[:] = (255, 0, 255)
    b = a.copy()
    a[0, 0] = (1, 2, 3)
    b[0, 0] = (1, 2, 3)
    assert ssim_masked(a, b) == 0.0


def test_score_views_skips_nadir_and_defaults_empty_holdout():
    cameras = (
        Camera("nadir", "nadir", (0, 0, 1), 0, -90, 40, 8, 8, None),
        Camera("fit_0", "fit", (0, 0, 1), 0, -20, 40, 8, 8, "a.png"),
    )
    scores = score_views({"fit_0": 0.4, "nadir": 0.0}, cameras)
    assert scores.fit_ssim == 0.4
    assert scores.holdout_ssim == 1.0


def test_compare_strip_is_three_panels(tmp_path: Path):
    panel = np.zeros((4, 5, 3), np.uint8)
    panel[:] = (1, 2, 3)
    other = panel.copy()
    other[:] = (4, 5, 6)
    path = tmp_path / "c.png"
    write_compare(path, panel, other, panel)
    strip = np.asarray(Image.open(path).convert("RGB"))
    assert strip.shape == (4, 15, 3)
    assert np.array_equal(strip[:, :5], panel)
    assert np.array_equal(strip[:, 5:10], other)
