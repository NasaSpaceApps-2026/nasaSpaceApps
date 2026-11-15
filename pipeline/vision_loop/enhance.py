"""Break up a smooth satellite picture with fine grain, without moving the big shapes.

The colour and the sand-ridge pattern stay the colourised HiRISE image. Close-up
Mastcam-Z frames are the references for the later enhance pass; copying their
spectrum onto the whole map combs the ground into stripes, so this step only
adds soft speckle.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _fine_grain(rows: int, cols: int) -> np.ndarray:
    """Soft speckle. Directional frequencies from a single photo turn into corduroy at map scale."""
    noise = np.random.default_rng(7).standard_normal((rows, cols)).astype(np.float32)
    soft = noise.copy()
    for shift in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        soft += np.roll(np.roll(noise, shift[0], axis=0), shift[1], axis=1)
    soft /= 5.0
    return soft / (float(soft.std()) + 1e-6)


def enhance_color(color: np.ndarray, photos: list[Path], gain: float = 0.12) -> np.ndarray:
    if not photos:
        return np.asarray(color, dtype=np.uint8)
    # The photographs are the references for the enhance pass. Here they only
    # confirm we have close-ups; the laid-in grain stays fine and directionless
    # so it does not comb the satellite picture.
    del photos
    rows, cols = color.shape[:2]
    field = _fine_grain(rows, cols)
    out = np.asarray(color, dtype=np.float32) * (1.0 + gain * field[..., None])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)
