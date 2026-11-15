"""Paleolake flooding and EVA safety envelope (atlas_core.lake, atlas_core.envelope, devapi routes)."""
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from atlas_core import SetStore, load_site
from atlas_core.envelope import check_eva_plan, eva_envelope
from atlas_core.errors import AtlasError
from atlas_core.lake import NAMED_LEVELS, flood_at_level
from atlas_core.terrain import slope_deg
from atlas_core.testing import N, RES, _grid, make_site
from devapi import create_app

REAL = Path(__file__).resolve().parents[1] / "data" / "build" / "site" / "jezero"


# ---- lake -----------------------------------------------------------------------------------

def _pit_site():
    """Synthetic site with an extra isolated 10 m pit at (150, -100)."""
    site = make_site()
    X, Y = _grid()
    z = site.layers["elevation"].data.copy()
    r = np.hypot(X - 150, Y + 100)
    z = z - np.where(r < 20, 10.0 * (1 - (r / 20) ** 2), 0.0).astype(np.float32)
    site.layers["elevation"].data = z.astype(np.float32)
    return site, z


def test_flood_bowl_matches_oracle():
    site = make_site()
    z = site.layers["elevation"].data
    level = -2515.0
    r = flood_at_level(site, SetStore(), level_m=level)
    X, Y = _grid()
    inside = np.hypot(X + 120, Y - 120) < 70
    expect = np.isfinite(z) & (z <= level) & inside
    assert r["water_cells"] == int(expect.sum()) > 100
    assert r["area_m2"] == pytest.approx(expect.sum() * RES * RES)
    assert r["depth_m"]["max"] == pytest.approx(float(level - np.nanmin(z)), abs=1e-2)
    assert not r["touches_site_edge"] and r["shoreline"]["cells"] > 10
    assert r["shoreline"]["set_id"].startswith("set:")
    assert "PRESENT-DAY" in " ".join(r["notes"])


def test_isolated_depression_not_flooded_unless_asked():
    site, z = _pit_site()
    level = -2500.0
    base = flood_at_level(site, SetStore(), level_m=level)
    pit = (np.hypot(*(_grid()[0] - 150, _grid()[1] + 100)) < 20) & (z <= level)
    assert pit.sum() > 20 and base["isolated_below_level_cells"] >= pit.sum()
    assert base["water_cells"] > 0
    both = flood_at_level(site, SetStore(), level_m=level, connected_to=[{"x": -120, "y": 120}, {"x": 150, "y": -100}])
    assert both["water_cells"] == base["water_cells"] + int(pit.sum())
    only_pit = flood_at_level(site, SetStore(), level_m=level, connected_to={"x": 150, "y": -100})
    assert only_pit["water_cells"] == int(pit.sum())


def test_underwater_depth_and_errors():
    site = make_site()
    # base at (0,0): z ~ -2500; flood to -2490 -> base underwater? only if connected; use connected_to=base
    r = flood_at_level(site, SetStore(), level_m=-2495.0, connected_to="base")
    ids = {u["id"]: u for u in r["underwater"]["listed"]}
    z0 = float(site.layers["elevation"].data[128, 128])
    assert "poi:base" in ids and ids["poi:base"]["depth_m"] == pytest.approx(-2495.0 - z0, abs=0.01)
    with pytest.raises(AtlasError):
        flood_at_level(site, SetStore())
    with pytest.raises(AtlasError):
        flood_at_level(site, SetStore(), level_m=-2500, named_level="delta_top_2410")
    with pytest.raises(AtlasError):
        flood_at_level(site, SetStore(), named_level="nope")
    assert flood_at_level(site, SetStore(), level_m=-9000.0)["water_cells"] == 0


def test_named_levels_documented():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "lake-levels.md").read_text()
    for k, v in NAMED_LEVELS.items():
        assert f"`{k}`" in doc and f"| {v['level_m']:.0f} |" in doc


@pytest.mark.skipif(not REAL.exists(), reason="real site not built")
@pytest.mark.parametrize("name", ["delta_top_2410", "model_max_fill_2243", "delta_low_2500"])
def test_real_site_landing_underwater(name):
    site = load_site(REAL)
    level = NAMED_LEVELS[name]["level_m"]
    r = flood_at_level(site, SetStore(), named_level=name, check="poi:octavia_e_butler_landing")
    z = float(site.layers["elevation"].data[tuple(int(v) for v in site.rc_index(1844.26, -432.71))])
    u = r["underwater"]["listed"]
    assert len(u) == 1 and u[0]["depth_m"] == pytest.approx(level - z, abs=0.01) and u[0]["depth_m"] > 50


def test_lake_endpoint():
    c = TestClient(create_app(make_site(), synthetic=True))
    lv = c.get("/lake/levels").json()
    assert {x["named_level"] for x in lv["levels"]} == set(NAMED_LEVELS)
    r = c.post("/lake/flood", json={"level_m": -2515})
    assert r.status_code == 200 and r.json()["area_m2"] > 0
    assert c.post("/lake/flood", json={"bogus": 1}).status_code == 422
    assert c.post("/lake/flood", json={"named_level": "nope"}).status_code == 422


# ---- envelope -------------------------------------------------------------------------------

def _flat_site():
    site = make_site()
    dem = np.full((N, N), -2500.0, np.float32)
    site.layers["elevation"].data = dem
    site.layers["slope_2m"].data = slope_deg(dem, RES)
    return site


def test_envelope_flat_plane_radius():
    site = _flat_site()
    # speed 1 m/s, budget 10 min, reserve 20% -> usable 480 s -> one-way 240 s -> 240 m
    r = eva_envelope(site, SetStore(), {"x": 1, "y": 1}, budget_min=10, reserve_fraction=0.2)
    assert r["one_way_limit_min"] == pytest.approx(4.0)
    assert r["max_one_way_radius_m"] == pytest.approx(240.0, abs=4.5)
    by = {s["sector"]: s for s in r["sectors"]}
    for k in ("N", "E", "S", "W"):
        assert by[k]["farthest_straight_m"] == pytest.approx(240.0, abs=4.5)
    assert r["values"]["flat_speed_mps"]["value"] == 1.0 and "source" in r["values"]["flat_speed_mps"]
    # stops cut the radius: 2 min of stops -> one-way (480-120)/2 = 180 m
    r2 = eva_envelope(site, SetStore(), {"x": 1, "y": 1}, budget_min=10, reserve_fraction=0.2, stops_min=2)
    assert r2["max_one_way_radius_m"] == pytest.approx(180.0, abs=4.5)
    assert r["area_m2"] > r2["area_m2"]


def test_envelope_ridge_asymmetry_and_havens():
    site = make_site()
    r = eva_envelope(site, SetStore(), "base", budget_min=8, reserve_fraction=0.0)
    by = {s["sector"]: s["farthest_straight_m"] for s in r["sectors"]}
    assert by["W"] > 150 and by["E"] < 0.6 * by["W"]       # the scarp ridge blocks the east
    # a haven to the east shrinks nothing but changes the return leg: reachable set differs
    r2 = eva_envelope(site, SetStore(), "base", budget_min=8, reserve_fraction=0.0, havens=[{"x": -100, "y": 0}])
    assert r2["area_m2"] != r["area_m2"]
    with pytest.raises(AtlasError):
        eva_envelope(site, SetStore(), "base", budget_min=-1)
    with pytest.raises(AtlasError):
        eva_envelope(site, SetStore(), "base", reserve_fraction=1.2)


def test_check_eva_flags_point_of_no_return():
    site = _flat_site()
    # usable = 5 * 0.8 = 4 min = 240 s; elapsed d s + back d s <= 240 -> first violation just past 120 m
    res = check_eva_plan(site, SetStore(), points=[{"x": 1, "y": 1}, {"x": 201, "y": 1}], budget_min=5,
                         reserve_fraction=0.2)
    assert res["ok"] is False
    p, s = res["point_of_no_return"], res["last_safe_point"]
    assert 118 <= p["distance_m"] <= 130 and s["distance_m"] < p["distance_m"] and s["margin_min"] >= 0
    assert "NOT OK" in res["verdict"]
    ok = check_eva_plan(site, SetStore(), points=[{"x": 1, "y": 1}, {"x": 81, "y": 1}], budget_min=5,
                        reserve_fraction=0.2)
    assert ok["ok"] is True and ok["point_of_no_return"] is None
    with pytest.raises(AtlasError):
        check_eva_plan(site, SetStore())


def test_eva_endpoints_and_route_id():
    c = TestClient(create_app(make_site(), synthetic=True))
    e = c.post("/eva/envelope", json={"base": "base", "budget_min": 8, "reserve_fraction": 0.1})
    assert e.status_code == 200 and e.json()["set_id"].startswith("set:") and len(e.json()["sectors"]) == 8
    rt = c.post("/route", json={"start": "base", "goal": {"x": -150, "y": 0}, "profile": "eva_walkable"}).json()
    assert rt["feasible"]
    k = c.post("/eva/check", json={"route_id": rt["route_id"], "budget_min": 3, "reserve_fraction": 0.2})
    assert k.status_code == 200 and k.json()["ok"] is False and k.json()["route_id"] == rt["route_id"]
    assert c.post("/eva/check", json={"route_id": "nope"}).status_code == 422
    assert c.post("/eva/check", json={}).status_code == 422
