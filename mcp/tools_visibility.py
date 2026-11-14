"""MCP tools for line of sight: viewshed, line_of_sight, find_vantage_points.

Registered from mcp/server.py with `tools_visibility.register(...)`, passing the server's own `tool`
decorator, `call` helper and shared description fragments, so these tools behave exactly like the others
(text summary + structuredContent, API errors as readable error results, agent.event emission).
"""
from __future__ import annotations

from typing import Any

import format as fmt  # noqa: E402  (mcp/format.py)


# ---- text formatting (pure functions over the API results) ----------------------------------------

def _assumed(d: dict) -> str | None:
    return fmt.assumptions_line(d.get("assumptions"))


def _thing(t: dict) -> str:
    name = t.get("name") or t.get("id")
    extra = f" ({t['id']})" if t.get("name") and t.get("id") and t["id"] != t["name"] else ""
    return f"{name}{extra} {fmt.num(t['distance_m'], 0)} m"


def fmt_viewshed(d: dict) -> str:
    o = d["observer"]
    out = [f"From {fmt.xy(o['x'], o['y'])} (eye {fmt.num(o['eye_elevation_m'] - o['elevation_m'])} m above terrain, "
           f"ground elevation {fmt.num(o['elevation_m'])} m): sees {fmt.num(d['visible_fraction'] * 100)}% of the known "
           f"terrain within {fmt.num(d['max_radius_m'], 0)} m = {fmt.area_text(d['visible_area_m2'])}; "
           f"farthest visible ground {fmt.num(d['farthest_visible_m'], 0)} m away"
           + (f" at {fmt.xy(d['farthest_visible_point']['x'], d['farthest_visible_point']['y'])}"
              if d.get("farthest_visible_point") else "") + ".",
           f"Visible region stored as {d['set_id']} (kind regions): usable as a location in nearest / find_* / describe_location."]
    vt, ht = d.get("visible_things") or [], d.get("hidden_things") or []
    if vt or ht:
        out.append(f"Named places in range: {d['visible_things_count']} visible, {d['hidden_things_count']} hidden.")
    if vt:
        out.append("Visible: " + "; ".join(_thing(t) for t in vt[:12]) + (" ..." if len(vt) > 12 else ""))
    if ht:
        out.append("Hidden: " + "; ".join(_thing(t) for t in ht[:12]) + (" ..." if len(ht) > 12 else ""))
    a = _assumed(d)
    if a:
        out.append(a)
    for n in d.get("notes") or []:
        out.append("Note: " + n)
    if d.get("hint"):
        out.append("Next: " + d["hint"])
    return "\n".join(out)


def fmt_los(d: dict) -> str:
    a, b = d["a"], d["b"]
    if d["clear"]:
        mc = d.get("min_clearance_m")
        head = (f"CLEAR line of sight over {fmt.num(d['distance_m'], 0)} m between {a.get('id') or fmt.xy(a['x'], a['y'])} "
                f"and {b.get('id') or fmt.xy(b['x'], b['y'])}"
                + (f"; tightest clearance {fmt.num(mc, 2)} m at {fmt.num(d['tightest_point']['distance_from_a_m'], 0)} m from A." if mc is not None else "."))
    else:
        bp = d["blocking_point"]
        head = (f"BLOCKED over {fmt.num(d['distance_m'], 0)} m: terrain at {fmt.xy(bp['x'], bp['y'])}, "
                f"{fmt.num(bp['distance_from_a_m'], 0)} m from A, is {fmt.num(bp['deficit_m'], 2)} m above the sight line "
                f"(terrain {fmt.num(bp['terrain_elevation_m'])} m vs line {fmt.num(bp['sight_line_elevation_m'])} m). "
                f"Terrain is in the way from {fmt.num(d['blocked_span_m'][0], 0)} to {fmt.num(d['blocked_span_m'][1], 0)} m.")
    p = d["profile"]
    out = [head,
           f"Profile: {p['samples']} samples, terrain {fmt.num(p['terrain_min_m'])} to {fmt.num(p['terrain_max_m'])} m, "
           f"B is {fmt.num(p['elevation_difference_m'])} m relative to A."]
    a_ = _assumed(d)
    if a_:
        out.append(a_)
    for n in d.get("notes") or []:
        out.append("Note: " + n)
    if d.get("hint"):
        out.append("Next: " + d["hint"])
    return "\n".join(out)


def fmt_vantage(d: dict) -> str:
    res = d.get("results") or []
    if not d["count"]:
        out = ["No vantage point found."]
        for x in d.get("diagnostics") or []:
            out.append(f"  {x['step']}: {fmt.num(x['pass_fraction'] * 100, 2)}% of candidate cells")
    else:
        out = [f"{d['count']} vantage point(s); top {len(res)} by {d['objective']}. Stored as {d['set_id']}."]
        for i, m in enumerate(res, 1):
            at = m["attributes"]
            above = at.get("elevation_above_target_m")
            sl = at.get("slope_2m")
            out.append(f"{i}. {m['id']} at {fmt.xy(m['x'], m['y'])}: sees {fmt.num(m['score'] * 100, 0)}% of the target "
                       f"from {fmt.num(at['distance_m'], 0)} m"
                       + (f", {fmt.num(abs(above))} m {'above' if above >= 0 else 'below'} it" if above is not None else "")
                       + (f", slope {fmt.num(sl)} deg" if sl is not None else ""))
    a = _assumed(d)
    if a:
        out.append(a)
    for n in d.get("notes") or []:
        out.append("Note: " + n)
    if d.get("hint"):
        out.append("Next: " + d["hint"])
    return "\n".join(out)


# ---- registration ---------------------------------------------------------------------------------

def register(tool, call, loc, coerce, schema_patch: dict, units: str, location_refs: str, conditions_text: str,
             within_text: str, conditions_schema: dict, within_schema: dict) -> None:
    schema_patch.update({
        "viewshed": {"at": loc("observer position"),
                     "check": loc("extra places (e.g. a set) whose visibility is reported individually", many=True)},
        "line_of_sight": {"a": loc("first end (e.g. the base)"), "b": loc("second end (e.g. the rover or a marker)")},
        "find_vantage_points": {"target": loc("the thing to be seen: a point, a region/spot set or one member"),
                                "conditions": conditions_schema, "within": within_schema},
    })

    @tool("viewshed", (
        "WHAT CAN I SEE FROM HERE? Computes everything visible from an observer standing at `at` (eye "
        "observer_height_m above ground, default 1.7 m, an assumed standing astronaut), out to max_radius_m "
        "(default 3000, max 8000), using the 2 m elevation layer with Mars curvature (horizon about 3.4 km on flat "
        "ground for 1.7 m; refraction negligible). Returns visible area in m2, visible fraction of known terrain "
        "within the radius, farthest visible ground, and which POIs and markers (and any `check` locations) are "
        "visible or hidden. Stores the visible region as a set (kind regions, set_id) you can pass to nearest, "
        "describe_location or find_locations(within=...). Use this for open-ended 'what can I see / what is hidden "
        "from X'. Use line_of_sight instead for one specific pair of places (radio / comm check), "
        "find_vantage_points to find WHERE to stand to see something. target_height_m (default 0) is the height of "
        "the thing being looked at above its ground, e.g. 2 for a mast. Cells with no elevation data block nothing and are "
        "reported (visibility behind them is optimistic). Geometric only: no atmosphere haze, shadows or lighting. "
        + units + " " + location_refs + " "
        'Example: viewshed(at="base", observer_height_m=1.7, max_radius_m=3000)'))
    async def viewshed(at: Any, observer_height_m: float = 1.7, target_height_m: float = 0.0,
                       max_radius_m: float = 3000.0, check: Any = None, curvature: bool = True) -> Any:
        body = {"at": coerce(at), "observer_height_m": observer_height_m, "target_height_m": target_height_m,
                "max_radius_m": max_radius_m, "check": coerce(check), "curvature": curvature}
        return await call("POST", "/visibility/viewshed", fmt_viewshed, body={k: v for k, v in body.items() if v is not None})

    @tool("line_of_sight", (
        "IS B IN LINE OF SIGHT OF A? (e.g. 'is the base in radio line of sight from this spot?'). Checks "
        "geometric line of sight between two single places over the 2 m terrain with Mars curvature, with "
        "antenna / eye heights height_a and height_b above ground (defaults 1.7 m, ASSUMED: pass the real mast "
        "height for radio). Returns clear or blocked; if blocked, the blocking point (x, y, distance from A) and "
        "the clearance deficit in metres (how far terrain pokes above the sight line); if clear, the tightest "
        "clearance; plus a profile summary. NOT modelled: Fresnel-zone clearance, antenna pattern, frequency, "
        "multipath, so 'clear' means optical line of sight, not a guaranteed radio link. Use viewshed to see "
        "everything visible from one place, find_vantage_points to find a spot that sees a target. "
        + units + " " + location_refs + " "
        'Example: line_of_sight(a="base", b="marker:m1", height_a=3, height_b=1.7)'))
    async def line_of_sight(a: Any, b: Any, height_a: float = 1.7, height_b: float = 1.7,
                            curvature: bool = True) -> Any:
        body = {"a": coerce(a), "b": coerce(b), "height_a": height_a, "height_b": height_b, "curvature": curvature}
        return await call("POST", "/visibility/line_of_sight", fmt_los, body={k: v for k, v in body.items() if v is not None})

    @tool("find_vantage_points", (
        "FIND A VANTAGE POINT OVERLOOKING X: candidate observer spots (on a 4 m lattice within max_distance_m, "
        "default 1500, of the target) ranked by the fraction of the target they can see, then by higher ground, "
        "then nearer; results are spread apart (spacing_m) and verified on the full 2 m terrain. `target` is a "
        "point / POI / marker, a region or spot set from find_regions / find_locations, or one set member; a region "
        "is represented by up to 12 spread sample points, so the fraction is a sample estimate. Prune the "
        "candidates with `conditions` (same language as find_locations, e.g. slope_2m <= 15 so someone can stand "
        "there) and `within` reachability (e.g. 30 min walk from base). observer_height_m (default 1.7 m) is "
        "assumed; min_distance_m keeps spots away from the target. Returns a set of spots (set_id) usable by "
        "plan_route / describe_location / nearest. A point target is seen or not (score 0 or 1). Use viewshed "
        "for 'what can I see from X', line_of_sight to check a specific pair. Geometric only (no lighting). "
        + conditions_text + " " + within_text + " " + units + " " + location_refs + " "
        'Example: find_vantage_points(target="2.1", conditions=[{"layer": "slope_2m", "op": "<=", "value": 15}], '
        'within={"from": "base", "max": 30, "metric": "walk_time_min"}, limit=3)'))
    async def find_vantage_points(target: Any, observer_height_m: float = 1.7, within: dict | None = None,
                                  conditions: list[dict] | None = None, limit: int = 5,
                                  target_height_m: float = 0.0, max_distance_m: float = 1500.0,
                                  min_distance_m: float = 0.0, spacing_m: float | None = None) -> Any:
        body = {"target": coerce(target), "observer_height_m": observer_height_m, "within": coerce(within),
                "conditions": coerce(conditions), "limit": limit, "target_height_m": target_height_m,
                "max_distance_m": max_distance_m, "min_distance_m": min_distance_m, "spacing_m": spacing_m}
        return await call("POST", "/visibility/vantage", fmt_vantage, body={k: v for k, v in body.items() if v is not None})
