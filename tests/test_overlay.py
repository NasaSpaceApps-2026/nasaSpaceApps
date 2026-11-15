"""Image-overlay endpoints (devapi/routes_overlay.py) on the synthetic test site."""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from atlas_core.testing import make_site
from devapi import create_app
from devapi.routes_overlay import build_traverse


@pytest.fixture(scope="module")
def site():
    return make_site()


@pytest.fixture(scope="module")
def c(site):
    return TestClient(create_app(site, synthetic=True))


def png(c, path, **params):
    r = c.get(path, params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    im = Image.open(io.BytesIO(r.content))
    assert im.mode == "RGBA"
    return np.asarray(im)


def meta(c, path, **params):
    r = c.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_png_dimensions_and_bounds(c, site):
    a = png(c, "/overlay/geology.png")
    assert a.shape == (site.size_px, site.size_px, 4)       # <= 2048 and exactly the site grid
    m = meta(c, "/overlay/geology.json")
    assert m["bounds"]["half_m"] == site.half_m and m["bounds"]["lat0"] == site.lat0 and m["bounds"]["lon0"] == site.lon0
    for k in ("title", "legend", "source", "caveat", "stats"):
        assert m[k]
    assert all(set(e) == {"color", "label"} and e["color"].startswith("#") and len(e["color"]) == 7 for e in m["legend"])


def test_evidence_tiers_orientation_and_transparency(c, site):
    a = png(c, "/overlay/evidence.png", layer="carbonate")
    t = site.layer("carbonate").data
    # confirmed single cell and strong block are painted, not_detected is transparent, no_coverage is faint
    assert (a[t == 3][:, 3] > 150).all()
    assert (a[t == 0][:, 3] == 0).all()
    nc = a[t == -1][:, 3]
    assert nc.max() <= 100 and nc.max() > 0
    # strong and weak differ in colour; north is row 0 (no_coverage band is Y > 200, i.e. the top rows)
    assert not np.array_equal(a[t == 3][0, :3], a[t == 2][0, :3])
    assert (t[:10] == -1).all() and a[:10, :, 3].max() > 0
    m = meta(c, "/overlay/evidence.json", layer="carbonate")
    assert {"Confirmed by the rover on the ground", "Strong signal from orbit"} <= {e["label"] for e in m["legend"]}
    assert m["stats"]["layer"] == "carbonate" and "tiers_km2" in m["stats"]


def test_evidence_bad_layer_is_422(c):
    assert c.get("/overlay/evidence.png", params={"layer": "nope"}).status_code == 422
    assert c.get("/overlay/evidence.png", params={"bogus": 1}).status_code == 422
    assert c.get("/overlay/nothing.png").status_code == 422


def test_geology_legend_has_unit_names(c, site):
    m = meta(c, "/overlay/geology.json")
    names = set(site.layer("geologic_unit").classes.values())
    assert names <= {e["label"] for e in m["legend"]}
    a = png(c, "/overlay/geology.png")
    assert a[..., 3].max() > 0


def test_slope_profiles(c, site):
    s = site.layer("slope_2m").data
    a = png(c, "/overlay/slope.png", profile="eva_walkable")
    nogo = np.isfinite(s) & (s > 25)
    safe = np.isfinite(s) & (s <= 15)
    assert nogo.any() and safe.any()
    assert (a[nogo][:, 0] > 200).all()          # red
    assert a[safe][:, 3].max() < a[nogo][:, 3].min()   # safe is fainter than no-go
    m = meta(c, "/overlay/slope.json", profile="rover_scout")
    assert m["stats"]["limits_source"] == "assumed" and 0 <= m["stats"]["no_go_fraction"] <= 1


def test_lake_area_monotonic_and_depth(c, site):
    z = site.layer("elevation").data
    lo, hi = float(np.nanmin(z)), float(np.nanmax(z))
    levels = np.linspace(lo - 0.5, hi, 6)
    areas = []
    for lv in levels:
        m = meta(c, "/overlay/lake.json", level=lv)
        areas.append(m["stats"]["area_km2"])
        assert m["stats"]["max_depth_m"] >= 0
    assert areas == sorted(areas) and areas[-1] > areas[0]
    a = png(c, "/overlay/lake.png", level=(lo + hi) / 2)
    assert a[..., 3].max() > 0 and (a[..., 3] == 0).any()      # water and dry ground
    assert meta(c, "/overlay/lake.json", level=(lo + hi) / 2)["stats"]["named_levels"]


def test_lake_level_out_of_range(c):
    assert c.get("/overlay/lake.png", params={"level": 99999}).status_code == 422


def test_sun_shadow_low_vs_noon(c):
    low = meta(c, "/overlay/sun.json", ltst=17.8, sol=1000)
    noon = meta(c, "/overlay/sun.json", ltst=12, sol=1000)
    assert low["stats"]["sun_elevation_deg"] < noon["stats"]["sun_elevation_deg"]
    shade = lambda s: s["cast_shadow_fraction"] + s["self_shadow_fraction"]
    assert shade(low["stats"]) > shade(noon["stats"])
    for k in ("sunrise_ltst", "sunset_ltst", "sun_azimuth_deg"):
        assert k in noon["stats"]
    a_low = png(c, "/overlay/sun.png", ltst=17.8, sol=1000)
    a_noon = png(c, "/overlay/sun.png", ltst=12, sol=1000)
    assert (a_low[..., 3] > 0).sum() > (a_noon[..., 3] > 0).sum()


def test_sun_night_is_dark(c):
    m = meta(c, "/overlay/sun.json", ltst=0, sol=1000)
    assert m["stats"]["sun_up"] is False and m["stats"]["lit_fraction"] == 0.0
    a = png(c, "/overlay/sun.png", ltst=0, sol=1000)
    assert (a[..., 3] > 0).mean() > 0.5


def test_viewshed_sanity(c, site):
    m = meta(c, "/overlay/viewshed.json", x=0, y=0, h=1.7, radius=200)
    assert 0 < m["stats"]["visible_km2"] <= np.pi * 0.2 ** 2 * 1.01
    a = png(c, "/overlay/viewshed.png", x=0, y=0, h=1.7, radius=200)
    assert a[..., 3].max() > 0
    far = png(c, "/overlay/viewshed.png", x=0, y=0, h=1.7, radius=200)[0, 0]
    assert far[3] == 0           # corner is outside the radius
    tall = meta(c, "/overlay/viewshed.json", x=0, y=0, h=50, radius=200)["stats"]["visible_km2"]
    assert tall >= m["stats"]["visible_km2"]


def test_envelope_sanity(c):
    small = meta(c, "/overlay/envelope.json", x=0, y=0, budget_min=6)["stats"]
    big = meta(c, "/overlay/envelope.json", x=0, y=0, budget_min=12)["stats"]
    assert 0 < small["area_km2"] < big["area_km2"]
    assert small["max_radius_m"] < big["max_radius_m"]
    # one-way limit = budget * (1 - reserve) / 2 minutes at <= 0.9 m/s
    assert small["max_radius_m"] <= small["one_way_limit_min"] * 60 * 0.9 + 15
    a = png(c, "/overlay/envelope.png", x=0, y=0, budget_min=6)
    assert a[..., 3].max() > 0 and (a[..., 3] == 0).any()
    assert c.get("/overlay/envelope.png", params={"profile": "nope"}).status_code == 422


def test_cache_hit_is_fast_and_shared(c, site, monkeypatch):
    import time
    from devapi import routes_overlay as RO
    calls = []
    orig = RO.BUILDERS["lake"]
    monkeypatch.setitem(RO.BUILDERS, "lake", (lambda ctx, p: (calls.append(1), orig[0](ctx, p))[1], orig[1]))
    z = float(np.nanmedian(site.layer('elevation').data)) + 0.25
    c.get("/overlay/lake.png", params={"level": z})
    c.get("/overlay/lake.json", params={"level": z})        # same params -> same computation
    t = time.perf_counter()
    c.get("/overlay/lake.png", params={"level": z})
    assert time.perf_counter() - t < 0.1
    assert len(calls) == 1


def test_kinds_schema(c):
    k = c.get("/overlay/kinds").json()
    by = {e["kind"]: e for e in k["kinds"]}
    assert set(by) == {"evidence", "geology", "slope", "lake", "sun", "viewshed", "envelope"}
    for e in by.values():
        assert e["title"] and isinstance(e["params"], list)
        for p in e["params"]:
            assert {"name", "type", "default"} <= set(p)
            if p["type"] == "enum":
                assert p["options"] and p["default"] in p["options"]
            elif p["type"] in ("number", "integer") and isinstance(p["default"], (int, float)):
                assert p["min"] <= p["default"] <= p["max"]
    nl = by["lake"]["named_levels"]
    assert nl and all({"id", "level_m", "label", "citation"} <= set(n) for n in nl)
    assert k["image"]["half_m"] > 0


def test_cors_any_localhost_origin(c):
    r = c.get("/overlay/kinds", headers={"Origin": "http://localhost:5199"})
    assert r.headers["access-control-allow-origin"] in ("*", "http://localhost:5199")
    r = c.options("/overlay/geology.png", headers={"Origin": "http://127.0.0.1:3000",
                                                   "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] in ("*", "http://127.0.0.1:3000")


def test_traverse_geojson_with_sols(site):
    xs = [0.0, 10.0, 20.0, 30.0]
    trav = {"type": "FeatureCollection", "metadata": {}, "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[77.0, 18.0]] * 4},
         "properties": {"x": xs, "y": [0.0] * 4}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.0, 18.0]}, "properties": {"sol": 5, "x": 30.0, "y": 0.0}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.0, 18.0]}, "properties": {"sol": 2, "x": 10.0, "y": 0.0}}]}
    cl = TestClient(create_app(site, synthetic=True, traverse=trav))
    g = cl.get("/traverse.geojson").json()
    pts = [f for f in g["features"] if f["geometry"]["type"] == "Point"]
    assert [p["properties"]["sol"] for p in pts] == [2, 5]
    assert [p["properties"]["vertex_index"] for p in pts] == [1, 3]
    assert g["metadata"]["sol_first"] == 2 and g["metadata"]["sol_last"] == 5
    assert build_traverse(site, {"type": "FeatureCollection", "features": []})["features"] == []
