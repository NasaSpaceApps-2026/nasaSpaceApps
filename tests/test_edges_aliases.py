"""Region edges in nearest() and layer aliases (find_sites / evidence conditions / catalogue)."""
import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from atlas_core import SetStore, catalog, load_site, search, travel
from atlas_core.conditions import expand_aliases
from atlas_core.errors import AtlasError
from atlas_core.site import Layer
from atlas_core.testing import make_site
from devapi.search_sites import search_sites
from tests.oracle_travel import oracle_field

SITE = make_site()
N = SITE.size_px
BASE = {"x": 0.0, "y": 0.0}
CARB = [{"evidence": "carbonate", "min_tier": "weak_orbital"}]
SITE_DIR = Path(__file__).resolve().parents[1] / "data/build/site/jezero"


def region_set(store, within=None):
    r = search.find_regions(SITE, store, CARB, None, 400.0, within, "largest", 5)
    # the carbonate patch (far from base); the crater floor is tier 1 and excluded by weak_orbital
    return r, store.get(r["set_id"]).members[0]


# ---- region edges ----------------------------------------------------------------------------------

def test_region_cells_are_private():
    store = SetStore()
    r, mem = region_set(store)
    assert "_cells" in mem and len(mem["_cells"]) == int(round(mem["area_m2"] / 4))
    assert all("_cells" not in m for m in r["results"])
    assert all("_cells" not in m for m in store.page(r["set_id"])["members"])
    json.dumps(r)
    json.dumps(store.page(r["set_id"]))


@pytest.mark.parametrize("metric", ["walk_m", "walk_time_min"])
def test_nearest_measures_to_region_edge_matches_oracle(metric):
    store = SetStore()
    r, mem = region_set(store)
    rep = {"x": mem["x"], "y": mem["y"]}
    out = travel.nearest(SITE, store, "base", r["set_id"], metric, limit=5)
    pair = out["results"][0]
    # oracle: min over every planning cell the region touches
    field, cell_m = oracle_field(SITE, [(0.0, 0.0)], metric)
    k = round(4.0 / SITE.res_m)
    cells = np.asarray(mem["_cells"], np.int64)
    pi, pj = (cells // N) // k, (cells % N) // k
    want = float(np.min(field[pi, pj]))
    assert math.isfinite(want)
    assert pair["cost"] == pytest.approx(want, rel=1e-3)
    # the representative point is far from the nearest edge, so the old cost was strictly larger
    old = travel.nearest(SITE, store, "base", rep, metric)["results"][0]["cost"]
    assert old > pair["cost"] * 1.2
    # entry point is a cell of the region, much closer to base than the representative point
    ex, ey = pair["to_entry_x"], pair["to_entry_y"]
    r_, c_ = SITE.rc_index(ex, ey)
    assert int(r_) * N + int(c_) in set(cells.tolist())
    assert math.hypot(ex, ey) < math.hypot(rep["x"], rep["y"]) - 30
    assert "from_entry_x" not in pair
    assert not any("representative" in n for n in out["notes"])


def test_nearest_from_region_same_cost_and_gt25_path():
    store = SetStore()
    r, mem = region_set(store)
    to_region = travel.nearest(SITE, store, "base", r["set_id"], "walk_m")["results"][0]
    back = travel.nearest(SITE, store, r["set_id"], "base", "walk_m")["results"][0]
    assert back["cost"] == pytest.approx(to_region["cost"], rel=1e-3)
    ex, ey = back["from_entry_x"], back["from_entry_y"]
    assert (ex, ey) == (to_region["to_entry_x"], to_region["to_entry_y"]) or \
        math.hypot(ex - to_region["to_entry_x"], ey - to_region["to_entry_y"]) <= 8.0
    # > 25 from members goes through the multi-source path and must agree
    many = [r["set_id"]] * 26
    out = travel.nearest(SITE, store, many, "base", "walk_m", limit=1)
    assert out["count"] == 26
    assert out["results"][0]["cost"] == pytest.approx(back["cost"], rel=1e-3)
    # region -> point, multi-source direction: to side is the many-member set
    out2 = travel.nearest(SITE, store, "base", many, "walk_m", limit=1)
    assert out2["results"][0]["cost"] == pytest.approx(to_region["cost"], rel=1e-3)
    assert out2["results"][0]["to_entry_x"] is not None


def test_nearest_straight_is_gap_between_nearest_cells():
    store = SetStore()
    r, mem = region_set(store)
    out = travel.nearest(SITE, store, "base", r["set_id"], "straight_m")["results"][0]
    cells = np.asarray(mem["_cells"], np.int64)
    x, y = SITE.rc_to_xy(cells // N, cells % N)
    want = float(np.min(np.hypot(x, y)))
    assert out["cost"] == pytest.approx(want, abs=1e-2)
    assert out["straight_m"] == pytest.approx(want, abs=0.1)


def test_within_attribute_is_min_over_region_cells():
    store = SetStore()
    within = {"from": "base", "max": 5000, "metric": "walk_m"}
    r, mem = region_set(store, within)
    field = travel.travel_field(SITE, [BASE], "walk_m")
    cells = np.asarray(mem["_cells"], np.int64)
    want = float(np.min(field[cells // N, cells % N]))
    assert r["results"][0]["attributes"]["walk_m"] == pytest.approx(want, abs=1e-3)
    # and it equals the edge-based nearest() cost
    pair = travel.nearest(SITE, store, "base", r["set_id"], "walk_m")["results"][0]
    assert pair["cost"] == pytest.approx(want, rel=1e-3)


# ---- aliases -----------------------------------------------------------------------------------------

def alias_site():
    s = copy.copy(SITE)
    layers = dict(SITE.layers)
    carb = layers["carbonate"]
    meta = {**carb.meta, "aliases": ["carbonates", "carbonate rock"]}
    layers["carbonate"] = Layer("carbonate", "evidence", carb.data, meta)
    dol = np.zeros_like(carb.data)
    X = (np.arange(N) + 0.5) * SITE.res_m - SITE.half_m
    xs, ys = np.meshgrid(X, -X)
    dol[(np.abs(xs - 100) <= 25) & (np.abs(ys - 100) <= 25)] = 3
    layers["dolomite"] = Layer("dolomite", "evidence", dol,
                               {**carb.meta, "title": "Dolomite", "aliases": ["carbonates", "Dolo-mite"]})
    s.layers = layers
    return s


def test_resolve_alias_forms():
    s = alias_site()
    assert catalog.resolve_alias(s, "carbonates") == ["carbonate", "dolomite"]
    assert catalog.resolve_alias(s, "Carbonate Rock") == ["carbonate"]
    assert catalog.resolve_alias(s, "carbonate-rock") == ["carbonate"]
    assert catalog.resolve_alias(s, "dolo mite") == ["dolomite"]
    assert catalog.resolve_alias(s, "granite") is None
    assert catalog.resolve_alias(SITE, "carbonates") is None      # no aliases on the plain site


def test_evidence_condition_alias_group_is_any_of():
    s = alias_site()
    conds, notes = expand_aliases(s, [{"evidence": "carbonates", "min_tier": "strong_orbital"}])
    assert notes == ["carbonates -> carbonate, dolomite"]
    assert conds == [{"any_of": [{"evidence": "carbonate", "min_tier": "strong_orbital"},
                                 {"evidence": "dolomite", "min_tier": "strong_orbital"}]}]
    # a real layer id is never rewritten, a single-layer alias becomes that layer
    assert expand_aliases(s, [{"evidence": "carbonate", "min_tier": 2}])[1] == []
    assert expand_aliases(s, [{"evidence": "carbonate rock", "min_tier": 2}])[0] == [{"evidence": "carbonate", "min_tier": 2}]
    store = SetStore()
    r = search.find_regions(s, store, [{"evidence": "carbonates", "min_tier": "strong_orbital"}], None, 400.0, None,
                            "best_evidence:carbonate,dolomite", 10)
    assert r["alias_notes"] == ["carbonates -> carbonate, dolomite"]
    # union of both layers: the carbonate patch and the dolomite patch
    assert r["count"] == 2
    assert any(abs(m["x"] - 100) < 30 for m in r["results"])
    assert all("_cells" not in m for m in r["results"])


def test_find_sites_alias_group_and_resolution_text():
    s = alias_site()
    out = search_sites(s, SetStore(), {"target": "Carbonates", "near": "base"})
    assert out["resolved"] == "Carbonates -> carbonate, dolomite"
    assert out["layers"] == ["carbonate", "dolomite"]
    assert out["count"] >= 2
    for site_ in out["sites"]:
        assert {e["layer"] for e in site_["evidence"]} - {"slope_2m"} == {"carbonate", "dolomite"}
        assert site_["evidence"][0]["tier"] == site_["best_tier"]       # best layer first
    near_dol = [x for x in out["sites"] if abs(x["x"] - 100) < 30][0]
    assert near_dol["evidence"][0]["layer"] == "dolomite"
    # an exact id is not an alias: no resolution line
    assert search_sites(s, SetStore(), {"target": "carbonate", "near": "base"})["resolved"] is None
    # single-layer alias
    one = search_sites(s, SetStore(), {"target": "carbonate rock", "near": "base"})
    assert one["resolved"] == "carbonate rock -> carbonate" and one["layers"] == ["carbonate"]


def test_unknown_target_hint_suggests_close_matches_and_aliases():
    s = alias_site()
    with pytest.raises(AtlasError) as e:
        search_sites(s, SetStore(), {"target": "carbonat", "near": "base"})
    hint = e.value.hint
    assert "Did you mean" in hint and "carbonate" in hint
    assert "Aliases:" in hint and "carbonates" in hint
    with pytest.raises(AtlasError) as e:
        search_sites(s, SetStore(), {"target": "zzzz", "near": "base"})
    assert "Did you mean" not in e.value.hint and "Valid targets" in e.value.hint


def test_list_layers_and_overview_know_aliases():
    s = alias_site()
    ids = [l["id"] for l in catalog.list_layers(s, query="carbonate rock")["layers"]]
    assert "carbonate" in ids and "dolomite" not in ids
    assert {l["id"] for l in catalog.list_layers(s, query="carbonates")["layers"]} >= {"carbonate", "dolomite"}
    assert [l["id"] for l in catalog.list_layers(s, query="dolo-mite")["layers"]] == ["dolomite"]
    ov = catalog.site_overview(s)
    assert ov["aliases"][0]["layers"] == ["carbonate", "dolomite"]
    assert "carbonates" in ov["aliases"][0]["aliases"]
    assert catalog.site_overview(SITE)["aliases"] == []


# ---- real site -----------------------------------------------------------------------------------------

needs_site = pytest.mark.skipif(not (SITE_DIR / "site.yaml").exists()
                                or "aliases" not in (SITE_DIR / "site.yaml").read_text(), reason="site not built with aliases")


@pytest.fixture(scope="module")
def jez():
    return load_site(SITE_DIR)


@needs_site
def test_real_site_clay_aliases(jez):
    clay = ["fe_mg_smectite", "kaolinite", "mg_smectite_2"]
    for word in ("clay", "Clays", "phyllosilicates"):
        assert sorted(catalog.resolve_alias(jez, word)) == clay
    assert sorted(catalog.resolve_alias(jez, "smectite")) == ["fe_mg_smectite", "mg_smectite_2"]
    assert sorted(catalog.resolve_alias(jez, "pyroxene")) == ["high_ca_pyroxene", "low_ca_pyroxene"]
    assert catalog.resolve_alias(jez, "carbonates") == ["carbonate"]
    assert catalog.resolve_alias(jez, "olivine-rich rock") == ["olivine"]
    assert {l["id"] for l in catalog.list_layers(jez, query="clay")["layers"]} >= set(clay)
    out = search_sites(jez, SetStore(), {"target": "clay", "near": "base", "top_k": 3})
    assert out["resolved"] == "clay -> fe_mg_smectite, kaolinite, mg_smectite_2"
    assert out["layers"] == clay and out["count"] > 0
    assert out["sites"][0]["best_tier"] >= out["sites"][-1]["best_tier"]
    assert {e["layer"] for e in out["sites"][0]["evidence"]} - {"slope_2m"} == set(clay)
