from __future__ import annotations

from dataclasses import dataclass

MAX_TICKS = 5
FIT_IMPROVE = 0.05
FIT_WORSE = 0.01
HOLDOUT_DROP = 0.02
SSIM_MIN_PIXELS = 16
THRESHOLD_SSIM_RECORDED = 0.75


@dataclass(frozen=True)
class Scores:
    fit_ssim: float
    holdout_ssim: float


def mean_or_default(values: list[float], default: float) -> float:
    if not values:
        return default
    return float(sum(values) / len(values))


def decide_pass(
    baseline: Scores,
    best: Scores,
    geometry_delta_m: float,
    outside_unchanged: bool,
) -> bool:
    if geometry_delta_m != 0.0 or not outside_unchanged:
        return False
    if best.fit_ssim < baseline.fit_ssim + FIT_IMPROVE:
        return False
    if best.holdout_ssim < baseline.holdout_ssim - HOLDOUT_DROP:
        return False
    return True


def verdict(prev: Scores, new: Scores) -> str:
    if new.fit_ssim < prev.fit_ssim - FIT_WORSE:
        return "worse"
    if new.holdout_ssim < prev.holdout_ssim - HOLDOUT_DROP:
        return "worse"
    if new.fit_ssim > prev.fit_ssim + FIT_WORSE:
        return "better"
    return "same"
