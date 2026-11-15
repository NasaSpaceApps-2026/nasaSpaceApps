"""MCP tools: flood_at_level (paleolake), eva_envelope, check_eva_return (EVA safety).

Registered from mcp/server.py with `tools_lake.register(...)`. Formatters live here (pure functions over the API
dicts from devapi/routes_lake.py and routes_envelope.py) so format.py stays untouched.
"""
from __future__ import annotations

from typing import Any

from atlas_core.lake import NAMED_LEVELS, named_levels_text

_LAKE_LEVELS = ("Named levels (published, docs/lake-levels.md; elevations as printed in the sources, MOLA-type datum "
                "NOT stated by them, no offset applied): " + named_levels_text() + ".")
_LAKE_CAVEAT = ("PRESENT-DAY TOPOGRAPHY APPROXIMATION: today's surface is not the ancient lake floor (erosion, later "
                "deposition, the delta itself was built into the lake), so area and depth are not ancient bathymetry. "
                "Water = elevation <= level AND connected to the lake body (seeded at the lowest cell of the site, or "
                "at connected_to); isolated depressions are not flooded.")
_EVA_MODEL = ("Travel model: placeholder isotropic Tobler-shaped speed, approximate; out-and-back = 2 x one-way walk "
              "time. Profile values (speed, slope limits, EVA limit, reserve) are printed with their cited/assumed "
              "labels; they are team assumptions unless a citation is shown.")


def _n(v, nd=1):
    return "n/a" if v is None else f"{v:.{nd}f}"


def _values_line(values: dict) -> str:
    from format import source_label
    parts = []
    for k in ("flat_speed_mps", "max_slope_deg", "eva_limit_min", "reserve_fraction"):
        v = values.get(k)
        if v:
            parts.append(f"{k} {v['value']:g} {v['unit']} [{source_label(v['source'])}]")
    return "Profile values: " + "; ".join(parts) + "."


def fmt_flood(d: dict) -> str:
    """Text summary of flood_at_level: level, flooded area (km2), depths, what is underwater, plus the present-day-topography caveat."""
    lv = d["level"]
    name = f" ({lv['named_level']}, {lv['kind']})" if lv.get("named_level") else " (user-specified)"
    lines = [f"Lake at {d['level_m']:g} m{name}: area {_n(d['area_km2'], 2)} km2 "
             f"({d['fraction_of_site'] * 100:.0f}% of the site window)."]
    if d.get("depth_m"):
        lines.append(f"Depth max {_n(d['depth_m']['max'])} m, mean {_n(d['depth_m']['mean'])} m; deepest point at "
                     f"({d['deepest_point']['x']:.0f}, {d['deepest_point']['y']:.0f}) m, volume in window "
                     f"{d['volume_m3_in_window'] / 1e9:.2f} km3.")
    sh = d.get("shoreline")
    if sh:
        lines.append(f"Shoreline: {sh['segments']} segment(s), about {sh['approx_length_m'] / 1000:.1f} km; set "
                     f"{sh['set_id']} (regions); water body {d['water_set_id']}.")
    u = d["underwater"]
    if u["count"]:
        top = "; ".join(f"{x['id']} {x['depth_m']:.0f} m" for x in u["listed"][:5])
        types = ", ".join(f"{v} {k}" for k, v in u["by_type"].items())
        lines.append(f"Underwater: {u['count']} of the checked places ({types}); deepest {top}.")
    else:
        lines.append("Underwater: none of the checked places.")
    if u["above_water_count"]:
        a = u["nearest_above_water"][0]
        lines.append(f"{u['above_water_count']} stay dry; nearest to the waterline {a['id']} ({a['height_above_level_m']:.0f} m above).")
    if u["below_level_but_disconnected"]:
        lines.append(f"{len(u['below_level_but_disconnected'])} place(s) lie below the level but are not connected to the lake.")
    lines.append(f"Source of level: {lv['source']}.")
    lines += d["notes"]
    return "\n".join(lines)


def fmt_envelope(d: dict) -> str:
    """Text summary of eva_envelope: usable minutes, reachable area, limits and the isotropic-travel caveat."""
    lines = [f"EVA envelope from {d['base'].get('id')} (profile {d['profile']}): budget {d['budget_min']:g} min, "
             f"reserve {d['reserve_fraction'] * 100:.0f}%, usable {d['usable_min']:g} min"
             + (f", stops {d['stops_min']:g} min" if d["stops_min"] else "") + ".",
             _values_line(d["values"])]
    if not d.get("area_m2"):
        return "\n".join(lines + ["Nothing is reachable and recoverable. " + d.get("hint", "")])
    from format import dist_text, dur_text
    lines.append(f"Reachable and recoverable area {_n(d['area_km2'], 2)} km2; max one-way radius {d['max_one_way_radius_m']:.0f} m "
                 f"(walk {d['max_one_way_walk_min']:.0f} min); set {d['set_id']}."
                 + (f" One-way limit {d['one_way_limit_min']:g} min." if "one_way_limit_min" in d else ""))
    head = (f"Totals: budget {dur_text(d['budget_min'])}; usable after {d['reserve_fraction'] * 100:.0f}% reserve "
            f"{dur_text(d['usable_min'])}; max radius {dist_text(d['max_one_way_radius_m'])} one-way, "
            f"{dur_text(d['max_one_way_walk_min'])} one-way walk")
    if "one_way_limit_min" in d:
        rt = 2 * d["max_one_way_walk_min"]
        head += (f"; round trip (out and back) at the max radius {dur_text(rt)}; "
                 f"one-way time limit {dur_text(d['one_way_limit_min'])}")
    lines.insert(1, head + ".")
    far = [f"{s['sector']} {s['farthest_straight_m']:.0f} m" if s["reachable"] else f"{s['sector']} none" for s in d["sectors"]]
    lines.append("Farthest reach by direction: " + ", ".join(far) + ".")
    lines += d["notes"]
    return "\n".join(lines)


def fmt_check(d: dict) -> str:
    """Text summary of check_eva_return: verdict, walking times, budget/reserve and the point of no return (isotropic travel model)."""
    lines = [d["verdict"], _values_line(d["values"]),
             f"Route: {d['route_distance_m']:.0f} m, {d['route_walk_min']:.0f} min walking; havens: {d['havens']}; "
             f"budget {d['budget_min']:g} min, reserve {d['reserve_fraction'] * 100:.0f}%, usable {d['usable_min']:g} min."]
    from format import dist_text, dur_text
    last = (d.get("samples") or [None])[-1]
    stops = d.get("stops_min") or 0
    tot = (f"Totals: route {dist_text(d['route_distance_m'])}; one-way walk {dur_text(d['route_walk_min'])}")
    if last and last.get("walk_back_min") is not None:
        rt = last["elapsed_min"] + last["walk_back_min"]
        tot += (f"; round trip (out and back to the haven) {dur_text(rt)}"
                + (f" plus stops {dur_text(stops)}" if stops else "")
                + f"; budget {dur_text(d['budget_min'])}; usable after {d['reserve_fraction'] * 100:.0f}% reserve "
                f"{dur_text(d['usable_min'])}; margin at the route end {dur_text(d['usable_min'] - rt - stops)}")
    else:
        tot += (f"; budget {dur_text(d['budget_min'])}; usable after {d['reserve_fraction'] * 100:.0f}% reserve "
                f"{dur_text(d['usable_min'])}; no walkable path back from the route end")
    mm = d.get("min_margin")
    if d["ok"]:
        tot += (f"; point of no return: none (tightest margin {dur_text(mm['margin_min'])} at {dist_text(mm['distance_m'])} along)"
                if mm else "; point of no return: none")
    else:
        p0 = d["point_of_no_return"]
        tot += f"; point of no return at {dist_text(p0['distance_m'])} along the route (elapsed {dur_text(p0['elapsed_min'])})"
    lines.insert(1, tot + ".")
    if not d["ok"]:
        p, s = d["point_of_no_return"], d.get("last_safe_point")
        lines.append(f"Point of no return: ({p['x']:.0f}, {p['y']:.0f}) m, {p['distance_m']:.0f} m along, elapsed "
                     f"{p['elapsed_min']:.0f} min, walk back {_n(p['walk_back_min'], 0)} min.")
        if s:
            lines.append(f"Last safe point: ({s['x']:.0f}, {s['y']:.0f}) m, {s['distance_m']:.0f} m along, margin "
                         f"{_n(s['margin_min'], 0)} min.")
    lines += d["notes"][:2]
    return "\n".join(lines)


def register(tool, call, coerce, location_refs: str, schema_patch: dict, loc) -> None:
    """Register flood_at_level, eva_envelope and check_eva_return on the MCP server using its tool decorator, request helper and shared schema fragments."""
    schema_patch["flood_at_level"] = {
        "named_level": {"enum": list(NAMED_LEVELS)},
        "connected_to": loc("place(s) whose basin is flooded (default: the lowest cell of the site)", many=True),
        "check": loc("places or a set to test for being underwater (default: all POIs)", many=True)}
    schema_patch["eva_envelope"] = {"base": loc("departure place (default: the base)"),
                                    "havens": loc("optional safe havens (rover, shelter): the return leg goes to the nearest", many=True)}
    schema_patch["check_eva_return"] = {"havens": loc("optional safe havens; default: the route start", many=True)}

    @tool("flood_at_level", (
        "Ancient-lake reconstruction: flood today's topography to a lake level and report the water area, depth, "
        "shoreline (a regions set) and which POIs, sample sites and named regions would have been underwater and at "
        "what depth. Give EITHER level_m (metres, same datum as the elevation layer) OR named_level from the published "
        "levels. Use for 'was the landing site underwater', 'where was the shoreline at the delta level', 'how deep was "
        "the lake over Roubion'. " + _LAKE_LEVELS + " " + _LAKE_CAVEAT + " " + location_refs +
        ' Example: flood_at_level(named_level="delta_top_2410") or flood_at_level(level_m=-2500, check="poi:base")'))
    async def flood_at_level(level_m: float | None = None, named_level: str | None = None,
                             connected_to: Any = None, check: Any = None):
        body = {"level_m": level_m, "named_level": named_level, "connected_to": coerce(connected_to),
                "check": coerce(check)}
        return await call("POST", "/lake/flood", fmt_flood, body={k: v for k, v in body.items() if v is not None})

    @tool("eva_envelope", (
        "EVA safety envelope: every place an astronaut can walk to from the base AND walk back from within the EVA time "
        "budget minus the reserve (out + back + stops <= budget x (1 - reserve)). Returns the reachable region (a "
        "regions set), area, max one-way radius and the farthest reachable point in each of 8 compass sectors, with the "
        "profile values labelled cited/assumed. Optional havens (a rover or shelter at a location) make the return leg "
        "go to the nearest haven. Defaults: the eva_walkable profile's EVA limit and reserve. Use for 'how far can "
        "the crew go in an 8 h EVA with a 20% reserve'. " + _EVA_MODEL + " " + location_refs +
        ' Example: eva_envelope(base="base", budget_min=240, reserve_fraction=0.25)'))
    async def eva_envelope(base: Any = "base", budget_min: float | None = None, reserve_fraction: float | None = None,
                           profile: str = "eva_walkable", stops_min: float = 0.0, havens: Any = None):
        body = {"base": coerce(base), "budget_min": budget_min, "reserve_fraction": reserve_fraction,
                "profile": profile, "stops_min": stops_min, "havens": coerce(havens)}
        return await call("POST", "/eva/envelope", fmt_envelope, body={k: v for k, v in body.items() if v is not None})

    @tool("check_eva_return", (
        "Walk-back safety check for an EVA plan: for each point of a route (route_id from plan_route / "
        "plan_science_traverse, or points that are routed on foot), compares elapsed time plus the walk back to the "
        "nearest haven (default: the route start) with the budget minus reserve, and flags the first point where the "
        "margin goes below the reserve (the point of no return) and the last safe point. Call it BEFORE telling the "
        "user an EVA route is safe. It checks the point of no return only; it does not replace assess_route (slope and "
        "feasibility GO/NO-GO). " + _EVA_MODEL + " " + location_refs +
        ' Example: check_eva_return(route_id="route-3", budget_min=480) or check_eva_return(points=["base", "poi:sample_10_swift_run"])'))
    async def check_eva_return(route_id: str | None = None, points: Any = None, profile: str = "eva_walkable",
                               budget_min: float | None = None, reserve_fraction: float | None = None,
                               stops_min: float = 0.0, havens: Any = None):
        body = {"route_id": route_id, "points": coerce(points), "profile": profile, "budget_min": budget_min,
                "reserve_fraction": reserve_fraction, "stops_min": stops_min, "havens": coerce(havens)}
        return await call("POST", "/eva/check", fmt_check, body={k: v for k, v in body.items() if v is not None})
