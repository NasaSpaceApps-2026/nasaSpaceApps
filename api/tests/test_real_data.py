"""Endpoints on the REAL Jezero build (data/build from Caden's pipeline): sanity + PRD performance targets.

Skipped when data/build/manifest.json is absent. Build it with:
  pipeline/00_fetch.py dtm, pipeline/build_tiles.py, curl the M20 traverse/waypoints into data/raw
  (see data/SOURCES.md), pipeline/05_vectors.py, pipeline/05b_names.py.
Mineral layers do not exist for the real site yet, so search/science timing uses SYNTHETIC layers on the
real grid (written to a temp dir); the API labels them "not Mars data" and the test checks that it does.
"""
import inspect
import json
import math
import time
from pathlib import Path

import numpy as np
import pytest
import rasterio

BUILD = Path(__file__).resolve().parents[2] / "data" / "build"
pytestmark = pytest.mark.skipif(not (BUILD / "manifest.json").exists(),
                                reason="real data build (data/build/manifest.json) not present")


def _waypoints():
    feats = json.loads((BUILD / "data" / "pois.geojson").read_text())["features"]
    return [f["properties"] for f in feats
            if f["properties"].get("type") == "rover_waypoint"
            and abs(f["properties"]["x"]) < 4000 and abs(f["properties"]["y"]) < 4000]


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    from fastapi.testclient import TestClient
    from ares_api.main import create_app

    # Synthetic carbonate hotspots on the real 2 m grid, around the early traverse (for timing only).
    extra = tmp_path_factory.mktemp("synthetic_layers")
    with rasterio.open(BUILD / "analysis" / "dem.tif") as src:
        meta, tr, (h, w) = src.meta.copy(), src.transform, src.shape
    xs = tr.c + (np.arange(w) + 0.5) * tr.a
    ys = tr.f + (np.arange(h) + 0.5) * tr.e
    a = np.zeros((h, w), np.float32)
    for x0, y0, r, peak in [(1700, -300, 40, 0.9), (2100, -700, 30, 0.8), (1500, -800, 50, 0.7), (2300, -200, 25, 0.6)]:
        a = np.maximum(a, peak * np.exp(-((xs[None, :] - x0) ** 2 + (ys[:, None] - y0) ** 2) / r ** 2))
    meta.update(dtype="float32", count=1, nodata=None)
    with rasterio.open(extra / "layer_min_carbonate.tif", "w", **meta) as d:
        d.write(a, 1)

    kw = {"state_file": None} if "state_file" in inspect.signature(create_app).parameters else {}
    app = create_app(BUILD, extra_layer_dir=extra, **kw)
    with TestClient(app) as c:
        yield c


def timed(fn, *a, **k):
    t = time.perf_counter()
    r = fn(*a, **k)
    return r, time.perf_counter() - t


def test_site_and_sources_are_real(real):
    site = real.get("/site").json()
    assert not site["manifest"].get("fixture")
    assert site["bounds"]["res_m"] == 2.0 and site["bounds"]["half_size_m"] == 4096
    wp = _waypoints()[0]
    s = real.get("/terrain/sample", params={"at": f"poi:{wp['id']}"}).json()
    assert "HiRISE DTM" in s["sources"]["elevation"] and "10.5066/P9REJ9JN" in s["sources"]["elevation"]
    assert s["elevation_m"] is not None and abs(s["elevation_m"] - wp["z"]) < 1.0
    assert s["layers"]["min_carbonate"] is not None  # synthetic layer loaded on the real grid ...
    assert real.get("/search/targets").json()["targets"][0]["data"] == "synthetic"  # ... and labelled so


def test_route_3km_between_real_waypoints(real):
    wps = _waypoints()
    a = wps[0]
    b = min(wps, key=lambda p: abs(math.hypot(p["x"] - a["x"], p["y"] - a["y"]) - 3000))
    assert abs(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) - 3000) < 300
    r, dt = timed(real.post, "/route", json={"start": f"poi:{a['id']}", "goal": f"poi:{b['id']}",
                                            "profile": "rover_scout"})
    body = r.json()
    assert r.status_code == 200 and body["feasible"], body
    assert body["max_slope_deg"] <= body["slope_limit_deg"]
    assert body["distance_m"] >= body["direct_line"]["distance_m"] * 0.98
    assert "HiRISE" in body["sources"]["slope"]
    assert dt < 3.0, f"/route took {dt:.2f} s (target < 3 s)"


def test_search_sites_fast(real):
    r, dt = timed(real.post, "/search/sites", json={"target": "carbonate", "near": {"x": 1849, "y": -437}})
    body = r.json()
    assert r.status_code == 200 and body["count"] >= 1, body
    assert "synthetic" in body.get("caveat", "")
    assert all(s["reachable"] is not None for s in body["sites"])
    assert dt < 2.0, f"/search/sites took {dt:.2f} s (target < 2 s)"


def test_science_traverse_4h_fast(real):
    r, dt = timed(real.post, "/route/science", json={"start": {"x": 1849, "y": -437}, "time_budget_min": 240,
                                                      "interests": ["carbonate"]})
    body = r.json()
    assert r.status_code == 200 and body.get("feasible"), body
    assert body["duration_min"] + body["stop_minutes"] <= 240
    assert dt < 5.0, f"/route/science took {dt:.2f} s (target < 5 s)"


def test_validation_on_real_traverse(real):
    r, dt = timed(real.get, "/validation")
    v = r.json()
    assert v["available"] and v["traverse_drives"] > 100 and v["traverse_length_m"] > 10000
    assert 0 <= v["hazard_agreement"] <= 1
    assert v["hazard_agreement"] >= 0.95  # Perseverance drove almost entirely on ground we class drivable
    assert v["hazard_share"]["no-go"] < v["site_hazard_share"]["no-go"]  # it avoided steep ground
    assert v["route_realism_summary"]["planned"] >= 4
    assert dt < 10.0, f"/validation took {dt:.2f} s (target < 10 s)"
    _, dt2 = timed(real.get, "/validation")
    assert dt2 < 0.5  # cached
    print("\nhazard_agreement", v["hazard_agreement"], v["hazard_share"], "site", v["site_hazard_share"],
          "route_realism", v["route_realism_summary"])
