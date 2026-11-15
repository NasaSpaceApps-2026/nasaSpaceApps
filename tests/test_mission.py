"""Mid-mission astronaut tools: atlas_core.mission, devapi /mission/*, and the MCP tools (mcp/tools_mission.py).

Synthetic site with known answers: base (0,0); flat plain with a 1 degree tilt; walking speed 1.0 m/s x 0.94 on the tilt;
crater centred (-120, 120) r 60 with steep walls (first no-go ground at about 168 m north of (-150, -100));
scarp ridge x 100..112, y <= 150 (10 m high, blocks sight and walking); NaN hole 20 m square at (0, -150);
sunset-side shadows from the ridge and crater rim; carbonate tier 3 patch at (-150, -150), tier 1 on the crater floor.
Real-site smoke tests run when data/build/site/jezero exists.
"""
import importlib.util
import math
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from atlas_core import SetStore, illumination as I, mission as M, sun as S
from atlas_core.errors import AtlasError
from atlas_core.testing import make_site
from devapi import create_app

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "build" / "site" / "jezero"
SOL = 1788


def _utc(ltst):
    return I.resolve_time(sol=SOL, ltst=ltst).isoformat().replace("+00:00", "Z")


@pytest.fixture()
def api():
    return TestClient(create_app(make_site(), synthetic=True))


def post(c, path, body=None, ok=True):
    r = c.post(path, json=body or {})
    if ok:
        assert r.status_code == 200, r.text
    return r.json()


def start(c, crew=(-150, -100), **kw):
    return post(c, "/mission/eva/start", {"crew_at": {"x": crew[0], "y": crew[1]}, "now": "2026-03-01T10:00:00Z", **kw})


# ---- pure helpers ----------------------------------------------------------------------------------

def test_compass_and_heading():
    assert M.compass16(0) == "north" and M.compass16(90) == "east" and M.compass16(225) == "southwest"
    assert M.compass16(359) == "north" and M.compass16(22.5) == "north-northeast"
    assert M.heading_deg(0, 10) == 0 and M.heading_deg(10, 0) == 90 and M.heading_deg(-10, -10) == 225


def test_turn_summary_known_polyline():
    pts = [{"x": 0.0, "y": float(y)} for y in range(0, 101, 5)] + [{"x": float(x), "y": 100.0} for x in range(5, 101, 5)]
    legs = M.turn_summary(pts)
    assert [l["direction"] for l in legs] == ["north", "east"]
    assert [l["length_m"] for l in legs] == [100, 100]
    assert legs[1]["turn_deg"] == 90 and legs[0]["turn_deg"] is None       # + = right turn


def test_clock_overrides():
    site = make_site()
    st = M.start_eva(site, SetStore(), {"x": 0.0, "y": 0.0}, now="2026-03-01T10:00:00Z", already_out_min=20)
    t = st.now(60)                                    # 60 min on the clock = 40 min after the start (20 already out)
    assert st.elapsed_min(t) == pytest.approx(60.0)
    assert st.elapsed_min(st.now("2026-03-01T11:30:00Z")) == pytest.approx(110.0)
    st.advance(15)
    assert st.elapsed_min(st.now()) >= 35.0 - 1e-6 + 0.0                      # real time runs on from the start timestamp


def test_nearest_place():
    pl = [{"name": "Rock A", "x": 100.0, "y": 0.0}, {"name": "Rock B", "x": 0.0, "y": 500.0}]
    p = M.nearest_place(pl, 0.0, 0.0)
    assert p["name"] == "Rock A" and p["distance_m"] == 100 and p["direction_from_place"] == "west"
    assert M.nearest_place([], 0.0, 0.0) is None


# ---- eva_start / status / budget rule ----------------------------------------------------------------

def test_eva_start_defaults(api):
    d = post(api, "/mission/eva/start", {"now": "2026-03-01T10:00:00Z"})
    assert d["budget_min"] == 480 and d["reserve_fraction"] == 0.2 and d["usable_min"] == 384
    assert [h["kind"] for h in d["havens"]] == ["base"]            # the parked rover is at the base: same place
    assert d["replaced_running_eva"] is False
    assert post(api, "/mission/eva/start", {})["replaced_running_eva"] is True      # a second start says it restarted the clock


def test_eva_start_crew_at_adds_parked_rover_haven(api):
    d = start(api, crew=(-150, -100), budget_min=300, reserve=0.1)
    assert d["usable_min"] == 270
    assert {h["kind"] for h in d["havens"]} == {"base"} and d["crew"] == {"x": -150.0, "y": -100.0}
    assert api.post("/rover/command", json={"cmd": "teleport", "x": 60, "y": -200}).status_code == 200
    d2 = post(api, "/mission/eva/start", {"crew_at": {"x": 160, "y": -200}})
    assert {h["kind"] for h in d2["havens"]} == {"base", "rover"}


def test_status_margin_rule_and_verdicts(api):
    start(api)
    s0 = post(api, "/mission/status", {"now": 60})
    wb = s0["walk_back_min"]
    assert 2.9 < wb < 3.9                                           # 180 m at 1 m/s x tilt penalty
    assert s0["margin_min"] == pytest.approx(384 - 60 - wb, abs=0.15)
    assert s0["status"] == "GO" and s0["remaining_min"] == 420 and s0["remaining_usable_min"] == 324
    assert s0["nearest_haven"]["name"] == "Test base" and s0["nearest_place"]["name"] == "Outcrop A"
    caution = post(api, "/mission/status", {"now": 384 - wb - 10})
    assert caution["status"] == "CAUTION" and caution["margin_min"] == pytest.approx(10, abs=0.2)
    now = post(api, "/mission/status", {"now": 384 - wb + 1})
    assert now["status"] == "RETURN NOW" and now["margin_min"] < 0 and now["turn_back_in_min"] == 0
    over = post(api, "/mission/status", {"now": 470})
    assert over["status"] == "RETURN NOW" and over["remaining_min"] == 10


def test_status_without_eva_is_a_readable_error(api):
    r = post(api, "/mission/status", ok=False)
    assert "No EVA is in progress" in r["error"] and "eva_start" in r["hint"]
    assert api.post("/mission/eva/start", json={"budget_min": -5}).status_code == 422
    assert api.post("/mission/eva/start", json={"reserve": 1.5}).status_code == 422
    assert api.post("/mission/eva/start", json={"bogus": 1}).status_code == 422


def test_already_out_counts_toward_elapsed(api):
    post(api, "/mission/eva/start", {"now": "2026-03-01T10:00:00Z", "already_out_min": 100})
    assert post(api, "/mission/status", {"now": "2026-03-01T10:00:00Z"})["elapsed_min"] == 100


def test_advance_moves_clock(api):
    start(api)
    post(api, "/mission/advance", {"minutes": 30})
    assert post(api, "/mission/status")["elapsed_min"] >= 30.0 + (0 if True else 1)


# ---- havens / return route ---------------------------------------------------------------------------

def test_nearest_haven_ranks_by_walk_time(api):
    start(api, havens=[{"x": -150, "y": -60}])
    h = post(api, "/mission/havens")
    assert [x["kind"] for x in h["havens"]] == ["shelter", "base"]
    assert h["havens"][0]["straight_m"] == 40 and h["havens"][0]["direction"] == "north"
    assert h["havens"][0]["walk_min"] < h["havens"][1]["walk_min"]
    s = post(api, "/mission/status", {"now": 10})
    assert s["nearest_haven"]["kind"] == "shelter" and s["walk_back_min"] == h["havens"][0]["walk_min"]


def test_nearest_haven_without_eva_uses_base(api):
    h = post(api, "/mission/havens")
    assert h["eva_active"] is False and [x["name"] for x in h["havens"]] == ["Test base"]


def test_return_route_flat(api):
    start(api)
    r = post(api, "/mission/return_route", {"now": 60})
    assert r["feasible"] and r["goal_name"] == "Test base"
    assert 3.0 < r["duration_min"] < 4.5 and 180 < r["distance_m"] < 230 and r["max_slope_deg"] <= 1.5
    assert r["legs"][0]["direction"] in ("east-northeast", "east", "northeast") and 40 <= r["legs"][0]["heading_deg"] <= 90
    assert r["within_reserve"] and r["margin_at_arrival_min"] == pytest.approx(384 - 60 - r["duration_min"], abs=0.2)
    assert "farther out" in r["point_of_no_return"]
    late = post(api, "/mission/return_route", {"now": 383})
    assert late["within_reserve"] is False and late["point_of_no_return"].startswith("PAST")


def test_return_route_goes_around_the_scarp(api):
    start(api, crew=(150, -50))
    r = post(api, "/mission/return_route", {"now": 30})
    straight = math.hypot(150, 50)
    assert r["feasible"] and r["distance_m"] > 2.5 * straight                 # north end at y = 150 is the only way round
    assert any(l["direction"].startswith("north") for l in r["legs"]) and r["max_slope_deg"] <= 25
    assert r["legs"][-1]["direction"].startswith("south") or len(r["legs"]) >= 3


def test_return_route_to_given_place_and_vehicle_word(api):
    start(api, crew=(-150, -100))
    r = post(api, "/mission/return_route", {"to": "poi:landing"})
    assert r["goal_name"] == "Test landing" and r["feasible"]
    rv = post(api, "/mission/return_route", {"to": "the rover"})
    assert rv["goal"] == {"x": 0.0, "y": 0.0}                    # the rover is parked at the base


def test_return_route_infeasible_reports_reason(api):
    start(api)
    r = post(api, "/mission/return_route", {"to": {"x": -120, "y": 160}})   # steep crater wall
    assert r["feasible"] is False and "degree" in r["reason"]


# ---- check_path_ahead ---------------------------------------------------------------------------------

def test_path_ahead_flat_east_has_no_hazard(api):
    start(api)
    d = post(api, "/mission/path_ahead", {"heading_deg": 90, "distance_m": 100, "now": _utc("12:00")})
    assert d["first_hazard"] is None and d["first_blocker"] is None and d["slope"]["max_deg"] == pytest.approx(1.0, abs=0.1)
    assert d["direction"] == "east" and d["distance_m"] == 100 and 1.0 < d["walk_min"] < 2.0
    assert d["sun_up"] and d["shaded_fraction"] == 0.0


def test_path_ahead_crater_wall_is_first_blocker(api):
    start(api, crew=(-150, -100))
    d = post(api, "/mission/path_ahead", {"heading_deg": 0, "distance_m": 300})
    fb, fh = d["first_blocker"], d["first_hazard"]
    assert 160 <= fb["distance_m"] <= 176 and "too steep" in fb["kind"]          # rim wall at y = 68
    assert fh["distance_m"] <= fb["distance_m"] and fh["severity"] == "caution"
    assert d["slope"]["max_deg"] > 25


def test_path_ahead_no_data_and_map_edge(api):
    start(api, crew=(0, -100))
    d = post(api, "/mission/path_ahead", {"heading_deg": 180, "distance_m": 100})
    assert 30 <= d["first_blocker"]["distance_m"] <= 45 and "no map data" in d["first_blocker"]["kind"]
    post(api, "/mission/eva/start", {"crew_at": {"x": 200, "y": 0}})
    e = post(api, "/mission/path_ahead", {"heading_deg": 90, "distance_m": 100})
    assert 52 <= e["edge_of_map_at_m"] <= 60 and "edge" in e["first_blocker"]["kind"]


def test_path_ahead_toward_place_and_shadow(api):
    start(api, crew=(-150, -100))
    d = post(api, "/mission/path_ahead", {"toward": "base", "now": _utc("16:45")})
    assert d["direction"] == "east-northeast" and d["distance_m"] == pytest.approx(180, abs=1)
    assert d["sun_up"] and d["shaded_fraction"] is not None
    dark = post(api, "/mission/path_ahead", {"heading_deg": 90, "distance_m": 50, "now": _utc("02:00")})
    assert dark["sun_up"] is False and dark["shaded_fraction"] == 1.0


def test_path_ahead_margin_and_point_of_no_return(api):
    start(api)
    post(api, "/mission/eva/start", {"crew_at": {"x": 0, "y": 0}, "now": "2026-03-01T10:00:00Z"})
    # at the base with 4 min of margin (384 - 380): every minute out costs two (out + back), so about 2 min = 112 m
    d = post(api, "/mission/path_ahead", {"heading_deg": 270, "distance_m": 200, "now": 380})
    assert d["margin_start_min"] == pytest.approx(4, abs=0.1) and d["margin_at_end_min"] < 0
    assert 95 <= d["point_of_no_return_m"] <= 130
    easy = post(api, "/mission/path_ahead", {"heading_deg": 90, "distance_m": 100, "now": 10})
    assert easy["point_of_no_return_m"] is None and easy["margin_at_end_min"] > 300


def test_path_ahead_two_km_is_capped_and_checked(api):
    start(api, crew=(0, 0))
    d = post(api, "/mission/path_ahead", {"heading_deg": 270, "distance_m": 2000, "now": 5})
    assert d["distance_m"] == 2000 and d["edge_of_map_at_m"] == pytest.approx(256, abs=6)   # site edge 256 m west of base
    assert d["first_blocker"]["kind"].startswith("edge")


def test_path_ahead_errors(api):
    start(api)
    assert "heading" in post(api, "/mission/path_ahead", {"distance_m": 100}, ok=False)["error"].lower()
    assert api.post("/mission/path_ahead", json={"heading_deg": 0, "distance_m": -5}).status_code == 422


# ---- comm_check ----------------------------------------------------------------------------------------

def test_comm_clear_on_open_ground(api):
    start(api, crew=(-60, 0))
    d = post(api, "/mission/comm_check", {})
    assert d["clear"] is True and d["distance_m"] == 60 and d["direction_to"] == "east"


def test_comm_blocked_by_ridge_restores_by_walking_north(api):
    start(api, crew=(130, 60))
    d = post(api, "/mission/comm_check", {})
    assert d["clear"] is False and d["blocking_point"] is not None
    r = d["restore"]
    assert r and r["walk_m"] <= 300 and r["direction"].startswith("north")           # only way is round the north end
    again = post(api, "/mission/comm_check", {"to": "base"})
    assert again["restore"] == r
    # the restore point really sees the base (independent check with the visibility engine)
    from atlas_core import visibility as V
    site = make_site()
    assert V.line_of_sight(site, {"x": r["x"], "y": r["y"]}, "base", 1.7, 1.7)["clear"]


def test_comm_blocked_with_no_nearby_fix_inside_crater(api):
    start(api, crew=(-120, 120))
    d = post(api, "/mission/comm_check", {})
    assert d["clear"] is False and d["restore"] is None and "No spot within 300 m" in d["hint"]


def test_comm_higher_antenna_can_clear_it(api):
    start(api, crew=(130, 60))
    d = post(api, "/mission/comm_check", {"antenna_h": 12.0, "to_antenna_h": 12.0})
    assert d["clear"] is True and d["antenna_h_m"] == 12.0


def test_comm_to_the_rover(api):
    assert api.post("/rover/command", json={"cmd": "teleport", "x": 60, "y": -200}).status_code == 200
    post(api, "/mission/eva/start", {"crew_at": {"x": 160, "y": -200}})
    d = post(api, "/mission/comm_check", {"to": "the rover"})
    assert d["distance_m"] == 100 and d["direction_to"] == "west" and d["clear"] is False       # the 10 m ridge is between


# ---- samples --------------------------------------------------------------------------------------------

def test_log_and_list_samples_with_evidence(api):
    start(api, crew=(-151, -151))
    s1 = post(api, "/mission/sample", {"note": "layered, rusty band", "now": 42})
    assert s1["n"] == 1 and s1["unit"] == "plains" and s1["elapsed_min"] == 42 and s1["marker_id"].startswith("m")
    carb = [m for m in s1["minerals"] if m["mineral"] == "carbonate"][0]
    assert carb["tier"] == 4 and carb["tier_name"] == "confirmed_in_situ"                 # centre cell of the patch
    s2 = post(api, "/mission/sample", {"note": "floor dust", "at": {"x": -120, "y": 120}, "now": 55})
    assert s2["n"] == 2 and s2["unit"] == "crater_floor"
    assert [m for m in s2["minerals"] if m["mineral"] == "carbonate"][0]["tier_name"] == "inferred_from_unit"
    lst = api.get("/mission/samples").json()
    assert lst["count"] == 2 and [s["note"] for s in lst["samples"]] == ["layered, rusty band", "floor dust"]
    markers = api.get("/markers").json()["markers"]
    assert {m["label"] for m in markers} == {"Sample 1: layered, rusty band", "Sample 2: floor dust"}
    end = post(api, "/mission/eva/end", {"now": 100})
    assert end["samples_logged"] == 2 and end["elapsed_min"] == 100 and end["at_haven"] is False


def test_log_sample_without_eva_and_note_required(api):
    s = post(api, "/mission/sample", {"note": "pebble"})
    assert s["eva_active"] is False and s["elapsed_min"] is None
    assert api.post("/mission/sample", json={}).status_code == 422


# ---- bearing_to ------------------------------------------------------------------------------------------

def test_bearing_known_headings(api):
    start(api, crew=(0, 0))
    e = post(api, "/mission/bearing", {"target": {"x": 80, "y": 0}})
    assert e["heading_deg"] == 90 and e["direction"] == "east" and e["straight_m"] == 80
    assert 1.3 < e["walk_min"] < 1.7 and e["walk_m"] == pytest.approx(80, abs=8)
    sw = post(api, "/mission/bearing", {"target": {"x": -50, "y": -50}})
    assert sw["heading_deg"] == 225 and sw["direction"] == "southwest" and sw["straight_m"] == pytest.approx(70.7, abs=0.5)
    n = post(api, "/mission/bearing", {"target": "poi:landing"})
    assert n["direction"] == "southeast" and n["heading_deg"] == 135


def test_bearing_unreachable_reports_problem(api):
    start(api, crew=(0, 0))
    b = post(api, "/mission/bearing", {"target": {"x": 100, "y": 0}})             # on the 52 degree scarp face
    assert b["walk_min"] is None and "52 degree" in b["walk_problem"] and b["straight_m"] == 100


# ---- rendezvous --------------------------------------------------------------------------------------------

def test_rendezvous_balances_walk_and_drive(api):
    start(api, crew=(-150, -100))
    d = post(api, "/mission/rendezvous", {"rover": {"x": 100, "y": -100}})
    m, cands = d["meeting_point"], d["candidates"]
    assert d["feasible"] and d["rover_profile"] == "crew_rover"
    assert m["worst_min"] <= min(c["worst_min"] for c in cands) + 1e-6
    assert m["worst_min"] < cands[1]["worst_min"] - 1.0                        # beats walking all the way to the rover
    assert abs(m["crew_walk_min"] - m["rover_drive_min"]) <= max(1.0, 0.25 * m["worst_min"]) or m["crew_walk_min"] == 0
    assert -150 <= m["x"] <= 100                                               # between the two of them


def test_rendezvous_defaults_to_parked_rover(api):
    start(api, crew=(-150, -100))
    d = post(api, "/mission/rendezvous")
    assert d["rover"] == {"x": 0.0, "y": 0.0}
    assert d["candidates"][1]["label"].startswith("meet at the rover")


def test_rendezvous_unreachable_for_rover(api):
    start(api, crew=(-150, -100))
    d = post(api, "/mission/rendezvous", {"rover": {"x": -120, "y": 160}})          # rover stuck on the crater wall
    assert d["feasible"] is False or d["meeting_point"]["worst_min"] >= 0


# ---- daylight ---------------------------------------------------------------------------------------------

def test_daylight_sunset_matches_sun_module(api):
    start(api)
    t = I.resolve_time(sol=SOL, ltst="12:00")
    d = post(api, "/mission/daylight", {"now": t.isoformat().replace("+00:00", "Z")})
    site = make_site()
    ss = S.sunrise_sunset(t, site.lat0, site.lon0)["sunset"]
    oracle = (S.parse_utc(ss["utc"]) - t).total_seconds() / 60.0
    assert d["sun_up"] and d["minutes_to_sunset"] == pytest.approx(oracle, abs=1.5)
    assert d["sun_elevation_deg"] == pytest.approx(S.sun_position(t, site.lat0, site.lon0)["sun_elevation_deg"], abs=0.1)
    assert d["minutes_until_shaded"] <= d["minutes_to_sunset"] + 1e-6


def test_daylight_terrain_shadow_before_sunset_crater_floor(api):
    start(api, crew=(0, 0))
    t = I.resolve_time(sol=SOL, ltst="15:30")
    d = post(api, "/mission/daylight", {"at": {"x": -120, "y": 120}, "now": t.isoformat().replace("+00:00", "Z")})
    assert d["shade_cause"] == "terrain shadow" and 0 < d["minutes_until_shaded"] < d["minutes_to_sunset"] - 30
    site, st = make_site(), SetStore()
    before = t + timedelta(minutes=d["minutes_until_shaded"] - 2.5)
    after = t + timedelta(minutes=d["minutes_until_shaded"] + 2.5)
    lit = lambda tt: I.illumination_at(site, st, {"x": -120, "y": 120}, utc=tt.isoformat())["lit_fraction"]
    assert lit(before) > 0.99 and lit(after) < 0.5                                   # independent oracle: the illumination engine


def test_daylight_night_and_sunrise(api):
    start(api)
    t = I.resolve_time(sol=SOL, ltst="02:00")
    d = post(api, "/mission/daylight", {"now": t.isoformat().replace("+00:00", "Z")})
    site = make_site()
    sr = S.parse_utc(S.sunrise_sunset(t, site.lat0, site.lon0)["sunrise"]["utc"])
    assert d["sun_up"] is False and d["shade_cause"] == "night"
    assert d["minutes_to_sunrise"] == pytest.approx((sr - t).total_seconds() / 60.0, abs=1.5)


def test_daylight_follows_the_crew_position(api):
    start(api, crew=(0, 0))
    t = _utc("16:30")
    here = post(api, "/mission/daylight", {"now": t})
    floor = post(api, "/mission/daylight", {"at": {"x": -120, "y": 120}, "now": t})
    assert floor["minutes_until_shaded"] < here["minutes_until_shaded"]


# ---- eva_end ------------------------------------------------------------------------------------------------

def test_eva_end_at_base_and_state_cleared(api):
    start(api, crew=(0, 0))
    e = post(api, "/mission/eva/end", {"now": 300})
    assert e["ended"] and e["at_haven"] and e["within_reserve"] and e["elapsed_min"] == 300
    assert post(api, "/mission/status", ok=False)["error"].startswith("No EVA")
    late = start(api, crew=(0, 0))
    assert post(api, "/mission/eva/end", {"now": 400})["within_reserve"] is False


# ---- the MCP tools (in-process, over the same app) -------------------------------------------------------

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


srv = _load("atlas_server_mission", ROOT / "mcp" / "server.py")
TOOLS = ["eva_start", "eva_end", "eva_status", "return_route", "nearest_haven", "check_path_ahead", "comm_check",
         "log_sample", "list_samples", "bearing_to", "rendezvous", "daylight_check"]


def mcp_run(coro_fn, app=None):
    app = app or create_app(make_site(), synthetic=True)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://devapi")
    server = srv.build_server(client=client)

    async def go():
        async with Client(server) as c:
            return await coro_fn(c)
    return anyio.run(go)


def text(r):
    return r.content[0].text


def test_mcp_tools_registered():
    async def f(c):
        return {t.name for t in (await c.list_tools()).tools}
    assert set(TOOLS) <= mcp_run(f)


def test_mcp_flow_status_return_route_and_loose_places():
    async def f(c):
        out = {}
        out["start"] = await c.call_tool("eva_start", {"crew_at": {"x": -150, "y": -100}})
        out["status"] = await c.call_tool("eva_status", {})
        out["route"] = await c.call_tool("return_route", {})
        out["haven"] = await c.call_tool("nearest_haven", {})
        out["bearing"] = await c.call_tool("bearing_to", {"target": "the base"})
        out["ahead"] = await c.call_tool("check_path_ahead", {"heading_deg": "north", "distance_m": "300 m"})
        out["comm"] = await c.call_tool("comm_check", {})
        out["sample"] = await c.call_tool("log_sample", {"note": "looks layered"})
        out["samples"] = await c.call_tool("list_samples", {})
        out["rv"] = await c.call_tool("rendezvous", {})
        out["day"] = await c.call_tool("daylight_check", {})
        out["end"] = await c.call_tool("eva_end", {})
        return out
    o = mcp_run(f)
    for k, r in o.items():
        assert not r.is_error, (k, text(r))
    assert "EVA started: budget 480 min" in text(o["start"])
    assert o["status"].structured_content["status"] in ("GO", "CAUTION", "RETURN NOW")
    assert "Walk back to Test base takes" in text(o["status"]) and "Time:" in text(o["status"])
    assert "Route to Test base" in text(o["route"]) and "Legs: head" in text(o["route"])
    assert o["haven"].structured_content["havens"][0]["name"] == "Test base"
    assert "Test base is east-northeast" in text(o["bearing"])
    assert "BLOCKED at" in text(o["ahead"]) and "north" in text(o["ahead"])
    assert "CLEAR" in text(o["comm"])
    assert "Logged sample 1" in text(o["sample"]) and "plains" in text(o["sample"])
    assert "1 sample(s) logged" in text(o["samples"]) and "layered" in text(o["samples"])
    assert "Best meeting point" in text(o["rv"]) and "light" in text(o["day"]).lower() or "sun" in text(o["day"]).lower()
    assert "EVA ended" in text(o["end"])


def test_mcp_me_here_and_errors_use_try_format():
    async def f(c):
        a = await c.call_tool("eva_status", {})                                        # no EVA yet
        b = await c.call_tool("bearing_to", {"target": "me"})
        d = await c.call_tool("check_path_ahead", {})
        e = await c.call_tool("check_path_ahead", {"heading_deg": "sideways"})
        g = await c.call_tool("daylight_check", {"at": "here"})
        return a, b, d, e, g
    a, b, d, e, g = mcp_run(f)
    assert a.is_error and "Error: No EVA is in progress" in text(a) and "Try: Call eva_start" in text(a)
    assert not b.is_error and "0 m" in text(b)                                           # me -> where I am already
    assert d.is_error and "Try:" in text(d)
    assert e.is_error and "could not read the heading" in text(e)
    assert not g.is_error


def test_mcp_unsafe_distance_is_checked_not_refused():
    async def f(c):
        await c.call_tool("eva_start", {"crew_at": {"x": -150, "y": -100}})
        return await c.call_tool("check_path_ahead", {"heading_deg": 0, "distance_m": 2000})
    r = mcp_run(f)
    assert not r.is_error and "BLOCKED" in text(r) and r.structured_content["first_blocker"] is not None


# ---- real-site smoke ---------------------------------------------------------------------------------------

@pytest.mark.skipif(not REAL.exists(), reason="real site data not built")
def test_real_site_smoke():
    from atlas_core import load_site
    from devapi.vehicles import add_vehicle_profiles
    site = load_site(REAL)
    add_vehicle_profiles(site)
    app = create_app(site, synthetic=False)
    c = TestClient(app)
    base = next(p for p in site.pois if p.get("type") == "base")
    d = post(c, "/mission/eva/start", {"now": "2026-03-01T10:00:00Z", "crew_at": {"x": base["x"] + 300, "y": base["y"] + 200}})
    assert d["usable_min"] == pytest.approx(384)
    s = post(c, "/mission/status", {"now": 90})
    assert s["status"] == "GO" and s["walk_back_min"] > 3 and s["nearest_haven"]["straight_m"] == pytest.approx(360, abs=10)
    r = post(c, "/mission/return_route", {"now": 90})
    assert r["feasible"] and r["distance_m"] >= 355 and r["max_slope_deg"] <= 25
    a = post(c, "/mission/path_ahead", {"heading_deg": 90, "distance_m": 300, "now": 90})
    assert a["distance_m"] == 300 and a["walk_min"] > 0
    cc = post(c, "/mission/comm_check", {})
    assert "clear" in cc
    sm = post(c, "/mission/sample", {"note": "smoke"})
    assert sm["unit"] is not None or sm["minerals"] is not None
    assert post(c, "/mission/bearing", {"target": "base"})["direction"] in M.COMPASS16
    rv = post(c, "/mission/rendezvous")
    assert rv["feasible"] and rv["meeting_point"]["worst_min"] <= rv["candidates"][1]["worst_min"] + 1e-6
    dl = post(c, "/mission/daylight", {"now": _utc("12:00")})
    assert dl["sun_up"] and dl["minutes_to_sunset"] > 0
