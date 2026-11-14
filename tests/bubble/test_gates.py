from bubble.gates import (
    FIT_IMPROVE,
    HOLDOUT_DROP,
    THRESHOLD_SSIM_RECORDED,
    Scores,
    decide_pass,
    verdict,
)


def test_pass_ignores_recorded_ssim_threshold():
    base = Scores(0.20, 0.40)
    best = Scores(0.30, 0.39)
    assert best.fit_ssim < THRESHOLD_SSIM_RECORDED
    assert best.fit_ssim - base.fit_ssim >= FIT_IMPROVE
    assert base.holdout_ssim - best.holdout_ssim <= HOLDOUT_DROP
    assert decide_pass(base, best, 0.0, True) is True


def test_geometry_or_footprint_or_small_gain_fails():
    base = Scores(0.20, 0.40)
    assert decide_pass(base, Scores(0.90, 0.40), 0.01, True) is False
    assert decide_pass(base, Scores(0.90, 0.40), 0.0, False) is False
    assert decide_pass(base, Scores(0.90, 0.37), 0.0, True) is False
    assert decide_pass(base, Scores(0.24, 0.40), 0.0, True) is False


def test_verdict_labels():
    prev = Scores(0.50, 0.50)
    assert verdict(prev, Scores(0.52, 0.50)) == "better"
    assert verdict(prev, Scores(0.50, 0.50)) == "same"
    assert verdict(prev, Scores(0.48, 0.50)) == "worse"
    assert verdict(prev, Scores(0.50, 0.47)) == "worse"
