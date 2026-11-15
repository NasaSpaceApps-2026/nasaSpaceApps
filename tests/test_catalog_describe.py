"""Site overview, layer catalogue and describe_location (atlas_core.catalog / describe) on the synthetic site with known ground truth."""
import math

import pytest

from atlas_core import AtlasError, SetStore
from atlas_core.catalog import describe_layer, list_layers, site_overview
from atlas_core.describe import describe_location
from atlas_core.testing import make_site


@pytest.fixture(scope="module")
def site():
    return make_site()


@pytest.fixture
def store():
    return SetStore()


def loc(site, store, x, y, **kw):
    return describe_location(site, store, {"x": x, "y": y}, **kw)["locations"][0]


def test_overview_shape(site):
    o = site_overview(site)
    assert o["site"]["size_m"] == 512 and "x east" in o["site"]["frame"]
    assert {l["id"] for l in o["layers"]} == set(site.layers)
    assert o["pois"][0]["id"] == "base"           # bases first
    assert {p["name"] for p in o["profiles"]} == {"landing_pad", "eva_walkable", "rover_scout"}
    assert o["evidence_tiers"][0] == "not_detected" and o["evidence_tiers"][-1] == "confirmed_in_situ"
    assert o["rover"] is None and "describe_layer" in o["next"]


def test_list_layers_filters(site):
    assert {l["id"] for l in list_layers(site, kind="evidence")["layers"]} == {"carbonate"}
    ids = {l["id"] for l in list_layers(site, query="SLOPE")["layers"]}
    assert ids == {"slope_2m"}
    with pytest.raises(AtlasError):
        list_layers(site, kind="bogus")


def test_describe_layer(site):
    d = describe_layer(site, "slope_2m")
    assert d["kind"] == "continuous" and d["does_not_measure"] and d["source"]["mission"] == "SYNTHETIC"
    assert d["min"] < 1 < d["median"] + 0.01 and d["max"] > 55
    assert 0.99 < d["coverage_fraction"] < 1.0           # the NaN hole
    assert describe_layer(site, "geologic_unit")["classes"] == ["plains", "crater_floor", "scarp_block"]
    ev = describe_layer(site, "carbonate")
    assert len(ev["classes"]) == 5 and ev["coverage_fraction"] < 0.95
    with pytest.raises(AtlasError) as e:
        describe_layer(site, "slope")
    assert "slope_2m" in e.value.hint


def test_point_slope_plain_and_ridge(site, store):
    plain = loc(site, store, 0, 0)["values"]["slope_2m"]
    assert plain["units"] == "degrees" and plain["value"] == pytest.approx(1.0, abs=0.05)
    ridge = loc(site, store, 103, -50)["values"]["slope_2m"]["value"]
    assert 57 < ridge < 61


def test_evidence_tier4_and_unknown_hole(site, store):
    v = loc(site, store, -151, -151)["values"]["carbonate"]
    assert v == {"tier": 4, "tier_name": "confirmed_in_situ"}
    hole = loc(site, store, 0, -150)["values"]
    assert all(x == {"unknown": True} for x in hole.values())


def test_elevation_bilinear(site, store):
    # 1 degree tilt: elevation changes by tan(1 deg) per metre in x; bilinear is exact on a plane
    e0 = loc(site, store, 0, 0)["values"]["elevation"]["value"]
    e1 = loc(site, store, 10, 0)["values"]["elevation"]["value"]
    assert (e1 - e0) / 10 == pytest.approx(math.tan(math.radians(1)), rel=1e-2)  # float32 elevations


def test_radius_stats(site, store):
    v = loc(site, store, -150, -150, radius_m=30)["values"]
    c = v["carbonate"]
    assert c["best_tier"] == 4 and c["best_tier_name"] == "confirmed_in_situ"
    assert sum(c["shares"].values()) + c["unknown_fraction"] == pytest.approx(1.0)
    assert "strong_orbital" in c["shares"] and "weak_orbital" in c["shares"]
    s = v["slope_2m"]
    assert s["min"] - 1e-4 <= s["mean"] <= s["max"] + 1e-4 and s["unknown_fraction"] == 0
    # footprint across the ridge flank reports the MAX slope
    r = loc(site, store, 103, -50, radius_m=10)["values"]["slope_2m"]
    assert r["max"] > 55
    # footprint overlapping the hole reports unknown share, and units are kept
    h = loc(site, store, 0, -150, radius_m=20)["values"]["slope_2m"]
    assert 0 < h["unknown_fraction"] < 1 and h["units"] == "degrees"
    g = loc(site, store, 0, -150, radius_m=20)["values"]["geologic_unit"]
    assert g["shares"]["plains"] + g["unknown_fraction"] == pytest.approx(1.0)


def test_nearest_ground_truth(site, store):
    g = loc(site, store, -150, -150)["nearest_ground_truth"]
    assert (g["x"], g["y"]) == (-151, -151)
    assert g["distance_m"] == pytest.approx(math.sqrt(2))
    assert "carbonate" in g["what"]
    far = loc(site, store, 200, 200)["nearest_ground_truth"]
    assert far["distance_m"] > 300


def test_quality_not_recorded(site, store):
    assert loc(site, store, 0, 0)["quality"] == "not recorded"


def test_layer_subset_and_unknown_layer(site, store):
    d = describe_location(site, store, "base", layers=["slope_2m"])
    assert list(d["locations"][0]["values"]) == ["slope_2m"]
    assert d["locations"][0]["name"] == "Test base"
    with pytest.raises(AtlasError):
        describe_location(site, store, "base", layers=["nope"])


def test_set_and_limit(site, store):
    members = [{"x": float(i), "y": 0.0, "lat": 0, "lon": 0, "score": i} for i in range(1, 12)]
    s = store.create("spots", "test", {}, members)
    big = describe_location(site, store, s.set_id)          # 11 members: first 10 described, the rest summarised
    assert len(big["locations"]) == 10
    assert big["truncated"]["total"] == 11 and "get_set" in big["truncated"]["note"]
    ok = describe_location(site, store, [m["id"] for m in s.members[:10]])
    assert len(ok["locations"]) == 10
    with pytest.raises(AtlasError):
        describe_location(site, store, "base", radius_m=-1)


def test_out_of_bounds(site, store):
    with pytest.raises(AtlasError):
        describe_location(site, store, {"x": 5000, "y": 0})
