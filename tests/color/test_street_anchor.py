import numpy as np
from colorize import STREET_ANCHOR, colorize


def test_mid_grey_becomes_red_brown():
    gray = np.full((4, 4), 128, np.uint8)
    chroma = np.ones((4, 4, 3), np.float32)
    rgb = colorize(gray, chroma)
    mean = rgb.reshape(-1, 3).mean(axis=0)
    assert mean[0] > mean[1] > mean[2]
    assert mean[1] / mean[0] < 0.55
    assert np.allclose(STREET_ANCHOR, [1.00, 0.42, 0.28])