import dataclasses

import numpy as np
import pytest

from atlas_core.conditions import combine, compile_all, compile_condition, merge_profile
from atlas_core.errors import AtlasError
from atlas_core.site import Layer
from atlas_core.testing import make_site

from tests import oracle

SITE = make_site()


def rc(x, y):
    r, c = SITE.rc_index(x, y)
    return int(r), int(c)


HOLE = rc(0, -150)
NORTH_STRIP = rc(0, 250)

ALL_CONDS = [
    {"layer": "slope_2m", "op": "<=", "value": 1000},
    {"layer": "slope_2m", "op": ">=", "value": -1},
    {"layer": "slope_2m", "op": "<", "value": 1000},
    {"layer": "slope_2m", "op": ">", "value": -1},
    {"layer": "slope_2m", "op": "between", "value": [-1, 1000]},
    {"layer": "elevation", "op": "between", "value": [-1e9, 1e9]},
    {"layer": "geologic_unit", "op": "!=", "value": "crater_floor"},
    {"layer": "geologic_unit", "op": "not_in", "value": ["crater_floor", "scarp_block"]},
    {"layer": "geologic_unit", "op": "in", "value": ["plains", "crater_floor", "scarp_block"]},
    {"layer": "geologic_unit", "op": "==", "value": "plains"},
    {"evidence": "carbonate", "min_tier": 0},
    {"any_of": [{"layer": "geologic_unit", "op": "!=", "value": "plains"},
                {"evidence": "carbonate", "min_tier": "not_detected"}]},
]


@pytest.mark.parametrize("cond", ALL_CONDS, ids=lambda c: str(c)[:60])
def test_unknown_cells_fail_every_condition(cond):
    m = compile_condition(SITE, cond).mask
    assert not m[HOLE]
    assert not m[rc(-9, -149)]       # inside the hole (not just its centre)
    assert m.dtype == bool and m.shape == (256, 256)


@pytest.mark.parametrize("cond", [c for c in ALL_CONDS if "evidence" in c or "any_of" in c][:2])
def test_no_coverage_strip_fails_evidence(cond):
    assert not compile_condition(SITE, cond).mask[NORTH_STRIP]


def test_negated_ops_do_not_pass_unknown_but_pass_known():
    m = compile_condition(SITE, {"layer": "geologic_unit", "op": "!=", "value": "crater_floor"}).mask
    assert m[rc(0, 0)] and not m[rc(-120, 120)] and not m[HOLE]
    m = compile_condition(SITE, {"layer": "geologic_unit", "op": "not_in", "value": ["plains"]}).mask
    assert m[rc(-120, 120)] and not m[rc(0, 0)] and not m[HOLE]


def test_masks_match_oracle_over_whole_site():
    for cond in ALL_CONDS + [
        {"layer": "slope_2m", "op": "<=", "value": 5},
        {"layer": "rough_5x5", "op": ">", "value": 0.5},
        {"layer": "geologic_unit", "op": "==", "value": 2},
        {"evidence": "carbonate", "min_tier": "strong_orbital"},
        {"evidence": "carbonate", "min_tier": 4},
        {"any_of": [{"evidence": "carbonate", "min_tier": "weak_orbital"},
                    {"layer": "slope_2m", "op": ">=", "value": 30}]},
    ]:
        m = compile_condition(SITE, cond).mask
        expected = np.array(oracle.passing_grid(SITE, [cond]))
        assert (m == expected).all(), cond


def test_labels_and_source():
    c = compile_condition(SITE, {"layer": "slope_2m", "op": "<=", "value": 10, "source": "assumed"})
    assert c.label == "slope_2m <= 10" and c.source == "assumed"
    c = compile_condition(SITE, {"evidence": "carbonate", "min_tier": 3})
    assert c.label == "carbonate >= strong_orbital" and c.source is None


def test_combine():
    cs = compile_all(SITE, [{"layer": "slope_2m", "op": "<=", "value": 5},
                            {"layer": "geologic_unit", "op": "==", "value": "plains"}])
    both = combine(cs, (256, 256))
    assert (both == (cs[0].mask & cs[1].mask)).all()
    assert combine([], (3, 4)).all() and combine([], (3, 4)).shape == (3, 4)


def err(cond):
    with pytest.raises(AtlasError) as e:
        compile_condition(SITE, cond)
    assert e.value.hint or "." in e.value.message
    return e.value


def test_wrong_op_for_kind():
    e = err({"layer": "slope_2m", "op": "==", "value": 3})
    assert "<=" in e.hint
    e = err({"layer": "geologic_unit", "op": "<=", "value": 1})
    assert "not_in" in e.hint
    e = err({"layer": "carbonate", "op": ">=", "value": 3})
    assert "evidence" in e.hint
    e = err({"evidence": "slope_2m", "min_tier": 3})
    assert "slope_2m" in e.hint


def test_unknown_layer_class_tier_and_shape_errors():
    e = err({"layer": "slope", "op": "<=", "value": 3})
    assert "slope_2m" in e.hint
    e = err({"evidence": "olivine", "min_tier": 3})
    assert "carbonate" in e.hint
    e = err({"layer": "geologic_unit", "op": "==", "value": "dune"})
    assert "plains" in e.hint and "crater_floor" in e.hint
    e = err({"layer": "geologic_unit", "op": "in", "value": ["plains", "lava"]})
    assert "lava" in e.message
    e = err({"layer": "geologic_unit", "op": "==", "value": 9})
    assert "plains" in e.hint
    e = err({"evidence": "carbonate", "min_tier": "certain"})
    assert "strong_orbital" in e.hint
    err({"evidence": "carbonate", "min_tier": 5})
    err({"evidence": "carbonate", "min_tier": True})
    err({"layer": "slope_2m", "op": "between", "value": [5, 1]})
    err({"layer": "slope_2m", "op": "<=", "value": "ten"})
    err({"layer": "slope_2m", "op": "<=", "value": float("nan")})
    err({"layer": "geologic_unit", "op": "in", "value": "plains"})
    err({"any_of": []})
    err({"foo": 1})
    err({"layer": "slope_2m", "op": "<="})


def test_nan_threshold_and_inf_data_do_not_pass():
    d = SITE.layers["slope_2m"].data.copy()
    d[10, 10] = np.inf
    site = dataclasses.replace(SITE, layers={**SITE.layers, "slope_2m": Layer(
        "slope_2m", "continuous", d, SITE.layers["slope_2m"].meta)})
    m = compile_condition(site, {"layer": "slope_2m", "op": ">=", "value": 0}).mask
    assert not m[10, 10]


def test_merge_profile_and_override():
    conds, params = merge_profile(SITE, None, "landing_pad")
    assert [c["layer"] for c in conds] == ["slope_2m", "rough_5x5"]
    assert params["footprint_radius_m"] == {"value": 10, "source": "assumed"}
    assert "conditions" not in params
    explicit = [{"layer": "slope_2m", "op": "<=", "value": 2},
                {"evidence": "carbonate", "min_tier": 3}]
    conds, _ = merge_profile(SITE, explicit, "landing_pad")
    slopes = [c for c in conds if c.get("layer") == "slope_2m"]
    assert slopes == [explicit[0]]
    assert len(conds) == 3 and any(c.get("layer") == "rough_5x5" for c in conds)
    conds, params = merge_profile(SITE, explicit, None)
    assert conds == explicit and params == {}
    assert merge_profile(SITE, None, None) == ([], {})
    # profile without conditions
    conds, params = merge_profile(SITE, None, "rover_scout")
    assert conds == [] and "travel" in params


def test_merge_profile_does_not_mutate_inputs():
    explicit = [{"layer": "slope_2m", "op": "<=", "value": 2}]
    merge_profile(SITE, explicit, "landing_pad")
    assert SITE.profiles["landing_pad"]["conditions"][0]["value"] == 5
    assert explicit == [{"layer": "slope_2m", "op": "<=", "value": 2}]


def test_unknown_profile():
    with pytest.raises(AtlasError) as e:
        merge_profile(SITE, None, "moon_base")
    assert "landing_pad" in e.value.hint
