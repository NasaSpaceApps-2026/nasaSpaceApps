"""MCP server (mcp/server.py) end to end over the in-process devapi stand-in: tool list, text + structured results, error results, events."""
import importlib.util
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


srv = _load("atlas_server", ROOT / "mcp" / "server.py")
fmt = sys.modules["format"]  # mcp/format.py, put on sys.path by server.py
BANNER = "[SYNTHETIC TEST SITE — not Mars data]"


def make_server(app=None, targets=None):
    """MCP server wired to a devapi app in-process (no sockets)."""
    app = app or create_app(make_site(), synthetic=True)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://devapi")
    return srv.build_server(client=client, targets=targets), app


def run(coro_fn, app=None):
    server, _ = make_server(app)

    async def go():
        async with Client(server) as c:
            return await coro_fn(c)
    return anyio.run(go)


def record_events(app):
    """Capture every WS broadcast of the app's hub: list of (type, payload)."""
    hub = app.state.world.hub
    seen = []
    orig = hub.broadcast

    async def spy(t, payload):
        seen.append((t, payload))
        await orig(t, payload)
    hub.broadcast = spy
    return seen


def text(r):
    return r.content[0].text


def test_lists_tools_and_descriptions():
    async def f(c):
        return (await c.list_tools()).tools
    tools = {t.name: t for t in run(f)}
    assert set(tools) == {
        "get_site_overview", "list_layers", "describe_layer", "describe_location", "sample_terrain", "find_locations",
        "find_regions", "nearest", "get_set",
        "plan_route", "assess_route", "plan_science_traverse", "get_elevation_profile", "search_pois",
        "get_rover_state", "drive_rover", "goto_location", "follow_route", "fsd_enable", "fsd_disable", "fsd_status",
        "stop_rover", "set_view", "list_markers", "place_marker", "find_sites", "teleport_rover", "fly_camera",
        "viewshed", "line_of_sight", "find_vantage_points",
        "flood_at_level", "eva_envelope", "check_eva_return",
        "sun_position", "illumination_at", "find_lit_windows", "select_vehicle", "drive_to", "get_drive_progress"}
    for name, t in tools.items():
        assert "Example" in t.description, name
        assert len(t.description) > 80, name
    d = tools["find_locations"].description
    for needle in ("metres", "minutes", "any_of", "Example", "footprint_radius_m", "set"):
        assert needle in d
    assert "Example" in tools["describe_location"].description


def test_overview_tool_and_resource():
    async def f(c):
        r = await c.call_tool("get_site_overview", {})
        res = await c.read_resource("atlas://site/overview")
        return r, res
    r, res = run(f)
    assert not r.is_error
    assert text(r).startswith(BANNER)
    assert r.structured_content["site"]["name"] == "synthetic"
    assert r.structured_content["synthetic"] is True
    assert "carbonate" in text(r) and "landing_pad" in text(r)
    assert "Markers: none placed" in text(r) and "Search targets for find_sites: " in text(r)
    assert "Rover: (0, 0)" in text(r)
    assert BANNER in res.contents[0].text


def test_describe_location_text_and_structured():
    async def f(c):
        return await c.call_tool("describe_location", {"at": {"x": 103.4, "y": -50.2}})
    r = run(f)
    assert not r.is_error
    t = text(r)
    assert "(103, -50)" in t and "slope_2m: 55" in t        # rounded to the metre; ridge flank
    loc = r.structured_content["locations"][0]
    assert loc["x"] == 103.4 and 50 < loc["values"]["slope_2m"]["value"] < 61
    assert len(t.split()) < 300


def test_describe_location_string_args_and_alias():
    async def f(c):
        a = await c.call_tool("describe_location", {"at": '{"x": 0, "y": -150}'})
        b = await c.call_tool("sample_terrain", {"x": 0, "y": 0})
        return a, b
    a, b = run(f)
    assert "unknown (no data)" in text(a)
    assert not b.is_error and b.structured_content["locations"][0]["values"]["slope_2m"]["value"] < 2


def test_unknown_layer_is_readable_error_result():
    async def f(c):
        return await c.call_tool("describe_layer", {"layer_id": "slope"})
    r = run(f)
    assert r.is_error is True
    assert 'Unknown layer "slope"' in text(r) and "slope_2m" in text(r)
    assert "Traceback" not in text(r)
    assert r.structured_content["hint"]


def test_other_errors_are_readable():
    async def f(c):
        return [await c.call_tool("describe_location", {"at": "nowhere"}),
                await c.call_tool("get_set", {"set_id": "set:99"}),
                await c.call_tool("describe_location", {"at": {"x": "abc", "y": 1}})]
    for r in run(f):
        assert r.is_error and text(r).startswith(BANNER) and "Error:" in text(r)
        assert "Traceback" not in text(r)


def test_list_layers_and_get_set():
    async def f(c):
        return await c.call_tool("list_layers", {"kind": "evidence"})
    r = run(f)
    assert [l["id"] for l in r.structured_content["layers"]] == ["carbonate"]


def test_get_set_roundtrip():
    async def f(c):
        return await c.call_tool("get_set", {"set_id": "set:1"})
    r = run(f)
    assert r.is_error and "Unknown set" in text(r)


def test_find_locations_tool():
    async def f(c):
        return await c.call_tool("find_locations", {"profile": "landing_pad", "limit": 3})
    r = run(f)
    assert not r.is_error and r.structured_content["set_id"].startswith("set:")
    assert "Thresholds used" in text(r)
    assert any(a["source"] == "assumed" for a in r.structured_content["assumptions"])


def test_find_regions_tool():
    async def f(c):
        return await c.call_tool("find_regions", {"conditions": [{"evidence": "carbonate", "min_tier": "strong_orbital"}]})
    r = run(f)
    assert not r.is_error and r.structured_content["count"] >= 1


def test_nearest_tool_and_get_set():
    async def f(c):
        n = await c.call_tool("nearest", {"from_ref": "base", "to_ref": "poi:landing", "metric": "straight_m"})
        s = await c.call_tool("get_set", {"set_id": n.structured_content["set_id"]})
        return n, s
    n, s = run(f)
    assert not n.is_error and not s.is_error
    assert n.structured_content["results"][0]["cost"] == pytest.approx(70.7, abs=2)


# ---- navigation, rover, view, markers, sites (via REST) ----

def test_plan_route_assess_and_infeasible():
    async def f(c):
        a = await c.call_tool("plan_route", {"start": "base", "goal": "outcrop_a", "profile": "eva_walkable"})
        b = await c.call_tool("assess_route", {"route_id": a.structured_content["route_id"]})
        d = await c.call_tool("plan_route", {"start": "base", "goal": {"x": 106, "y": -100}})
        e = await c.call_tool("assess_route", {"route_id": "route-99"})
        return a, b, d, e
    a, b, d, e = run(f)
    assert not a.is_error and a.structured_content["feasible"] and text(a).startswith(BANNER)
    assert "m," in text(a) and "Consumables" in text(a) and "Assessment: GO" in text(a)
    assert len(text(a).split()) < 300
    assert text(b).splitlines()[1].startswith("GO for route-") and "margin above reserve" in text(b)
    assert not d.is_error and d.structured_content["feasible"] is False      # infeasible is an answer, not a tool error
    assert "NO ROUTE" in text(d) and "Nearest reachable point" in text(d)
    assert e.is_error and "Unknown route" in text(e)


def test_markers_flow_and_routes_between_markers():
    async def f(c):
        p1 = await c.call_tool("place_marker", {"x": 40, "y": -60, "label": "camp"})
        p2 = await c.call_tool("place_marker", {"at": "poi:landing"})
        lm = await c.call_tool("list_markers", {})
        r = await c.call_tool("plan_route", {"start": "marker:m1", "goal": "marker:m2", "profile": "eva_walkable"})
        ov = await c.call_tool("get_site_overview", {})
        return p1, p2, lm, r, ov
    p1, p2, lm, r, ov = run(f)
    assert "marker:m1" in text(p1) and "camp" in text(p1)
    assert [m["id"] for m in lm.structured_content["markers"]] == ["m1", "m2"] and "marker:m2" in text(lm)
    assert r.structured_content["feasible"] and r.structured_content["goal"]["x"] == 50
    assert "marker:m1" in text(ov) and "camp" in text(ov)


def test_find_sites_search_pois_science_profile():
    async def f(c):
        s = await c.call_tool("find_sites", {"target": "carbonate", "near": "base", "top_k": 3})
        bad = await c.call_tool("find_sites", {"target": "gold"})
        p = await c.call_tool("search_pois", {"near": {"x": 0, "y": 0}, "radius_m": 100})
        p2 = await c.call_tool("search_pois", {"query": "outcrop"})
        sc = await c.call_tool("plan_science_traverse", {"time_budget_min": 240})
        pr = await c.call_tool("get_elevation_profile", {"points": [{"x": 0, "y": 0}, {"x": 150, "y": 0}]})
        st = await c.call_tool("sample_terrain", {"x": 106, "y": -50})
        return s, bad, p, p2, sc, pr, st
    s, bad, p, p2, sc, pr, st = run(f)
    assert "confirmed_in_situ" in text(s) and "SYNTHETIC/test fixture" in text(s) and "reachable" in text(s)
    assert "not abundance" in text(s) and s.structured_content["sites"][0]["id"] == "1.1"
    assert bad.is_error and "carbonate" in text(bad)
    assert "poi:base" in text(p) and "poi:landing" in text(p) and "science value not recorded" in text(p)
    assert "poi:outcrop_a" in text(p2)
    assert "3 stop(s)" in text(sc) and "assumed" in text(sc) and sc.structured_content["route"]["route_id"]
    assert "Profile 150 m" in text(pr) and "max" in text(pr)
    assert "Hazard class for rover_scout: no-go" in text(st) and st.structured_content["hazard_class"] == "no-go"


def test_rover_tools_drive_and_stop_reasons():
    async def f(c):
        s0 = await c.call_tool("get_rover_state", {})
        d = await c.call_tool("drive_rover", {"wait": True, "animate": False, "wait": True, "animate": False, "distance_m": 300, "heading_deg": 90})
        fs = await c.call_tool("fsd_status", {})
        t = await c.call_tool("teleport_rover", {"target": "poi:landing", "heading_deg": 90})
        g = await c.call_tool("goto_location", {"wait": True, "animate": False, "wait": True, "animate": False, "x": 80, "y": -50})
        bad = await c.call_tool("goto_location", {"wait": True, "animate": False, "wait": True, "animate": False, "x": 106, "y": -50})
        none = await c.call_tool("goto_location", {"wait": True, "animate": False})
        tb = await c.call_tool("teleport_rover", {"x": 106, "y": -50})
        sp = await c.call_tool("stop_rover", {})
        return s0, d, fs, t, g, bad, none, tb, sp
    s0, d, fs, t, g, bad, none, tb, sp = run(f)
    assert "Rover (0, 0)" in text(s0) and "mode idle" in text(s0)
    assert "STOPPED: blocked_slope" in text(d) and d.structured_content["stop_reason"] == "blocked_slope"
    assert "Last hazard" in text(fs)
    assert "ARRIVED" in text(t) and "(50, -50)" in text(t)
    assert "ARRIVED" in text(g) and g.structured_content["state"]["x"] == pytest.approx(80, abs=0.01)
    assert "NOT DRIVEN" in text(bad) and not bad.is_error and "Nearest reachable point" in text(bad)
    assert none.is_error and tb.is_error and "Nearest walkable" in text(tb)
    assert "stopped_by_user" in text(sp)


def test_follow_route_with_fsd_replan_through_mcp():
    import math
    import numpy as np
    app = create_app(make_site(), synthetic=True)
    w = app.state.world
    pts = [{"x": 60 + 100 * t, "y": -100.0, "z": 0.0} for t in np.linspace(0, 1, 21)]
    w.routes["route-90"] = {"route_id": "route-90", "feasible": True, "reason": None, "points": pts,
                            "distance_m": 100.0, "goal": {"x": 160.0, "y": -100.0}}

    async def f(c):
        await c.call_tool("teleport_rover", {"x": 60, "y": -100})
        en = await c.call_tool("fsd_enable", {"route_id": "route-90"})
        r = await c.call_tool("follow_route", {"wait": True, "animate": False})
        new_id = r.structured_content["new_route"]["route_id"]
        r2 = await c.call_tool("follow_route", {"wait": True, "animate": False, "wait": True, "animate": False, "route_id": new_id})
        fs = await c.call_tool("fsd_disable", {})
        return en, r, r2, fs
    en, r, r2, fs = run(f, app)
    assert "FSD ARMED" in text(en) and en.structured_content["fsd"]["armed"] is True
    assert "STOPPED: replanned" in text(r) and "New route route-" in text(r)
    assert "ARRIVED" in text(r2) and math.hypot(r2.structured_content["state"]["x"] - 160, r2.structured_content["state"]["y"] + 100) < 3
    assert "FSD off" in text(fs)


def test_view_tools():
    app = create_app(make_site(), synthetic=True)
    seen = record_events(app)

    async def f(c):
        a = await c.call_tool("fly_camera", {"target": "poi:landing", "distance_m": 300})
        b = await c.call_tool("set_view", {"layers": {"carbonate": True}, "highlights": ["poi:base"]})
        bad = await c.call_tool("set_view", {"mode": "flying"})
        return a, b, bad
    a, b, bad = run(f, app)
    assert "mode orbit" in text(a) and "(50, -50)" in text(a) and "No viewer" in text(a)
    assert "carbonate on" in text(b) and bad.is_error and "orbit" in text(bad)
    types = [t for t, _ in seen]
    assert "view.camera" in types and "view.layer" in types and "view.highlight" in types


def test_every_tool_call_posts_agent_event():
    app = create_app(make_site(), synthetic=True)
    seen = record_events(app)

    async def f(c):
        await c.call_tool("get_rover_state", {})
        await c.call_tool("describe_layer", {"layer_id": "slope"})        # error result
        await c.call_tool("plan_route", {"goal": "outcrop_a", "start": "base"})
    run(f, app)
    ev = [p for t, p in seen if t == "agent.event"]
    by = [(e["tool"], e["status"]) for e in ev]
    assert ("get_rover_state", "started") in by and ("get_rover_state", "done") in by
    assert ("describe_layer", "error") in by and ("plan_route", "done") in by
    done = next(e for e in ev if e["tool"] == "get_rover_state" and e["status"] == "done")
    assert "Rover (0, 0)" in done["result_summary"]
    pr = next(e for e in ev if e["tool"] == "plan_route" and e["status"] == "started")
    assert "outcrop_a" in pr["args_summary"]


def test_api_unreachable_is_readable_and_events_never_fail_tool():
    class Dead(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused", request=request)
    client = httpx.AsyncClient(transport=Dead(), base_url="http://localhost:8000")
    server = srv.build_server(client=client, api_url="http://localhost:8000")

    async def go():
        async with Client(server) as c:
            return [await c.call_tool("get_site_overview", {}), await c.call_tool("plan_route", {"goal": "base"})]
    for r in anyio.run(go):
        assert r.is_error and "API not reachable at http://localhost:8000" in text(r) and "python -m devapi" in text(r)
        assert "Traceback" not in text(r)


def test_find_sites_description_lists_targets_when_known():
    server, _ = make_server(targets=["carbonate", "olivine"])

    async def go():
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}
    d = anyio.run(go)["find_sites"].description
    assert "carbonate, olivine" in d and "Example" in d


# ---- format.py unit tests (no server needed) ----

def test_format_search_empty_shows_diagnostics_and_labels():
    d = {"set_id": "set:1", "count": 0, "results": [], "objective": "flattest",
         "assumptions": [{"what": "slope_2m <= 10", "source": "assumed"},
                         {"what": "rough_5x5 <= 0.5", "source": "TBD: cite"},
                         {"what": "x", "source": "Smith 2020"}],
         "diagnostics": [{"step": "slope_2m <= 10", "pass_fraction": 0.71},
                         {"step": "+ footprint radius 50 m", "pass_fraction": 0.003}],
         "hint": "relax the footprint radius"}
    t = fmt.fmt_search(d, "spots")
    assert "No spots found" in t and "71%" in t and "0.3%" in t
    assert "[assumed]" in t and "[citation pending]" in t and "[cited: Smith 2020]" in t
    assert "Hint: relax" in t


def test_format_search_results_rounds_to_metre():
    d = {"set_id": "set:2", "count": 7, "objective": "flattest", "assumptions": [], "diagnostics": [],
         "hint": None,
         "results": [{"id": "2.1", "x": 812.6, "y": -1220.4, "score": 2.1, "clear_radius_m": 64.2,
                      "attributes": {"slope_2m_mean": 2.1, "slope_2m_max": 6.4, "walk_time_min": 22.0}}]}
    t = fmt.fmt_search(d, "spots")
    assert "(813, -1220)" in t and "clear radius 64 m" in t and "walk_time_min=22" in t
    assert "get_set" in t


def test_format_num():
    assert fmt.num(-2493.2) == "-2493"
    assert fmt.num(59.298) == "59.3"
    assert fmt.num(0.0493) == "0.05"
    assert fmt.num(None) == "n/a"
