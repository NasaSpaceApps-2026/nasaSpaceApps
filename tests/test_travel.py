"""Travel cost fields, within_mask and nearest (atlas_core.travel): MCP_Geometric cost semantics, unit conversions, snapping, against tests/oracle_travel.py."""
import copy
import math
import time

import numpy as np
import pytest
from skimage.graph import MCP_Geometric

from atlas_core.errors import AtlasError
from atlas_core.refs import resolve_ref
from atlas_core.sets import SetStore
from atlas_core.site import Layer, Site
from atlas_core.testing import make_site
from atlas_core.travel import METRICS, nearest, travel_field, within_mask

from tests.oracle_travel import oracle_field

BASE = {"x": 0.0, "y": 0.0}
LANDING = {"x": 50.0, "y": -50.0}
OUTCROP = {"x": -151.0, "y": -151.0}
EAST = {"x": 200.0, "y": -200.0}
CELL = 4.0   # planning cell for the 2 m synthetic site


@pytest.fixture(scope="module")
def site():
    return make_site()


def at(site, f, p):
    r, c = site.rc_index(p["x"], p["y"])
    return float(f[r, c])


def flat_site():
    s = make_site()
    s = copy.copy(s)
    s.layers = dict(s.layers)
    old = s.layers["slope_2m"]
    s.layers["slope_2m"] = Layer("slope_2m", "continuous", np.zeros_like(old.data), old.meta)
    return s


# ---- MCP_Geometric semantics (what the conversion relies on) ------------------

def test_mcp_geometric_cost_semantics():
    # edge cost = mean of the two cell costs x step length (1 or sqrt 2)
    c = np.array([[1.0, 3.0, 1.0]])
    cost, _ = MCP_Geometric(c, fully_connected=True).find_costs([(0, 0)])
    assert cost[0].tolist() == pytest.approx([0.0, 2.0, 4.0])
    g = np.ones((3, 3))
    cost, _ = MCP_Geometric(g, fully_connected=True).find_costs([(0, 0)])
    assert cost[2, 2] == pytest.approx(2 * math.sqrt(2))
    # inf cells are never entered
    g[1, :] = np.inf
    cost, _ = MCP_Geometric(g, fully_connected=True).find_costs([(0, 0)])
    assert np.isinf(cost[1:]).all()


def test_walk_m_unit_conversion_straight_line(site):
    """A straight east-west run of N planning steps on flat ground is exactly N * cell_m metres."""
    s = flat_site()
    y = 1.0
    f = travel_field(s, [{"x": -100.0, "y": y}], "walk_m")
    # planning cells are 4 m: analysis cell centres -99 (cell -100..-96 block centre = -98) and +98
    b = at(s, f, {"x": 100.0, "y": y})
    assert at(s, f, {"x": -100.0, "y": y}) == 0.0
    # 50 planning steps apart (200 m / 4 m): cost must be 200 exactly
    assert b == pytest.approx(200.0, abs=1e-3)


# ---- flat grid ----------------------------------------------------------------

def test_flat_walk_vs_straight_and_time():
    s = flat_site()
    src = {"x": -101.0, "y": 77.0}
    straight = travel_field(s, [src], "straight_m")
    wm = travel_field(s, [src], "walk_m")
    wt = travel_field(s, [src], "walk_time_min")
    # Bound: 8-connected octile path <= Euclid / cos(pi/8) = 1.0824 x Euclid (worst-case direction),
    # and the source and target are both quantised to a 4 m planning cell centre, each moving the
    # point by at most half a planning-cell diagonal (2.83 m). So walk in [straight - 5.7, 1.0824 straight + 5.7].
    assert np.isfinite(wm).all()
    lo = straight - 2 * 2.83 - 1e-3
    hi = straight / math.cos(math.pi / 8) + 2 * 2.83 + 1e-3
    assert (wm >= lo).all()
    assert (wm <= hi).all()
    # time: 1 m/s flat speed
    flat_speed = s.profiles["eva_walkable"]["travel"]["flat_speed_mps"]
    np.testing.assert_allclose(wt, wm / flat_speed / 60.0, rtol=0.01, atol=1e-6)


def test_straight_m_exact(site):
    f = travel_field(site, [BASE], "straight_m")
    assert f.dtype == np.float32 and f.shape == (256, 256)
    r, c = site.rc_index(0.0, 0.0)
    x, y = site.rc_to_xy(r, c)
    assert f[r, c] == pytest.approx(math.hypot(x, y))     # cell centre (1, -1)
    assert at(site, f, {"x": 201.0, "y": -201.0}) == pytest.approx(math.hypot(201 - 1, -201 + 1) + 0, abs=2.9)
    # exact for an arbitrary cell
    rr, cc = 10, 200
    x, y = site.rc_to_xy(rr, cc)
    assert f[rr, cc] == pytest.approx(math.hypot(x - 0.0, y - 0.0), rel=1e-6)


def test_straight_m_multi_source_many(site):
    pts = [{"x": float(-200 + 13 * i), "y": float(150 - 11 * i)} for i in range(30)]
    f = travel_field(site, pts, "straight_m")
    x, y = site.rc_to_xy(120, 77)
    exp = min(math.hypot(x - p["x"], y - p["y"]) for p in pts)
    assert f[120, 77] == pytest.approx(exp, rel=1e-5)


# ---- terrain: ridge, hole, crater ---------------------------------------------

def test_ridge_detour_matches_oracle(site):
    wm = travel_field(site, [BASE], "walk_m")
    st = travel_field(site, [BASE], "straight_m")
    w, d = at(site, wm, EAST), at(site, st, EAST)
    assert d == pytest.approx(math.hypot(200, 200), abs=3)
    # Must reach y > 150 and come back: lower bound is the two-leg path via (100, 152)
    via = math.hypot(100, 152) + math.hypot(100, 352)
    assert w > 1.4 * d
    assert w >= via * 0.97
    # oracle
    of, cell = oracle_field(site, [(0.0, 0.0)], "walk_m")
    r, c = site.rc_index(200.0, -200.0)
    assert abs(w - of[r // 2, c // 2]) <= cell
    assert np.allclose(of[np.isfinite(of)].max(), wm[np.isfinite(wm)].max(), atol=cell)


@pytest.mark.parametrize("metric", ["walk_m", "walk_time_min"])
def test_matches_oracle_everywhere(site, metric):
    f = travel_field(site, [BASE], metric)
    of, cell = oracle_field(site, [(0.0, 0.0)], metric)
    pf = f[::2, ::2]
    both = np.isfinite(of)
    assert (np.isfinite(pf) == both).all()
    np.testing.assert_allclose(pf[both], of[both], rtol=1e-5, atol=1e-4)


def test_impassable_cells_are_inf(site):
    f = travel_field(site, [BASE], "walk_m")
    # no-data hole centred (0, -150), 20 m square
    for p in [(0, -150), (8, -145), (-8, -155)]:
        assert np.isinf(at(site, f, {"x": p[0], "y": p[1]}))
    # ridge body (59 deg sides) x = 100..112, y <= 150
    for x in (102, 106, 110):
        assert np.isinf(at(site, f, {"x": x, "y": 0.0}))
    # slope-above-limit cells are inf, and no passable analysis cell exceeds the limit
    slope = site.layers["slope_2m"].data
    steep = ~(slope <= 25)
    assert np.isinf(f[steep]).all()
    # crater walls are impassable, so the crater is unreachable from base (floor sealed off)
    assert np.isinf(at(site, f, {"x": -120.0, "y": 120.0}))
    assert np.isfinite(at(site, f, {"x": 0.0, "y": 100.0}))


def test_reachable_path_never_crosses_hole_or_ridge(site):
    """Distances beside the hole exceed straight-line: detour is forced."""
    f = travel_field(site, [{"x": 0.0, "y": -100.0}], "walk_m")
    south = at(site, f, {"x": 0.0, "y": -200.0})
    assert south > 105      # straight is 100 m; the 20 m hole forces a sidestep
    assert south < 130


def test_profile_without_travel_raises(site):
    with pytest.raises(AtlasError) as e:
        travel_field(site, [BASE], "walk_m", profile="landing_pad")
    assert "eva_walkable" in e.value.hint and "rover_scout" in e.value.hint
    with pytest.raises(AtlasError):
        travel_field(site, [BASE], "walk_m", profile="nope")


def test_rover_profile_slower(site):
    a = at(site, travel_field(site, [BASE], "walk_time_min", "eva_walkable"), LANDING)
    b = at(site, travel_field(site, [BASE], "walk_time_min", "rover_scout"), LANDING)
    assert b / a == pytest.approx(25.0, rel=0.1)


def test_slope_slows_walking():
    """Time cost uses the slope curve; on a uniform 10 degree slope, speed is exp(-3.5 tan 10)."""
    s = flat_site()
    s.layers["slope_2m"] = Layer("slope_2m", "continuous", np.full((256, 256), 10.0, np.float32), s.layers["slope_2m"].meta)
    src = {"x": -101.0, "y": 1.0}
    wt = travel_field(s, [src], "walk_time_min")
    wm = travel_field(s, [src], "walk_m")
    v = 1.0 * math.exp(-3.5 * math.tan(math.radians(10)))
    np.testing.assert_allclose(wt, wm / v / 60.0, rtol=1e-4, atol=1e-6)


# ---- multi-source ---------------------------------------------------------------

@pytest.mark.parametrize("metric", METRICS)
def test_multi_source_is_elementwise_min(site, metric):
    a = travel_field(site, [BASE], metric)
    b = travel_field(site, [LANDING], metric)
    c = travel_field(site, [OUTCROP], metric)
    both = travel_field(site, [BASE, LANDING, OUTCROP], metric)
    np.testing.assert_allclose(both, np.minimum(np.minimum(a, b), c), rtol=1e-6, atol=1e-5)


# ---- within_mask ----------------------------------------------------------------

def test_within_mask(site):
    store = SetStore()
    mask, field, label = within_mask(site, {"from": "base", "max": 5, "metric": "walk_time_min"}, store)
    assert (mask == (field <= 5)).all()
    assert label == "within 5 walk_time_min of base"
    assert mask[site.rc_index(0.0, 0.0)]
    assert not mask[site.rc_index(200.0, -200.0)]
    m2, f2, l2 = within_mask(site, {"from": "poi:base", "max": 100, "metric": "straight_m"}, store)
    assert m2.sum() == pytest.approx(math.pi * 100 ** 2 / 4, rel=0.03)
    assert "poi:base" in l2


def test_within_mask_set_and_errors(site):
    store = SetStore()
    s = store.create("points", "test", {}, [dict(m) for m in resolve_ref(site, ["base", "poi:landing"], None)])
    m, f, label = within_mask(site, {"from": s.set_id, "max": 50, "metric": "walk_m"}, store)
    single = within_mask(site, {"from": "base", "max": 50, "metric": "walk_m"}, store)[0]
    assert m.sum() > single.sum()
    with pytest.raises(AtlasError) as e:
        within_mask(site, {"from": "base", "max": 5, "metric": "furlongs"}, store)
    assert "walk_time_min" in e.value.hint
    with pytest.raises(AtlasError):
        within_mask(site, {"from": "base", "metric": "walk_m"}, store)
    with pytest.raises(AtlasError):
        within_mask(site, {"from": "base", "max": "far", "metric": "walk_m"}, store)


# ---- nearest --------------------------------------------------------------------

def _pts_set(store, pts, prefix="p"):
    return store.create("points", "test", {}, [
        {"id": f"{prefix}{i}", "name": f"{prefix}{i}", "x": p[0], "y": p[1], "lat": 0.0, "lon": 0.0, "score": 0}
        for i, p in enumerate(pts)]).set_id


def test_nearest_basic(site):
    store = SetStore()
    out = nearest(site, store, ["base", "poi:landing"], ["poi:outcrop_a", "poi:east_point"], "walk_time_min")
    assert out["count"] == 2 and out["unreachable"] == [] and out["approximate"] is True
    assert any("placeholder" in n.lower() for n in out["notes"])
    assert any("approximate" in n.lower() for n in out["notes"])
    costs = [r["cost"] for r in out["results"]]
    assert costs == sorted(costs)
    fields = {n: travel_field(site, [p], "walk_time_min") for n, p in
              {"poi:base": BASE, "poi:landing": LANDING}.items()}
    targets = {"poi:outcrop_a": OUTCROP, "poi:east_point": EAST}
    for r in out["results"]:
        expect = {t: at(site, fields[r["from_id"]], p) for t, p in targets.items()}
        best = min(expect, key=expect.get)
        assert r["to_id"] == best
        assert r["cost"] == pytest.approx(expect[best], abs=1e-2)
        assert r["cost_metric"] == "walk_time_min"
    # base -> outcrop_a is nearer than base -> east_point (ridge detour); landing too
    assert {r["to_id"] for r in out["results"]} == {"poi:outcrop_a"}
    st = store.get(out["set_id"])
    assert st.kind == "pairs" and st.count == 2
    assert out["results"][0]["straight_m"] > 0


def test_nearest_limit_and_straight(site):
    store = SetStore()
    out = nearest(site, store, ["base", "poi:landing"], ["poi:outcrop_a", "poi:east_point"], "straight_m", limit=1)
    assert len(out["results"]) == 1 and out["count"] == 2 and out["approximate"] is False
    r = out["results"][0]
    assert r["cost"] == pytest.approx(r["straight_m"], abs=0.01)


def test_nearest_multi_source_path_matches_per_member(site):
    store = SetStore()
    rng = np.random.default_rng(3)
    ok = [(float(x), float(y)) for x, y in rng.uniform(-240, 240, (30, 2))]
    from_set = _pts_set(store, ok, "f")
    to_set = _pts_set(store, [(0, 0), (50, -50), (-151, -151), (200, -200), (-200, 200)], "t")
    big = nearest(site, store, from_set, to_set, "walk_time_min", limit=100)
    # per-member path: run the <=25 branch one from-point at a time
    for r in big["results"]:
        one = nearest(site, store, {"x": r["x"], "y": r["y"]}, to_set, "walk_time_min", limit=1)
        assert one["results"], r
        o = one["results"][0]
        assert o["cost"] == pytest.approx(r["cost"], abs=1e-2)
        assert o["to_id"] == r["to_id"]
    assert big["count"] + len(big["unreachable"]) == 30
    # unreachable ones agree too
    for fid in big["unreachable"]:
        i = int(fid[1:])
        one = nearest(site, store, {"x": ok[i][0], "y": ok[i][1]}, to_set, "walk_time_min")
        assert one["unreachable"] == ["point"]


def test_nearest_unreachable_from(site):
    store = SetStore()
    # crater wall sealed floor: centre (-120, 120) is passable flat floor but cut off.
    slope = site.layers["slope_2m"].data
    r, c = site.rc_index(-120.0, 120.0)
    assert slope[r, c] <= 25                  # floor is walkable ground
    f = travel_field(site, [BASE], "walk_m")
    assert np.isinf(f[r, c])                   # but not reachable from base
    out = nearest(site, store, [{"x": -120.0, "y": 120.0, "id": "floor"}, "base"], [LANDING], "walk_m")
    assert out["unreachable"] == ["floor"]
    assert [x["from_id"] for x in out["results"]] == ["poi:base"]
    # same for the multi-source branch
    pts = [(-120.0 + dx, 120.0 + dy) for dx in (-10, 0, 10) for dy in (-10, 0, 10)] * 3
    pts = pts[:26]
    fs = _pts_set(store, pts, "c")
    out2 = nearest(site, store, fs, [LANDING], "walk_m")
    assert out2["count"] == 0 and len(out2["unreachable"]) == 26


def test_source_snapping_and_ignored(site):
    # a point inside the hole (0, -150): nearest walkable cell within 20 m exists -> snapped, note
    f, notes = __import__("atlas_core.travel", fromlist=["x"])._field_and_notes(site, [{"x": 0.0, "y": -150.0}], "walk_m", "eva_walkable")
    assert np.isfinite(f).any() and any("moved" in n for n in notes)
    # deep on the ridge/crater wall the snap may fail only if nothing walkable within 20 m; hole centre is 10 m from edge
    # fully impassable site: everything ignored
    s = flat_site()
    s.layers["slope_2m"] = Layer("slope_2m", "continuous", np.full((256, 256), 60.0, np.float32), s.layers["slope_2m"].meta)
    f, notes = __import__("atlas_core.travel", fromlist=["x"])._field_and_notes(s, [BASE], "walk_m", "eva_walkable")
    assert np.isinf(f).all() and any("ignored" in n for n in notes)


# ---- performance ----------------------------------------------------------------

def test_performance_large_grid():
    """4096 x 4096 analysis grid at 2 m -> 2048 x 2048 planning grid (k = 2)."""
    n = 4096
    slope = np.full((n, n), 3.0, np.float32)
    slope[:, 2000:2010] = 60.0
    slope[:3000, 2000:2010] = 60.0
    slope[3000:3100, 2000:2010] = 3.0       # a gap to force a detour
    meta = make_site().layers["slope_2m"].meta
    s = Site(name="perf", lat0=0.0, lon0=0.0, res_m=2.0, size_px=n,
             layers={"slope_2m": Layer("slope_2m", "continuous", slope, meta)},
             profiles=make_site().profiles)
    t0 = time.time()
    f = travel_field(s, [{"x": -3000.0, "y": 0.0}, {"x": 3000.0, "y": 100.0}], "walk_time_min")
    dt = time.time() - t0
    print(f"\ntravel_field 2048x2048 planning grid: {dt:.2f} s")
    assert f.shape == (n, n) and f.dtype == np.float32
    assert np.isfinite(f).sum() > 0.9 * n * n
    assert dt < 120


def test_travel_params_accept_provenance_form():
    """Profiles may wrap numbers as {"value", "source"} (as the real site build does)."""
    from atlas_core.testing import make_site
    from atlas_core.travel import travel_field
    a, b = make_site(), make_site()
    b.profiles["eva_walkable"]["travel"] = {
        k: ({"value": v, "source": "assumed"} if isinstance(v, (int, float)) else v)
        for k, v in a.profiles["eva_walkable"]["travel"].items()}
    src = [{"x": 1.0, "y": 1.0}]
    fa = travel_field(a, src, "walk_time_min")
    fb = travel_field(b, src, "walk_time_min")
    assert np.array_equal(fa, fb)
