import pytest

from bubble.errors import BubbleError
from bubble.geom import Camera
from bubble.params import RefineParams, apply_operator


def _cams():
    return (
        Camera("nadir", "nadir", (0, 0, 1), 0, -90, 40, 8, 8, None),
        Camera("fit_0", "fit", (0, 0, 1), 0, -20, 40, 8, 8, "refs/fit_0.png"),
        Camera("holdout_0", "holdout", (0, 0, 1), 0, -20, 40, 8, 8, "refs/holdout_0.png"),
    )


def test_one_field_changes():
    params = RefineParams()
    nxt = apply_operator(params, "set_opacity", "0.4", _cams())
    assert nxt.opacity == 0.4
    assert nxt.enabled_frames == params.enabled_frames
    assert nxt.fade_end_m == params.fade_end_m
    assert nxt.angle_power == 0.0


def test_enable_fit_and_reject_holdout():
    params = apply_operator(RefineParams(), "enable_frame", "fit_0", _cams())
    assert params.enabled_frames == ("fit_0",)
    again = apply_operator(params, "enable_frame", "fit_0", _cams())
    assert again == params
    with pytest.raises(BubbleError, match="cannot enable holdout"):
        apply_operator(params, "enable_frame", "holdout_0", _cams())


def test_disable_and_unknown_and_range():
    params = apply_operator(RefineParams(enabled_frames=("fit_0", "fit_1")), "disable_frame", "fit_0", _cams())
    assert params.enabled_frames == ("fit_1",)
    with pytest.raises(BubbleError, match="unknown operator"):
        apply_operator(RefineParams(), "sculpt_mesh", "1", _cams())
    with pytest.raises(BubbleError, match="opacity"):
        apply_operator(RefineParams(), "set_opacity", "1.5", _cams())
