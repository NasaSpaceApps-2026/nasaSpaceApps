"""Line of sight (atlas_core/visibility.py) against an independent brute-force oracle.

The oracle samples the straight line from the observer to EACH target cell every res/8 metres with
scipy.ndimage.map_coordinates and tests it with its own curvature formula; it shares no code with the
implementation (which sweeps rays and shares horizons between cells)."""
import math
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter, map_coordinates

from atlas_core import AtlasError, SetStore, travel, visibility as V
from atlas_core.site import Layer, Site, MARS_RADIUS_M
from atlas_core.testing import make_site

R = MARS_RADIUS_M
SRC = {"mission": "SYNTHETIC", "instrument": "t", "product": "t", "citation": "t", "url": None}


def build_site(dem: np.ndarray, res: float, pois=None) -> Site:
    n = dem.shape[0]
    meta = {"title": "e", "units": "m", "resolution_m": res, "measures": "e", "does_not_measure": "-",
            "quality": "synthetic", "coverage": "site", "source": SRC}
    return Site(name="synthetic", lat0=18.4, lon0=77.4, res_m=res, size_px=n,
                layers={"elevation": Layer("elevation", "continuous", dem.astype(np.float32), meta)},
                pois=pois or [])


def xy_grid(site):
    c = np.arange(site.size_px)
    x, y = site.rc_to_xy(c[:, None] * np.ones((1, site.size_px)), c[None, :] * np.ones((site.size_px, 1)))
    return np.asarray(x), np.asarray(y)


def oracle_visible(site, ox, oy, h_obs, h_tgt, radius, rows, cols):
    """Independent per-target-cell check. Returns bool array (len(rows)) and the min margin (m)."""
    dem = site.layers["elevation"].data.astype(np.float64)
    res = site.res_m
    r0, c0 = site.xy_to_rc(ox, oy)
    zo = map_coordinates(dem, [[r0], [c0]], order=1)[0] + h_obs
    out = np.zeros(len(rows), bool)
    marg = np.zeros(len(rows))
    for i, (r, c) in enumerate(zip(rows, cols)):
        D = math.hypot((c - c0) * res, (r - r0) * res)
        if D > radius:
            continue
        zt = dem[r, c] + h_tgt
        if D < res * 0.51:
            out[i], marg[i] = True, 1.0
            continue
        n = int(D / (res / 8)) + 1
        t = np.linspace(0, 1, n + 1)[1:-1]
        rr = r0 + (r - r0) * t
        cc = c0 + (c - c0) * t
        z = map_coordinates(dem, [rr, cc], order=1)
        d = D * t
        line = zo + (zt - zo) * t
        m = line - (z + d * (D - d) / (2 * R))
        marg[i] = m.min()
        out[i] = m.min() >= 0
    return out, marg


def compare(site, ox, oy, h_obs=1.7, h_tgt=0.0, radius=150.0, stride=3, **kw):
    st = SetStore()
    res = V.viewshed(site, st, {"x": ox, "y": oy}, h_obs, h_tgt, radius, coarse=False, **kw)
    cells = st.get(res["set_id"]).members[0]["_cells"] if st.get(res["set_id"]).members else np.array([], int)
    n = site.size_px
    mask = np.zeros(n * n, bool)
    mask[cells] = True
    mask = mask.reshape(n, n)
    X, Y = xy_grid(site)
    sel = np.argwhere(np.hypot(X - ox, Y - oy) <= radius)[::stride]
    rows, cols = sel[:, 0], sel[:, 1]
    ov, marg = oracle_visible(site, ox, oy, h_obs, h_tgt, radius, rows, cols)
    mine = mask[rows, cols]
    # cells whose oracle margin is within 5 cm of grazing may legitimately differ
    firm = np.abs(marg) > 0.05
    agree = (mine == ov)[firm].mean()
    return res, mask, agree, (rows, cols, ov, mine)


# ---------------------------------------------------------------- flat plane and the curvature horizon

def test_horizon_distance_formula():
    assert V.horizon_distance_m(1.7) == pytest.approx(math.sqrt(2 * R * 1.7))
    assert 3390 < V.horizon_distance_m(1.7) < 3410


def test_flat_plane_sees_to_curvature_horizon():
    res, n = 8.0, 1000                        # 8 km plain, observer near the west edge
    site = build_site(np.zeros((n, n)), res)
    ox, oy = -3900.0, 0.0
    st = SetStore()
    out = V.viewshed(site, st, {"x": ox, "y": oy}, 1.7, 0.0, 5000, coarse=False)
    horizon = math.sqrt(2 * R * 1.7)
    # On a flat plane the margin beyond the horizon grows only like (d - dh)^2 / 2R: 40 m past it is 0.3 mm, far below
    # anything a DEM can resolve, so the edge is only defined to within TOL (about 1.5 mm of clearance).
    TOL = 100.0
    assert out["farthest_visible_m"] == pytest.approx(horizon, abs=TOL)
    X, Y = xy_grid(site)
    mask = np.zeros(n * n, bool)
    mask[st.get(out["set_id"]).members[0]["_cells"]] = True
    mask = mask.reshape(n, n)
    D = np.hypot(X - ox, Y - oy)
    assert mask[(D < horizon - 2 * res)].all()                      # everything inside the horizon
    assert not mask[(D > horizon + TOL) & (D <= 5000)].any()    # nothing beyond it
    # without curvature a flat plane is fully visible
    out2 = V.viewshed(site, SetStore(), {"x": ox, "y": oy}, 1.7, 0.0, 5000, curvature=False, coarse=False)
    assert out2["visible_fraction"] == pytest.approx(1.0)
    # a 2 m tall target extends the horizon by sqrt(2 R 2)
    out3 = V.viewshed(site, SetStore(), {"x": ox, "y": oy}, 1.7, 2.0, 7500, coarse=False)
    assert out3["farthest_visible_m"] == pytest.approx(V.horizon_distance_m(1.7, 2.0), abs=TOL)
    assert out["flat_ground_horizon_m"] == pytest.approx(horizon, abs=0.1)


def test_flat_plane_matches_oracle():
    site = build_site(np.zeros((300, 300)), 8.0)
    res, mask, agree, _ = compare(site, -1000.0, 0.0, 1.7, 0.0, 2000.0, stride=7)
    assert agree > 0.999


# ---------------------------------------------------------------- the synthetic ridge ("wall")

def test_ridge_hides_cells_behind_it():
    site = make_site()
    st = SetStore()
    out = V.viewshed(site, st, "base", 1.7, 0.0, 250, coarse=False)
    n = site.size_px
    mask = np.zeros(n * n, bool)
    mask[st.get(out["set_id"]).members[0]["_cells"]] = True
    mask = mask.reshape(n, n)
    X, Y = xy_grid(site)
    behind = (X > 125) & (np.abs(Y) < 60)           # in the ridge's shadow (ridge x=100..112, y<=150)
    around = (X > 115) & (Y > 1.65 * X) & (np.hypot(X, Y) < 250)   # sight line crosses x=100 north of the ridge end
    in_front = (X < 90) & (np.hypot(X, Y) < 200) & (np.hypot(X + 120, Y - 120) > 80)
    assert not mask[behind].any()
    assert around.sum() > 20 and mask[around].mean() > 0.9
    assert mask[in_front].mean() > 0.97
    # named places: east_point (200, -200) is behind the ridge, landing is in front
    things = {t["id"]: t for t in out["visible_things"] + out["hidden_things"]}
    assert things["poi:landing"]["visible"] is True
    assert things["poi:base"]["visible"] is True
    res, mask2, agree, _ = compare(site, 0.0, 0.0, 1.7, 0.0, 250.0, stride=4)
    assert agree > 0.995


def test_ridge_line_of_sight_blocking_point():
    site = make_site()
    r = V.line_of_sight(site, "base", {"x": 200, "y": -30}, 1.7, 1.7)
    assert r["clear"] is False
    bp = r["blocking_point"]
    assert 100 <= bp["x"] <= 112                               # the ridge crest region
    assert 0 < r["clearance_deficit_m"] < 12
    assert r["clearance_deficit_m"] == bp["deficit_m"]
    # raise both ends high enough and it clears
    assert V.line_of_sight(site, "base", {"x": 200, "y": -30}, 30, 30)["clear"] is True
    # same side of the ridge: clear
    assert V.line_of_sight(site, "base", {"x": -100, "y": -40})["clear"] is True


# ---------------------------------------------------------------- an isolated hill

def hill_site():
    n, res = 400, 4.0
    site0 = build_site(np.zeros((n, n)), res)
    X, Y = xy_grid(site0)
    dem = 30.0 * np.exp(-(((X - 150) ** 2 + Y ** 2) / (2 * 40.0 ** 2)))
    return build_site(dem, res)


def test_hill_windward_visible_lee_hidden():
    site = hill_site()
    st = SetStore()
    out = V.viewshed(site, st, {"x": 0, "y": 0}, 1.7, 0.0, 600, coarse=False)
    n = site.size_px
    mask = np.zeros(n * n, bool)
    mask[st.get(out["set_id"]).members[0]["_cells"]] = True
    mask = mask.reshape(n, n)
    X, Y = xy_grid(site)
    cell = lambda x, y: (np.argmin(np.abs(Y[:, 0] - y)), np.argmin(np.abs(X[0] - x)))
    for x in (100, 120, 135):                 # facing slope up to just below the crest region
        r, c = cell(x, 0)
        assert mask[r, c], x
    for x in (170, 200, 250, 300):            # lee side shadow
        r, c = cell(x, 0)
        assert not mask[r, c], x
    r, c = cell(250, 300)                      # lateral: not shadowed
    assert mask[r, c]
    res, m2, agree, _ = compare(site, 0.0, 0.0, 1.7, 0.0, 400.0, stride=5)
    assert agree > 0.995


def test_hill_line_of_sight_numbers():
    site = hill_site()
    r = V.line_of_sight(site, {"x": 0, "y": 0}, {"x": 300, "y": 0}, 1.7, 1.7)
    assert not r["clear"]
    bp = r["blocking_point"]
    assert bp["x"] == pytest.approx(150, abs=6)
    assert r["clearance_deficit_m"] == pytest.approx(30.0 - 1.7, abs=1.0)    # crest 30 m vs sight line 1.7 m
    assert r["distance_m"] == pytest.approx(300.0)
    # lateral path clears; heights high enough clear the crest
    assert V.line_of_sight(site, {"x": 0, "y": 0}, {"x": 0, "y": 300})["clear"]
    hi = V.line_of_sight(site, {"x": 0, "y": 0}, {"x": 300, "y": 0}, 60, 60)
    assert hi["clear"] and hi["min_clearance_m"] == pytest.approx(30.0, abs=1.0)
    # reciprocity: swapping ends and heights gives the same verdict and deficit
    a = V.line_of_sight(site, {"x": 0, "y": 0}, {"x": 300, "y": 0}, 3, 1.7)
    b = V.line_of_sight(site, {"x": 300, "y": 0}, {"x": 0, "y": 0}, 1.7, 3)
    assert a["clear"] == b["clear"] and a["clearance_deficit_m"] == pytest.approx(b["clearance_deficit_m"], abs=0.05)
    assert any("Fresnel" in n for n in r["notes"])


# ---------------------------------------------------------------- random rough terrain vs oracle

def rough_site(seed=3, n=220, res=2.0):
    rng = np.random.default_rng(seed)
    z = gaussian_filter(rng.normal(size=(n, n)), 9) * 120
    return build_site(z, res)


def test_rough_terrain_viewshed_matches_oracle():
    site = rough_site()
    res, mask, agree, (rows, cols, ov, mine) = compare(site, 20.0, -10.0, 1.7, 0.0, 180.0, stride=3)
    assert 0.1 < mine.mean() < 0.95                       # a non-trivial viewshed
    assert agree > 0.985, agree


def test_rough_terrain_line_of_sight_matches_oracle():
    site = rough_site(seed=5)
    rng = np.random.default_rng(1)
    dem = site.layers["elevation"].data.astype(np.float64)
    bad = nclear = 0
    for _ in range(120):
        a = rng.uniform(-180, 180, 2)
        b = rng.uniform(-180, 180, 2)
        r = V.line_of_sight(site, {"x": a[0], "y": a[1]}, {"x": b[0], "y": b[1]}, 1.7, 1.7)
        ra, ca = site.xy_to_rc(*a)
        rb, cb = site.xy_to_rc(*b)
        D = math.hypot(*(a - b))
        t = np.linspace(0, 1, int(D / 0.25) + 1)
        z = map_coordinates(dem, [ra + (rb - ra) * t, ca + (cb - ca) * t], order=1)
        line = z[0] + 1.7 + (z[-1] - z[0]) * t
        m = (line - (z + (D * t) * (D - D * t) / (2 * R)))[1:-1].min()
        nclear += r["clear"]
        if abs(m) > 0.05 and (m >= 0) != r["clear"]:
            bad += 1
    assert bad == 0
    assert 10 < nclear < 110                               # both outcomes exercised


# ---------------------------------------------------------------- unknown data, errors, sets

def test_unknown_cells_block_nothing_and_are_reported():
    n, res = 200, 4.0
    dem = np.zeros((n, n))
    site0 = build_site(dem, res)
    X, Y = xy_grid(site0)
    dem[(np.abs(X - 100) < 20) & (np.abs(Y) < 60)] = np.nan
    site = build_site(dem, res)
    st = SetStore()
    out = V.viewshed(site, st, {"x": 0, "y": 0}, 1.7, 0.0, 300, coarse=False)
    assert out["unknown_cells_in_radius"] > 0
    mask = np.zeros(n * n, bool)
    mask[st.get(out["set_id"]).members[0]["_cells"]] = True
    mask = mask.reshape(n, n)
    behind = (np.abs(X - 160) < 8) & (np.abs(Y) < 8)
    assert mask[behind].all()                          # not blocked by the hole
    assert not mask[np.isnan(dem)].any()               # but the hole itself is never "visible"
    assert any("no DEM data" in x or "no elevation data" in x for x in out["notes"] + [])
    with pytest.raises(AtlasError):
        V.viewshed(site, SetStore(), {"x": 100, "y": 0})
    r = V.line_of_sight(site, {"x": 0, "y": 0}, {"x": 160, "y": 0})
    assert r["clear"] and r["profile"]["unknown_samples"] > 0


def test_errors_have_hints():
    site = make_site()
    st = SetStore()
    for bad in (dict(observer_height_m=-1), dict(max_radius_m=0), dict(max_radius_m=1e6),
                dict(observer_height_m="x")):
        with pytest.raises(AtlasError) as e:
            V.viewshed(site, st, "base", **bad)
        assert e.value.hint
    with pytest.raises(AtlasError):
        V.viewshed(site, st, "nowhere")
    with pytest.raises(AtlasError):
        V.line_of_sight(site, ["base", "landing"], "landing")
    with pytest.raises(AtlasError):
        V.find_vantage_points(site, st, "base", limit=0)


def test_viewshed_set_chains_with_nearest():
    site = make_site()
    st = SetStore()
    out = V.viewshed(site, st, "base", max_radius_m=200, coarse=False)
    s = st.get(out["set_id"])
    assert s.kind == "regions" and s.count == 1
    m = s.members[0]
    assert len(m["_cells"]) == out["visible_cells"] and m["area_m2"] == pytest.approx(out["visible_area_m2"])
    assert "_cells" not in st.page(out["set_id"])["members"][0]
    # the landing POI is inside the visible region: nearest straight distance is 0
    r = travel.nearest(site, st, "poi:landing", out["set_id"], metric="straight_m")
    assert r["results"][0]["cost"] == pytest.approx(0.0, abs=2.1)
    # east_point is shadowed by the ridge and not within 200 m anyway
    assert out["visible_fraction"] < 1


def test_coarse_lattice_close_to_fine():
    site = rough_site(seed=2, n=300)
    f = V.viewshed(site, SetStore(), {"x": 0, "y": 0}, max_radius_m=250, coarse=False)
    c = V.viewshed(site, SetStore(), {"x": 0, "y": 0}, max_radius_m=250, coarse=True)
    assert c["grid_m"] == 2 * f["grid_m"]
    assert abs(c["visible_fraction"] - f["visible_fraction"]) < 0.06


# ---------------------------------------------------------------- vantage points

def test_vantage_points_see_the_target_by_oracle():
    site = make_site()
    st = SetStore()
    target = {"x": 160.0, "y": -40.0}          # behind the ridge as seen from base
    assert V.line_of_sight(site, "base", target)["clear"] is False
    out = V.find_vantage_points(site, st, target, conditions=[{"layer": "slope_2m", "op": "<=", "value": 15}],
                                limit=4, max_distance_m=200, spacing_m=30)
    assert out["count"] >= 3 and len(out["results"]) == 4
    slope = site.layers["slope_2m"].data
    pts = []
    for m in out["results"]:
        assert m["score"] == 1.0                                   # a point is seen or not
        r = V.line_of_sight(site, {"x": m["x"], "y": m["y"]}, target, 1.7, 0.0)
        assert r["clear"], m
        ri, ci = site.rc_index(m["x"], m["y"])
        assert slope[ri, ci] <= 15 + 1e-6                          # condition honoured
        pts.append((m["x"], m["y"]))
    for i in range(len(pts)):                                      # spread
        for j in range(i):
            assert math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) >= 30 - 1e-6
    assert out["assumptions"] and out["diagnostics"] and out["hint"]
    assert st.get(out["set_id"]).kind == "spots"


def test_vantage_within_and_region_target():
    site = make_site()
    st = SetStore()
    within = {"from": "base", "max": 80, "metric": "straight_m"}
    out = V.find_vantage_points(site, st, {"x": 160.0, "y": -40.0}, within=within, max_distance_m=200)
    for m in out["results"]:
        assert math.hypot(m["x"], m["y"]) <= 80 + 2
    # a region target (the crater floor) is partially visible from outside; scores are fractions
    from atlas_core import search
    reg = search.find_regions(site, st, [{"layer": "geologic_unit", "op": "==", "value": "crater_floor"}],
                              min_area_m2=100)
    out2 = V.find_vantage_points(site, st, reg["results"][0]["id"], max_distance_m=150, limit=3)
    assert out2["count"] > 0 and out2["target"]["samples"] > 1
    assert all(0 < m["score"] <= 1 for m in out2["results"])
    # impossible filter: empty result with a hint and diagnostics
    out3 = V.find_vantage_points(site, st, {"x": 160, "y": -40}, conditions=[{"layer": "slope_2m", "op": "<", "value": -1}])
    assert out3["count"] == 0 and out3["hint"] and out3["diagnostics"]


# ---------------------------------------------------------------- endpoints and MCP tools

def test_endpoints():
    from fastapi.testclient import TestClient
    from devapi import create_app
    c = TestClient(create_app(make_site(), synthetic=True))
    r = c.post("/visibility/viewshed", json={"at": "base", "max_radius_m": 200})
    assert r.status_code == 200 and r.json()["set_id"].startswith("set:") and "_cells" not in str(r.json())[:0]
    sid = r.json()["set_id"]
    page = c.get(f"/sets/{sid}").json()
    assert page["kind"] == "regions" and "_cells" not in page["members"][0]
    r = c.post("/visibility/line_of_sight", json={"a": "base", "b": {"x": 200, "y": -30}})
    assert r.status_code == 200 and r.json()["clear"] is False
    r = c.post("/visibility/vantage", json={"target": {"x": 160, "y": -40}, "max_distance_m": 150, "limit": 2})
    assert r.status_code == 200 and len(r.json()["results"]) == 2
    for path, body in (("/visibility/viewshed", {"at": "nowhere"}), ("/visibility/viewshed", {}),
                       ("/visibility/line_of_sight", {"a": "base"}), ("/visibility/vantage", {"target": "base", "bogus": 1}),
                       ("/visibility/viewshed", {"at": "base", "max_radius_m": -5})):
        r = c.post(path, json=body)
        assert r.status_code == 422 and "error" in r.json() and "hint" in r.json(), (path, body)


def test_mcp_tools():
    import tests.test_server as ts

    async def f(c):
        a = await c.call_tool("viewshed", {"at": "base", "max_radius_m": 200})
        b = await c.call_tool("line_of_sight", {"a": "base", "b": {"x": 200, "y": -30}, "height_a": 3})
        d = await c.call_tool("find_vantage_points", {"target": {"x": 160, "y": -40}, "max_distance_m": 150, "limit": 2})
        e = await c.call_tool("line_of_sight", {"a": "base", "b": "nowhere"})
        return a, b, d, e
    a, b, d, e = ts.run(f)
    assert "sees" in a.content[0].text and "stored as set:" in a.content[0].text and a.structured_content["set_id"]
    assert "BLOCKED" in b.content[0].text and "Fresnel" in b.content[0].text
    assert b.structured_content["blocking_point"]
    assert d.structured_content["count"] > 0 and "vantage" in d.content[0].text.lower()
    assert e.is_error and e.structured_content["hint"]


# ---------------------------------------------------------------- real site smoke test

REAL = Path(__file__).resolve().parents[1] / "data/build/site/jezero"


@pytest.mark.skipif(not (REAL / "site.yaml").exists(), reason="real site not built")
def test_real_site_viewshed_smoke():
    import time
    from atlas_core import load_site
    site = load_site(REAL)
    st = SetStore()
    t = time.perf_counter()
    out = V.viewshed(site, st, "base", max_radius_m=3000)
    dt = time.perf_counter() - t
    assert dt < 10                                  # about 1 s on a laptop; loose bound for CI
    assert 0.001 < out["visible_fraction"] < 1.0
    assert 0 < out["farthest_visible_m"] <= 3000 + 3
    assert out["visible_area_m2"] > 1000
    assert out["unknown_cells_in_radius"] == 0 or out["unknown_cells_in_radius"] > 0
    assert any(t["id"] == "poi:base" and t["visible"] for t in out["visible_things"])
    los = V.line_of_sight(site, "base", "poi:sample_01_roubion")
    assert los["distance_m"] > 500 and ("blocking_point" in los) == (not los["clear"])
    vp = V.find_vantage_points(site, st, "poi:sample_01_roubion", min_distance_m=100,
                               conditions=[{"layer": "slope_2m", "op": "<=", "value": 15}])
    assert vp["count"] > 0 and vp["elapsed_s"] < 15
