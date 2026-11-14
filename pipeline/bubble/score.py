from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from bubble.errors import BubbleError
from bubble.gates import SSIM_MIN_PIXELS, Scores, mean_or_default
from bubble.render import MAGENTA

C1 = 0.01 ** 2
C2 = 0.03 ** 2


def luminance(rgb: np.ndarray) -> np.ndarray:
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0


def ssim_masked(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.uint8)
    b = np.asarray(b, dtype=np.uint8)
    mask = ~np.all(a == MAGENTA, axis=-1) & ~np.all(b == MAGENTA, axis=-1)
    if int(mask.sum()) < SSIM_MIN_PIXELS:
        return 0.0
    aa = luminance(a)[mask]
    bb = luminance(b)[mask]
    mu_a = float(aa.mean())
    mu_b = float(bb.mean())
    var_a = float(aa.var())
    var_b = float(bb.var())
    cov = float(((aa - mu_a) * (bb - mu_b)).mean())
    num = (2 * mu_a * mu_b + C1) * (2 * cov + C2)
    den = (mu_a ** 2 + mu_b ** 2 + C1) * (var_a + var_b + C2)
    return float(num / den)


def score_views(per_camera: dict[str, float], cameras) -> Scores:
    fit = [per_camera[cam.name] for cam in cameras if cam.role == "fit"]
    hold = [per_camera[cam.name] for cam in cameras if cam.role == "holdout"]
    return Scores(
        fit_ssim=mean_or_default(fit, 0.0),
        holdout_ssim=mean_or_default(hold, 1.0),
    )


def write_compare(path: Path, ref: np.ndarray, baseline: np.ndarray, refined: np.ndarray) -> None:
    if ref.shape != baseline.shape or ref.shape != refined.shape:
        raise BubbleError("compare panels must share a shape")
    strip = np.concatenate([ref, baseline, refined], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(strip, dtype=np.uint8), mode="RGB").save(path)
