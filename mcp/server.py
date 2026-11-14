"""Ares Atlas MCP server: spatial queries over the Jezero site (contracts/mcp-tools.md).

Run:  python mcp/server.py            (stdio)
      python mcp/server.py --http     (streamable HTTP on 127.0.0.1:8765, path /mcp)
Env:  ATLAS_SITE_DIR=<site dir>  (real site).  Unset, or ATLAS_SITE=synthetic -> synthetic test site.
"""
from __future__ import annotations

import argparse
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

import numpy as np  # noqa: E402
from mcp_types import CallToolResult, TextContent, ToolAnnotations  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

import atlas_core  # noqa: E402
from atlas_core import AtlasError, SetStore, catalog, describe, load_site, search, travel  # noqa: E402
import format as fmt  # noqa: E402  (mcp/format.py)

# ---- shared description fragments -------------------------------------------------------------

UNITS = ("Units: distances in site-frame metres (x east, y north, origin at site centre); "
         "walking time in minutes; slope in degrees; area in m2.")
LOCATION_REFS = (
    'Location references (`at`, `from`): a point {"x": 120, "y": -340}; lat/lon {"lat": 18.44, "lon": 77.45}; '
    'a POI id "poi:<id>" (see get_site_overview); the keywords "base" and "rover"; a stored set "set:3" '
    '(every member) or one member "3.2"; or a list of these.')
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

# ---- result construction ------------------------------------------------------------------------


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


def load_configured_site() -> tuple[Any, bool]:
    d = os.environ.get("ATLAS_SITE_DIR")
    if d and os.environ.get("ATLAS_SITE", "").lower() != "synthetic":
        return load_site(d), False
    from atlas_core.testing import make_site
    return make_site(), True


def build_server(site=None, synthetic: bool | None = None, name: str = "ares-atlas") -> MCPServer:
    if site is None:
        site, synthetic = load_configured_site()
    synthetic = bool(synthetic)
    store = SetStore()

    def ok(text: str, data: dict) -> CallToolResult:
        data = _clean(data)
        if synthetic:
            data["synthetic"] = True
        return CallToolResult(content=[TextContent(type="text", text=fmt.with_banner(text, synthetic))],
                              structured_content=data)

    def err(message: str, hint: str | None = None) -> CallToolResult:
        data = {"error": message, "hint": hint}
        if synthetic:
            data["synthetic"] = True
        return CallToolResult(content=[TextContent(type="text", text=fmt.with_banner(fmt.fmt_error(message, hint), synthetic))],
                              structured_content=data, is_error=True)

    def run(fn, formatter, *args, **kwargs) -> CallToolResult:
        try:
            result = fn(*args, **kwargs)
            return ok(formatter(result), result)
        except AtlasError as e:
            return err(e.message, e.hint)
        except NotImplementedError:
            return err(f"{getattr(fn, '__name__', 'this tool')} is not available in this build yet.",
                       "Use get_site_overview, list_layers, describe_layer or describe_location meanwhile.")
        except (KeyError, TypeError, ValueError) as e:
            return err(f"Invalid request: {type(e).__name__}: {str(e)[:200]}",
                       "Check argument names and shapes against the tool description.")
        except Exception as e:  # never leak a stack trace to the model
            return err(f"Unexpected server error ({type(e).__name__}): {str(e)[:200]}",
                       "Retry with simpler arguments; if it persists, report this message.")

    ro = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
    mcp = MCPServer(
        name,
        instructions=("Ares Atlas answers spatial questions about the Jezero crater site on Mars. Start with "
                      "get_site_overview. Search tools store results as sets (set_id) that later tools accept. "
                      "Missing data is reported as unknown, never as safe or absent."),
    )

    # ---- overview / catalogue ----

    @mcp.tool(name="get_site_overview", annotations=ro, description=(
        "FIRST CALL in every session. Returns the site extent and coordinate frame, all layers grouped by kind "
        "with what each measures, profile names (named bundles of conditions such as landing_pad or "
        "eva_walkable), key points of interest with coordinates, rover position if known, and the evidence tier "
        "scale. Use it to learn valid layer ids and POI ids before calling other tools. "
        + UNITS + " Example: get_site_overview()"))
    def get_site_overview() -> CallToolResult:
        return run(catalog.site_overview, fmt.fmt_overview, site)

    @mcp.resource("atlas://site/overview", name="site_overview", mime_type="text/plain",
                  description="Site extent, layers, profiles, POIs and evidence tiers (same as get_site_overview).")
    def overview_resource() -> str:
        return fmt.with_banner(fmt.fmt_overview(catalog.site_overview(site)), synthetic)

    @mcp.tool(name="list_layers", annotations=ro, description=(
        "List data layers (id, kind, title, one-line measures, resolution). Kinds: continuous (numbers such as "
        "slope_2m), categorical (classes such as geologic_unit), evidence (ordinal tiers for detected things such "
        "as minerals). Filter by kind and/or a case-insensitive text query over id/title/measures. Use when you "
        "need a layer id; use describe_layer for limits and citations. "
        'Example: list_layers(kind="continuous", query="slope")'))
    def list_layers(kind: str | None = None, query: str | None = None) -> CallToolResult:
        return run(catalog.list_layers, fmt.fmt_layers, site, kind, query)

    @mcp.tool(name="describe_layer", annotations=ro, description=(
        "Full catalogue entry for one layer: what it measures, what it does NOT measure, quality notes, classes or "
        "tier scale, coverage (share of cells with data), value range, resolution and the source citation. "
        "Call this BEFORE relying on an unfamiliar layer or quoting its numbers. Not for values at a place (use "
        'describe_location). Example: describe_layer(layer_id="slope_2m")'))
    def describe_layer(layer_id: str) -> CallToolResult:
        return run(catalog.describe_layer, fmt.fmt_layer, site, layer_id)

    # ---- location description ----

    def _describe(at: Any, radius_m: float = 0.0, layers: list[str] | None = None) -> CallToolResult:
        return run(describe.describe_location, fmt.fmt_describe, site, store, _coerce(at), radius_m, _coerce(layers))

    desc_loc = (
        "Everything known at one place: value of every layer (or the chosen layers), data quality, and the nearest "
        "rover ground truth (confirmed in-situ cell or sample site) with its distance. radius_m=0 gives point "
        "values (elevation interpolated; classes and evidence tiers from the containing 2 m cell). radius_m>0 gives "
        "footprint statistics (mean/min/max, class shares, best evidence tier, unknown share): use it for pads, "
        "walking safety or outcrop checks, and read the MAX, not only the mean. 'unknown' means no data, never "
        "safe or absent. Accepts a set or list of up to 10 locations (compact table). Use for 'what is at X?'; "
        "use find_locations/find_regions to search. " + LOCATION_REFS + " " + UNITS + " "
        'Example: describe_location(at="poi:base", radius_m=50, layers=["slope_2m","carbonate"])')

    @mcp.tool(name="describe_location", annotations=ro, description=desc_loc)
    def describe_location(at: Any, radius_m: float = 0.0, layers: list[str] | None = None) -> CallToolResult:
        return _describe(at, radius_m, layers)

    @mcp.tool(name="sample_terrain", annotations=ro, description=(
        "Alias of describe_location(at, radius_m=0): point values of every layer at an {x, y} site-frame point "
        "(metres). Prefer describe_location. Example: sample_terrain(x=120, y=-340)"))
    def sample_terrain(x: float, y: float) -> CallToolResult:
        return _describe({"x": x, "y": y}, 0.0, None)

    # ---- search ----

    @mcp.tool(name="find_locations", annotations=ro, description=(
        "Find footprint-sized SPOTS (e.g. a landing pad or camp) where every cell within footprint_radius_m "
        "satisfies ALL conditions, exhaustively over the whole grid (the best answer is never missed). Results are "
        "spread apart (spacing_m), ranked by objective, and stored as a set (set_id) for describe_location, "
        "get_set and nearest. Use find_regions instead for connected areas such as outcrops or a geologic unit. "
        "An empty result includes per-condition pass rates showing which condition to relax. "
        "profile = a named bundle of conditions and footprint (see get_site_overview); explicit conditions "
        "override it; thresholds are reported as cited or assumed. objective: flattest (default), "
        "largest_clear_area, closest (needs within), highest, lowest, best_evidence:<layer>. limit 1-50 (default 5). "
        + CONDITIONS + " " + WITHIN + " " + UNITS + " "
        'Example: find_locations(profile="landing_pad", footprint_radius_m=50, '
        'within={"from": "base", "max": 60, "metric": "walk_time_min"}, objective="flattest", limit=3)'))
    def find_locations(conditions: list[dict] | None = None, profile: str | None = None,
                       footprint_radius_m: float | None = None, within: dict | None = None,
                       objective: str = "flattest", spacing_m: float | None = None,
                       limit: int = 5) -> CallToolResult:
        return run(search.find_locations, lambda r: fmt.fmt_search(r, "spots"), site, store,
                   _coerce(conditions), profile, footprint_radius_m, _coerce(within), objective, spacing_m, limit)

    @mcp.tool(name="find_regions", annotations=ro, description=(
        "Find connected REGIONS (8-connected areas of cells satisfying ALL conditions) of at least min_area_m2, "
        "e.g. carbonate outcrops, a geologic unit, or flat terrain patches. Each region reports area, a "
        "representative interior point, bounding box and statistics. Stored as a set (set_id). Use "
        "find_locations instead for footprint-sized spots. Empty results include per-condition pass rates. "
        "objective: largest (default), closest (needs within), flattest, best_evidence:<layer>. limit 1-50 "
        "(default 5). " + CONDITIONS + " " + WITHIN + " " + UNITS + " "
        'Example: find_regions(conditions=[{"evidence": "carbonate", "min_tier": "weak_orbital"}], '
        'min_area_m2=400, objective="largest")'))
    def find_regions(conditions: list[dict] | None = None, profile: str | None = None,
                     min_area_m2: float = 400.0, within: dict | None = None,
                     objective: str = "largest", limit: int = 5) -> CallToolResult:
        return run(search.find_regions, lambda r: fmt.fmt_search(r, "regions"), site, store,
                   _coerce(conditions), profile, min_area_m2, _coerce(within), objective, limit)

    @mcp.tool(name="nearest", annotations=ro, description=(
        "For each member of `from_ref`, find the nearest member of `to_ref` by metric, and rank the pairs by cost. "
        "Typical use: nearest landing pad (a find_locations set) to each carbonate outcrop (a find_regions set). "
        "Both arguments take any location reference, usually a set id. Walk metrics are approximate (isotropic "
        "placeholder speed model; uphill = downhill) and unreachable members are listed. Returns a new set of "
        "pairs. metric: walk_time_min (default, minutes), walk_m, straight_m (metres); profile: eva_walkable "
        "(default) sets speed and maximum slope. limit 1+ (default 5). " + LOCATION_REFS + " "
        'Example: nearest(from_ref="set:2", to_ref="set:1", metric="walk_time_min", limit=5)'))
    def nearest(from_ref: Any, to_ref: Any, metric: str = "walk_time_min",
                profile: str = "eva_walkable", limit: int = 5) -> CallToolResult:
        return run(travel.nearest, fmt.fmt_nearest, site, store, _coerce(from_ref), _coerce(to_ref),
                   metric, profile, limit)

    @mcp.tool(name="get_set", annotations=ro, description=(
        "Page through a stored location set (from find_locations, find_regions or nearest) or re-sort it by any "
        "member or attribute field (for example score, clear_radius_m, area_m2, slope_2m_max, cost, "
        "walk_time_min). Use when a search stored more members than its text result showed. "
        'Example: get_set(set_id="set:1", offset=5, limit=10, sort="slope_2m_max")'))
    def get_set(set_id: str, offset: int = 0, limit: int = 10, sort: str | None = None,
                descending: bool = False) -> CallToolResult:
        def go():
            if offset < 0 or not 1 <= limit <= 50:
                raise AtlasError("offset must be >= 0 and limit within 1..50.", hint="Try offset=0, limit=10.")
            page = store.page(set_id, offset, limit, sort, descending)
            page["query"] = store.get(set_id).query
            return page
        go.__name__ = "get_set"
        return run(go, fmt.fmt_set)

    return mcp


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ares Atlas MCP server")
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    server = build_server()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
