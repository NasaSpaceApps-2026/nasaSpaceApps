"""find_locations / find_regions (atlas_core.search) against the brute-force oracle in tests/oracle.py, plus the `within` reachability filter."""
import dataclasses
import math
import time

import numpy as np
import pytest
from scipy import ndimage

from atlas_core import search, travel
from atlas_core.errors import AtlasError
from atlas_core.sets import SetStore
from atlas_core.site import Layer, Site
from atlas_core.testing import make_site

from tests import oracle

SITE = make_site()
SLOPE5 = [{"layer": "slope_2m", "op": "<=", "value": 5}]
CARB3 = [{"evidence": "carbonate", "min_tier": "strong_orbital"}]


def locs(site=SITE, **kw):
    store = kw.pop("store", None) or SetStore()
    kw.setdefault("limit", 50)
    r = search.find_locations(site, store, **kw)
    return r, store.get(r["set_id"])


def rc_of(site, m):
    r, c = site.rc_index(m["x"], m["y"])
    return int(r), int(c)


def window_site(site, r0, c0, n, slope=None, elev=None):
    layers = {}
    for lid, L in site.layers.items():
        d = L.data[r0:r0 + n, c0:c0 + n].copy()
        if lid == "slope_2m" and slope is not None:
            d = slope.astype(np.float32)
        if lid == "elevation" and elev is not None:
            d = elev.astype(np.float32)
        layers[lid] = Layer(lid, L.kind, d, L.meta, L.classes)
    return dataclasses.replace(site, layers=layers, size_px=n)


# ---------------------------------------------------------------- properties against the oracle

def test_spots_footprint_property_and_spacing():
    conds = SLOPE5 + [{"layer": "geologic_unit", "op": "!=", "value": "scarp_block"}]
    R, spacing = 8, 30
    r, s = locs(conditions=conds, footprint_radius_m=R, spacing_m=spacing)
    assert s.count > 5
    passing = oracle.passing_grid(SITE, conds)
    for m in s.members:
        rr, cc = rc_of(SITE, m)
        assert oracle.footprint_valid(SITE, passing, rr, cc, R)
        for (pr, pc) in oracle.footprint_cells(SITE, rr, cc, R):
            assert passing[pr][pc]
    for i, a in enumerate(s.members):
        for b in s.members[i + 1:]:
            assert math.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= spacing - 1e-9


def test_valid_centres_and_scores_match_oracle_exhaustively():
    R = 6
    # window covering the crater wall (rows 20..110) and the plains to its east, incl. the grid edge
    site = window_site(SITE, 20, 40, 90)
    r, s = locs(site=site, conditions=SLOPE5, footprint_radius_m=R, spacing_m=1.0)   # no thinning
    fast = {rc_of(site, m): m for m in s.members}
    expected = oracle.valid_centres(site, SLOPE5, R)
    assert 100 < len(expected) < 90 * 90 - 500        # the window has both valid and invalid ground
    assert set(fast) == set(expected)
    for (rr, cc), m in list(fast.items())[::37]:
        assert m["score"] == pytest.approx(oracle.flattest_score(site, rr, cc, R), abs=1e-4)


def test_spreading_and_order_match_oracle_on_real_windows():
    rng = np.random.default_rng(7)
    for trial in range(6):
        n = 36
        r0, c0 = (int(v) for v in rng.integers(0, 256 - n, 2))
        slope = rng.uniform(0, 12, (n, n))
        if trial % 2:
            slope[rng.random((n, n)) < 0.03] = np.nan
        site = window_site(SITE, r0, c0, n, slope=slope)
        thr = float(rng.uniform(5, 11))
        R = float(rng.choice([0, 3, 4.5, 6]))
        spacing = float(rng.choice([2, 10, 16]))
        conds = [{"layer": "slope_2m", "op": "<=", "value": thr}]
        r, s = locs(site=site, conditions=conds, footprint_radius_m=R, spacing_m=spacing)
        exp = oracle.find_flattest(site, conds, R, spacing)
        got = [(*rc_of(site, m), m["score"]) for m in s.members]
        assert [(a, b) for a, b, _ in got] == [(a, b) for a, b, _ in exp], trial
        for (_, _, sg), (_, _, se) in zip(got, exp):
            assert sg == pytest.approx(se, abs=1e-3)
        # diagnostics fractions match the oracle
        n_all = len(oracle.valid_centres(site, conds, 0))
        assert r["diagnostics"][1]["pass_fraction"] == pytest.approx(n_all / n ** 2)
        assert r["diagnostics"][2]["pass_fraction"] == pytest.approx(
            len(oracle.valid_centres(site, conds, R)) / n ** 2)


def test_clear_radius_matches_brute_force():
    r, s = locs(conditions=SLOPE5, footprint_radius_m=10, spacing_m=100)
    passing = oracle.passing_grid(SITE, SLOPE5)
    for m in s.members[:4]:
        rr, cc = rc_of(SITE, m)
        assert m["clear_radius_m"] == pytest.approx(min(oracle.clear_radius(SITE, passing, rr, cc), 1000), abs=1e-3)
        assert m["clear_radius_m"] >= 10


def test_no_spot_on_ridge_or_crater_walls():
    r, s = locs(conditions=SLOPE5, footprint_radius_m=5, spacing_m=4)
    assert s.count > 20
    for m in s.members:
        x, y = m["x"], m["y"]
        assert not (96 <= x <= 116 and y <= 152)                       # scarp ridge
        assert not (30 <= math.hypot(x + 120, y - 120) <= 75)          # crater walls and rim
        assert m["attributes"]["slope_2m_max"] <= 5
    # the flat crater floor and the plains are still found
    assert any(math.hypot(m["x"] + 120, m["y"] - 120) < 25 for m in s.members)
    assert any(abs(m["x"]) > 150 for m in s.members)


def test_unknown_cells_never_inside_footprint():
    # a condition that would be satisfied by the hole if unknown were treated as "ok"
    conds = [{"layer": "geologic_unit", "op": "!=", "value": "crater_floor"}]
    r, s = locs(conditions=conds, footprint_radius_m=10, spacing_m=4, objective="largest_clear_area")
    assert s.count > 0
    unit = SITE.layers["geologic_unit"].data
    for m in s.members:
        rr, cc = rc_of(SITE, m)
        for a, b in oracle.footprint_cells(SITE, rr, cc, 10):
            assert unit[a][b] >= 0 and unit[a][b] != 1


def test_cells_outside_grid_fail():
    conds = [{"layer": "elevation", "op": ">", "value": -1e9}]
    r, s = locs(conditions=conds, footprint_radius_m=20, spacing_m=40, objective="largest_clear_area")
    half = SITE.half_m
    for m in s.members:
        assert abs(m["x"]) + 20 <= half + 1 + 1e-9 and abs(m["y"]) + 20 <= half + 1 + 1e-9


# ---------------------------------------------------------------- required scenarios

def test_best_evidence_carbonate_radius_zero():
    r, s = locs(conditions=CARB3, footprint_radius_m=0, objective="best_evidence:carbonate", limit=3)
    first = r["results"][0]
    assert (first["x"], first["y"]) == (-151.0, -151.0)
    assert first["score"] == 4
    assert r["results"][0]["id"] == s.members[0]["id"]


def test_best_evidence_with_footprint_sees_tier4_nearby():
    conds = [{"evidence": "carbonate", "min_tier": "weak_orbital"}]
    r, s = locs(conditions=conds, footprint_radius_m=4, objective="best_evidence:carbonate", spacing_m=2)
    assert s.members[0]["score"] == 4
    # every top-score spot is within 4 m of the tier-4 cell
    for m in s.members:
        if m["score"] == 4:
            assert math.hypot(m["x"] + 151, m["y"] + 151) < 4
    # scores are non-increasing
    sc = [m["score"] for m in s.members]
    assert sc == sorted(sc, reverse=True)


def test_impossible_query_diagnostics_and_hint():
    conds = [{"layer": "slope_2m", "op": "<=", "value": 0.1}]
    r, s = locs(conditions=conds, footprint_radius_m=50, limit=5)
    assert r["count"] == 0 and r["results"] == [] and s.count == 0
    assert r["hint"] and "slope_2m <= 0.1" in r["hint"]
    steps = [d["step"] for d in r["diagnostics"]]
    assert steps == ["slope_2m <= 0.1", "all conditions", "+ footprint radius 50 m"]
    frac = oracle.condition_fraction(SITE, conds)
    assert r["diagnostics"][0]["pass_fraction"] == pytest.approx(frac)
    assert r["diagnostics"][1]["pass_fraction"] == pytest.approx(frac)
    assert r["diagnostics"][2]["pass_fraction"] == 0


def test_footprint_too_large_hint_and_zero_all_hint():
    r, _ = locs(conditions=SLOPE5, footprint_radius_m=200)
    assert r["count"] == 0 and "footprint" in r["hint"]
    assert r["diagnostics"][1]["pass_fraction"] > 0 and r["diagnostics"][2]["pass_fraction"] == 0
    conds = SLOPE5 + [{"layer": "slope_2m", "op": ">=", "value": 40}]
    r, _ = locs(conditions=conds, footprint_radius_m=0)
    assert r["count"] == 0 and r["hint"]
    r, _ = locs(conditions=[{"layer": "slope_2m", "op": ">=", "value": 40}] + CARB3, footprint_radius_m=0)
    assert r["count"] == 0 and "together" in r["hint"]


def test_diagnostic_order_and_values_with_profile():
    r, s = locs(profile="landing_pad")
    steps = [d["step"] for d in r["diagnostics"]]
    assert steps == ["slope_2m <= 5", "rough_5x5 <= 0.5", "all conditions", "+ footprint radius 10 m"]
    assert r["diagnostics"][0]["pass_fraction"] == pytest.approx(
        float((SITE.layers["slope_2m"].data <= 5).sum()) / 256 ** 2)
    assert {a["what"]: a["source"] for a in r["assumptions"]}["slope_2m <= 5"] == "assumed"
    assert {a["what"]: a["source"] for a in r["assumptions"]}["footprint_radius_m = 10"] == "assumed"
    assert r["count"] == s.count and len(r["results"]) <= 50 and r["hint"]
    assert s.kind == "spots" and s.created_by == "find_locations"
    assert s.query["profile"] == "landing_pad" and s.query["footprint_radius_m"] == 10.0


def test_explicit_footprint_and_condition_override_profile():
    r, s = locs(profile="landing_pad", conditions=[{"layer": "slope_2m", "op": "<=", "value": 20}],
                footprint_radius_m=3)
    steps = [d["step"] for d in r["diagnostics"]]
    assert steps[0] == "rough_5x5 <= 0.5" and "slope_2m <= 20" in steps
    assert steps[-1] == "+ footprint radius 3 m"


def test_default_spacing():
    r, s = locs(conditions=SLOPE5, footprint_radius_m=10)
    assert s.query["spacing_m"] == 100.0
    r, s = locs(conditions=SLOPE5, footprint_radius_m=80)
    assert s.query["spacing_m"] == 160.0
    for i, a in enumerate(s.members):
        for b in s.members[i + 1:]:
            assert math.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= 160 - 1e-9


def test_deterministic():
    a = locs(conditions=SLOPE5, footprint_radius_m=10, spacing_m=20)[1].members
    b = locs(conditions=SLOPE5, footprint_radius_m=10, spacing_m=20)[1].members
    assert a == b or [{k: v for k, v in m.items() if k != "id"} for m in a] == \
        [{k: v for k, v in m.items() if k != "id"} for m in b]
    ra = search.find_regions(SITE, SetStore(), CARB3)
    rb = search.find_regions(SITE, SetStore(), CARB3)
    assert ra == rb


def test_tie_breaking_is_row_major_when_flat():
    # constant slope everywhere: every score ties; first spot must be the north-west-most valid centre
    n = 40
    site = window_site(SITE, 0, 0, n, slope=np.full((n, n), 2.0))
    r, s = locs(site=site, conditions=[{"layer": "slope_2m", "op": "<=", "value": 5}], footprint_radius_m=4,
                spacing_m=2)
    assert rc_of(site, s.members[0]) == (1, 1)
    assert rc_of(site, s.members[1]) == (1, 2)


def test_store_keeps_all_spots_results_are_top_limit():
    store = SetStore()
    r = search.find_locations(SITE, store, conditions=SLOPE5, footprint_radius_m=10, spacing_m=20, limit=3)
    s = store.get(r["set_id"])
    assert len(r["results"]) == 3 and s.count == r["count"] > 3
    assert r["results"] == s.members[:3]
    r2 = search.find_locations(SITE, store, conditions=SLOPE5, footprint_radius_m=10, spacing_m=20, limit=3)
    assert r2["set_id"] != r["set_id"]


def test_returned_results_do_not_alias_stored_members():
    store = SetStore()
    r = search.find_locations(SITE, store, conditions=SLOPE5, footprint_radius_m=10, limit=2)
    r["results"][0]["score"] = -999
    assert store.get(r["set_id"]).members[0]["score"] != -999


def test_member_shape_and_lat_lon():
    r, s = locs(conditions=SLOPE5, footprint_radius_m=10, limit=2)
    m = r["results"][0]
    for k in ("id", "x", "y", "lat", "lon", "score", "clear_radius_m", "attributes"):
        assert k in m
    for k in ("slope_2m_mean", "slope_2m_max", "elevation_mean"):
        assert k in m["attributes"]
    assert (m["lat"], m["lon"]) == tuple(round(v, 5) for v in SITE.xy_to_latlon(m["x"], m["y"]))
    assert m["x"] % 2 == 1 and m["y"] % 2 == 1          # cell centres on odd coordinates


def test_footprint_stats_match_oracle():
    R = 12
    r, s = locs(conditions=SLOPE5, footprint_radius_m=R, limit=5)
    sl = SITE.layers["slope_2m"].data
    for m in r["results"]:
        rr, cc = rc_of(SITE, m)
        cells = oracle.footprint_cells(SITE, rr, cc, R)
        vals = [float(sl[a][b]) for a, b in cells]
        assert m["attributes"]["slope_2m_mean"] == pytest.approx(sum(vals) / len(vals), abs=1e-3)
        assert m["attributes"]["slope_2m_max"] == pytest.approx(max(vals), abs=1e-3)


def test_objectives_orderings():
    for obj, key, sign in [("flattest", "slope_2m_mean", 1), ("highest", "elevation_mean", -1),
                           ("lowest", "elevation_mean", 1)]:
        r, s = locs(conditions=SLOPE5, footprint_radius_m=6, objective=obj, spacing_m=10)
        vals = [m["score"] * sign for m in s.members]
        assert vals == sorted(vals), obj
        assert all(m["score"] == m["attributes"][key] for m in s.members)
    r, s = locs(conditions=SLOPE5, footprint_radius_m=6, objective="largest_clear_area", spacing_m=10)
    vals = [m["score"] for m in s.members]
    assert vals == sorted(vals, reverse=True)
    assert all(m["score"] == m["clear_radius_m"] for m in s.members)
    # highest: top spot has the highest footprint mean among all valid centres
    r, s = locs(conditions=SLOPE5, footprint_radius_m=6, objective="highest", spacing_m=1.0)
    assert s.members[0]["score"] == max(m["score"] for m in s.members)


def test_flattest_excludes_footprints_with_unknown_slope():
    # slope_2m is not part of the conditions, so the hole's NaN slope can sit in a footprint
    conds = [{"layer": "elevation", "op": ">", "value": -1e9}]
    r, s = locs(conditions=conds, footprint_radius_m=6, spacing_m=2)
    assert s.count > 0
    sl = SITE.layers["slope_2m"].data
    for m in s.members:
        rr, cc = rc_of(SITE, m)
        assert all(np.isfinite(sl[a][b]) for a, b in oracle.footprint_cells(SITE, rr, cc, 6))


# ---------------------------------------------------------------- errors

def raises(**kw):
    with pytest.raises(AtlasError) as e:
        search.find_locations(SITE, SetStore(), **kw)
    assert e.value.hint
    return e.value


def test_errors():
    base = dict(conditions=SLOPE5, footprint_radius_m=5)
    assert "landing_pad" in raises(profile="nope", **base).hint
    assert "footprint" in raises(conditions=SLOPE5).message.lower()
    raises(**base, objective="closest")
    raises(**base, objective="nonsense")
    raises(**base, objective="best_evidence:geologic_unit")
    raises(**base, objective="best_evidence:olivine")
    raises(**base, limit=0)
    raises(**base, limit=51)
    raises(conditions=SLOPE5, footprint_radius_m=-1)
    raises(**base, spacing_m=0)
    raises(conditions=[], footprint_radius_m=5)
    raises(conditions=[{"layer": "slope", "op": "<=", "value": 1}], footprint_radius_m=5)
    with pytest.raises(AtlasError):
        search.find_regions(SITE, SetStore(), CARB3, limit=100)
    with pytest.raises(AtlasError):
        search.find_regions(SITE, SetStore(), CARB3, objective="closest")
    with pytest.raises(AtlasError):
        search.find_regions(SITE, SetStore(), CARB3, objective="largest_clear_area")


def test_explicit_zero_radius_allowed():
    site = window_site(SITE, 20, 40, 90)
    r, s = locs(site=site, conditions=SLOPE5, footprint_radius_m=0, spacing_m=2)
    assert s.count == int((site.layers["slope_2m"].data <= 5).sum())


# ---------------------------------------------------------------- find_regions

def test_carbonate_region():
    store = SetStore()
    r = search.find_regions(SITE, store, CARB3)
    layer = SITE.layers["carbonate"].data
    expected_cells = int((layer >= 3).sum())
    assert r["count"] == 1
    m = r["results"][0]
    assert m["area_m2"] == expected_cells * 4.0 == 1600.0
    assert m["score"] == 1600.0
    assert m["bbox"] == [-170.0, -170.0, -130.0, -130.0]
    assert m["attributes"]["centroid_x"] == pytest.approx(-150.0)
    assert m["attributes"]["centroid_y"] == pytest.approx(-150.0)
    rr, cc = rc_of(SITE, m)
    assert layer[rr, cc] >= 3
    # representative point is the farthest from the edge: 10 cells from the border
    assert max(abs(m["x"] + 150), abs(m["y"] + 150)) <= 2
    assert store.get(r["set_id"]).kind == "regions"
    assert [d["step"] for d in r["diagnostics"]] == ["carbonate >= strong_orbital", "all conditions"]
    assert r["diagnostics"][0]["pass_fraction"] == pytest.approx(expected_cells / 256 ** 2)
    assert m["attributes"]["slope_2m_mean"] == pytest.approx(1.0, abs=0.01)


def test_regions_match_scipy_labels_and_min_area():
    conds = [{"layer": "geologic_unit", "op": "!=", "value": "plains"}]
    r = search.find_regions(SITE, SetStore(), conds, min_area_m2=0, limit=50)
    mask = np.array(oracle.passing_grid(SITE, conds))
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    areas = sorted((int((lab == i).sum()) * 4.0 for i in range(1, n + 1)), reverse=True)
    assert [m["area_m2"] for m in r["results"]] == areas
    for m in r["results"]:
        rr, cc = rc_of(SITE, m)
        assert mask[rr, cc]
    big = search.find_regions(SITE, SetStore(), conds, min_area_m2=areas[0], limit=50)
    assert big["count"] == 1
    none = search.find_regions(SITE, SetStore(), conds, min_area_m2=areas[0] + 1)
    assert none["count"] == 0 and "min_area_m2" in none["hint"]


def test_regions_8_connectivity_and_edge_distance():
    n = 20
    site = window_site(SITE, 0, 0, n, slope=np.full((n, n), 50.0))
    sl = np.full((n, n), 50.0)
    for i in range(5):                      # diagonal chain: one region only with 8-connectivity
        sl[3 + i, 3 + i] = 1.0
    sl[10:15, 10:16] = 1.0                  # 5 x 6 block touching nothing
    site = window_site(SITE, 0, 0, n, slope=sl)
    r = search.find_regions(site, SetStore(), SLOPE5, min_area_m2=0, limit=10)
    assert [m["area_m2"] for m in r["results"]] == [30 * 4.0, 5 * 4.0]
    block = r["results"][0]
    rr, cc = rc_of(site, block)
    assert rr in (11, 12, 13) and cc in (12, 13)       # centre of the block
    assert block["bbox"][2] - block["bbox"][0] == 12.0 and block["bbox"][3] - block["bbox"][1] == 10.0
    # a region touching the grid edge: the edge counts as its boundary
    sl2 = np.full((n, n), 50.0)
    sl2[0:5, 0:9] = 1.0
    site2 = window_site(SITE, 0, 0, n, slope=sl2)
    r2 = search.find_regions(site2, SetStore(), SLOPE5, min_area_m2=0)
    rr, cc = rc_of(site2, r2["results"][0])
    assert (rr, cc) == (2, 2)      # first of the tied maxima, row-major


def test_regions_objectives():
    conds = [{"layer": "geologic_unit", "op": "!=", "value": "plains"}]
    r = search.find_regions(SITE, SetStore(), conds, objective="flattest", min_area_m2=0, limit=50)
    sc = [m["score"] for m in r["results"]]
    assert sc == sorted(sc)
    r = search.find_regions(SITE, SetStore(), conds + [{"evidence": "carbonate", "min_tier": 0}],
                            objective="best_evidence:carbonate", min_area_m2=0, limit=50)
    sc = [m["score"] for m in r["results"]]
    assert sc == sorted(sc, reverse=True) and sc[0] == 1


def test_regions_hints_when_nothing_matches():
    r = search.find_regions(SITE, SetStore(), [{"layer": "slope_2m", "op": ">", "value": 89.9}])
    assert r["count"] == 0 and r["hint"]
    r = search.find_regions(SITE, SetStore(), CARB3 + [{"layer": "slope_2m", "op": ">=", "value": 40}])
    assert r["count"] == 0 and r["hint"]


# ---------------------------------------------------------------- within

def _fake_within(site, within, store):
    from atlas_core.refs import resolve_ref
    p = resolve_ref(site, within["from"], store)[0]
    X, Y = np.meshgrid(site.rc_to_xy(0, np.arange(site.size_px))[0],
                       site.rc_to_xy(np.arange(site.size_px), 0)[1])
    f = np.hypot(X - p["x"], Y - p["y"]).astype(np.float32)
    return f <= within["max"], f, f"within {within['max']} {within['metric']} of {within['from']}"


def _check_within_flow(site_within):
    w = {"from": {"x": 0, "y": 0}, "max": 120, "metric": "straight_m"}
    r, s = locs(conditions=SLOPE5, footprint_radius_m=8, within=w, spacing_m=10)
    assert s.count > 0
    steps = [d["step"] for d in r["diagnostics"]]
    assert steps[-1].startswith("+ within 120 straight_m")
    assert r["diagnostics"][-1]["pass_fraction"] <= r["diagnostics"][-2]["pass_fraction"]
    for m in s.members:
        assert m["attributes"]["straight_m"] <= 120 + 1e-3
    # closest objective: sorted ascending by field value
    r, s = locs(conditions=SLOPE5, footprint_radius_m=8, within=w, objective="closest", spacing_m=10)
    vals = [m["score"] for m in s.members]
    assert vals == sorted(vals) and vals[0] <= 12
    # tie-break by distance to `from`: flat constant-slope window
    n = 40
    site = window_site(SITE, 0, 0, n, slope=np.full((n, n), 2.0))
    w2 = {"from": {"x": 0.0, "y": 0.0}, "max": 1000, "metric": "straight_m"}
    r, s = locs(site=site, conditions=[{"layer": "slope_2m", "op": "<=", "value": 5}], footprint_radius_m=2,
                within=w2, spacing_m=2)
    d = [math.hypot(m["x"], m["y"]) for m in s.members]
    assert d == sorted(d)
    # out-of-reach limit: empty with a within hint
    w3 = {"from": {"x": 0, "y": 0}, "max": 0.5, "metric": "straight_m"}
    r, _ = locs(conditions=SLOPE5, footprint_radius_m=8, within=w3)
    assert r["count"] == 0 and "limit" in r["hint"]
    assert r["diagnostics"][-1]["pass_fraction"] == 0
    # regions
    rg = search.find_regions(site_within, SetStore(), CARB3, within={"from": {"x": -150, "y": -150}, "max": 5,
                                                                      "metric": "straight_m"}, min_area_m2=0)
    assert rg["count"] == 1 and rg["results"][0]["attributes"]["straight_m"] == pytest.approx(0, abs=2)
    rg = search.find_regions(site_within, SetStore(), CARB3, min_area_m2=0, objective="closest",
                             within={"from": {"x": 0, "y": 0}, "max": 1000, "metric": "straight_m"})
    assert rg["results"][0]["score"] == pytest.approx(math.hypot(130, 130), abs=3)


def test_within_flow_with_fake_travel(monkeypatch):
    monkeypatch.setattr(travel, "within_mask", _fake_within)
    _check_within_flow(SITE)


def test_within_flow_with_real_travel():
    _check_within_flow(SITE)


def test_walk_time_within_base():
    w = {"from": "base", "max": 120, "metric": "walk_time_min", "profile": "eva_walkable"}
    r, s = locs(conditions=SLOPE5, footprint_radius_m=8, within=w, spacing_m=10)
    assert s.count > 0
    for m in s.members:
        assert m["attributes"]["walk_time_min"] <= 120 + 1e-6


# ---------------------------------------------------------------- performance (opt-in sanity)

def test_large_grid_runs_fast():
    n = 1024
    rng = np.random.default_rng(1)
    slope = rng.uniform(0, 12, (n, n)).astype(np.float32)
    # blobby field so that some footprints pass
    slope = ndimage.gaussian_filter(slope, 6) * 1.0
    base = SITE.layers["slope_2m"]
    layers = {"slope_2m": Layer("slope_2m", "continuous", slope, base.meta)}
    site = Site(name="big", lat0=0, lon0=0, res_m=2.0, size_px=n, layers=layers)
    t = time.time()
    r = search.find_locations(site, SetStore(), conditions=[{"layer": "slope_2m", "op": "<=", "value": 6.2}],
                              footprint_radius_m=50, limit=5)
    assert time.time() - t < 20
    assert r["count"] > 0
