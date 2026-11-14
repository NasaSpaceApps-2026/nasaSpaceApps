"""Fixes from docs/tool-review.md (blind tool-choice review of the MCP server)."""
import importlib.util
from pathlib import Path

import anyio
import httpx
import pytest
from mcp.client import Client

from atlas_core import SetStore, resolve_ref
from atlas_core.errors import AtlasError
from atlas_core.testing import make_site
from devapi import create_app

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("atlas_server_review", ROOT / "mcp" / "server.py")
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


def run(coro_fn, site=None):
    app = create_app(site or make_site(), synthetic=True)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://devapi")
    server = srv.build_server(client=client)

    async def go():
        async with Client(server) as c:
            return await coro_fn(c)
    return anyio.run(go)


def text(r):
    return r.content[0].text


# ---- 1. set member refs ----

def test_resolve_ref_accepts_set_prefix_for_members():
    from atlas_core import search
    site, store = make_site(), SetStore()
    res = search.find_locations(site, store, profile="landing_pad")
    sid = res["set_id"]
    assert sid == "set:1" and res["count"] >= 1
    a = resolve_ref(site, "1.1", store)
    b = resolve_ref(site, "set:1.1", store)
    assert a == b and len(b) == 1
    assert len(resolve_ref(site, sid, store)) >= 1
    with pytest.raises(AtlasError):
        resolve_ref(site, "set:9.9", store)


def test_here_and_me_mean_rover():
    site = make_site()
    site.rover_xy = (10.0, 20.0)
    assert resolve_ref(site, "here")[0]["x"] == 10.0 and resolve_ref(site, "me")[0]["y"] == 20.0


def test_set_member_prefix_works_through_the_tool():
    async def f(c):
        s = await c.call_tool("find_locations", {"profile": "landing_pad", "limit": 2})
        d = await c.call_tool("describe_location", {"at": "set:1.1"})
        return s, d
    s, d = run(f)
    assert not s.is_error and not d.is_error, text(d)


def test_location_description_documents_member_form():
    async def f(c):
        return {t.name: t.description for t in (await c.list_tools()).tools}
    descs = run(f)
    for name in ("describe_location", "plan_route", "nearest", "goto_location", "teleport_rover", "fly_camera"):
        assert 'WITHOUT the prefix' in descs[name] and '"3.2"' in descs[name], name


# ---- 2. nearest argument names ----

def test_nearest_hint_uses_real_argument_names_and_accepts_aliases():
    async def f(c):
        pads = await c.call_tool("find_locations", {"profile": "landing_pad", "limit": 3})
        reg = await c.call_tool("find_regions", {"conditions": [{"layer": "slope_2m", "op": "<", "value": 5}]})
        a = await c.call_tool("nearest", {"from": "set:2", "to": "set:1"})
        b = await c.call_tool("nearest", {"from_ref": "set:2", "to_ref": "set:1"})
        return reg, a, b
    reg, a, b = run(f)
    assert "nearest(from=" not in text(reg) and "from_ref=" in text(reg)
    assert not a.is_error, text(a)
    assert text(a) == text(b) or not b.is_error


# ---- 3. validation errors ----

def test_wrong_type_argument_gets_error_try_format():
    async def f(c):
        return await c.call_tool("find_locations", {"conditions": {"layer": "slope_2m", "op": "<=", "value": 10}})
    r = run(f)
    t = text(r)
    assert r.is_error and "Error:" in t and "\nTry:" in t
    assert "\"conditions\"" in t and "Example: find_locations(" in t and "find_locations(find_locations" not in t
    assert "validation error" not in t.lower().split("try:")[0]


def test_unknown_and_missing_arguments_named():
    async def f(c):
        return (await c.call_tool("describe_layer", {"layer": "slope_2m"}),
                await c.call_tool("nearest", {"from_ref": "set:1"}))
    unknown, missing = run(f)
    assert unknown.is_error and "Error:" in text(unknown) and "layer" in text(unknown)
    assert "layer_id" in text(unknown) and "Example: describe_layer(" in text(unknown)
    assert missing.is_error and "to_ref" in text(missing) and "Try:" in text(missing)


# ---- 3b. schemas ----

def test_schemas_have_enums_and_item_schemas():
    async def f(c):
        return {t.name: t.inputSchema if hasattr(t, "inputSchema") else t.input_schema
                for t in (await c.list_tools()).tools}
    sch = run(f)
    p = sch["find_locations"]["properties"]
    assert p["conditions"]["type"] == "array" and "layer" in p["conditions"]["items"]["properties"]
    assert "any_of" in p["conditions"]["items"]["properties"]
    assert p["conditions"]["items"]["properties"]["min_tier"]["enum"][-1] == "confirmed_in_situ"
    assert set(p["within"]["required"]) == {"from", "max", "metric"}
    assert p["within"]["properties"]["metric"]["enum"] == ["straight_m", "walk_m", "walk_time_min"]
    assert "best_evidence" in p["objective"]["pattern"]
    assert sch["nearest"]["properties"]["metric"]["enum"]
    assert sch["set_view"]["properties"]["mode"]["enum"] == ["globe", "orbit", "chase", "ground"]
    assert sch["fly_camera"]["properties"]["mode"]["enum"]
    assert sch["plan_route"]["properties"]["profile"]["enum"] == ["rover_scout", "eva_walkable"]
    for name, prop in (("describe_location", "at"), ("plan_route", "goal"), ("goto_location", "target"),
                       ("nearest", "from_ref")):
        alts = sch[name]["properties"][prop]["anyOf"]
        kinds = [a.get("type") for a in alts]
        assert "object" in kinds and "string" in kinds, (name, prop)


# ---- 4. follow_route default ----

def test_follow_route_default_is_latest_planned_and_says_so():
    async def f(c):
        await c.call_tool("plan_route", {"goal": {"x": 40, "y": 0}, "start": {"x": 0, "y": 0}})
        await c.call_tool("teleport_rover", {"x": 0, "y": 0})
        await c.call_tool("fsd_enable", {"route_id": "route-1"})
        await c.call_tool("plan_route", {"goal": {"x": 0, "y": 40}, "start": {"x": 0, "y": 0}})
        return await c.call_tool("follow_route", {})
    r = run(f)
    assert not r.is_error, text(r)
    assert "route-2" in text(r) and "most recently planned" in text(r)
    assert r.structured_content["route_used"] == "route-2"


def test_follow_route_not_at_start_offers_teleport_with_coordinates():
    async def f(c):
        await c.call_tool("plan_route", {"goal": {"x": 40, "y": 0}, "start": {"x": 0, "y": 0}})
        await c.call_tool("teleport_rover", {"x": 50, "y": -50})
        return await c.call_tool("follow_route", {"route_id": "route-1"})
    r = run(f)
    assert r.is_error and "teleport_rover(x=0, y=0)" in text(r) and "goto_location(x=0, y=0)" in text(r)


# ---- 5. overview ----

def test_overview_states_no_search_targets_and_lists_pois():
    site = make_site()
    for k in ("carbonate", "geologic_unit"):
        del site.layers[k]

    async def f(c):
        return await c.call_tool("get_site_overview", {})
    t = text(run(f, site))
    assert "Search targets for find_sites: none (terrain-only site: no mineral or geology layers)" in t
    assert "poi:outcrop_a" in t and "[science_target]" in t and "poi:east_point" in t


def test_overview_with_targets_still_lists_them():
    t = text(run(lambda c: c.call_tool("get_site_overview", {})))
    assert "Search targets for find_sites: plains" in t and "carbonate (" in t


# ---- 6. empty-result hints ----

def test_empty_search_hint_includes_layer_range():
    async def f(c):
        return await c.call_tool("find_locations", {"conditions": [{"layer": "slope_2m", "op": "<=", "value": -1}],
                                                    "footprint_radius_m": 10})
    r = run(f)
    t = text(r)
    assert "Layer slope_2m on this site: range" in t and "median" in t


def test_empty_regions_hint_includes_layer_range():
    async def f(c):
        return await c.call_tool("find_regions", {"conditions": [{"layer": "slope_2m", "op": ">", "value": 500}]})
    assert "Layer slope_2m on this site: range" in text(run(f))


def test_search_pois_miss_lists_known_pois():
    async def f(c):
        return await c.call_tool("search_pois", {"query": "delta front"})
    t = text(run(f))
    assert "No POIs match." in t and "Known POIs: base, landing, outcrop_a, east_point" in t


# ---- 7. cross-tool guidance ----

def test_descriptions_have_use_this_when_lines():
    async def f(c):
        return {t.name: t.description for t in (await c.list_tools()).tools}
    d = run(f)
    assert "prefer describe_location" in d["sample_terrain"]
    assert "teleport_rover for 'take me to X'" in d["goto_location"] and "advances simulated time" in d["goto_location"]
    assert "use goto_location instead" in d["teleport_rover"]
    assert "use fly_camera instead" in d["set_view"] and "Use set_view instead" in d["fly_camera"]
    assert 'Do NOT use profile="landing_pad"' in d["find_regions"]
    assert "confirmed_in_situ" in d["find_sites"] and "get_site_overview lists Search targets" in d["find_sites"]
    assert "NAMED places" in d["search_pois"]
    assert "Always pass route_id" in d["follow_route"]
