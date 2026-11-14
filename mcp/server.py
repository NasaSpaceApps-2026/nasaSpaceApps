"""Ares Atlas MCP server: a stateless wrapper over the REST API (contracts/mcp-tools.md, scope doc).

Every tool is one HTTP call to the API (default http://localhost:8000; stand-in: `python -m devapi`), a compact
text summary plus the full JSON as structuredContent, and a fire-and-forget POST /events/agent so the viewer can
show what the agent is doing.

Run:  python mcp/server.py            (stdio)
      python mcp/server.py --http     (streamable HTTP on 127.0.0.1:8765, path /mcp)
Env:  ATLAS_API_URL=http://localhost:8000
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx  # noqa: E402
import numpy as np  # noqa: E402
from mcp_types import CallToolResult, TextContent, ToolAnnotations  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

import format as fmt  # noqa: E402  (mcp/format.py)

DEFAULT_API_URL = "http://localhost:8000"

# ---- shared description fragments -------------------------------------------------------------

UNITS = ("Units: distances in site-frame metres (x east, y north, origin at site centre); "
         "walking time in minutes; slope in degrees; area in m2; headings in degrees clockwise from north.")
LOCATION_REFS = (
    'Location references (`at`, `from`, `start`, `goal`, `target`): a point {"x": 120, "y": -340}; lat/lon '
    '{"lat": 18.44, "lon": 77.45}; a POI id "poi:<id>" (see get_site_overview or search_pois); a marker '
    '"marker:<id>" (see list_markers); the keywords "base" and "rover" (current rover position); a stored set '
    '"set:3" (every member) or one member "3.2"; or a list of these.')
CONDITIONS = (
    "Condition language (conditions are ANDed; use any_of for OR; unknown/no-data cells always FAIL): "
    '{"layer": "slope_2m", "op": "<=", "value": 10}; '
    '{"layer": "geologic_unit", "op": "in", "value": ["plains", "crater_floor"]}; '
    '{"evidence": "carbonate", "min_tier": "strong_orbital"}; '
    '{"any_of": [{"evidence": "carbonate", "min_tier": "weak_orbital"}, {"layer": "slope_2m", "op": "<", "value": 3}]}. '
    "Ops: < <= > >= between (continuous), == != in not_in (categorical). Evidence tiers ascending: "
    "not_detected, inferred_from_unit, weak_orbital, strong_orbital, confirmed_in_situ.")
WITHIN = (
    'within = {"from": <location ref>, "max": number, "metric": "straight_m"|"walk_m"|"walk_time_min", '
    '"profile": "eva_walkable"}, e.g. {"from": "base", "max": 60, "metric": "walk_time_min"} keeps only places '
    "reachable on foot from the base in 60 minutes (walk metrics use a placeholder, approximate speed model).")
PROFILES = ('profile: "rover_scout" (rover, 0.04 m/s real speed, no-go slope 20 deg) or "eva_walkable" '
            '(astronaut on foot, no-go slope 25 deg); exact limits are in get_site_overview / the result.')
DRIVE_NOTE = ("Blocking: returns when the rover stops, with the final state and a stop_reason: arrived, "
              "blocked_slope, blocked_hazard, replanned, stopped_by_user or timeout. animate=true plays the motion "
              "live in the viewer at 10 Hz (takes real seconds; speed_factor scales simulated speed, default shows "
              "about 8 m/s); the default computes the drive instantly.")

# ---- helpers ------------------------------------------------------------------------------------


def _clean(o: Any) -> Any:
    """Make a result JSON-safe: numpy -> python, NaN/inf -> None."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def _coerce(v: Any) -> Any:
    """Some clients send objects/lists as JSON strings; accept that."""
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "{[":
            try:
                return json.loads(s)
            except ValueError:
                return v
    return v


def _short(o: Any, n: int = 140) -> str:
    s = json.dumps(_clean(o), separators=(",", ":"), default=str) if not isinstance(o, str) else o
    return s if len(s) <= n else s[: n - 1] + "…"


def fetch_targets(api_url: str | None = None) -> list[str] | None:
    """Search targets from the running API (for the find_sites description). None if unreachable."""
    try:
        r = httpx.get((api_url or os.environ.get("ATLAS_API_URL", DEFAULT_API_URL)).rstrip("/") + "/search/targets",
                      timeout=2.0)
        return [t["target"] for t in r.json()["targets"]]
    except Exception:
        return None


def build_server(client: httpx.AsyncClient | None = None, api_url: str | None = None,
                 targets: list[str] | None = None, name: str = "ares-atlas") -> MCPServer:
    """`client` may be a pre-built httpx.AsyncClient (tests pass one bound to the app via ASGITransport)."""
    api_url = (api_url or os.environ.get("ATLAS_API_URL", DEFAULT_API_URL)).rstrip("/")
    if client is None:
        client = httpx.AsyncClient(base_url=api_url, timeout=httpx.Timeout(300.0, connect=3.0))
    state: dict[str, Any] = {"synthetic": None}
    pending: set[asyncio.Task] = set()

    class Unreachable(Exception):
        pass

    async def request(method: str, path: str, *, json_body=None, params=None, timeout=None):
        """-> (status, data). Raises Unreachable when the API cannot be contacted."""
        try:
            kw = {"timeout": timeout} if timeout is not None else {}
            r = await client.request(method, path, json=json_body, params=params, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError,
                httpx.NetworkError) as e:
            raise Unreachable(str(e)) from e
        try:
            data = r.json()
        except ValueError:
            data = {"error": f"The API returned a non-JSON response (HTTP {r.status_code}): {r.text[:150]}",
                    "hint": "Check the API logs."}
        return r.status_code, data

    async def is_synthetic() -> bool:
        if state["synthetic"] is None:
            try:
                st, d = await request("GET", "/health", timeout=3.0)
                if st == 200:
                    state["synthetic"] = bool(d.get("synthetic"))
            except Unreachable:
                return False
        return bool(state["synthetic"])

    async def ok(text: str, data: dict) -> CallToolResult:
        data = _clean(data)
        synthetic = await is_synthetic()
        if synthetic:
            data["synthetic"] = True
        return CallToolResult(content=[TextContent(type="text", text=fmt.with_banner(text, synthetic))],
                              structured_content=data)

    async def err(message: str, hint: str | None = None) -> CallToolResult:
        data = {"error": message, "hint": hint}
        synthetic = await is_synthetic()
        if synthetic:
            data["synthetic"] = True
        return CallToolResult(content=[TextContent(type="text", text=fmt.with_banner(fmt.fmt_error(message, hint), synthetic))],
                              structured_content=data, is_error=True)

    async def call(method: str, path: str, formatter, *, body=None, params=None, adapt=None) -> CallToolResult:
        """One API call -> formatted tool result. API errors become readable error results."""
        try:
            st, data = await request(method, path, json_body=body, params=params)
        except Unreachable:
            return await err(f"API not reachable at {api_url}; start it with `python -m devapi` "
                             "(or set ATLAS_API_URL to the running API).",
                             "Start the API, then retry the same call.")
        if st >= 400:
            if isinstance(data, dict) and "error" in data:
                return await err(str(data["error"]), data.get("hint"))
            return await err(f"The API answered HTTP {st}: {_short(data, 200)}", "Check the arguments and retry.")
        try:
            if adapt:
                data = adapt(data)
            return await ok(formatter(data), data)
        except (KeyError, TypeError, ValueError) as e:
            return await err(f"Unexpected API response shape ({type(e).__name__}: {str(e)[:120]}).",
                             "The API and MCP server versions may be out of sync.")

    # ---- agent.event (fire and forget) --------------------------------------------------------
    def emit(tool: str, args: dict, status: str, result_summary: str | None = None, is_error: bool = False):
        async def go():
            try:
                await request("POST", "/events/agent", timeout=2.0, json_body={
                    "tool": tool, "args_summary": _short({k: v for k, v in args.items() if v is not None}),
                    "result_summary": result_summary, "status": status, "is_error": is_error})
            except Exception:
                pass
        t = asyncio.ensure_future(go())
        pending.add(t)
        t.add_done_callback(pending.discard)

    mcp = MCPServer(
        name,
        instructions=("Ares Atlas answers spatial questions about the Jezero crater site on Mars and operates a "
                      "simulated rover on it. Start with get_site_overview. Search tools store results as sets "
                      "(set_id) and markers are named places; both can be used as locations in later tools. "
                      "Always answer from tool results: never estimate distances, slopes or times from memory. "
                      "Missing data is reported as unknown, never as safe or absent."),
    )
    ro = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
    act = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
    act_idem = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    def tool(name: str, description: str, annotations: ToolAnnotations = ro):
        def deco(fn):
            @functools.wraps(fn)
            async def wrapper(*a, **kw):
                emit(name, kw, "started")
                try:
                    res = await fn(*a, **kw)
                except Exception as e:  # never leak a stack trace to the model
                    res = await err(f"Unexpected tool error ({type(e).__name__}): {str(e)[:200]}",
                                    "Retry with simpler arguments; if it persists, report this message.")
                first = res.content[0].text.splitlines()
                summary = next((l for l in first if l and not l.startswith("[SYNTHETIC")), "")
                emit(name, kw, "error" if res.is_error else "done", _short(summary, 200), bool(res.is_error))
                if pending:
                    await asyncio.wait(list(pending), timeout=1.0)
                return res
            mcp.tool(name=name, annotations=annotations, description=description)(wrapper)
            return wrapper
        return deco

    # ---- overview / catalogue ----

    @tool("get_site_overview", (
        "FIRST CALL in every session. Returns the site extent and coordinate frame, all layers grouped by kind "
        "with what each measures, profile names (named bundles of conditions such as landing_pad or "
        "eva_walkable), key points of interest with coordinates, the rover position and mode, user and agent "
        "markers, the searchable targets for find_sites (minerals and geologic units with their source mission), "
        "and the evidence tier scale. Use it to learn valid layer ids, POI ids, marker ids and targets before "
        "calling other tools. " + UNITS + " Example: get_site_overview()"))
    async def get_site_overview() -> CallToolResult:
        return await call("GET", "/overview", fmt.fmt_overview)

    @mcp.resource("atlas://site/overview", name="site_overview", mime_type="text/plain",
                  description="Site extent, layers, profiles, POIs, markers and targets (same as get_site_overview).")
    async def overview_resource() -> str:
        st, d = await request("GET", "/overview")
        if st >= 400:
            return fmt.fmt_error(str(d.get("error", "API error")), d.get("hint"))
        return fmt.with_banner(fmt.fmt_overview(d), bool(d.get("synthetic")))

    @tool("list_layers", (
        "List data layers (id, kind, title, one-line measures, resolution). Kinds: continuous (numbers such as "
        "slope_2m), categorical (classes such as geologic_unit), evidence (ordinal tiers for detected things such "
        "as minerals). Filter by kind and/or a case-insensitive text query over id/title/measures. Use when you "
        "need a layer id; use describe_layer for limits and citations. "
        'Example: list_layers(kind="continuous", query="slope")'))
    async def list_layers(kind: str | None = None, query: str | None = None) -> CallToolResult:
        return await call("GET", "/layers", fmt.fmt_layers, params={k: v for k, v in (("kind", kind), ("query", query)) if v})

    @tool("describe_layer", (
        "Full catalogue entry for one layer: what it measures, what it does NOT measure, quality notes, classes or "
        "tier scale, coverage (share of cells with data), value range, resolution and the source citation. "
        "Call this BEFORE relying on an unfamiliar layer or quoting its numbers. Not for values at a place (use "
        'describe_location). Example: describe_layer(layer_id="slope_2m")'))
    async def describe_layer(layer_id: str) -> CallToolResult:
        return await call("GET", f"/layers/{layer_id}", fmt.fmt_layer)

    # ---- location description ----

    async def _describe(at: Any, radius_m: float = 0.0, layers: list[str] | None = None) -> CallToolResult:
        body = {"at": _coerce(at), "radius_m": radius_m, "layers": _coerce(layers)}
        return await call("POST", "/describe", fmt.fmt_describe, body={k: v for k, v in body.items() if v is not None})

    @tool("describe_location", (
        "Everything known at one place: value of every layer (or the chosen layers), data quality, and the nearest "
        "rover ground truth (confirmed in-situ cell or sample site) with its distance. radius_m=0 gives point "
        "values (elevation interpolated; classes and evidence tiers from the containing 2 m cell). radius_m>0 gives "
        "footprint statistics (mean/min/max, class shares, best evidence tier, unknown share): use it for pads, "
        "walking safety or outcrop checks, and read the MAX, not only the mean. 'unknown' means no data, never "
        "safe or absent. Accepts a set or list of up to 10 locations (compact table). Use for 'what is at X?'; "
        "use find_locations/find_regions/find_sites to search. " + LOCATION_REFS + " " + UNITS + " "
        'Example: describe_location(at="poi:base", radius_m=50, layers=["slope_2m","carbonate"])'))
    async def describe_location(at: Any, radius_m: float = 0.0, layers: list[str] | None = None) -> CallToolResult:
        return await _describe(at, radius_m, layers)

    def _adapt_sample(d: dict) -> dict:
        loc = {"id": "point", "x": d["x"], "y": d["y"], "lat": d["lat"], "lon": d["lon"], "values": d["layers"],
               "quality": d["quality"], "nearest_ground_truth": d["nearest_ground_truth"]}
        return {"radius_m": 0.0, "locations": [loc], "hazard_class": d["hazard_class"],
                "hazard_profile": d["hazard_profile"], "no_go_slope_deg": d["no_go_slope_deg"],
                "caution_slope_deg": d["caution_slope_deg"]}

    def _fmt_sample(d: dict) -> str:
        return fmt.fmt_describe(d).replace(
            "Unknown means", f"Hazard class for {d['hazard_profile']}: {d['hazard_class']} (caution above "
            f"{fmt.num(d['caution_slope_deg'])} deg, no-go above {fmt.num(d['no_go_slope_deg'])} deg).\nUnknown means", 1)

    @tool("sample_terrain", (
        "Point values of every layer at an {x, y} site-frame point (metres), plus the hazard class for the rover "
        "(safe, caution, no-go or unknown, from slope). Same data as describe_location(at, radius_m=0). Use for a "
        "quick 'is this ground drivable?' check. " + UNITS + " Example: sample_terrain(x=120, y=-340)"))
    async def sample_terrain(x: float, y: float) -> CallToolResult:
        return await call("GET", "/terrain/sample", _fmt_sample, params={"x": x, "y": y}, adapt=_adapt_sample)

    @tool("get_elevation_profile", (
        "Elevation and slope along a polyline: total distance, min/max elevation, ascent, descent, mean and max "
        "slope, plus a short table of samples. Use to understand a path's relief before or after plan_route, or "
        "to check a straight line between two places. Points are location references (at least 2). Slope is per "
        "2 m cell, so rocks smaller than about 3 m are not resolved. " + LOCATION_REFS + " " + UNITS + " "
        'Example: get_elevation_profile(points=[{"x": 0, "y": 0}, "poi:landing", {"x": 150, "y": -80}])'))
    async def get_elevation_profile(points: Any, spacing_m: float | None = None) -> CallToolResult:
        body = {"points": _coerce(points), "spacing_m": spacing_m}
        return await call("POST", "/terrain/profile", fmt.fmt_profile, body={k: v for k, v in body.items() if v is not None})

    @tool("search_pois", (
        "Find named points of interest (landing sites, sample sites, science targets, landmarks, hazards) by text "
        "query, type and/or distance from a place. Returns id (usable as a location, e.g. poi:<id>), coordinates, "
        "type, science value (0 to 1, null if not recorded) and distance. Use to resolve a name such as 'the delta "
        "front' to coordinates; use find_sites to search for a mineral or geologic unit instead. near accepts "
        "{x, y}, a POI id, a marker or 'rover'. radius_m needs near. " + UNITS + " "
        'Example: search_pois(query="delta", type="science_target", near="rover", radius_m=500, limit=5)'))
    async def search_pois(query: str | None = None, type: str | None = None, near: Any = None,
                          radius_m: float | None = None, limit: int = 10) -> CallToolResult:
        near = _coerce(near)
        if isinstance(near, dict) and "x" in near and "y" in near:
            near = f"{near['x']},{near['y']}"
        elif near is not None and not isinstance(near, str):
            return await err("near must be {x, y}, a POI id, a marker id or 'rover'.")
        params = {"q": query, "type": type, "near": near, "radius": radius_m, "limit": limit}
        return await call("GET", "/pois", fmt.fmt_pois, params={k: v for k, v in params.items() if v is not None})

    # ---- search ----

    @tool("find_locations", (
        "Find footprint-sized SPOTS (e.g. a landing pad or camp) where every cell within footprint_radius_m "
        "satisfies ALL conditions, exhaustively over the whole grid (the best answer is never missed). Results are "
        "spread apart (spacing_m), ranked by objective, and stored as a set (set_id) for describe_location, "
        "get_set and nearest. Use find_regions instead for connected areas such as outcrops or a geologic unit, "
        "and find_sites for ranked mineral or unit targets with evidence. "
        "An empty result includes per-condition pass rates showing which condition to relax. "
        "profile = a named bundle of conditions and footprint (see get_site_overview); explicit conditions "
        "override it; thresholds are reported as cited or assumed. objective: flattest (default), "
        "largest_clear_area, closest (needs within), highest, lowest, best_evidence:<layer>. limit 1-50 (default 5). "
        + CONDITIONS + " " + WITHIN + " " + UNITS + " "
        'Example: find_locations(profile="landing_pad", footprint_radius_m=50, '
        'within={"from": "base", "max": 60, "metric": "walk_time_min"}, objective="flattest", limit=3)'))
    async def find_locations(conditions: list[dict] | None = None, profile: str | None = None,
                             footprint_radius_m: float | None = None, within: dict | None = None,
                             objective: str = "flattest", spacing_m: float | None = None,
                             limit: int = 5) -> CallToolResult:
        body = {"conditions": _coerce(conditions), "profile": profile, "footprint_radius_m": footprint_radius_m,
                "within": _coerce(within), "objective": objective, "spacing_m": spacing_m, "limit": limit}
        return await call("POST", "/search/locations", lambda r: fmt.fmt_search(r, "spots"),
                          body={k: v for k, v in body.items() if v is not None})

    @tool("find_regions", (
        "Find connected REGIONS (8-connected areas of cells satisfying ALL conditions) of at least min_area_m2, "
        "e.g. carbonate outcrops, a geologic unit, or flat terrain patches. Each region reports area, a "
        "representative interior point, bounding box and statistics. Stored as a set (set_id). Use "
        "find_locations instead for footprint-sized spots, and find_sites when you want the best places for a "
        "mineral or unit ranked with evidence and reachability. Empty results include per-condition pass rates. "
        "objective: largest (default), closest (needs within), flattest, best_evidence:<layer>. limit 1-50 "
        "(default 5). " + CONDITIONS + " " + WITHIN + " " + UNITS + " "
        'Example: find_regions(conditions=[{"evidence": "carbonate", "min_tier": "weak_orbital"}], '
        'min_area_m2=400, objective="largest")'))
    async def find_regions(conditions: list[dict] | None = None, profile: str | None = None,
                           min_area_m2: float = 400.0, within: dict | None = None,
                           objective: str = "largest", limit: int = 5) -> CallToolResult:
        body = {"conditions": _coerce(conditions), "profile": profile, "min_area_m2": min_area_m2,
                "within": _coerce(within), "objective": objective, "limit": limit}
        return await call("POST", "/search/regions", lambda r: fmt.fmt_search(r, "regions"),
                          body={k: v for k, v in body.items() if v is not None})

    targets_txt = (" Valid targets right now: " + ", ".join(targets) + "." if targets else
                   " Valid targets are listed in get_site_overview under 'Search targets'.")

    @tool("find_sites", (
        "Best places to find a MINERAL or GEOLOGIC UNIT (a target), ranked, with the evidence behind each: for "
        "minerals the best evidence tier (confirmed_in_situ > strong_orbital > weak_orbital) from orbital "
        "instruments such as CRISM, the area, mean slope, distance from `near` and whether the rover can reach it "
        "(slope-limited walk). Evidence tiers are detections, NOT abundance: say so and cite the layer and mission. "
        "Use for 'where are the best places to find carbonates?'. Use find_regions with custom conditions, find_locations "
        "for flat pads, search_pois for named places. near defaults to the rover; radius_m is a straight-line limit "
        "from near; max_slope_deg drops steeper ground; min_tier (default weak_orbital) is the weakest evidence "
        "accepted for minerals. Stored as a set; site ids work as locations (e.g. plan_route(goal=\"2.1\"))."
        + targets_txt + " " + UNITS + " "
        'Example: find_sites(target="carbonate", near="base", radius_m=2000, max_slope_deg=20, top_k=5)'))
    async def find_sites(target: str, near: Any = None, radius_m: float | None = None,
                         max_slope_deg: float | None = None, top_k: int = 5, min_tier: str | None = None) -> CallToolResult:
        body = {"target": target, "near": _coerce(near), "radius_m": radius_m, "max_slope_deg": max_slope_deg,
                "top_k": top_k, "min_tier": min_tier}
        return await call("POST", "/search/sites", fmt.fmt_sites, body={k: v for k, v in body.items() if v is not None})

    @tool("nearest", (
        "For each member of `from_ref`, find the nearest member of `to_ref` by metric, and rank the pairs by cost. "
        "Typical use: nearest landing pad (a find_locations set) to each carbonate outcrop (a find_regions set). "
        "Both arguments take any location reference, usually a set id. Walk metrics are approximate (isotropic "
        "placeholder speed model; uphill = downhill) and unreachable members are listed. Returns a new set of "
        "pairs. metric: walk_time_min (default, minutes), walk_m, straight_m (metres); profile: eva_walkable "
        "(default) sets speed and maximum slope. limit 1+ (default 5). For the actual path use plan_route. "
        + LOCATION_REFS + " "
        'Example: nearest(from_ref="set:2", to_ref="set:1", metric="walk_time_min", limit=5)'))
    async def nearest(from_ref: Any, to_ref: Any, metric: str = "walk_time_min",
                      profile: str = "eva_walkable", limit: int = 5) -> CallToolResult:
        body = {"from": _coerce(from_ref), "to": _coerce(to_ref), "metric": metric, "profile": profile, "limit": limit}
        return await call("POST", "/search/nearest", fmt.fmt_nearest, body=body)

    @tool("get_set", (
        "Page through a stored location set (from find_locations, find_regions, find_sites or nearest) or re-sort "
        "it by any member or attribute field (for example score, clear_radius_m, area_m2, slope_2m_max, cost, "
        "walk_time_min). Use when a search stored more members than its text result showed. "
        'Example: get_set(set_id="set:1", offset=5, limit=10, sort="slope_2m_max")'))
    async def get_set(set_id: str, offset: int = 0, limit: int = 10, sort: str | None = None,
                      descending: bool = False) -> CallToolResult:
        params = {"offset": offset, "limit": limit, "sort": sort, "descending": descending}
        return await call("GET", f"/sets/{set_id}", fmt.fmt_set, params={k: v for k, v in params.items() if v is not None})

    # ---- routes ----

    @tool("plan_route", (
        "Plan the least-cost drivable/walkable route between two places over the real terrain and return a route_id "
        "(also drawn in the viewer). Reports distance, travel time, ascent, max slope, caution cells, consumables "
        "(time used vs the EVA limit and reserve) and a go/no-go assessment. If the goal is on ground above the slope "
        "limit, has no data, or is cut off, it returns feasible=false with the reason and the nearest reachable "
        "point: then offer that point or a looser limit, never claim a route exists. Use this BEFORE moving the "
        "rover or recommending a path; use assess_route to re-check a stored route; use nearest only for ranking "
        "many pairs. via are waypoints visited in order. max_slope_deg tightens (or loosens) the profile's slope "
        "limit (e.g. 15 for 'under 15 degrees'); time_budget_min flags routes that take longer. start defaults to "
        "the rover. " + PROFILES + " Travel times use a placeholder isotropic speed curve (approximate). "
        + LOCATION_REFS + " " + UNITS + " "
        'Example: plan_route(start="marker:m1", goal="marker:m2", via=["poi:landing"], profile="rover_scout", '
        "max_slope_deg=15)"))
    async def plan_route(goal: Any, start: Any = "rover", via: list | None = None, profile: str = "rover_scout",
                         max_slope_deg: float | None = None, time_budget_min: float | None = None) -> CallToolResult:
        body = {"start": _coerce(start), "goal": _coerce(goal), "via": _coerce(via), "profile": profile,
                "max_slope_deg": max_slope_deg, "time_budget_min": time_budget_min}
        return await call("POST", "/route", fmt.fmt_route, body={k: v for k, v in body.items() if v is not None})

    @tool("assess_route", (
        "Go/no-go for a stored route (by route_id from plan_route or plan_science_traverse): distance, time, max "
        "slope against the no-go and caution limits, and the consumables margin above the reserve, with reasons. "
        "Call it after plan_route and BEFORE telling the user a route is safe; never call a route safe when it is "
        "NO-GO or the margin is below the reserve. Example: assess_route(route_id=\"route-3\")"))
    async def assess_route(route_id: str) -> CallToolResult:
        return await call("GET", f"/route/{route_id}", fmt.fmt_assess)

    @tool("plan_science_traverse", (
        "Plan a time-boxed science walk or drive: pick and order the highest science-value stops within "
        "time_budget_min (greedy insertion: best value per added minute; 15 min per stop by default; returns to the "
        "start unless return_to_start=false) and return the joined route with consumables. interests narrows the "
        "stops by text (name, type, description), evidence layers (e.g. carbonate) or unit names. POIs without a "
        "recorded science value count as 0.5 and are marked as assumed. Use for 'plan a 4-hour Marswalk to the best "
        "science targets'; use plan_route when the destination is already known; follow with assess_route. "
        + PROFILES + " (default eva_walkable). " + LOCATION_REFS + " " + UNITS + " "
        'Example: plan_science_traverse(time_budget_min=240, interests=["carbonate"], max_slope_deg=15)'))
    async def plan_science_traverse(time_budget_min: float, start: Any = "rover", interests: list[str] | None = None,
                                    profile: str = "eva_walkable", max_slope_deg: float | None = None,
                                    stop_minutes: float = 15.0, max_stops: int | None = None,
                                    return_to_start: bool = True) -> CallToolResult:
        body = {"start": _coerce(start), "time_budget_min": time_budget_min, "interests": _coerce(interests),
                "profile": profile, "max_slope_deg": max_slope_deg, "stop_minutes": stop_minutes,
                "max_stops": max_stops, "return_to_start": return_to_start}
        return await call("POST", "/route/science", fmt.fmt_science, body={k: v for k, v in body.items() if v is not None})

    # ---- rover ----

    @tool("get_rover_state", (
        "Current rover pose and status: position and elevation, heading, pitch and roll, mode (idle, manual, auto, "
        "fsd), speed, odometer, active route, status, last event, FSD armed. Use before planning from the rover or "
        "after any drive to confirm where it is. " + UNITS + " Example: get_rover_state()"))
    async def get_rover_state() -> CallToolResult:
        return await call("GET", "/rover/state", fmt.fmt_rover_state)

    async def command(body: dict, animate: bool, speed_factor: float | None) -> CallToolResult:
        if animate:
            body["realtime"] = True
        if speed_factor is not None:
            body["speed_factor"] = speed_factor
        return await call("POST", "/rover/command", fmt.fmt_command, body=body)

    @tool("drive_rover", (
        "Drive the rover in a straight line for distance_m along heading_deg (degrees clockwise from north: 0 north, "
        "90 east; default current heading). It stops on slopes above the no-go limit (stop_reason blocked_slope) or "
        "unknown ground (blocked_hazard) and tells you why. Use for short manual-style moves ('drive 200 m "
        "north-west' = heading 315); for a destination use goto_location, and for a planned route follow_route. "
        + DRIVE_NOTE + " " + UNITS + " Example: drive_rover(distance_m=200, heading_deg=315)"), act)
    async def drive_rover(distance_m: float, heading_deg: float | None = None, animate: bool = False,
                          speed_factor: float | None = None) -> CallToolResult:
        body = {"cmd": "drive", "distance_m": distance_m, "heading_deg": heading_deg}
        return await command({k: v for k, v in body.items() if v is not None}, animate, speed_factor)

    @tool("goto_location", (
        "Plan a route from the rover to a place and drive it (one call). target is any location reference (POI, "
        "marker, point); or pass x and y. If the place is unreachable it does not move and returns the reason and "
        "the nearest reachable point. Use for 'drive to the landing site'; use plan_route + assess_route + "
        "follow_route when the user wants to review the route first; use teleport_rover to jump instantly. "
        + DRIVE_NOTE + " " + LOCATION_REFS + " " + UNITS + " Example: goto_location(target=\"poi:landing\")"), act)
    async def goto_location(target: Any = None, x: float | None = None, y: float | None = None,
                            animate: bool = False, speed_factor: float | None = None) -> CallToolResult:
        body: dict = {"cmd": "goto"}
        if target is not None:
            body["target"] = _coerce(target)
        elif x is not None and y is not None:
            body.update(x=x, y=y)
        else:
            return await err("goto_location needs a target, or x and y.",
                             'Example: goto_location(target="poi:landing") or goto_location(x=50, y=-50).')
        return await command(body, animate, speed_factor)

    @tool("follow_route", (
        "Drive the rover along a stored route (route_id from plan_route). The rover must be within 3 m of the route "
        "start (plan with start=\"rover\" or goto/teleport first). With FSD armed (fsd_enable) caution slopes slow "
        "it and a blocked route is replanned once (stop_reason replanned, a new route_id is returned and the rover "
        "waits for you to call follow_route again); without FSD it stops on a no-go slope (blocked_slope) and "
        "reports the hazard. Use for 'scout that route'. Omit route_id to use the route armed with fsd_enable. "
        + DRIVE_NOTE + " " + UNITS + " Example: follow_route(route_id=\"route-3\", animate=true, speed_factor=200)"), act)
    async def follow_route(route_id: str | None = None, animate: bool = False,
                           speed_factor: float | None = None) -> CallToolResult:
        body = {"cmd": "follow_route"}
        if route_id:
            body["route_id"] = route_id
        return await command(body, animate, speed_factor)

    async def fsd_cmd(body: dict) -> CallToolResult:
        return await call("POST", "/rover/command", fmt.fmt_fsd_change, body=body)

    @tool("fsd_enable", (
        "Arm FSD (full self-driving) mode: the rover follows routes with the slope policy (halved speed on caution "
        "slopes, stop at no-go slopes or unknown ground, one automatic replan with a 10% lower slope limit when "
        "blocked). Optionally bind a route_id so follow_route needs no argument. policy keys (booleans): "
        "replan_on_block, caution_slowdown. Arming does not move the rover. Use before follow_route when scouting; "
        "fsd_status shows blockers; fsd_disable disarms. "
        "Example: fsd_enable(route_id=\"route-3\", policy={\"replan_on_block\": true})"), act_idem)
    async def fsd_enable(route_id: str | None = None, policy: dict | None = None) -> CallToolResult:
        body: dict = {"cmd": "fsd_enable"}
        if route_id:
            body["route_id"] = route_id
        if policy:
            body["policy"] = _coerce(policy)
        return await fsd_cmd(body)

    @tool("fsd_disable", ("Disarm FSD mode (the rover keeps its pose; follow_route still works but without the "
                          "caution slowdown or automatic replan). Example: fsd_disable()"), act_idem)
    async def fsd_disable() -> CallToolResult:
        return await fsd_cmd({"cmd": "fsd_disable"})

    @tool("fsd_status", (
        "FSD state: armed or not, policy, replans used, active route and metres remaining, the last hazard (what "
        "stopped the rover and where) and current blockers. Use after a blocked or replanned stop to report the hazard "
        "to the user, or before follow_route. Example: fsd_status()"))
    async def fsd_status() -> CallToolResult:
        return await call("GET", "/fsd/status", fmt.fmt_fsd)

    @tool("stop_rover", ("Stop the rover now (cancels the active drive; any blocking drive call returns "
                         "stopped_by_user). Returns the state. Example: stop_rover()"), act_idem)
    async def stop_rover() -> CallToolResult:
        return await call("POST", "/rover/command", fmt.fmt_command, body={"cmd": "stop"})

    @tool("teleport_rover", (
        "Place the rover at a location instantly (no driving, no time cost), optionally with a heading. Use for "
        "'take me to the delta front' together with fly_camera, after search_pois or find_sites gives a place. "
        "Refuses ground above the no-go slope or without data and names the nearest walkable point. target is any "
        "location reference or give x and y. " + LOCATION_REFS + " " + UNITS + " "
        'Example: teleport_rover(target="poi:landing", heading_deg=90)'), act)
    async def teleport_rover(target: Any = None, x: float | None = None, y: float | None = None,
                             heading_deg: float | None = None) -> CallToolResult:
        body: dict = {"cmd": "teleport"}
        if target is not None:
            body["target"] = _coerce(target)
        elif x is not None and y is not None:
            body.update(x=x, y=y)
        else:
            return await err("teleport_rover needs a target, or x and y.",
                             'Example: teleport_rover(target="poi:landing") or teleport_rover(x=50, y=-50).')
        if heading_deg is not None:
            body["heading_deg"] = heading_deg
        return await call("POST", "/rover/command", fmt.fmt_command, body=body)

    # ---- view and markers ----

    @tool("set_view", (
        "Control the viewer: camera mode (globe, orbit, chase, ground), the camera target (any location), layer "
        "visibility ({layer_id: true|false}, ids from list_layers) and highlights (POIs, markers, set members or "
        "whole sets such as a find_sites result). Use after searches to show results ('highlights=[\"set:2\"]'). "
        "Use fly_camera for a plain 'show me X'. The reply says whether a viewer is connected. "
        "Example: set_view(mode=\"orbit\", target=\"poi:landing\", layers={\"carbonate\": true}, highlights=[\"set:2\"])"), act)
    async def set_view(mode: str | None = None, target: Any = None, layers: Any = None,
                       highlights: Any = None) -> CallToolResult:
        body = {"mode": mode, "target": _coerce(target), "layers": _coerce(layers), "highlights": _coerce(highlights)}
        return await call("POST", "/view", fmt.fmt_view, body={k: v for k, v in body.items() if v is not None})

    @tool("fly_camera", (
        "Fly the viewer camera to a place: a point, POI, marker or search result. mode: orbit (default), chase, "
        "ground, globe; distance_m is the camera distance. Use for 'show me the delta front' (with teleport_rover "
        "if the rover should go too). Does not move the rover. " + LOCATION_REFS + " "
        'Example: fly_camera(target="poi:landing", mode="orbit", distance_m=300)'), act)
    async def fly_camera(target: Any, mode: str = "orbit", distance_m: float | None = None) -> CallToolResult:
        body = {"target": _coerce(target), "mode": mode, "distance_m": distance_m}
        return await call("POST", "/view", fmt.fmt_view, body={k: v for k, v in body.items() if v is not None})

    @tool("list_markers", (
        "List all markers (places the user clicked or the agent placed) with id, coordinates, elevation and label. "
        "Use first when the user says 'my marker', 'these two markers' or 'here'. Marker ids work as locations: "
        "\"marker:m1\". Example: list_markers()"))
    async def list_markers() -> CallToolResult:
        return await call("GET", "/markers", fmt.fmt_markers)

    @tool("place_marker", (
        "Drop a labelled marker the user can see in the viewer (created_by agent), at x and y or at any location "
        "reference. Use to flag a candidate site or waypoint for later tools and for the user. "
        + UNITS + " Example: place_marker(x=120, y=-340, label=\"carbonate candidate\")"), act)
    async def place_marker(x: float | None = None, y: float | None = None, label: str | None = None,
                           at: Any = None) -> CallToolResult:
        body = {"x": x, "y": y, "at": _coerce(at), "label": label, "created_by": "agent"}
        return await call("POST", "/markers", fmt.fmt_marker, body={k: v for k, v in body.items() if v is not None})

    return mcp


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ares Atlas MCP server (wrapper over the REST API)")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--api-url", default=None, help=f"REST API base URL (default $ATLAS_API_URL or {DEFAULT_API_URL})")
    args = ap.parse_args(argv)
    server = build_server(api_url=args.api_url, targets=fetch_targets(args.api_url))
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
