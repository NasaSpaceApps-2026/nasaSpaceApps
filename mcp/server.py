"""Ares Atlas MCP server: a stateless wrapper over the REST API (contracts/mcp-tools.md, scope doc).

Every tool is one HTTP call to the API (default http://localhost:8000; stand-in: `python -m devapi`), a compact
text summary plus the full JSON as structuredContent, and a fire-and-forget POST /events/agent so the viewer can
show what the agent is doing.

Run:  python mcp/server.py            (stdio)
      python mcp/server.py --http     (streamable HTTP on 127.0.0.1:8765, path /mcp)
Env:  ATLAS_API_URL=http://localhost:8000        query engine (layers, describe, search, sets, sun, lake...)
      ATLAS_ROVER_API_URL=http://localhost:8000  optional: Yousef's rover API; when set, rover state/commands, routes,
                                                 markers, view, find_sites, pois and terrain go there (mcp/backends.py)
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
from pydantic import ValidationError  # noqa: E402
import numpy as np  # noqa: E402
from mcp_types import CallToolResult, TextContent, ToolAnnotations  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

import format as fmt  # noqa: E402  (mcp/format.py)
import backends  # noqa: E402  (mcp/backends.py: rover API / query engine split)
import place_resolution  # noqa: E402  (mcp/place_resolution.py: 'the crater' -> coordinates)

DEFAULT_API_URL = "http://localhost:8000"

# ---- shared description fragments -------------------------------------------------------------

UNITS = ("Units: distances in site-frame metres (x east, y north, origin at site centre); "
         "walking time in minutes; slope in degrees; area in m2; headings in degrees clockwise from north.")
LOCATION_REFS = (
    'Location reference (`at`, `near`, `start`, `goal`, `target`, `points`, `highlights`, `from_ref`, `to_ref`): '
    'an object {"x": 120, "y": -340} (site metres) OR an object {"lat": 18.44, "lon": 77.45} OR a string: '
    '"poi:<id>" (see get_site_overview or search_pois), "marker:<id>" (see list_markers), "base", "rover" '
    '("here" and "me" mean "rover"), a stored set "set:3" (every member) or ONE member of a set written '
    'WITHOUT the prefix, e.g. "3.2" ("set:3.2" is also accepted); or a list of these.')
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
    '"profile": "eva_walkable"|"rover_scout"} (from, max and metric are all required), e.g. {"from": "base", "max": 60, "metric": "walk_time_min"} keeps only places '
    "reachable on foot from the base in 60 minutes (walk metrics use a placeholder, approximate speed model).")
PROFILES = ('profile: "rover_scout" (scout rover), "crew_buggy" (the crewed buggy), "crew_rover", or "eva_walkable" '
            '(astronaut on foot); speeds and no-go slopes come from the API (select_vehicle lists them, the result '
            'prints the ones used, cited or assumed). For a route a vehicle will drive, omit profile: it uses the '
            'ACTIVE vehicle (select_vehicle).')
DRIVE_NOTE = ("NON-BLOCKING by default: the drive starts at once and plays LIVE on the viewer screen (about 8 to 60 "
              "seconds for a typical trip, speed chosen from the route length; speed_factor overrides it), and the tool "
              "returns immediately with 'Driving now ...' and the route summary. Check progress with get_drive_progress "
              "or get_rover_state; stop_rover cancels. Pass wait=true only when you need the final stop_reason in the "
              "same call (arrived, blocked_slope, blocked_hazard, replanned, stopped_by_user or timeout); animate=false "
              "(with wait=true) computes the drive instantly with no on-screen motion. Driving advances simulated time "
              "and the odometer.")


# ---- JSON schemas (patched into the tools' input schemas; runtime validation stays lenient) -----

_NUM = {"type": "number"}
_PT_XY = {"type": "object", "properties": {"x": _NUM, "y": _NUM}, "required": ["x", "y"],
          "description": "site-frame metres"}
_PT_LL = {"type": "object", "properties": {"lat": _NUM, "lon": _NUM}, "required": ["lat", "lon"],
          "description": "planetocentric degrees, east longitude"}
_REF_ONE = [_PT_XY, _PT_LL, {"type": "string", "description": 'e.g. "poi:base", "marker:m1", "rover", "set:3", "3.2"'}]


def _loc(description: str, many: bool = False) -> dict:
    alts = list(_REF_ONE)
    if many:
        alts.append({"type": "array", "items": {"anyOf": list(_REF_ONE)}})
    return {"anyOf": alts, "description": description}


_LOC_NOTE = ' Location reference: {x,y} | {lat,lon} | "poi:<id>" | "marker:<id>" | "base" | "rover" | "set:N" | "N.K".'
TIERS = ["not_detected", "inferred_from_unit", "weak_orbital", "strong_orbital", "confirmed_in_situ"]
METRICS = ["straight_m", "walk_m", "walk_time_min"]
PROFILES_ENUM = ["rover_scout", "crew_buggy", "crew_rover", "eva_walkable"]
VIEW_MODES = ["globe", "planet", "orbit", "chase", "ground"]   # "planet" = "globe" (the viewer's name for it)
CONDITION_SCHEMA = {
    "type": "object",
    "description": ('One condition: {"layer": id, "op": ..., "value": ...} or {"evidence": id, "min_tier": tier} or '
                    '{"any_of": [conditions]}. Example: {"layer": "slope_2m", "op": "<=", "value": 10}'),
    "properties": {
        "layer": {"type": "string"},
        "op": {"enum": ["<", "<=", ">", ">=", "between", "==", "!=", "in", "not_in"]},
        "value": {"description": "number, [lo, hi] for between, class name, or list of class names for in/not_in"},
        "evidence": {"type": "string", "description": "evidence layer id, e.g. carbonate"},
        "min_tier": {"enum": TIERS},
        "any_of": {"type": "array", "items": {"type": "object"}, "description": "OR of conditions"},
    },
}
CONDITIONS_SCHEMA = {"type": "array", "items": CONDITION_SCHEMA, "description": "ANDed conditions (a LIST of objects)"}
WITHIN_SCHEMA = {
    "type": "object", "required": ["from", "max", "metric"],
    "description": 'Reachability limit, e.g. {"from": "base", "max": 60, "metric": "walk_time_min"}',
    "properties": {
        "from": _loc("where the distance is measured from"),
        "max": {"type": "number", "description": "limit in the unit of metric (m or minutes)"},
        "metric": {"enum": METRICS},
        "profile": {"enum": PROFILES_ENUM, "description": "speed/slope model for walk metrics"},
    },
}
_BEST_EV = r"best_evidence:[A-Za-z0-9_]+"

SCHEMA_PATCH: dict[str, dict[str, dict]] = {
    "list_layers": {"kind": {"enum": ["continuous", "categorical", "evidence"]}},
    "describe_location": {"at": _loc("place(s) to describe (list of up to 10 allowed)", many=True)},
    "get_elevation_profile": {"points": {"type": "array", "minItems": 2, "items": {"anyOf": list(_REF_ONE)},
                                         "description": "at least 2 location references"}},
    "search_pois": {"near": _loc("centre for radius_m / distance ranking")},
    "find_locations": {
        "conditions": CONDITIONS_SCHEMA, "within": WITHIN_SCHEMA,
        "objective": {"type": "string", "pattern": r"^(flattest|largest_clear_area|closest|highest|lowest|" + _BEST_EV + r")$",
                      "description": "flattest (default) | largest_clear_area | closest (needs within) | highest | lowest | best_evidence:<layer>"}},
    "find_regions": {
        "conditions": CONDITIONS_SCHEMA, "within": WITHIN_SCHEMA,
        "objective": {"type": "string", "pattern": r"^(largest|closest|flattest|" + _BEST_EV + r")$",
                      "description": "largest (default) | closest (needs within) | flattest | best_evidence:<layer>"}},
    "find_sites": {"near": _loc("centre of the search (default: the rover)"), "min_tier": {"enum": TIERS}},
    "nearest": {"from_ref": _loc("members to start from (usually a set id)", many=True),
                "to_ref": _loc("members to look for (usually a set id)", many=True),
                "metric": {"enum": METRICS}, "profile": {"enum": PROFILES_ENUM}},
    "plan_route": {"goal": _loc("destination"), "start": _loc("start (default: the rover)"),
                   "via": {"type": "array", "items": {"anyOf": list(_REF_ONE)}, "description": "waypoints in order"},
                   "profile": {"enum": PROFILES_ENUM}},
    "plan_science_traverse": {"start": _loc("start (default: the rover)"), "profile": {"enum": PROFILES_ENUM}},
    "goto_location": {"target": _loc("destination")},
    "teleport_rover": {"target": _loc("destination")},
    "set_view": {"mode": {"enum": VIEW_MODES}, "target": _loc("camera target"),
                 "layers": {"anyOf": [{"type": "object", "additionalProperties": {"type": "boolean"}},
                                  {"type": "array", "items": {"type": "string"}}],
                            "description": '{layer_id: true|false}, e.g. {"carbonate": true}, or a list of ids to show'},
                 "highlights": {"anyOf": [{"type": "array", "items": {"anyOf": list(_REF_ONE)}}] + list(_REF_ONE),
                                "description": "POIs, markers, set members or whole sets (first 50 shown)"}},
    "fly_camera": {"target": _loc("place to look at"), "mode": {"enum": VIEW_MODES}},
    "place_marker": {"at": _loc("alternative to x and y")},
}
ALIASES = {"nearest": {"from": "from_ref", "to": "to_ref"}}

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


_COMPASS = {"n": 0, "north": 0, "ne": 45, "northeast": 45, "e": 90, "east": 90, "se": 135, "southeast": 135, "s": 180,
            "south": 180, "sw": 225, "southwest": 225, "w": 270, "west": 270, "nw": 315, "northwest": 315,
            "nne": 22.5, "ene": 67.5, "ese": 112.5, "sse": 157.5, "ssw": 202.5, "wsw": 247.5, "wnw": 292.5, "nnw": 337.5}


def _loose_move(distance, heading):
    """Accept '300 m', '0.3 km', 300 and 'north', 'north-west', 'NE', 315 -> (metres, degrees or None, error or None)."""
    import re
    d = distance
    if isinstance(d, str):
        m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*(km|kilomet\w*|m|met\w*)?\s*$", d.strip(), re.I)
        if not m:
            return None, None, f'I could not read the distance "{distance}".'
        d = float(m.group(1)) * (1000.0 if (m.group(2) or "").lower().startswith("k") else 1.0)
    if not isinstance(d, (int, float)) or isinstance(d, bool) or d <= 0:
        return None, None, "The distance must be a positive number of metres."
    h = heading
    if isinstance(h, str):
        key = re.sub(r"[^a-z]", "", h.lower())
        if key in _COMPASS:
            h = float(_COMPASS[key])
        else:
            try:
                h = float(h)
            except ValueError:
                return None, None, f'I could not read the direction "{heading}".'
    return float(d), (None if h is None else float(h)), None


def _short(o: Any, n: int = 140) -> str:
    s = json.dumps(_clean(o), separators=(",", ":"), default=str) if not isinstance(o, str) else o
    return s if len(s) <= n else s[: n - 1] + "…"


def fetch_targets(api_url: str | None = None, rover_url: str | None = None) -> list[str] | None:
    """Search targets from the running API (for the find_sites description). None if unreachable.
    find_sites runs on the rover backend when one is configured, so its targets are the valid ones."""
    base = rover_url or os.environ.get("ATLAS_ROVER_API_URL") or api_url or os.environ.get("ATLAS_API_URL", DEFAULT_API_URL)
    try:
        r = httpx.get(base.rstrip("/") + "/search/targets", timeout=2.0)
        return [t["target"] for t in r.json()["targets"] if t.get("available", True)]
    except Exception:
        return None


def build_server(client: httpx.AsyncClient | None = None, api_url: str | None = None,
                 targets: list[str] | None = None, name: str = "ares-atlas",
                 rover_client: httpx.AsyncClient | None = None, rover_url: str | None = None) -> MCPServer:
    """`client` may be a pre-built httpx.AsyncClient (tests pass one bound to the app via ASGITransport).
    rover_url / rover_client (env ATLAS_ROVER_API_URL) select Yousef's API for the rover side (mcp/backends.py)."""
    api_url = (api_url or os.environ.get("ATLAS_API_URL", DEFAULT_API_URL)).rstrip("/")
    if client is None:
        client = httpx.AsyncClient(base_url=api_url, timeout=httpx.Timeout(300.0, connect=3.0))
    rover_url = (rover_url or os.environ.get("ATLAS_ROVER_API_URL") or "").rstrip("/")
    if rover_client is None and rover_url:
        rover_client = httpx.AsyncClient(base_url=rover_url, timeout=httpx.Timeout(340.0, connect=3.0))
    bk = backends.Backends(client, rover_client, api_url, rover_url)
    state: dict[str, Any] = {"synthetic": None}
    drive_state: dict[str, Any] = {}      # the visible drive in progress (tools_drive / goto / follow_route fill it)
    drive_helpers: dict[str, Any] = {}    # functions tools_drive.register exposes to the rover tools below
    drive_helpers.update(about_dist=lambda m: __import__("tools_drive").about_dist(m),
                         about_secs=lambda x: __import__("tools_drive").about_secs(x))
    pending: set[asyncio.Task] = set()

    Unreachable = backends.Unreachable

    async def request(method: str, path: str, *, json_body=None, params=None, timeout=None):
        """-> (status, data). Raises Unreachable when a backend cannot be contacted. Routing and shape
        translation between Yousef's rover API and the query engine live in mcp/backends.py."""
        return await bk.request(method, path, json_body=json_body, params=params, timeout=timeout)

    async def is_synthetic() -> bool:
        if state["synthetic"] is None:
            try:
                st, d = await request("GET", "/health", timeout=3.0)
                if st == 200:
                    state["synthetic"] = bool(d.get("synthetic"))
                    if bk.split:    # his /health says "fixture": true for a synthetic data dir
                        rst, rd = await bk._raw("rover", "GET", "/health", timeout=3.0)
                        if rst == 200 and rd.get("fixture"):
                            state["synthetic"] = True
            except Unreachable:
                return False
        return bool(state["synthetic"])

    async def frame_banner() -> str | None:
        """Loud warning on every result while the two backends disagree about the site frame."""
        if not bk.split:
            return None
        fr = await bk.check_frame()
        return f"[{fr['message']}]" if fr and fr.get("message") else None

    state["frame_banner"] = frame_banner

    async def ok(text: str, data: dict) -> CallToolResult:
        data = _clean(data)
        synthetic = await is_synthetic()
        if synthetic:
            data["synthetic"] = True
        fb = await frame_banner()
        if fb:
            data["frame_warning"] = fb
        return CallToolResult(content=[TextContent(type="text", text=(fb + "\n" if fb else "") + fmt.with_banner(text, synthetic))],
                              structured_content=data)

    async def err(message: str, hint: str | None = None) -> CallToolResult:
        data = {"error": message, "hint": hint}
        synthetic = await is_synthetic()
        if synthetic:
            data["synthetic"] = True
        fb = await frame_banner()
        if fb:
            data["frame_warning"] = fb
        return CallToolResult(content=[TextContent(type="text", text=(fb + "\n" if fb else "") + fmt.with_banner(fmt.fmt_error(message, hint), synthetic))],
                              structured_content=data, is_error=True)

    async def call(method: str, path: str, formatter, *, body=None, params=None, adapt=None) -> CallToolResult:
        """One API call -> formatted tool result. API errors become readable error results."""
        try:
            st, data = await request(method, path, json_body=body, params=params)
        except Unreachable as u:
            if bk.split:
                return await err(f"The {u.which} backend is not reachable at {u.url}; "
                                 + ("start Yousef's API (see mcp/README.md) or fix ATLAS_ROVER_API_URL."
                                    if u.which == "rover" else "start `python -m devapi` or fix ATLAS_API_URL."),
                                 "Start that backend, then retry the same call.")
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
                summary = next((l for l in first if l and not l.startswith(("[SYNTHETIC", "[FRAME MISMATCH", "[EXTENT DIFFERS"))), "")
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
        "markers, the searchable targets for find_sites (minerals and geologic units with their source mission; the "
        "line says \"none (terrain-only site: no mineral or geology layers)\" when there are none, in which case "
        "mineral questions cannot be answered) and the evidence tier scale. Use it to learn valid layer ids, POI "
        "ids, marker ids and targets before calling other tools. " + UNITS + " Example: get_site_overview()"))
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
        "safe or absent. Accepts a set or list; more than 10 locations are cut to the first 10 with a note (page the rest with get_set). Evidence lines carry per-tier area with provenance (tier 4 = rover sample, named) and a registration caveat near unit contacts or tier changes. Use for 'what is at X?'; "
        "use find_locations/find_regions/find_sites to search. Use this (not sample_terrain) for any 'what is "
        "here?' question; radius_m=0 is a point lookup. " + LOCATION_REFS + " " + UNITS + " "
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
        "(safe, caution, no-go or unknown, from slope). Same layer values as describe_location(at, radius_m=0): "
        "prefer describe_location unless you specifically need the safe/caution/no-go hazard class for one {x, y} "
        "point ('is this ground drivable?'). " + UNITS + " Example: sample_terrain(x=120, y=-340)"))
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
        "type (sample sites also give region, in-situ minerals and the full description with its source for up to 3 hits), science value (0 to 1, null if not recorded) and distance. Use to resolve a name such as 'the delta "
        "front' to coordinates. Use this for NAMED places; use find_sites for a mineral or geologic unit, "
        "find_locations for flat pads, find_regions for areas matching conditions. near accepts {x, y} (or the "
        "string \"x,y\"), a POI id, a marker or 'rover'; radius_m needs near. Without near, every hit still gets its straight-line distance from the rover (\"from the rover at (x, y)\"). A miss lists the known POI ids. " + UNITS + " "
        'Example: search_pois(query="delta", type="science_target", near="rover", radius_m=500, limit=5)'))
    async def search_pois(query: str | None = None, type: str | None = None, near: Any = None,
                          radius_m: float | None = None, limit: int = 10) -> CallToolResult:
        near = _coerce(near)
        if isinstance(near, dict) and "x" in near and "y" in near:
            near = f"{near['x']},{near['y']}"
        elif near is not None and not isinstance(near, str):
            return await err("near must be {x, y}, a POI id, a marker id or 'rover'.")
        origin = None
        if near is None or near == "rover":
            # distances are always reported; without an explicit centre they are from the rover (when its pose is known)
            try:
                st, rs = await request("GET", "/rover/state")
            except Unreachable:
                st, rs = 599, None
            if st < 400 and isinstance(rs, dict) and rs.get("x") is not None and rs.get("y") is not None:
                origin = {"label": "the rover", "x": rs["x"], "y": rs["y"]}
                near = f"{rs['x']:.1f},{rs['y']:.1f}"
        elif isinstance(near, str):
            try:
                a, b = near.split(",")
                origin = {"label": "the given point", "x": float(a), "y": float(b)}
            except ValueError:
                pass
        params = {"q": query, "type": type, "near": near, "radius": radius_m, "limit": limit}
        res = await call("GET", "/pois", lambda d: fmt.fmt_pois(d, origin),
                         params={k: v for k, v in params.items() if v is not None})
        if query and not type and radius_m is None and not res.is_error:
            # the POI text match found nothing, or the words are generic ("the crater", "the hill"): use the gazetteer
            # (fuzzy names, aliases, detected craters, nearest-crater / high-ground / flat-ground terms)
            sc = res.structured_content if isinstance(res.structured_content, dict) else {}
            gz = await places_fetch(query)
            if gz and not gz.get("resolved") and sc.get("count") == 0:
                res.content[0].text += "\n" + (gz.get("explanation") or "")
                sc["resolution"] = gz
            elif gz and (sc.get("count") == 0 or gz.get("kind") in place_resolution.GENERIC_KINDS):
                items = ([gz["chosen"]] if gz.get("chosen") else []) + list(gz.get("candidates") or [])
                rows = []
                for it in items[:max(1, limit)]:
                    ref = it.get("ref") or f"{it['x']},{it['y']}"
                    rows.append(f"- {it.get('name')} ({ref})"
                                + (f", {fmt.dist_text(it['distance_m'])} {it.get('direction', '')} of the rover"
                                   if it.get("distance_m") is not None else "")
                                + (" [approximate: no exact position in our data]" if it.get("approximate") else ""))
                head = gz.get("explanation") or ""
                if not gz.get("resolved"):
                    head = f"No exact match for \"{query}\". " + head
                data = {"count": len(items), "pois": [{"id": it.get("id"), "ref": it.get("ref"), "name": it.get("name"),
                                                       "type": it.get("type") or it.get("kind"), "x": it.get("x"),
                                                       "y": it.get("y"), "distance_m": it.get("distance_m")}
                                                      for it in items], "resolution": gz,
                        "types": [], "known_ids": []}
                return await ok(head + ("\nOptions:\n" + "\n".join(rows) if rows else "") +
                                "\nNext: use the best match as the target of teleport_rover, goto_location or drive_to "
                                "(a phrase such as the one the user said works there too).", data)
        return res

    # ---- search ----

    @tool("find_locations", (
        "Find footprint-sized SPOTS (e.g. a landing pad or camp) where every cell within footprint_radius_m "
        "satisfies ALL conditions, exhaustively over the whole grid (the best answer is never missed). Results are "
        "spread apart (spacing_m), ranked by objective, and stored as a set (set_id) for describe_location, "
        "get_set and nearest. Use find_regions instead for connected areas such as outcrops or a geologic unit, "
        "and find_sites for ranked mineral or unit targets with evidence. "
        "An empty result includes per-condition pass rates and the real range of the failing layer, so you can pick a "
        "realistic threshold. "
        "profile = a named bundle of conditions and footprint (see get_site_overview); explicit conditions "
        "override it; thresholds are reported as cited or assumed. Use this when the answer is a SPOT of a given "
        "size; use find_regions for areas, find_sites for minerals/units, search_pois for named places. objective: flattest (default), "
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
        "mineral or unit ranked with evidence and reachability; search_pois for named places. Do NOT use "
        "profile=\"landing_pad\" here (that profile is for find_locations; with a pad profile this returns huge flat "
        "regions, not pads). Empty results include per-condition pass rates and the real range of the failing layer. "
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
        "Use for 'where are the best places to find carbonates?' and, with min_tier=\"confirmed_in_situ\", for 'where "
        "has the rover confirmed carbonate?'. Only works when get_site_overview lists Search targets (otherwise it "
        "errors: say the data cannot answer). Use find_regions instead for custom conditions or combined "
        "constraints, find_locations for flat pads, search_pois for named places. near defaults to the rover; radius_m is a straight-line limit "
        "from near; max_slope_deg drops steeper ground; min_tier (default weak_orbital) is the weakest evidence "
        "accepted for minerals. target also takes everyday words (aliases): 'clay' searches fe_mg_smectite, kaolinite and "
        "mg_smectite_2 together and the result says how it was resolved. Stored as a set; site ids work as locations (e.g. plan_route(goal=\"2.1\"))."
        + targets_txt + " " + UNITS + " "
        'Example: find_sites(target="carbonate", near="base", radius_m=2000, max_slope_deg=20, top_k=5)'))
    async def find_sites(target: str, near: Any = None, radius_m: float | None = None,
                         max_slope_deg: float | None = None, top_k: int = 5, min_tier: str | None = None) -> CallToolResult:
        body = {"target": target, "near": _coerce(near), "radius_m": radius_m, "max_slope_deg": max_slope_deg,
                "top_k": top_k, "min_tier": min_tier}
        return await call("POST", "/search/sites", fmt.fmt_sites, body={k: v for k, v in body.items() if v is not None})

    @tool("nearest", (
        "For each member of `from_ref` (alias `from`), find the nearest member of `to_ref` by metric, and rank the pairs by cost. "
        "Typical use: nearest landing pad (a find_locations set) to each carbonate outcrop (a find_regions set). "
        "Both arguments (`from_ref`/`to_ref`, aliases `from`/`to`) take any location reference, usually a set id. Walk metrics are approximate (isotropic "
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
    async def plan_route(goal: Any, start: Any = "rover", via: list | None = None, profile: str | None = None,
                         max_slope_deg: float | None = None, time_budget_min: float | None = None) -> CallToolResult:
        if profile in ("buggy", "scout"):     # words the model may use; canonical names go to the backends
            profile = backends.map_vehicle(profile)
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
        "fsd), speed, odometer, active route, status, last event, FSD armed, and the vehicle's caution and no-go slopes. Use before planning from the rover or "
        "after any drive to confirm where it is. " + UNITS + " Example: get_rover_state()"))
    async def get_rover_state() -> CallToolResult:
        res = await call("GET", "/rover/state", fmt.fmt_rover_state)
        if not res.is_error and drive_state and "progress" in drive_helpers:
            try:
                p = await drive_helpers["progress"]()
                if res.content and hasattr(res.content[0], "text"):
                    res.content[0].text = drive_helpers["fmt_progress"](p).split("\nDetails:")[0] + "\n" + res.content[0].text
                if isinstance(res.structured_content, dict):
                    res.structured_content["drive_progress"] = {k: v for k, v in p.items() if k != "state"}
            except Exception:  # noqa: BLE001  (progress is a bonus; the state itself is already in the result)
                pass
        return res

    async def command(body: dict, animate: bool | None, speed_factor: float | None, wait: bool = False,
                      total_m: float | None = None, duration_s: float | None = None, label: str | None = None,
                      route: dict | None = None) -> CallToolResult:
        """Send a motion command. Visible (live in the viewer) and non-blocking unless wait=true; animate=false computes
        instantly and implies wait. total_m / duration_s (planned) pick the on-screen speed."""
        animate = True if animate is None else bool(animate)
        if not animate:
            wait = True
        await drive_helpers["settle"]() if "settle" in drive_helpers else None
        if animate:
            body["realtime"] = True
            if speed_factor is None and total_m and duration_s and "visible_factor" in drive_helpers:
                speed_factor = drive_helpers["visible_factor"](total_m, duration_s)
        if speed_factor is not None:
            body["speed_factor"] = speed_factor
        if not wait:
            body["wait"] = False
        res = await call("POST", "/rover/command", fmt.fmt_command, body=body)
        sc = res.structured_content if isinstance(res.structured_content, dict) else {}
        if not wait and not res.is_error and (sc.get("started") or sc.get("stop_reason") is None) and sc.get("state"):
            import time as _time
            screen = (duration_s / speed_factor) if (duration_s and speed_factor) else None
            drive_state.clear()
            drive_state.update(goal=label, total_m=float(total_m or 0.0), t0=_time.monotonic(), screen_s=screen,
                               disarm_fsd=False, route_id=body.get("route_id"))
            head = (f"Driving now: {drive_helpers['about_dist'](total_m)} to {label or 'the destination'}"
                    f"{', ' + drive_helpers['about_secs'](screen) + ' on screen' if screen else ''}. "
                    "I'll stop if it gets too steep.")
            if route:
                head += (f"\nRoute: steepest part about {fmt.num(route.get('max_slope_deg'), 0)} degrees"
                         + (f"; {fmt.num(route['filled_length_m'], 0)} m crosses ground the orbital data only estimates"
                            if route.get("filled_length_m") else "") + ".")
            head += "\nCheck get_drive_progress for how far it got; stop_rover cancels."
            res.content[0].text = head + "\nDetails: " + res.content[0].text.replace("\n", "\n  ")
            sc["driving_now"] = {"goal": label, "total_m": total_m, "screen_s": screen, "speed_factor": speed_factor}
        return res

    async def plan_for_drive(goal: Any) -> dict | None:
        """Plan rover -> goal for the active vehicle; the route dict when feasible, else None (the goto call then
        reports why)."""
        try:
            st, r = await request("POST", "/route", json_body={"start": "rover", "goal": goal}, timeout=120.0)
        except Unreachable:
            return None
        return r if st < 400 and isinstance(r, dict) and r.get("feasible", True) and r.get("route_id") else None

    async def vehicle_flat_speed() -> float | None:
        try:
            t = await drive_helpers["vehicles"]()
            return float(t["vehicles"][t["current"]].get("flat_speed_mps") or 0.0) or None
        except Exception:  # noqa: BLE001
            return None

    async def with_nearby(res: CallToolResult) -> CallToolResult:
        """After a placement/drive, state where the rover now is relative to the base and the nearest POIs, so the
        reply never has to estimate those distances itself."""
        sc = res.structured_content
        if res.is_error or not isinstance(sc, dict) or sc.get("feasible") is False or not isinstance(sc.get("state"), dict):
            return res
        st = sc["state"]
        try:
            code, d = await request("GET", "/pois", params={"near": f"{st['x']:.1f},{st['y']:.1f}", "limit": 100})
        except Unreachable:
            return res
        if code >= 400 or not isinstance(d, dict) or not d.get("pois"):
            return res
        pois = d["pois"]
        base = next((p for p in pois if p.get("type") == "base"), None)
        near = [p for p in pois if p.get("type") != "base" and p.get("distance_m") is not None][:4]
        bits = ([f"base {fmt.dist_text(base['distance_m'])}"] if base and base.get("distance_m") is not None else []) + \
               [f"{p.get('name') or p['id']} ({p['ref']}) {fmt.dist_text(p['distance_m'])}" for p in near]
        if bits and res.content and hasattr(res.content[0], "text"):
            res.content[0].text += "\nStraight-line distances from the rover now: " + "; ".join(bits) + "."
            sc["nearby"] = [{"ref": p["ref"], "distance_m": p["distance_m"]} for p in ([base] if base else []) + near]
        return res

    @tool("drive_rover", (
        "Drive the rover in a straight line for distance_m along heading_deg (degrees clockwise from north: 0 north, "
        "90 east; default current heading). It stops on slopes above the no-go limit (stop_reason blocked_slope) or "
        "unknown ground (blocked_hazard) and tells you why. Use for short manual-style moves ('drive 200 m "
        "north-west' = heading 315); for a destination use goto_location or drive_to, and for a planned route "
        "follow_route. wait=true returns the final stop reason (default: the drive plays live and the call returns at once). "
        + DRIVE_NOTE + " " + UNITS + " Example: drive_rover(distance_m=200, heading_deg=315)"), act)
    async def drive_rover(distance_m: Any, heading_deg: Any = None, animate: bool | None = None,
                          speed_factor: float | None = None, wait: bool = False) -> CallToolResult:
        distance_m, heading_deg, bad = _loose_move(distance_m, heading_deg)
        if bad:
            return await err(bad, 'Example: drive_rover(distance_m=300, heading_deg="north") or heading_deg=315.')
        body = {"cmd": "drive", "distance_m": distance_m, "heading_deg": heading_deg}
        flat = await vehicle_flat_speed()
        return await command({k: v for k, v in body.items() if v is not None}, animate, speed_factor, wait,
                             total_m=distance_m, duration_s=(distance_m / flat) if flat else None,
                             label=f"{distance_m:.0f} m on")

    @tool("goto_location", (
        "Plan a route from the rover to a place and drive it (one call). target is any location reference (POI, "
        "marker, point) or plain words (\"the crater\", \"seitah\", \"the delta\", \"landing site\": resolved for you; "
        "the result says what was chosen); or pass x and y. If the place is unreachable it does not move and returns the "
        "reason and the nearest reachable point. Use goto_location only when the user asks to DRIVE ('drive to the "
        "landing site'); the rover drives live on screen and the call returns at once (wait=true to get the final stop "
        "reason). Use plan_route + assess_route + follow_route when the user wants to review the route first, and "
        "teleport_rover for 'take me to X' (instant, no time cost). " + DRIVE_NOTE + " " + LOCATION_REFS + " " + UNITS +
        " Example: goto_location(target=\"poi:landing\")"), act)
    async def goto_location(target: Any = None, x: float | None = None, y: float | None = None,
                            animate: bool | None = None, speed_factor: float | None = None,
                            wait: bool = False) -> CallToolResult:
        body: dict = {"cmd": "goto"}
        if target is not None:
            body["target"] = _coerce(target)
        elif x is not None and y is not None:
            body.update(x=x, y=y)
        else:
            return await err("goto_location needs a target, or x and y.",
                             'Example: goto_location(target="poi:landing") or goto_location(x=50, y=-50).')
        goal = body.get("target") if "target" in body else {"x": x, "y": y}
        label = None
        for r in place_resolution.current.get() or []:
            if r.get("arg") == "target":
                label = r["chosen"].get("name")
        label = label or ("the spot you picked" if isinstance(goal, dict) else await drive_helpers["goal_label"](goal))
        route = await plan_for_drive(goal)       # plan first: the route length picks the on-screen speed
        if route:
            res = await command({"cmd": "follow_route", "route_id": route["route_id"]}, animate, speed_factor, wait,
                                total_m=route.get("distance_m"), duration_s=(route.get("duration_min") or 0) * 60.0,
                                label=label, route=route)
        else:
            res = await command(body, animate, speed_factor, True)   # infeasible: the backend explains why, nothing moves
        return await with_nearby(res)

    @tool("follow_route", (
        "Drive the rover along a stored route (route_id from plan_route). The rover must be within 3 m of the route "
        "start (plan with start=\"rover\" or goto/teleport first). With FSD armed (fsd_enable) caution slopes slow "
        "it and a blocked route is replanned once; without FSD it stops on a no-go slope (blocked_slope) and "
        "reports the hazard. Use for 'scout that route'. Always pass route_id from the most recent plan_route. If omitted, it uses the "
        "MOST RECENTLY PLANNED route (or the one bound by fsd_enable(route_id=...), whichever is newer) and the "
        "result says which route it used. If the rover is not at the route start the error gives the "
        "teleport_rover/goto_location call to get there. The rover drives live on screen and the call returns at once "
        "(wait=true to get the final stop reason). "
        + DRIVE_NOTE + " " + UNITS + " Example: follow_route(route_id=\"route-3\", wait=true)"), act)
    async def follow_route(route_id: str | None = None, animate: bool | None = None,
                           speed_factor: float | None = None, wait: bool = False) -> CallToolResult:
        body = {"cmd": "follow_route"}
        if route_id:
            body["route_id"] = route_id
        total = dur = None
        route = None
        rid = route_id
        if rid:
            try:
                st, r = await request("GET", f"/route/{rid}")
                if st < 400 and isinstance(r, dict):
                    route, total, dur = r, r.get("distance_m"), (r.get("duration_min") or 0) * 60.0
            except Unreachable:
                pass
        label = None
        if route:
            g = route.get("goal")
            label = g if isinstance(g, str) else (g.get("label") or g.get("name")) if isinstance(g, dict) else None
            if isinstance(label, str) and (label.startswith("(") or label.startswith("poi:") or label.startswith("route")):
                label = None
        return await command(body, animate, speed_factor, wait, total_m=total, duration_s=dur,
                             label=label or "the end of the route", route=route)

    async def fsd_cmd(body: dict) -> CallToolResult:
        return await call("POST", "/rover/command", fmt.fmt_fsd_change, body=body)

    @tool("fsd_enable", (
        "Arm FSD (full self-driving) mode: the rover follows routes with the slope policy (halved speed on caution "
        "slopes, stop at no-go slopes or unknown ground, one automatic replan with a 10% lower slope limit when "
        "blocked). Optionally bind a route_id (it then becomes the default for follow_route). policy keys: booleans "
        "replan_on_block, caution_slowdown, and filled = \"block\"|\"caution\"|\"allow\" (interpolated, not measured "
        "terrain: caution, the default, crosses it at half speed and reports filled_crossed_m; block stops at it). Arming does not move the rover. Use before follow_route when scouting; "
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
        res = await call("POST", "/rover/command", fmt.fmt_command, body={"cmd": "stop"})
        if "settle" in drive_helpers:
            await drive_helpers["settle"]()
        return res

    @tool("teleport_rover", (
        "Place the rover at a location instantly (no driving, no time cost, odometer unchanged), optionally with a "
        "heading. Use for 'take me to the delta front' together with fly_camera, after search_pois or find_sites "
        "gives a place; use goto_location instead only when the user wants the rover to drive there. "
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
        return await with_nearby(await call("POST", "/rover/command", fmt.fmt_command, body=body))

    # ---- view and markers ----

    @tool("set_view", (
        "Control the viewer: camera mode (globe, orbit, chase, ground), the camera target (any location), layer "
        "visibility ({layer_id: true|false}, ids from list_layers) and highlights (POIs, markers, set members or "
        "whole sets such as a find_sites result). Use it to highlight the result set when the answer IS a set of places the user asked to find or see ('highlights=[\"set:2\"]'); "
        "do not move the camera unprompted otherwise. "
        "Use set_view when you need layers or highlights or several things at once; use fly_camera instead for a "
        "plain 'show me X' (camera only). Highlighting a whole set shows at most the first 50 members. The reply says whether a viewer is connected. "
        "Example: set_view(mode=\"orbit\", target=\"poi:landing\", layers={\"carbonate\": true}, highlights=[\"set:2\"])"), act)
    async def set_view(mode: str | None = None, target: Any = None, layers: Any = None,
                       highlights: Any = None) -> CallToolResult:
        mode = "globe" if mode == "planet" else mode      # the API's name for the planet view
        body = {"mode": mode, "target": _coerce(target), "layers": _coerce(layers), "highlights": _coerce(highlights)}
        return await call("POST", "/view", fmt.fmt_view, body={k: v for k, v in body.items() if v is not None})

    @tool("fly_camera", (
        "Fly the viewer camera to a place: a point, POI, marker or search result. mode: orbit (default), chase, "
        "ground, globe; distance_m is the camera distance. Use for 'show me the delta front' (with teleport_rover "
        "if the rover should go too). Does not move the rover. Use set_view instead to toggle layers or highlight "
        "results. " + LOCATION_REFS + " "
        'Example: fly_camera(target="poi:landing", mode="orbit", distance_m=300)'), act)
    async def fly_camera(target: Any, mode: str = "orbit", distance_m: float | None = None) -> CallToolResult:
        mode = "globe" if mode == "planet" else mode
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

    import tools_visibility  # mcp/tools_visibility.py: viewshed, line_of_sight, find_vantage_points
    tools_visibility.register(tool, call, _loc, _coerce, SCHEMA_PATCH, UNITS, LOCATION_REFS, CONDITIONS, WITHIN,
                              CONDITIONS_SCHEMA, WITHIN_SCHEMA)

    import tools_sun  # mcp/tools_sun.py: sun_position, illumination_at, find_lit_windows
    tools_sun.register(tool, call, _coerce, LOCATION_REFS, SCHEMA_PATCH, _loc)

    import tools_lake  # mcp/tools_lake.py: flood_at_level, eva_envelope, check_eva_return
    tools_lake.register(tool, call, _coerce, LOCATION_REFS, SCHEMA_PATCH, _loc)

    import tools_drive  # mcp/tools_drive.py: select_vehicle, drive_to (buggy or scout, highest point)
    tools_drive.register({"tool": tool, "act": act, "act_idem": act_idem, "request": request, "ok": ok, "err": err,
                          "Unreachable": Unreachable, "coerce": _coerce, "location_refs": LOCATION_REFS,
                          "units": UNITS, "drive_note": DRIVE_NOTE, "schema_patch": SCHEMA_PATCH, "loc": _loc,
                          "backends": backends, "drive_state": drive_state, "helpers": drive_helpers, "ro": ro})

    # ---- schema patch, aliases and friendly validation errors ----------------------------------
    import re as _re

    def _example(desc: str) -> str | None:
        m = _re.search(r"Example: (.+)$", desc, _re.S)
        return m.group(1).strip() if m else None

    def _describe_type(v: Any) -> str:
        return {dict: "an object", list: "a list", str: "a string", int: "a number", float: "a number",
                bool: "a boolean", type(None): "null"}.get(type(v), type(v).__name__)

    async def places_fetch(text: str) -> dict | None:
        """Gazetteer lookup (devapi GET /places/resolve) relative to the rover; None when the backend has no gazetteer."""
        near = None
        try:
            st, rs = await request("GET", "/rover/state", timeout=5.0)
            if st < 400 and isinstance(rs, dict) and rs.get("x") is not None and rs.get("y") is not None:
                near = f"{rs['x']},{rs['y']}"
        except Unreachable:
            pass
        try:
            params = {"q": text, **({"near": near} if near else {})}
            st, d = await request("GET", "/places/resolve", params=params, timeout=60.0)
        except Unreachable:
            return None
        return d if st == 200 and isinstance(d, dict) and "resolved" in d else None

    state["places_fetch"] = places_fetch

    ACTS_ON_GLOBE = ("drive_to", "goto_location", "teleport_rover", "plan_route", "follow_route", "drive_rover")

    async def outside_site_answer(tname: str, res: dict) -> CallToolResult:
        """The place exists on Mars but not in the 8 km drivable Jezero site: say so in one friendly breath and act on the
        best alternative (fly the globe camera there, point to a street-view patch) instead of failing."""
        ch = res["chosen"]
        lon = ch["lon"] if ch["lon"] <= 180 else ch["lon"] - 360
        view_ok, view_note = False, ""
        if tname in ACTS_ON_GLOBE:
            try:
                st, d = await request("POST", "/view", json_body={"mode": "globe", "target": {"lat": ch["lat"], "lon": lon}},
                                      timeout=15.0)
                view_ok = st < 400
            except Unreachable:
                pass
            view_note = (" I've flown the globe view to it." if view_ok else
                         " (I could not move the globe camera: no viewer or the backend cannot take lat/lon.)")
        sw = ch.get("street_world")
        text = (res.get("explanation") or "") + view_note
        if tname in ACTS_ON_GLOBE:
            text += (" The rover did not move." +
                     (f" To drive there yourself, switch to Drive and choose '{sw}'." if sw else
                      " I can still drive to anything inside the Jezero area (for example the delta front, Séítah or a crater)."))
        data = {"outside_site": True, "place": ch, "distance_m": ch.get("distance_m"), "globe_view_sent": view_ok,
                "street_world": sw, "alternatives": res.get("candidates") or [],
                "view_event": {"type": "view.camera", "mode": "globe", "lat": ch["lat"], "lon": lon, "label": ch.get("name"),
                               "note": "sent as POST /view {mode: globe, target: {lat, lon}}; the API relays view.camera "
                                       "with site-frame x, y (equirectangular about the site centre), not lat/lon"}}
        return await ok(text, data)

    def _wrap_tool(tname: str, t) -> None:
        props_schema = t.parameters.setdefault("properties", {})
        for pname, patch in SCHEMA_PATCH.get(tname, {}).items():
            if pname in props_schema:
                old = props_schema[pname]
                new = dict(patch)
                if "description" not in new and old.get("description"):
                    new["description"] = old["description"]
                props_schema[pname] = new
        valid = list(props_schema)
        required = list(t.parameters.get("required", []))
        aliases = ALIASES.get(tname, {})
        example = _example(t.description)
        orig = t.run

        def bad(msg: str) -> CallToolResult:
            hint = (f"Valid arguments: {', '.join(valid) or 'none'} (required: {', '.join(required) or 'none'}). "
                    if valid else "This tool takes no arguments. ")
            return err(msg, hint + ((f"Example: {example}" if example.startswith(tname + "(") else f"Example: {tname}({example})")
                       if example else ""))

        async def run(arguments, context, convert_result=False):
            args = dict(arguments or {})
            for a, real in aliases.items():
                if a in args and real not in args:
                    args[real] = args.pop(a)
            unknown = [k for k in args if k not in valid]
            if unknown:
                return await bad(f"Unknown argument(s) for {tname}: {', '.join(unknown)}.")
            missing = [k for k in required if k not in args]
            if missing:
                return await bad(f"Missing required argument(s) for {tname}: {', '.join(missing)}.")
            places_done: list[dict] = []
            if tname in place_resolution.LOC_ARGS or tname in place_resolution.LOC_LIST_ARGS:
                args, places_done, failure = await place_resolution.resolve_arguments(tname, args, places_fetch)
                if failure and failure.get("outside_site"):
                    return await outside_site_answer(tname, failure)
                if failure:
                    cands = failure.get("candidates") or []
                    hint = ("Pick the one the user most likely means and retry with it, e.g. target=\"" +
                            str(cands[0].get("ref")) + "\"." if cands else "Ask the user for a place name or coordinates.")
                    return await err(failure.get("explanation") or f"I could not place \"{failure.get('query')}\".", hint)
            tok = place_resolution.current.set(places_done)
            try:
                res = await orig(args, context, convert_result=convert_result)
                if places_done and isinstance(res, CallToolResult):
                    note = place_resolution.notes_text(places_done)
                    if note and res.content and hasattr(res.content[0], "text"):
                        res.content[0].text += "\n" + note
                    if isinstance(res.structured_content, dict):
                        res.structured_content["places_resolved"] = [
                            {k: d[k] for k in ("arg", "query", "kind", "explanation", "ref", "chosen")} for d in places_done]
                return res
            except ToolError as e:
                if not isinstance(e.__cause__, ValidationError):
                    raise
                parts, seen = [], set()
                for ve in e.__cause__.errors():
                    name = str(ve["loc"][0]) if ve["loc"] else "?"
                    if name in seen:
                        continue
                    seen.add(name)
                    got = args.get(name)
                    parts.append(f'"{name}" has the wrong type or form (got {_describe_type(got)}): {ve["msg"]}')
                return await bad(f"Invalid argument(s) for {tname}. " + " ".join(parts))
            finally:
                place_resolution.current.reset(tok)
        object.__setattr__(t, "run", run)

    for _t in list(mcp._tool_manager.list_tools()):
        _wrap_tool(_t.name, _t)

    return mcp


def _startup_frame_check(atlas_url: str, rover_url: str) -> dict | None:
    """GET /site on both backends and warn loudly on stderr if they are not the same coordinate frame."""
    try:
        a = httpx.get(atlas_url.rstrip("/") + "/site", timeout=3.0).json()
        r = httpx.get(rover_url.rstrip("/") + "/site", timeout=3.0).json()
        res = backends.compare_sites(a, r)
    except Exception as e:  # noqa: BLE001  (a backend not up yet is not fatal; tools re-check lazily)
        print(f"[ares-atlas] frame check skipped ({type(e).__name__}: {str(e)[:100]}); it re-runs on the first tool call.",
              file=sys.stderr)
        return None
    if res:
        bar = "!" * 100
        print(f"{bar}\n[ares-atlas] {res['message']}\n{bar}", file=sys.stderr)
    else:
        print("[ares-atlas] frame check OK: both backends load the same site frame.", file=sys.stderr)
    return res


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: serve over stdio, or with --http over streamable HTTP (default 127.0.0.1:8765, path /mcp)."""
    ap = argparse.ArgumentParser(description="Ares Atlas MCP server (wrapper over the REST API)")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--api-url", default=None, help=f"query-engine API base URL (default $ATLAS_API_URL or {DEFAULT_API_URL})")
    ap.add_argument("--rover-api-url", default=None,
                    help="Yousef's rover API base URL (default $ATLAS_ROVER_API_URL; unset = everything uses --api-url)")
    args = ap.parse_args(argv)
    rover_url = args.rover_api_url or os.environ.get("ATLAS_ROVER_API_URL") or None
    if rover_url:
        _startup_frame_check(args.api_url or os.environ.get("ATLAS_API_URL", DEFAULT_API_URL), rover_url)
    server = build_server(api_url=args.api_url, rover_url=rover_url, targets=fetch_targets(args.api_url, rover_url))
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
