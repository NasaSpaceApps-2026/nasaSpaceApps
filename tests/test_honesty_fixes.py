"""Scientific-honesty fixes from the blind tool review (docs/tool-review.md, "Re-run after honesty fixes").

Tier-4 provenance, per-tier areas, tier glosses, registration caveats, assumed science value, POI/sample sources,
describe_location paging, plan/drive profile warning. Synthetic site for logic; the real Jezero build for catalogue facts.
"""
import copy
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from atlas_core import SetStore, describe, evidence, load_site, route as R, search, travel
from atlas_core.testing import make_site
from devapi import create_app
from devapi.search_sites import search_sites

ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = ROOT / "data/build/site/jezero"
spec = importlib.util.spec_from_file_location("fmt_honesty", ROOT / "mcp" / "format.py")
fmt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fmt)

SAMPLE_CITE = "Test et al. 2099 (unit-level statement)"


def honest_site():
    """Synthetic site whose carbonate layer carries tier provenance and whose layers carry registration limits."""
    site = make_site()
    carb = site.layers["carbonate"]
    carb.meta["tier_sources"] = {
        "weak_orbital": {"kind": "orbital", "platform": "SYNTHETIC", "instrument": "test fixture", "citation": "synthetic"},
        "strong_orbital": {"kind": "orbital", "platform": "SYNTHETIC", "instrument": "test fixture", "citation": "synthetic"},
        "confirmed_in_situ": {"kind": "rover_sample", "platform": "Test rover", "citation": SAMPLE_CITE, "samples": [
            {"poi": "outcrop_a", "name": "Sample 99: Testrock", "sample_no": 99, "x": -151.0, "y": -151.0,
             "radius_m": 2.0, "citation": SAMPLE_CITE}]}}
    carb.meta["registration"] = {"pixel_m": 36.0, "residual_measured": False, "contact_caveat_m": 36.0}
    site.layers["geologic_unit"].meta["registration"] = {
        "shift_applied": True, "mosaic_offset_north_m": -98.1, "residual_spread_max_m": 25.2, "contact_caveat_m": 100.0}
    return site


def region_query(site, store, **kw):
    return search.find_regions(site, store, [{"evidence": "carbonate", "min_tier": "weak_orbital"}], None,
                               kw.pop("min_area_m2", 100.0), None, kw.pop("objective", "best_evidence:carbonate"), 5)


# ---- 1 + 2: provenance per tier and area by tier ---------------------------------------------------

def test_region_reports_area_per_tier_and_names_rover_sample_for_tier4():
    site, store = honest_site(), SetStore()
    res = region_query(site, store)
    reg = res["results"][0]
    e = reg["evidence"]["carbonate"]
    by = e["area_by_tier_m2"]
    assert e["best_tier_name"] == "confirmed_in_situ"
    assert by["confirmed_in_situ"] == 4.0                       # one 2 m cell, not the whole region
    assert by["strong_orbital"] > 1000 and by["weak_orbital"] > 1000
    assert abs(sum(by.values()) - reg["area_m2"]) < 1e-6
    assert by["confirmed_in_situ"] < 0.01 * reg["area_m2"]
    assert [s["name"] for s in e["in_situ"]["samples"]] == ["Sample 99: Testrock"]
    text = fmt.fmt_search(res, "regions")
    line = text.splitlines()[1]
    assert "confirmed_in_situ 4 m2 [rover sample: Sample 99: Testrock]" in line
    assert "whole region, not the area at the best tier" in line
    tier4_part = line.split("confirmed_in_situ 4 m2")[1].split(",")[0]
    assert "SYNTHETIC" not in tier4_part and "orbital" not in tier4_part      # never the orbital instrument
    assert "[orbital: SYNTHETIC/test fixture]" in line                        # orbital tiers cite the orbiter
    assert SAMPLE_CITE in text and "rover sample claim" in text


def test_tier4_without_catalogue_source_says_so_not_orbital():
    site, store = make_site(), SetStore()          # plain synthetic site: no tier_sources recorded
    res = region_query(site, store)
    line = fmt.fmt_search(res, "regions").splitlines()[1]
    t4 = line.split("confirmed_in_situ 4 m2")[1].split(",")[0]
    assert t4.startswith(" [rover sample: sample not identified]") and "SYNTHETIC" not in t4


def test_find_sites_tier4_basis_is_rover_sample_and_area_split():
    site, store = honest_site(), SetStore()
    res = search_sites(site, store, {"target": "carbonate", "near": "base", "top_k": 3, "min_area_m2": 50})
    top = res["sites"][0]
    assert top["best_tier_name"] == "confirmed_in_situ"
    assert top["best_tier_basis"] == "rover sample: Sample 99: Testrock"
    assert top["area_by_tier_m2"]["confirmed_in_situ"] == 4.0 and top["area_m2"] > 1000
    txt = fmt.fmt_sites(res)
    assert "confirmed_in_situ 4 m2 [rover sample: Sample 99: Testrock]" in txt and "from SYNTHETIC" not in txt
    assert "caveat" in txt                                         # registration note present


def test_find_locations_best_evidence_spot_carries_tiers():
    site, store = honest_site(), SetStore()
    res = search.find_locations(site, store, [{"layer": "slope_2m", "op": "<=", "value": 30}], None, 10,
                                None, "best_evidence:carbonate", None, 3)
    e = res["results"][0]["evidence"]["carbonate"]
    assert e["best_tier_name"] == "confirmed_in_situ" and e["in_situ"]["samples"][0]["name"].startswith("Sample 99")


def test_nearest_carries_region_tiers():
    site, store = honest_site(), SetStore()
    reg = region_query(site, store)
    pads = search.find_locations(site, store, None, "landing_pad", None, None, "flattest", None, 3)
    n = travel.nearest(site, store, reg["set_id"], pads["set_id"], "straight_m")
    p = n["results"][0]
    assert p["from_evidence"]["carbonate"]["area_by_tier_m2"]["confirmed_in_situ"] == 4.0
    assert p["to_x"] is not None
    txt = fmt.fmt_nearest(n)
    assert "area by tier" in txt and "rover sample: Sample 99" in txt and " at (" in txt


# ---- 3: gloss of not_detected / no_coverage ------------------------------------------------------------

def test_describe_glosses_not_detected_and_no_coverage():
    site, store = honest_site(), SetStore()
    d = describe.describe_location(site, store, [{"x": -250, "y": 0}, {"x": -250, "y": 220}], 0, ["carbonate"])
    t0, t1 = d["locations"][0]["values"]["carbonate"], d["locations"][1]["values"]["carbonate"]
    assert t0["tier_name"] == "not_detected" and t1.get("unknown")
    txt = fmt.fmt_describe(d)
    assert "covered, nothing detected (absence of evidence" in txt
    assert "no_coverage (no data here (cannot answer))" in txt


def test_describe_area_and_point_tier4_name_the_sample_and_source():
    site, store = honest_site(), SetStore()
    d = describe.describe_location(site, store, "poi:outcrop_a", 0, ["carbonate"])
    txt = fmt.fmt_describe(d)
    assert "tier 4 confirmed_in_situ (rover sample: Sample 99: Testrock)" in txt
    d = describe.describe_location(site, store, "poi:outcrop_a", 30, ["carbonate"])
    assert "[rover sample: Sample 99: Testrock]" in fmt.fmt_describe(d)


# ---- 4: registration caveats -------------------------------------------------------------------------

def test_unit_contact_caveat_only_near_a_contact():
    site, store = honest_site(), SetStore()
    near = describe.describe_location(site, store, {"x": 61, "y": 0}, 0, ["geologic_unit"])    # 45 m from the x=106 contact
    far = describe.describe_location(site, store, {"x": -250, "y": 0}, 0, ["geologic_unit"])
    assert near["locations"][0]["near_boundary_of"] == ["geologic_unit"]
    assert "unit contact" in near["registration_notes"]["geologic_unit"] and "98" in near["registration_notes"]["geologic_unit"]
    assert "near_boundary_of" not in far["locations"][0] and "registration_notes" not in far
    assert "geologic_unit caveat" in fmt.fmt_describe(near)
    assert "caveat" not in fmt.fmt_describe(far)


def test_crism_tier_change_caveat_within_36m():
    site, store = honest_site(), SetStore()
    near = describe.describe_location(site, store, {"x": -150, "y": -100}, 0, ["carbonate"])   # ring edge at y=-110: 10 m away
    assert near["locations"][0]["near_boundary_of"] == ["carbonate"]
    far = describe.describe_location(site, store, {"x": 200, "y": 100}, 0, ["carbonate"])
    assert "near_boundary_of" not in far["locations"][0]
    assert "not measured" in near["registration_notes"]["evidence_tiers:carbonate"]


def test_find_locations_flags_spot_near_boundary_and_regions_carry_note():
    site, store = honest_site(), SetStore()
    res = search.find_locations(site, store, [{"layer": "geologic_unit", "op": "==", "value": "plains"}], None, 20,
                                {"from": {"x": 80, "y": 0}, "max": 15, "metric": "straight_m"}, "closest", None, 3)
    assert res["results"][0]["near_boundary_of"] == ["geologic_unit"]
    assert "geologic_unit" in res["registration_notes"]
    reg = search.find_regions(site, store, [{"layer": "geologic_unit", "op": "==", "value": "crater_floor"}], None, 100,
                              None, "largest", 3)
    assert "geologic_unit" in reg["registration_notes"]


def test_site_without_registration_meta_prints_no_caveats():
    site, store = make_site(), SetStore()
    d = describe.describe_location(site, store, {"x": 61, "y": 0}, 0)
    assert "registration_notes" not in d


# ---- 5: assumed science value ---------------------------------------------------------------------------

def test_science_traverse_all_assumed_reports_no_total():
    site, store = make_site(), SetStore()
    r = R.science_traverse(site, store, "base", 240)
    assert all(s["value_assumed"] for s in r["stops"]) and r["stops"]
    assert r["science_value_total"] is None and "no science weighting" in r["science_weighting_note"]
    txt = fmt.fmt_science(r)
    assert "no science value reported" in txt and "science value 1.5" not in txt and "travel time only" in txt


def test_science_traverse_recorded_values_are_summed_and_mixed_is_flagged():
    site, store = make_site(), SetStore()
    for p in site.pois:
        if p["id"] in ("outcrop_a", "landing"):
            p["science_value"] = 0.8
    r = R.science_traverse(site, store, "base", 240)
    got = {s["id"]: s["value_assumed"] for s in r["stops"]}
    assert got["poi:outcrop_a"] is False
    assert r["science_value_total"] == round(0.8 * sum(1 for s in r["stops"] if not s["value_assumed"]), 3)
    assert "assumed value" in r["science_weighting_note"]


# ---- 6: POIs and ground truth ---------------------------------------------------------------------------

def test_search_pois_shows_sample_region_minerals_and_description():
    d = {"count": 1, "types": ["sample_site"], "known_ids": [], "pois": [{
        "ref": "poi:s7", "name": "Sample 7: Malay", "type": "sample_site", "x": 1.0, "y": 2.0, "region": "South Seitah",
        "rock_type": "Igneous", "in_situ_minerals": ["carbonate", "olivine"], "position_uncertainty_m": 18.3,
        "description": "Sample 7 of the NASA list ... APPROXIMATE position.", "source_url": "https://example.org/samples"}]}
    txt = fmt.fmt_pois(d)
    assert "South Seitah" in txt and "carbonate, olivine" in txt and "APPROXIMATE position" in txt and "example.org" in txt
    d["pois"][0]["in_situ_minerals"] = []
    assert "none read" in fmt.fmt_pois(d)


def test_nearest_ground_truth_names_sample_and_source():
    site, store = honest_site(), SetStore()
    site.pois.append({"id": "s99", "name": "Sample 99: Testrock", "type": "sample_site", "x": -151.0, "y": -151.0,
                      "in_situ_minerals": ["carbonate"], "region": "Test region", "source_url": "https://example.org/s"})
    carb = site.layers["carbonate"].meta["tier_sources"]["confirmed_in_situ"]["samples"][0]
    carb["poi"] = "s99"
    g = describe.describe_location(site, store, {"x": -100, "y": -100}, 0, ["carbonate"])["locations"][0]["nearest_ground_truth"]
    assert g["sample"]["name"] == "Sample 99: Testrock" and g["sample"]["source_url"] == "https://example.org/s"
    assert SAMPLE_CITE in g["citation"]
    txt = fmt.fmt_describe(describe.describe_location(site, store, {"x": -100, "y": -100}, 0, ["carbonate"]))
    assert "Sample 99: Testrock, NASA Perseverance sample list" in txt and "Test et al. 2099" in txt


# ---- 8: describe_location paging, profile warning --------------------------------------------------------

def test_describe_location_pages_over_ten():
    site, store = make_site(), SetStore()
    s = store.create("spots", "t", {}, [{"x": float(i), "y": 0.0, "lat": 0, "lon": 0, "score": i} for i in range(1, 26)])
    d = describe.describe_location(site, store, s.set_id, 0, ["slope_2m"])
    assert len(d["locations"]) == 10 and d["truncated"] == {"shown": 10, "total": 25, "note": d["truncated"]["note"]}
    assert "10 of 25" in fmt.fmt_describe(d)


def test_follow_route_warns_when_profile_differs_from_planning_profile():
    c = TestClient(create_app(make_site(), synthetic=True))
    r = c.post("/route", json={"start": "base", "goal": "poi:landing", "profile": "eva_walkable"}).json()
    res = c.post("/rover/command", json={"cmd": "follow_route", "route_id": r["route_id"]}).json()
    assert "eva_walkable" in res["profile_warning"] and "rover_scout" in res["profile_warning"]
    assert "does not guarantee" in fmt.fmt_command(res)


# ---- real Jezero build ----------------------------------------------------------------------------------

needs_site = pytest.mark.skipif(not (SITE_DIR / "geologic_unit.tif").exists(), reason="science layers not built")


@pytest.fixture(scope="module")
def jez():
    return load_site(SITE_DIR)


@needs_site
def test_real_catalogue_has_tier_sources_and_registration(jez):
    for lid in ("carbonate", "olivine"):
        ts = jez.layers[lid].meta["tier_sources"]
        assert ts["weak_orbital"]["instrument"].startswith("CRISM") and ts["weak_orbital"]["kind"] == "orbital"
        t4 = ts["confirmed_in_situ"]
        assert t4["kind"] == "rover_sample" and "CRISM" not in t4["platform"] and "Perseverance" in t4["platform"]
        assert t4["samples"] and all(s["citation"] and s["poi"].startswith("sample_") for s in t4["samples"])
    assert {s["sample_no"] for s in jez.layers["carbonate"].meta["tier_sources"]["confirmed_in_situ"]["samples"]} == {4, 5, 6, 7}
    assert jez.layers["olivine"].meta["registration"]["residual_measured"] is False
    assert "tier_sources" not in jez.layers["kaolinite"].meta or "confirmed_in_situ" not in jez.layers["kaolinite"].meta["tier_sources"]


@needs_site
def test_real_geologic_shift_recorded_and_consistent(jez):
    reg = jez.layers["geologic_unit"].meta["registration"]
    assert reg["shift_applied"] is True and reg["residual_spread_max_m"] < 30
    assert 80 < abs(reg["applied_shift_north_m"]) < 120 and abs(reg["applied_shift_east_m"]) < 50
    assert "SHIFT APPLIED" in jez.layers["geologic_unit"].meta["quality"]


@needs_site
def test_real_south_seitah_samples_fall_in_lower_etched_unit(jez):
    """Samples 4-7 (South Seitah): the literature puts the Seitah olivine-carbonate rocks in the lower etched unit (Nle)."""
    store = SetStore()
    for pid in ("sample_04_salette", "sample_05_coulettes", "sample_06_robine", "sample_07_malay"):
        d = describe.describe_location(jez, store, f"poi:{pid}", 0, ["geologic_unit"])
        assert d["locations"][0]["values"]["geologic_unit"]["class"] == "Lower etched unit (Nle)"


@needs_site
def test_real_olivine_sites_split_tier4_from_region_area(jez):
    store = SetStore()
    res = search_sites(jez, store, {"target": "olivine", "near": "base", "top_k": 5, "min_tier": "confirmed_in_situ"})
    assert res["sites"]
    for s in res["sites"]:
        assert s["best_tier_name"] == "confirmed_in_situ" and s["best_tier_basis"].startswith("rover sample: Sample")
        assert "CRISM" not in s["best_tier_basis"]
    big = search_sites(jez, store, {"target": "olivine", "near": "base", "top_k": 1})["sites"][0]
    assert big["area_by_tier_m2"]["confirmed_in_situ"] < 0.01 * big["area_m2"] if "confirmed_in_situ" in big["area_by_tier_m2"] else True
    txt = fmt.fmt_sites(search_sites(jez, store, {"target": "carbonate", "near": "base", "top_k": 3, "min_tier": "confirmed_in_situ"}))
    assert "rover sample: Sample" in txt and "Farley et al. 2022" in txt and "from MRO" not in txt


@needs_site
def test_real_describe_sample_reports_unit_caveat_and_sources(jez):
    store = SetStore()
    d = describe.describe_location(jez, store, "poi:sample_07_malay", 50)
    txt = fmt.fmt_describe(d)
    assert "rover sample: " in txt and "Farley et al. 2022" in txt
    assert "geologic_unit caveat" in txt and "CRISM pixels are 36 m" in txt
    assert "Sample 7: Malay, NASA Perseverance sample list" in txt
