"""select_vehicle / drive_to (mcp/tools_drive.py) and the vehicle adapters in mcp/backends.py. No network.

Two layers: the MCP tools against the in-process devapi stand-in (synthetic site, vehicle profiles copied from
api/profiles.yaml), and the split-mode adapters against canned ares_api responses (httpx.MockTransport)."""

import anyio
import httpx
import pytest

from test_backends import INFO, STATE, _router, _rover_default, bk
from test_server import run, text, make_server


def _call(name, **args):
    async def f(c):
        return await c.call_tool(name, args)
    return run(f)


def _seq(*calls):
    """Several tool calls in ONE session (state persists): [(name, args), ...] -> list of results."""
    async def f(c):
        return [await c.call_tool(n, a) for n, a in calls]
    return run(f)


# ---- aliases and tables (backends.py) ------------------------------------------------------------

@pytest.mark.parametrize("word,name", [("buggy", "crew_buggy"), ("Crew Buggy", "crew_buggy"), ("the-buggy", "crew_buggy"),
                                       ("scout rover", "rover_scout"), ("scout", "rover_scout"),
                                       ("crew_rover", "crew_rover"), ("eva_walkable", "eva_walkable"), (None, None)])
def test_map_vehicle(word, name):
    assert bk.map_vehicle(word) == name


def test_map_profile_keeps_walker_mapping_and_aliases():
    assert bk.map_profile("eva_walkable") == "astronaut_walk"
    assert bk.map_profile("buggy") == "crew_buggy"


def test_vehicle_table_uses_api_numbers_only():
    t = bk.vehicle_table({"crew_buggy": {"title": "B", "flat_speed_mps": 24, "nogo_slope_deg": 25, "source": "assumed x"}},
                         {"crew_buggy": {"caution_slope_deg": 15, "eva_limit_min": 480}}, "crew_buggy")
    assert t["current"] == "crew_buggy"
    assert t["vehicles"]["crew_buggy"] == {"title": "B", "flat_speed_mps": 24, "max_slope_deg": 25,
                                           "caution_slope_deg": 15, "eva_limit_min": 480, "source": "assumed x",
                                           "active": True}


def test_command_speed_factor_uses_active_vehicle_not_scout():
    info = {**INFO, "profiles": {**INFO["profiles"], "crew_buggy": {"flat_speed_mps": 24.0, "display_speed_factor": 1}}}
    b, notes = bk.command_to_rover({"cmd": "goto", "target": "base"}, info, None, "crew_buggy")
    assert b["speed_factor"] == pytest.approx(bk.INSTANT_DISPLAY_MPS / 24.0)
    b2, _ = bk.command_to_rover({"cmd": "goto", "target": "base"}, info, None)       # old behaviour: scout
    assert b2["speed_factor"] == pytest.approx(bk.INSTANT_DISPLAY_MPS / 0.04)
    b3, _ = bk.command_to_rover({"cmd": "goto", "target": "base", "realtime": True}, info, None, "crew_buggy")
    assert b3["speed_factor"] == 1.0


def test_adapt_state_profile_is_the_active_vehicle():
    assert bk.adapt_state({**STATE, "vehicle": "crew_buggy"}, INFO)["profile"] == "crew_buggy"
    assert bk.adapt_state(STATE, INFO)["profile"] == "rover_scout"


# ---- split mode: router against canned ares_api ---------------------------------------------------

def _his(method, path, params, body):
    if path == "/rover/vehicles":
        return httpx.Response(200, json={"current": "crew_buggy", "vehicles": {
            "rover_scout": {"title": "Scout", "flat_speed_mps": 0.04, "nogo_slope_deg": 20, "source": "assumed"},
            "crew_buggy": {"title": "Buggy", "flat_speed_mps": 24, "nogo_slope_deg": 25, "source": "assumed b"}}})
    if path == "/rover/vehicle" and method == "POST":
        if body["profile"] not in ("rover_scout", "crew_buggy"):
            return httpx.Response(422, json={"error": f"Unknown profile {body['profile']}."})
        return httpx.Response(200, json={"ok": True, "vehicle": body["profile"], "title": "T",
                                         "state": {**STATE, "vehicle": body["profile"]}})
    if path == "/rover/state":
        return httpx.Response(200, json={**STATE, "vehicle": "crew_buggy"})
    if path == "/site":
        d = _rover_default(method, path, params, body).json()
        d["profiles"]["crew_buggy"] = {"flat_speed_mps": 24, "display_speed_factor": 1, "caution_slope_deg": 15,
                                       "nogo_slope_deg": 25, "eva_limit_min": 480, "reserve_frac": 0.2}
        return httpx.Response(200, json=d)
    return _rover_default(method, path, params, body)


def _arun(coro):
    return anyio.run(lambda: coro)


def test_router_vehicle_get_and_switch_with_alias():
    b, calls = _router(_his)
    st, d = _arun(b.request("GET", "/rover/vehicle"))
    assert st == 200 and d["current"] == "crew_buggy" and d["vehicles"]["crew_buggy"]["caution_slope_deg"] == 15
    st, d = _arun(b.request("POST", "/rover/vehicle", json_body={"profile": "the scout rover"}))
    assert st == 200 and d["vehicle"] == "rover_scout" and d["state"]["profile"] == "rover_scout"
    assert next(c for c in calls["rover"] if c[0] == "POST" and c[1] == "/rover/vehicle")[3] == {"profile": "rover_scout"}
    st, d = _arun(b.request("POST", "/rover/vehicle", json_body={"profile": "hovercraft"}))
    assert st == 422 and "Valid vehicles: rover_scout, crew_buggy" in d["hint"]


def test_router_route_without_profile_uses_active_vehicle():
    b, calls = _router(_his)
    _arun(b.request("POST", "/route", json_body={"goal": "base"}))
    # no workaround any more: his /route defaults to the active vehicle, so no profile is sent
    assert "profile" not in next(c for c in calls["rover"] if c[1] == "/route" and c[0] == "POST")[3]
    b, calls = _router(_his)
    _arun(b.request("POST", "/route", json_body={"goal": "base", "profile": "buggy"}))
    assert next(c for c in calls["rover"] if c[1] == "/route" and c[0] == "POST")[3]["profile"] == "crew_buggy"


def test_router_goto_speed_factor_follows_active_vehicle():
    def h(method, path, params, body):
        if path == "/rover/command":
            return httpx.Response(200, json={"ok": True, "cmd": "goto", "stop_reason": "arrived", "message": "ok",
                                             "distance_m": 5, "state": {**STATE, "vehicle": "crew_buggy"}})
        return _his(method, path, params, body)
    b, calls = _router(h)
    _arun(b.request("POST", "/rover/command", json_body={"cmd": "goto", "target": "base"}))
    sent = next(c for c in calls["rover"] if c[1] == "/rover/command")[3]
    assert sent["speed_factor"] == pytest.approx(bk.INSTANT_DISPLAY_MPS / 24.0)


# ---- the tools against the stand-in -----------------------------------------------------------------

def test_select_vehicle_lists_and_switches():
    r = _call("select_vehicle")
    sc = r.structured_content
    assert {"rover_scout", "crew_buggy", "crew_rover", "eva_walkable"} <= set(sc["vehicles"])
    assert "no-go above" in text(r) and sc["current"] == "rover_scout"
    r = _call("select_vehicle", vehicle="the buggy")
    assert r.structured_content["current"] == "crew_buggy" and r.structured_content["switched"] is True
    assert "Switched to crew_buggy" in text(r)
    r = _call("select_vehicle", vehicle="hovercraft")
    assert r.is_error and "Valid vehicles" in text(r)


def test_buggy_limits_come_from_the_api_not_the_tool():
    server, app = make_server()
    lim = app.state.world.site.profiles["crew_buggy"]["travel"]
    r, = _seq(("drive_to", {"wait": True, "animate": False, "target": "poi:landing", "vehicle": "buggy"}))
    got = r.structured_content["limits"]
    assert got["max_slope_deg"] == lim["max_slope_deg"]["value"]
    assert got["flat_speed_mps"] == lim["flat_speed_mps"]["value"]
    assert "api/profiles.yaml" in got["source"]


def test_drive_to_buggy_arrives_and_reports_everything():
    sel, r, st = _seq(("select_vehicle", {}), ("drive_to", {"wait": True, "animate": False, "target": "poi:landing", "vehicle": "buggy"}),
                      ("get_rover_state", {}))
    sc = r.structured_content
    assert not r.is_error and sc["vehicle"] == "crew_buggy" and sc["driven"] and sc["stop_reason"] == "arrived"
    assert sc["distance_driven_m"] > 50 and sc["route"]["max_slope_deg"] is not None and sc["elapsed_sim_min"] is not None
    assert "ARRIVED with crew_buggy" in text(r) and "max slope" in text(r)
    assert st.structured_content["profile"] == "crew_buggy"
    assert abs(st.structured_content["x"] - 50) < 2          # the synthetic landing POI


def test_drive_to_other_start_teleports_first_and_says_so():
    r, st = _seq(("drive_to", {"wait": True, "animate": False, "target": "base", "start": "poi:landing", "vehicle": "buggy"}), ("get_rover_state", {}))
    sc = r.structured_content
    assert sc["repositioned"] == "teleport" and sc["driven"]
    assert any("teleport" in n and "no simulated time" in n for n in sc["notes"])
    assert abs(st.structured_content["x"]) < 3 and abs(st.structured_content["y"]) < 3
    assert abs(sc["distance_driven_m"] - sc["route"]["distance_m"]) < 10      # drove A->B, not rover->A->B


def test_drive_to_reposition_drive_and_none():
    r, = _seq(("drive_to", {"wait": True, "animate": False, "target": "base", "start": "poi:landing", "vehicle": "buggy", "reposition": "drive"}))
    sc = r.structured_content
    assert sc["repositioned"] == "drive" and sc["driven"] and any("Drove" in n and "start first" in n for n in sc["notes"])
    r, = _seq(("drive_to", {"wait": True, "animate": False, "target": "base", "start": "poi:landing", "reposition": "sideways"}))
    assert r.is_error and "reposition" in text(r)


def test_drive_to_infeasible_goal_does_not_move():
    r, st = _seq(("drive_to", {"wait": True, "animate": False, "target": {"x": 107, "y": -49}, "vehicle": "buggy"}), ("get_rover_state", {}))
    sc = r.structured_content
    assert not r.is_error and sc["feasible"] is False and sc["driven"] is False
    assert "NOT DRIVEN" in text(r)
    assert abs(st.structured_content["x"]) < 1 and abs(st.structured_content["y"]) < 1     # still at base


def test_drive_to_highest_flags_unreachable_summit_and_uses_reachable_point():
    r, = _seq(("drive_to", {"wait": True, "animate": False, "vehicle": "buggy", "highest_within_m": 200}))
    sc = r.structured_content
    h = sc["highest"]
    assert sc["driven"] and h["true_summit_reached"] is False
    assert h["summit"]["elevation_m"] > h["chosen"]["elevation_m"]
    assert h["summit"]["slope_deg"] > sc["limits"]["max_slope_deg"]                # steeper than the buggy limit
    assert "Highest REACHABLE point" in text(r) and "not reachable" in text(r) and "its own slope is above" in text(r)
    # the chosen point itself is within the limit
    assert sc["route"]["max_slope_deg"] <= sc["limits"]["max_slope_deg"]


def test_drive_to_highest_true_summit_when_reachable():
    r, = _seq(("drive_to", {"wait": True, "animate": False, "vehicle": "buggy", "highest_within_m": 60}))
    sc = r.structured_content
    assert sc["highest"]["true_summit_reached"] is True and "true highest cell" in text(r)


def test_drive_to_scout_to_point_and_state_changes_vehicle():
    server, app = make_server()
    r, st = _seq(("drive_to", {"wait": True, "animate": False, "target": {"x": 40, "y": -20}, "vehicle": "scout"}), ("get_rover_state", {}))
    sc = r.structured_content
    assert sc["vehicle"] == "rover_scout" and sc["stop_reason"] == "arrived"
    assert sc["limits"]["max_slope_deg"] == app.state.world.site.profiles["rover_scout"]["travel"]["max_slope_deg"]["value"] \
        if isinstance(app.state.world.site.profiles["rover_scout"]["travel"]["max_slope_deg"], dict) \
        else sc["limits"]["max_slope_deg"] == app.state.world.site.profiles["rover_scout"]["travel"]["max_slope_deg"]
    assert st.structured_content["profile"] == "rover_scout"


def test_drive_to_needs_target_or_radius_and_clamps_slope():
    r = _call("drive_to")
    assert r.is_error and "highest_within_m" in text(r)
    r = _call("drive_to", wait=True, animate=False, target="poi:landing", vehicle="buggy", max_slope_deg=80)
    assert any("looser" in n for n in r.structured_content["notes"])
    r = _call("drive_to", wait=True, animate=False, target="poi:landing", max_slope_deg=5)
    assert r.structured_content["slope_limit_used_deg"] == 5


def test_drive_to_fsd_is_disarmed_again():
    r, st = _seq(("drive_to", {"wait": True, "animate": False, "target": "poi:landing", "fsd": True}), ("get_rover_state", {}))
    assert r.structured_content["driven"] and st.structured_content["fsd_armed"] is False


def test_plan_route_default_profile_follows_active_vehicle():
    sel, r = _seq(("select_vehicle", {"vehicle": "buggy"}), ("plan_route", {"goal": "poi:landing"}))
    assert r.structured_content["profile"] == "crew_buggy"
    r2 = _call("plan_route", goal="poi:landing")
    assert r2.structured_content["profile"] == "rover_scout"


# ---- filled terrain policy, structured limits (final-pass API) -----------------------------------------

def test_policy_filled_passes_through_and_is_validated():
    assert bk.policy_to_rover({"filled": "block"}) == {"filled": "block"}
    with pytest.raises(ValueError, match="filled"):
        bk.policy_to_rover({"filled": "maybe"})


def test_vehicle_table_prefers_api_caution_field():
    t = bk.vehicle_table({"crew_buggy": {"title": "B", "nogo_slope_deg": 25, "caution_slope_deg": 14}},
                         {"crew_buggy": {"caution_slope_deg": 15}}, "crew_buggy")
    assert t["vehicles"]["crew_buggy"]["caution_slope_deg"] == 14


def test_get_rover_state_shows_slope_limits_from_api_fields():
    s = {**STATE, "vehicle": "crew_buggy", "caution_slope_deg": 15, "nogo_slope_deg": 25}
    from format import fmt_rover_state
    assert "caution above 15 deg, no-go above 25 deg" in fmt_rover_state(bk.adapt_state(s, INFO))


def test_fsd_status_shows_limits_and_filled_policy():
    from format import fmt_fsd
    f = bk.adapt_fsd({"armed": True, "mode": "idle", "caution_slope_deg": 15, "nogo_slope_deg": 25,
                      "policy": {"filled": "block", "replan": True}, "profile": "crew_buggy"})
    t = fmt_fsd(f)
    assert f["policy"]["filled"] == "block" and "filled=block" in t and "caution above 15 deg, no-go above 25 deg" in t


def test_drive_to_defaults_to_fsd_caution_and_reports_filled_fields():
    r, st = _seq(("drive_to", {"wait": True, "animate": False, "target": "poi:landing", "vehicle": "buggy"}), ("get_rover_state", {}))
    sc = r.structured_content
    assert sc["fsd"] is True and sc["filled_policy"] == "caution" and sc["driven"]
    assert sc["filled_crossed_m"] == 0 and sc["route"]["filled_policy"] == "caution"
    assert st.structured_content["fsd_armed"] is False and st.structured_content["caution_slope_deg"] is not None


@pytest.mark.parametrize("pol", ["block", "allow"])
def test_drive_to_filled_option_and_fsd_false(pol):
    r = _call("drive_to", wait=True, animate=False, target="poi:landing", vehicle="buggy", filled=pol, fsd=False)
    assert r.structured_content["filled_policy"] == pol and r.structured_content["fsd"] is False
    assert r.structured_content["stop_reason"] == "arrived"


def test_drive_to_bad_filled_is_an_error():
    r = _call("drive_to", wait=True, animate=False, target="poi:landing", filled="sometimes")
    assert r.is_error


def test_drive_to_split_mode_sends_filled_and_reports_crossing():
    def h(method, path, params, body):
        if path == "/route" and method == "POST":
            pts = [{"x": 0, "y": 0, "z": 0}, {"x": 100, "y": 0, "z": 0}]
            return httpx.Response(200, json={
                "route_id": "route-1", "profile": "crew_buggy", "points": pts, "distance_m": 100, "duration_min": 1,
                "ascent_m": 0, "max_slope_deg": 3, "slope_limit_deg": 25, "filled_policy": body.get("filled"),
                "filled_cells": 4, "filled_length_m": 40.0, "warnings": [
                    "Route crosses 4 of 25 points (40 m) of interpolated (filled) DEM terrain: elevation there is estimated."],
                "start": "rover", "goal": "x", "legs": [], "stops": []})
        if path == "/rover/command" and body.get("cmd") == "follow_route":
            return httpx.Response(200, json={"ok": True, "cmd": "follow_route", "stop_reason": "arrived",
                                             "message": "ok", "distance_m": 100, "filled_crossed_m": 38.0,
                                             "state": {**STATE, "vehicle": "crew_buggy"}})
        return _his(method, path, params, body)
    b, calls = _router(h)
    # adapter level: the filled policy reaches his /route and fsd_enable, his crossing comes back
    _arun(b.request("POST", "/rover/command", json_body={"cmd": "fsd_enable", "policy": {"filled": "block"}}))
    cmd = [c for c in calls["rover"] if c[1] == "/rover/command"][-1][3]
    assert cmd["policy"] == {"filled": "block"}
    st, d = _arun(b.request("POST", "/route", json_body={"goal": "base", "filled": "block"}))
    assert next(c for c in calls["rover"] if c[1] == "/route" and c[0] == "POST")[3]["filled"] == "block"
    assert d["filled_cells"] == 4 and d["filled_policy"] == "block"
    st, d = _arun(b.request("POST", "/rover/command", json_body={"cmd": "follow_route", "route_id": "route-1"}))
    assert d["filled_crossed_m"] == 38.0
    from format import fmt_command
    assert "Crossed 38 m of interpolated" in fmt_command(d)
