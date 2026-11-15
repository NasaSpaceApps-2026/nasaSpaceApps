"""mcp/backends.py: backend routing, request/response mapping to Yousef's ares_api shapes, and the frame check.

No network: his responses are canned dicts captured from a live ares_api run (docs/rover-connection-test.md);
the router is exercised through httpx.MockTransport, the frame banner through the real MCP server."""
import importlib.util
import json
import sys
from pathlib import Path

import anyio
import httpx
import pytest
from mcp.client import Client

from atlas_core.testing import make_site
from devapi import create_app

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


srv = _load("atlas_server_bk", ROOT / "mcp" / "server.py")
bk = sys.modules["backends"]
fmt = sys.modules["format"]

INFO = {"rover_profile": "rover_scout", "default_profile": "astronaut_walk",
        "profiles": {"rover_scout": {"flat_speed_mps": 0.04, "display_speed_factor": 200, "caution_slope_deg": 15,
                                     "nogo_slope_deg": 20, "eva_limit_min": 1440, "reserve_frac": 0.1},
                     "astronaut_walk": {"flat_speed_mps": 0.9, "caution_slope_deg": 15, "nogo_slope_deg": 25,
                                        "eva_limit_min": 480, "reserve_frac": 0.2}}}
STATE = {"x": 1844.3, "y": -432.7, "z": -2569.83, "heading_deg": 0.0, "pitch_deg": -0.1, "roll_deg": 0.1,
         "speed_mps": 0.0, "mode": "idle", "odometer_m": 0.0, "route_id": None, "status": "ready", "last_event": None,
         "fsd_armed": False, "lat": 18.4447, "lon": 77.4508, "slope_deg": 0.3, "mission_time_min": 12.0}
ROUTE = {"feasible": True, "profile": "rover_scout", "start": "(1844, -433)", "goal": "(1900, -400)",
         "stops": ["(1870, -410)"],
         "points": [{"x": 1844.0, "y": -433.0, "z": -2569.83, "slope": 0.3}, {"x": 1862.6, "y": -413.6, "z": -2569.91, "slope": 2.0},
                    {"x": 1887.1, "y": -404.9, "z": -2569.2, "slope": 1.7}, {"x": 1900.0, "y": -400.0, "z": -2568.9, "slope": 0.7}],
         "distance_m": 69, "duration_min": 31.0, "stop_minutes": 15, "ascent_m": 1.1, "descent_m": 0.2,
         "max_slope_deg": 2.3, "slope_limit_deg": 20, "hazard_cells": 0,
         "legs": [{"from": "(1844, -433)", "to": "(1870, -410)", "distance_m": 36, "duration_min": 16.1},
                  {"from": "(1870, -410)", "to": "(1900, -400)", "distance_m": 33, "duration_min": 14.9}],
         "consumables": {"used_frac": 0.032, "reserve_frac": 0.1, "margin_frac": 0.868, "eva_limit_min": 1440,
                         "basis": "time-based"},
         "time_budget_min": None, "warnings": [], "sources": {"slope": "MRO HiRISE DTM", "speeds": "slopes assumed"},
         "route_id": "r5"}
ASSESS = {"route_id": "r5", "go": True, "verdict": "GO", "reasons": ["all checks pass"]}


# ---- routing and frame check ---------------------------------------------------------------------

@pytest.mark.parametrize("method,path,which", [
    ("GET", "/rover/state", "rover"), ("POST", "/rover/command", "rover"), ("GET", "/fsd/status", "rover"),
    ("POST", "/route", "rover"), ("GET", "/route/r1", "rover"), ("POST", "/route/science", "rover"),
    ("GET", "/markers", "rover"), ("POST", "/view", "rover"), ("POST", "/search/sites", "rover"),
    ("GET", "/search/targets", "rover"), ("GET", "/pois", "rover"), ("GET", "/terrain/sample", "rover"),
    ("POST", "/terrain/profile", "rover"), ("POST", "/events/agent", "rover"),
    ("GET", "/overview", "atlas"), ("POST", "/describe", "atlas"), ("POST", "/search/locations", "atlas"),
    ("POST", "/search/regions", "atlas"), ("POST", "/search/nearest", "atlas"), ("GET", "/sets/1", "atlas"),
    ("GET", "/layers", "atlas"), ("GET", "/health", "atlas"), ("POST", "/visibility/viewshed", "atlas"),
    ("GET", "/sun/position", "atlas"), ("POST", "/lake/flood", "atlas")])
def test_backend_for(method, path, which):
    assert bk.backend_for(method, path) == which


def _site(lat0, lon0, size, name="x"):
    return {"name": name, "lat0": lat0, "lon0": lon0, "size_m": size, "radius_m": 3396190.0}


def _his(lat0, lon0, size, name="x"):
    return {"manifest": {"site": name, "lat0": lat0, "lon0": lon0, "radius_m": 3396190.0, "size_m": size},
            "bounds": {"half_size_m": size / 2}}


def test_frame_same_is_none():
    assert bk.compare_sites(_site(18.452, 77.418, 8192), _his(18.452, 77.418, 8192)) is None


def test_frame_within_one_metre_is_same():
    # 1e-5 deg of latitude is 0.59 m
    assert bk.compare_sites(_site(18.452, 77.418, 8192), _his(18.45201, 77.418, 8192)) is None


def test_frame_origin_offset_is_mismatch():
    r = bk.compare_sites(_site(18.452, 77.418, 8192, "jezero"), _his(18.46, 77.41, 2048, "playground"))
    assert r["frame_mismatch"] and 600 < r["offset_m"] < 700
    assert r["message"].startswith("FRAME MISMATCH") and "jezero" in r["message"] and "playground" in r["message"]
    assert r["dx_m"] < 0 and r["dy_m"] > 0     # his origin is west and north of ours


def test_frame_size_only_is_extent_note_not_mismatch():
    r = bk.compare_sites(_site(18.46, 77.41, 512), _his(18.46, 77.41, 2048))
    assert r and not r["frame_mismatch"] and r["extent_differs"] and r["message"].startswith("EXTENT DIFFERS")


def test_site_fields_flat_and_size_px():
    f = bk._site_fields({"name": "j", "lat0": 1, "lon0": 2, "size_px": 100, "res_m": 2.0})
    assert f["size_m"] == 200.0


# ---- request-side mapping ------------------------------------------------------------------------

def test_policy_mapping_and_unknown_key():
    assert bk.policy_to_rover({"replan_on_block": False, "caution_slowdown": False}) == {
        "replan": False, "caution_speed_factor": 1.0}
    assert bk.policy_to_rover({"max_replans": 2}) == {"max_replans": 2}
    with pytest.raises(ValueError, match="Unknown FSD policy key"):
        bk.policy_to_rover({"fly": True})


def test_command_xy_becomes_target_and_instant_speed():
    b, notes = bk.command_to_rover({"cmd": "goto", "x": 1, "y": 2}, INFO, None)
    assert b["target"] == {"x": 1, "y": 2} and "x" not in b
    assert b["speed_factor"] == pytest.approx(400 / 0.04) and any("animate=false" in n for n in notes)


def test_command_animate_uses_display_factor():
    b, notes = bk.command_to_rover({"cmd": "drive", "distance_m": 5, "realtime": True}, INFO, None)
    assert b["speed_factor"] == 200 and not notes and "realtime" not in b


def test_command_explicit_speed_factor_kept():
    b, _ = bk.command_to_rover({"cmd": "drive", "distance_m": 5, "speed_factor": 7.0}, INFO, None)
    assert b["speed_factor"] == 7.0


def test_follow_route_defaults_to_last_route_or_errors():
    b, notes = bk.command_to_rover({"cmd": "follow_route"}, INFO, "r9")
    assert b["route_id"] == "r9" and any("r9" in n for n in notes)
    with pytest.raises(ValueError, match="route_id"):
        bk.command_to_rover({"cmd": "follow_route"}, INFO, None)


def test_view_layers_dict_and_list():
    assert bk.view_to_rover({"layers": {"slope": True, "ortho": False}})["layers"] == [
        {"id": "slope", "visible": True}, {"id": "ortho", "visible": False}]
    assert bk.view_to_rover({"layers": ["slope"], "mode": "orbit"})["layers"] == [{"id": "slope", "visible": True}]
    assert bk.view_to_rover({"layers": {"a": {"visible": False, "opacity": 0.5}}})["layers"] == [
        {"id": "a", "visible": False, "opacity": 0.5}]


def test_science_unsupported_params_become_notes():
    b, notes = bk.science_to_rover({"time_budget_min": 60, "profile": "eva_walkable", "stop_minutes": 30.0,
                                    "return_to_start": False, "max_slope_deg": 12})
    assert b == {"time_budget_min": 60, "profile": "astronaut_walk"}
    assert len(notes) == 3 and all("ignores" in n for n in notes)
    b, notes = bk.science_to_rover({"time_budget_min": 60, "stop_minutes": 15.0, "return_to_start": True})
    assert notes == []


def test_sites_min_tier_note_and_near_default():
    b, notes = bk.sites_to_rover({"target": "carbonate", "min_tier": "strong_orbital", "top_k": 99})
    assert b == {"target": "carbonate", "near": "rover", "top_k": 20}
    assert notes and "min_tier" in notes[0]


def test_map_profile():
    assert bk.map_profile("eva_walkable") == "astronaut_walk"
    assert bk.map_profile("rover_scout") == "rover_scout" and bk.map_profile(None) is None


# ---- response-side mapping (and that the formatters accept the result) ------------------------------

def test_adapt_error_shapes():
    assert bk.adapt_error(422, {"error": "bad"}) == {"error": "bad", "hint": None}
    e = bk.adapt_error(422, {"detail": [{"loc": ["body", "points"], "msg": "too short"}]})
    assert "points: too short" in e["error"] and e["hint"]
    assert "HTTP 500" in bk.adapt_error(500, {"x": 1})["error"]


def test_state_fills_profile_remaining_and_formats():
    s = bk.adapt_state({**STATE, "last_event": "Arrived at goal A."}, INFO, 5.0)
    assert s["profile"] == "rover_scout" and s["remaining_m"] == 5.0 and s["sim_elapsed_s"] == 720.0
    assert s["last_event"] == "Arrived at goal A" and s["lat"] == 18.4447     # his fields are kept
    assert "Rover (1844, -433)" in fmt.fmt_rover_state(s)


def test_fsd_adapt_policy_hazard_and_format():
    f = bk.adapt_fsd({"armed": True, "mode": "idle",
                      "policy": {"max_slope_deg": 20, "caution_slope_deg": 15, "caution_speed_factor": 0.5,
                                 "replan": True, "max_replans": 1},
                      "route_id": None, "remaining_route_m": 12,
                      "last_hazard": {"x": 183, "y": 463, "slope_deg": 20.5, "reason": "blocked_slope"}})
    assert f["policy"] == {"replan_on_block": True, "caution_slowdown": True}
    assert f["last_hazard"]["detail"] == "20.5 degree slope" and f["replans_allowed"] == 1
    assert len(f["blockers"]) == 2
    text = fmt.fmt_fsd(f)
    assert "FSD ARMED" in text and "20.5 degree slope at (183, 463)" in text
    off = bk.adapt_fsd({"armed": False, "policy": {"replan": False, "caution_speed_factor": 1.0}})
    assert off["policy"] == {"replan_on_block": False, "caution_slowdown": False} and off["replans_allowed"] == 0


def test_command_arrived_blocked_and_teleport():
    d = bk.adapt_command({"ok": True, "cmd": "goto", "stop_reason": "arrived", "message": "Arrived at x.",
                          "distance_m": 69.0, "replans": 0, "events": [], "route_id": "r1", "state": STATE, "fsd": None}, INFO)
    assert d["detail"] == "Arrived at x." and "ARRIVED" in fmt.fmt_command(d)
    b = bk.adapt_command({"ok": False, "cmd": "drive", "stop_reason": "blocked_slope", "message": "Stopped before 21 deg slope.",
                          "replans": 1, "events": ["Hit 21 deg slope; replan failed: nope"], "state": STATE}, INFO)
    assert "Arm FSD" in b["hint"] and "attempted 1 replan" in b["note"] and "replan failed" in b["note"]
    assert "STOPPED: blocked_slope" in fmt.fmt_command(b)
    t = bk.adapt_command({"ok": True, "cmd": "teleport", "at": "base", "state": STATE,
                          "warnings": ["Landed on a 16 deg slope."]}, INFO)
    assert t["stop_reason"] == "arrived" and "16 deg" in t["note"] and "ARRIVED" in fmt.fmt_command(t)
    e = bk.adapt_command({"ok": False, "cmd": "goto", "stop_reason": "out_of_bounds", "message": "edge", "state": STATE}, INFO)
    assert "STOPPED: out_of_bounds" in fmt.fmt_command(e)


def test_fsd_enable_result_formats_via_fsd_change():
    d = bk.adapt_command({"ok": True, "cmd": "fsd_enable", "fsd": {"armed": True, "policy": {"replan": True}}}, INFO)
    assert "FSD ARMED" in fmt.fmt_fsd_change(d)


def test_route_adapt_consumables_stops_assessment_and_formats():
    r = bk.adapt_route(ROUTE, ASSESS, INFO)
    assert r["consumables"]["used"] == {"minutes": 46.1, "fraction": 0.032}
    assert r["consumables"]["margin"]["fraction"] == 0.868 and r["consumables"]["reserve_fraction"] == 0.1
    assert r["max_slope_limit_deg"] == 20 and r["caution_slope_deg"] == 15
    assert r["assessment"] == {"go": True, "reasons": [], "caution": []}
    (stop,) = r["stops"]
    assert stop["id"] == "(1870, -410)" and stop["distance_m"] == 36 and stop["kind"] == "via"
    assert r["start"]["x"] == 1844.0 and r["goal"]["x"] == 1900.0 and r["points"][1]["slope"] == 2.0
    text = fmt.fmt_route(r)
    assert "Route r5 (rover_scout, slope limit 20 deg): 69 m" in text and "Assessment: GO" in text
    assert "GO for r5" in fmt.fmt_assess(r)


def test_route_nogo_assessment_and_caution_split():
    r = bk.adapt_route({**ROUTE, "warnings": ["3 of 40 route points are on caution slopes (>= 15 deg)."]},
                       {"go": False, "reasons": ["max slope 25 deg exceeds the 20 deg limit"]}, INFO)
    assert r["assessment"]["go"] is False and r["assessment"]["reasons"] and r["assessment"]["caution"]
    assert "NO-GO" in fmt.fmt_route(r)


def test_infeasible_from_error():
    msg = ("No route from rover to (99, 99) stays under 20 deg slope for profile rover_scout. Nearest reachable point is "
           "(120, -34), 45 m from the goal. Use that point as the goal.")
    r = bk.infeasible_from_error(msg, INFO, "rover_scout")
    assert r["feasible"] is False and r["nearest_reachable"]["x"] == 120.0 and r["nearest_reachable"]["distance_to_goal_m"] == 45.0
    assert "NO ROUTE" in fmt.fmt_route(r) and "120" in fmt.fmt_route(r)
    assert bk.infeasible_from_error("(1, 2) is outside the site (|x|, |y| <= 4096 m).", INFO, None)["nearest_reachable"] is None
    assert bk.infeasible_from_error("Unknown place 'x'. Use search_pois or list_markers to find ids.", INFO, None) is None


def test_science_adapt_stops_arrival_and_empty():
    r = {**ROUTE, "science_stops": [{"order": 1, "label": "Delta", "ref": "poi:delta", "x": 1870, "y": -410,
                                     "science_value": 0.9, "source": "Mars 2020"}],
         "science_value_total": 0.9, "skipped": ["Other"]}
    d = bk.adapt_science(r, ASSESS, INFO, {"time_budget_min": 120, "interests": ["delta"]}, ["note one"])
    assert d["stops"][0]["id"] == "poi:delta" and d["stops"][0]["arrive_min"] == 16.1
    assert d["time"]["stops_min"] == 15 and d["time"]["total_min"] == 46.0 and d["time"]["budget_min"] == 120
    assert d["skipped"][0]["id"] == "Other" and "note one" in d["notes"]
    assert "1 science stop" in fmt.fmt_science(d) or "Traverse with 1 stop" in fmt.fmt_science(d)
    empty = bk.adapt_science({"feasible": False, "message": "No science targets match."}, None, INFO,
                             {"time_budget_min": 600}, [])
    assert empty["stops"] == [] and "No science stops fit" in fmt.fmt_science(empty) and "No science targets match" in fmt.fmt_science(empty)


def test_markers_view_pois_profile_sample():
    m = bk.adapt_markers({"markers": [{"id": "m1", "x": 1, "y": 2, "z": 3, "lat": 1.0, "lon": 2.0, "label": "A",
                                       "created_by": "agent"}]})
    assert m["count"] == 1 and "marker:m1" in fmt.fmt_markers(m)
    v = bk.adapt_view({"ok": True, "camera": {"mode": "orbit", "x": 1.0, "y": 2.0, "z": 3.0, "target": "Base"},
                       "highlights": ["base"], "layers": ["slope"], "viewers_connected": 0},
                      {"mode": "orbit", "layers": [{"id": "slope", "visible": True}]})
    text = fmt.fmt_view(v)
    assert "mode orbit" in text and "target Base" in text and "slope on" in text and "No viewer is connected" in text
    p = bk.adapt_pois({"count": 1, "pois": [{"id": "base", "name": "Base", "type": "base", "x": 1, "y": 2}]})
    assert p["pois"][0]["ref"] == "poi:base" and p["pois"][0]["science_value"] is None
    assert "poi:base" in fmt.fmt_pois(p)
    assert "No POIs match" in fmt.fmt_pois(bk.adapt_pois({"count": 0, "pois": []}, ["base"], ["base"]))
    prof = bk.adapt_profile({"distance_m": 65, "samples": 3, "elevation_min_m": -2570.0, "elevation_max_m": -2569.0,
                             "gain_m": 0.9, "max_slope_deg": 1.6,
                             "series": [{"d": 0, "x": 0, "y": 0, "z": -2570.0, "slope": 0.3},
                                        {"d": 30, "x": 1, "y": 0, "z": -2569.5, "slope": 1.6},
                                        {"d": 65, "x": 2, "y": 0, "z": -2569.7, "slope": 0.7}]}, 30.0)
    assert prof["count"] == 3 and prof["descent_m"] == 0.2 and prof["samples"][1]["slope_deg"] == 1.6
    assert "Profile 65 m" in fmt.fmt_profile(prof)


def test_sample_adapt_matches_server_shape():
    d = bk.adapt_sample({"x": 1900, "y": -400, "lat": 18.4, "lon": 77.4, "elevation_m": -2569.0, "slope_deg": 0.7,
                         "roughness_m": 0.03, "hazard": "safe", "filled": False,
                         "layers": {"min_carbonate": None, "min_clay": 0.5}}, INFO, "rover_scout")
    assert d["hazard_class"] == "safe" and d["no_go_slope_deg"] == 20 and d["layers"]["min_carbonate"] == {"unknown": True}
    assert d["layers"]["min_clay"] == {"value": 0.5} and d["layers"]["slope_2m"]["value"] == 0.7
    loc = {"id": "point", "x": d["x"], "y": d["y"], "lat": d["lat"], "lon": d["lon"], "values": d["layers"],
           "quality": d["quality"], "nearest_ground_truth": d["nearest_ground_truth"]}
    assert "slope_2m: 0.7 degrees" in fmt.fmt_describe({"radius_m": 0.0, "locations": [loc]})
    assert "interpolated" in bk.adapt_sample({**{"x": 0, "y": 0, "lat": 0, "lon": 0}, "hazard": "unknown", "filled": True,
                                              "layers": {}}, INFO, "rover_scout")["quality"]


def test_sites_adapt_and_format_never_claims_tiers():
    d = bk.adapt_sites(
        {"target": "carbonate", "count": 1, "near": "Base",
         "sites": [{"id": "site1", "x": 1327, "y": -1155, "z": -2500.0, "score": 0.71, "area_m2": 1060,
                    "mean_slope_deg": 4.9, "distance_m": 888, "reachable": True, "travel_min": 120.0,
                    "evidence": [{"layer": "min_carbonate", "mission": "MRO", "instrument": "CRISM", "mean_value": 0.7}]}]},
        "abundance", {"id": "Base", "x": 1844.0, "y": -433.0}, ["min_tier was not applied"])
    assert d["kind"] == "abundance_index" and d["set_id"] is None and d["sites"][0]["best_tier"] is None
    text = fmt.fmt_sites(d)
    assert "score 0.71 (0-1 index, not abundance)" in text and "Not stored as a set" in text
    assert "min_tier was not applied" in text and "Orbital tiers are detections" not in text
    assert "reachable" in text and "layer min_carbonate" in text
    unk = {**d, "sites": [{**d["sites"][0], "reachable": None}]}
    assert "reachability unknown" in fmt.fmt_sites(unk)


# ---- the router (mock transports) ---------------------------------------------------------------

def _client(handler, base):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base)


def _router(rover_handler, atlas_handler=None):
    calls = {"rover": [], "atlas": []}

    async def rh(req):
        body = json.loads(req.content) if req.content else None
        calls["rover"].append((req.method, req.url.path, dict(req.url.params), body))
        return rover_handler(req.method, req.url.path, dict(req.url.params), body)

    async def ah(req):
        body = json.loads(req.content) if req.content else None
        calls["atlas"].append((req.method, req.url.path, dict(req.url.params), body))
        return (atlas_handler or (lambda *a: httpx.Response(200, json={"ok": True})))(req.method, req.url.path, dict(req.url.params), body)

    b = bk.Backends(_client(ah, "http://atlas"), _client(rh, "http://rover"), "http://atlas", "http://rover")
    return b, calls


def _rover_default(method, path, params, body):
    if path == "/site":
        return httpx.Response(200, json={"profiles": INFO["profiles"], "rover_profile": "rover_scout",
                                         "default_profile": "astronaut_walk",
                                         "manifest": {"site": "j", "lat0": 18.452, "lon0": 77.418, "size_m": 8192}})
    if path == "/rover/state":
        return httpx.Response(200, json=STATE)
    if path == "/fsd/status":
        return httpx.Response(200, json={"armed": False, "policy": {}, "remaining_route_m": 0})
    if path == "/markers":
        return httpx.Response(200, json={"markers": [{"id": "m1", "x": 10.0, "y": 20.0, "z": 0, "label": "A",
                                                      "created_by": "agent", "lat": 0, "lon": 0}]})
    if path == "/route" and method == "POST":
        return httpx.Response(200, json=ROUTE)
    if path.endswith("/assess"):
        return httpx.Response(200, json=ASSESS)
    if path == "/agent/event":
        return httpx.Response(200, json={"ok": True})
    return httpx.Response(404, json={"detail": "nf"})


def run(coro):
    return anyio.run(lambda: coro)


def test_router_passthrough_when_no_rover_backend():
    seen = []

    async def ah(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={"hi": 1})
    b = bk.Backends(_client(ah, "http://atlas"), None, "http://atlas", "")
    st, d = run(b.request("POST", "/route", json_body={"goal": "x"}))
    assert (st, d) == (200, {"hi": 1}) and seen == ["/route"] and not b.split


def test_router_plan_route_translates_refs_profile_and_assesses():
    b, calls = _router(_rover_default)
    st, d = run(b.request("POST", "/route", json_body={"start": "here", "goal": "marker:m1", "via": ["base"],
                                                       "profile": "eva_walkable"}))
    assert st == 200 and d["feasible"] and d["assessment"]["go"] and b.last_route == "r5"
    sent = next(c for c in calls["rover"] if c[1] == "/route")[3]
    assert sent == {"start": "rover", "goal": "marker:m1", "via": ["base"], "profile": "astronaut_walk"}
    assert any(c[1] == "/route/r5/assess" for c in calls["rover"])


def test_router_set_ref_resolved_through_query_engine():
    def atlas(method, path, params, body):
        assert path == "/describe" and body["at"] == "3.1"
        return httpx.Response(200, json={"locations": [{"id": "3.1", "x": 5.0, "y": 6.0}]})
    b, calls = _router(_rover_default, atlas)
    run(b.request("POST", "/route", json_body={"goal": "3.1"}))
    assert next(c for c in calls["rover"] if c[1] == "/route")[3]["goal"] == {"x": 5.0, "y": 6.0}


def test_router_set_with_many_members_for_single_ref_is_error():
    def atlas(method, path, params, body):
        return httpx.Response(200, json={"locations": [{"id": "3.1", "x": 1, "y": 2}, {"id": "3.2", "x": 3, "y": 4}]})
    b, _ = _router(_rover_default, atlas)
    st, d = run(b.request("POST", "/route", json_body={"goal": "set:3"}))
    assert st == 422 and "resolves to 2 locations" in d["error"]


def test_router_infeasible_route_422_becomes_feasible_false():
    def rover(method, path, params, body):
        if path == "/route":
            return httpx.Response(422, json={"error": "No route from rover to (5, 5) stays under 20 deg slope for profile "
                                                      "rover_scout. Nearest reachable point is (9, 9), 7 m from the goal."})
        return _rover_default(method, path, params, body)
    b, _ = _router(rover)
    st, d = run(b.request("POST", "/route", json_body={"goal": {"x": 5, "y": 5}}))
    assert st == 200 and d["feasible"] is False and d["nearest_reachable"]["x"] == 9.0


def test_router_place_error_stays_an_error():
    def rover(method, path, params, body):
        if path == "/route":
            return httpx.Response(422, json={"error": "Unknown place 'zzz'. Use search_pois or list_markers to find ids."})
        return _rover_default(method, path, params, body)
    b, _ = _router(rover)
    st, d = run(b.request("POST", "/route", json_body={"goal": "zzz"}))
    assert st == 422 and "Unknown place" in d["error"] and d["hint"] is None


def test_router_atlas_refs_rover_and_marker_become_coordinates():
    b, calls = _router(_rover_default)
    run(b.request("POST", "/describe", json_body={"at": ["rover", "marker:m1", "poi:base"], "radius_m": 0.0}))
    sent = calls["atlas"][-1][3]
    assert sent["at"] == [{"x": 1844.3, "y": -432.7}, {"x": 10.0, "y": 20.0}, "poi:base"]
    # strings that are not references (layer ids, class names) are untouched
    run(b.request("POST", "/search/locations", json_body={"conditions": [{"layer": "rover", "op": "==", "value": "me"}],
                                                          "within": {"from": "here", "max": 5, "metric": "straight_m"}}))
    sent = calls["atlas"][-1][3]
    assert sent["conditions"][0]["layer"] == "rover" and sent["within"]["from"] == {"x": 1844.3, "y": -432.7}


def test_router_atlas_unknown_marker_is_readable():
    b, _ = _router(_rover_default)
    st, d = run(b.request("POST", "/describe", json_body={"at": "marker:m9"}))
    assert st == 422 and "marker:m9" in d["error"]


def test_router_find_sites_caches_ids_for_later_refs():
    def rover(method, path, params, body):
        if path == "/search/sites":
            return httpx.Response(200, json={"target": "carbonate", "count": 1, "near": "rover", "sites": [
                {"id": "site7", "x": 100, "y": 200, "score": 0.5, "area_m2": 100, "mean_slope_deg": 3, "distance_m": 9,
                 "reachable": True, "evidence": [{"layer": "min_carbonate"}]}]})
        if path == "/search/targets":
            return httpx.Response(200, json={"targets": [{"target": "carbonate", "kind": "abundance", "available": True}]})
        if path == "/terrain/sample":
            return httpx.Response(200, json={"x": 1844, "y": -433})
        return _rover_default(method, path, params, body)
    b2, calls2 = _router(rover)
    st, d = run(b2.request("POST", "/search/sites", json_body={"target": "carbonate", "near": "rover", "min_tier": "weak_orbital"}))
    assert st == 200 and d["sites"][0]["id"] == "site7" and "min_tier" in d["hint"]
    sent = next(c for c in calls2["rover"] if c[1] == "/search/sites")[3]
    assert "min_tier" not in sent and sent["profile"] == "rover_scout"
    run(b2.request("POST", "/describe", json_body={"at": "site7"}))
    assert calls2["atlas"][-1][3]["at"] == {"x": 100, "y": 200}
    run(b2.request("POST", "/route", json_body={"goal": "site7"}))
    assert next(c for c in calls2["rover"] if c[1] == "/route")[3]["goal"] == {"x": 100, "y": 200}


def test_router_command_goto_xy_and_unreachable_backend():
    def rover(method, path, params, body):
        if path == "/rover/command":
            assert body["target"] == {"x": 5, "y": 6} and body["speed_factor"] > 1 and "x" not in body
            return httpx.Response(200, json={"ok": True, "cmd": "goto", "stop_reason": "arrived", "message": "ok",
                                             "state": STATE, "fsd": None, "distance_m": 3.0})
        return _rover_default(method, path, params, body)
    b, _ = _router(rover)
    st, d = run(b.request("POST", "/rover/command", json_body={"cmd": "goto", "x": 5, "y": 6}))
    assert st == 200 and d["stop_reason"] == "arrived" and d["state"]["profile"] == "rover_scout"

    async def boom(req):
        raise httpx.ConnectError("refused")
    dead = bk.Backends(_client(boom, "http://atlas"), _client(boom, "http://rover"), "http://atlas", "http://rover")
    with pytest.raises(bk.Unreachable) as ei:
        run(dead.request("GET", "/health"))
    assert ei.value.which == "atlas"
    with pytest.raises(bk.Unreachable) as ei:
        run(dead.request("GET", "/rover/state"))
    assert ei.value.which == "rover" and ei.value.url == "http://rover"


def test_router_agent_event_goes_to_his_path_with_his_status_values():
    b, calls = _router(_rover_default)
    run(b.request("POST", "/events/agent", json_body={"tool": "t", "args_summary": "{}", "status": "done",
                                                      "result_summary": "x", "is_error": False}))
    m, path, _, body = calls["rover"][-1]
    assert path == "/agent/event" and body["status"] == "ok" and "is_error" not in body
    run(b.request("POST", "/events/agent", json_body={"tool": "t", "status": "error", "is_error": True}))
    assert calls["rover"][-1][3]["status"] == "error"


def test_router_unmapped_rover_path_is_readable_error():
    b, _ = _router(_rover_default)
    st, d = run(b.request("DELETE", "/markers/m1"))
    assert st == 422 and "no equivalent" in d["error"]


def test_router_validation_error_shape_is_translated():
    def rover(method, path, params, body):
        if path == "/terrain/profile":
            return httpx.Response(422, json={"detail": [{"loc": ["body", "points"], "msg": "List should have at least 2 items"}]})
        return _rover_default(method, path, params, body)
    b, _ = _router(rover)
    st, d = run(b.request("POST", "/terrain/profile", json_body={"points": ["base"]}))
    assert st == 422 and "points: List should have at least 2 items" in d["error"]


def test_router_view_highlight_coordinates_refused():
    b, _ = _router(_rover_default)
    st, d = run(b.request("POST", "/view", json_body={"highlights": [{"x": 1, "y": 2}]}))
    assert st == 422 and "can only highlight ids" in d["error"]


# ---- the frame banner through the real MCP server ----------------------------------------------------

def _server(rover_site):
    def rover(method, path, params, body):
        if path == "/site":
            return httpx.Response(200, json=rover_site)
        if path == "/health":
            return httpx.Response(200, json={"ok": True, "fixture": False})
        return _rover_default(method, path, params, body)

    async def rh(req):
        body = json.loads(req.content) if req.content else None
        return rover(req.method, req.url.path, dict(req.url.params), body)
    app = create_app(make_site(), synthetic=True)          # synthetic site: lat0 18.46, lon0 77.41, 512 m
    atlas = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://devapi")
    return srv.build_server(client=atlas, rover_client=_client(rh, "http://rover"), rover_url="http://rover")


def _call(server, tool, args=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})
    return anyio.run(go)


def test_server_warns_loudly_on_frame_mismatch():
    r = _call(_server(_his(18.452, 77.418, 8192, "jezero")), "get_rover_state")
    text = r.content[0].text
    assert text.startswith("[FRAME MISMATCH") and "654 m apart" in text and not r.is_error
    assert "FRAME MISMATCH" in r.structured_content["frame_warning"]
    ov = _call(_server(_his(18.452, 77.418, 8192, "jezero")), "get_site_overview")
    assert "FRAME MISMATCH" in ov.content[0].text and not ov.is_error


def test_server_no_banner_when_frames_agree():
    r = _call(_server(_his(18.46, 77.41, 512, "synthetic")), "get_rover_state")
    assert "FRAME MISMATCH" not in r.content[0].text and "EXTENT DIFFERS" not in r.content[0].text
    assert "frame_warning" not in r.structured_content


def test_server_extent_note_when_only_size_differs():
    r = _call(_server(_his(18.46, 77.41, 2048, "playground")), "get_rover_state")
    assert r.content[0].text.startswith("[EXTENT DIFFERS")


def test_server_rover_backend_down_is_a_readable_error():
    async def boom(req):
        raise httpx.ConnectError("refused")
    app = create_app(make_site(), synthetic=True)
    atlas = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://devapi")
    s = srv.build_server(client=atlas, rover_client=_client(boom, "http://rover"), rover_url="http://rover")
    r = _call(s, "get_rover_state")
    assert r.is_error and "rover backend is not reachable at http://rover" in r.content[0].text


# ---- agent context reads rover state and markers from the rover backend ------------------------------

def test_agent_context_uses_rover_url_when_set():
    from agent.context import fetch_context, render_context_block
    seen = []

    async def handler(req):
        seen.append(f"{req.url.host}{req.url.path}")
        if req.url.path == "/rover/state":
            return httpx.Response(200, json=STATE)
        return httpx.Response(200, json={"markers": [{"id": "m1", "x": 10.0, "y": 20.0, "label": "A", "created_by": "user"}]})

    async def go(rover_url):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as h:
            return await fetch_context("http://atlas-api", None, 1.0, h, rover_url=rover_url)
    ctx = anyio.run(go, "http://rover-api")
    assert set(seen) == {"rover-api/rover/state", "rover-api/markers"}
    assert ctx["rover"]["x"] == 1844.3 and ctx["markers"][0]["id"] == "m1"
    assert '"id":"m1"' in render_context_block(ctx)
    seen.clear()
    anyio.run(go, None)
    assert set(seen) == {"atlas-api/rover/state", "atlas-api/markers"}
