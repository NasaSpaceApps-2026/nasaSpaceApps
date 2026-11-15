"""MCP tools for Mars sun position and terrain illumination: sun_position, illumination_at, find_lit_windows.

Registered from mcp/server.py with `tools_sun.register(...)`. Formatters live here (pure functions over the API
dicts from devapi/routes_sun.py) so format.py stays untouched.
"""
from __future__ import annotations

from typing import Any

SUN_TIME = (
    "Time: give EITHER utc (ISO 8601 like \"2026-03-01T10:00:00Z\", or omit/\"now\" for the current time) OR a "
    "mission sol plus ltst. LTST = local TRUE solar time (the apparent sun: 12:00 = sun on the meridian, "
    "clock 0-24 h, \"HH:MM\" or decimal hours) and is what sun-angle questions use; LMST = local MEAN solar time "
    "(uniform clock, differs from LTST by the equation of time, up to about 50 min) and is what mission clocks "
    "show. A sol is a Mars day (24 h 39.6 min). Sol numbering = Mars 2020 mission sol: sol 0 = touchdown sol "
    "(2021-02-18), a sol starts at local mean midnight at the landing site (77.45 E); with ltst but no sol the "
    "current sol is used. ")
SUN_UNITS = ("Units: angles in degrees; elevation 0 = horizon, 90 = overhead; azimuth clockwise from north (90 east, "
             "180 south, 270 west); incidence angle = angle between the sun and the surface normal (0 = facing the "
             "sun, 90 = edge-on, >90 = facing away); times in UTC and local solar time. ")
SUN_MODEL = ("Model (assumptions): geometric direct sun from the Mars24 algorithm (Allison & McEwen 2000, NASA GISS) "
             "on the 2 m DEM, sun disc treated as a point; NOT modelled: atmospheric refraction, dust scattering "
             "or twilight (Mars is not dark at sunset: dusty twilight lasts roughly 2 h, so 'shadow' here means no "
             "direct sun, not darkness), terrain beyond the site edge, boulders below DEM resolution, reflected light.")


def _n(v, nd=1):
    return "n/a" if v is None else f"{v:.{nd}f}"


def fmt_sun_position(d: dict) -> str:
    """Text summary of sun_position: elevation/azimuth (degrees), local true solar time, Ls and season, sunrise and sunset."""
    ss = d.get("sunrise_sunset") or {}
    el = d["sun_elevation_deg"]
    state = "sun up" if el > 0 else "sun BELOW horizon (night/twilight)"
    lines = [
        f"Sun at {_n(d['site_lat_planetocentric_deg'], 3)} N, {_n(d['site_lon_east_deg'], 3)} E on {d['utc']}: "
        f"elevation {_n(el)} deg, azimuth {_n(d['sun_azimuth_deg'])} deg ({state}).",
        f"Local true solar time (LTST) {d['ltst']}; local mean solar time (LMST) {d['lmst']}; mission sol "
        f"{d['mission_sol']}; Ls {_n(d['ls_deg'])} deg ({d['season']}).",
        f"Equation of time {_n(d['equation_of_time_min'])} min; subsolar point {_n(d['subsolar_lat_planetocentric_deg'])} "
        f"deg lat (planetocentric), {_n(d['subsolar_lon_east_deg'])} E; MSD {_n(d['msd'], 4)}."]
    if ss.get("sunrise") or ss.get("sunset") or ss.get("note"):
        r, s = ss.get("sunrise"), ss.get("sunset")
        lines.append("This sol: " + (f"sunrise {r['ltst']} LTST ({r['utc']}), " if r else "no sunrise, ")
                     + (f"sunset {s['ltst']} LTST ({s['utc']})" if s else "no sunset")
                     + (f", day length {_n(ss['day_length_ltst_hours'])} h" if ss.get("day_length_ltst_hours") else "")
                     + (f". {ss['note']}" if ss.get("note") else "") + " (sun centre at the geometric horizon).")
    lines.append("Assumptions: geometric sun centre, no refraction/dust scattering; Mars24 (cited). "
                 "Next: illumination_at(at=...) for terrain shadows, find_lit_windows for imaging windows.")
    return "\n".join(lines)


def fmt_illumination(d: dict) -> str:
    """Text summary of illumination_at: sun angles, incidence angle, lit or shadowed (cast/self), approximations stated."""
    t, s = d["time"], d["sun"]
    head = (f"At {t['utc']} (sol {t['mission_sol']}, {t['ltst']} LTST, {t['lmst']} LMST) the sun is at elevation "
            f"{_n(s['elevation_deg'])} deg, azimuth {_n(s['azimuth_deg'])} deg.")
    if d["status"] == "sun_below_horizon":
        return head + "\nSun is below the horizon: nothing is in direct sun (lit fraction 0). Not modelled: twilight glow.\nNext: pick a daytime ltst."
    out = [head]
    if "locations" in d:
        locs = d["locations"]
        if len(locs) == 1:
            l = locs[0]
            if l.get("lit_fraction") is None:
                out.append("No DEM data at this location (unknown, not shadowed).")
            else:
                out.append(f"{l.get('id') or 'location'} ({l['kind']}): {d.get('verdict', '').upper() or 'lit'}; lit "
                           f"{l['lit_fraction'] * 100:.0f}%, cast shadow {l['cast_shadow_fraction'] * 100:.0f}%, "
                           f"self-shadowed (faces away) {l['self_shadow_fraction'] * 100:.0f}%; incidence "
                           f"{_n(l['incidence_deg_mean'])} deg (range {_n(l['incidence_deg_min'])} to {_n(l['incidence_deg_max'])}).")
        else:
            out.append(f"{len(locs)}{'+' if d.get('truncated') else ''} locations, overall lit fraction "
                       + (f"{d['lit_fraction'] * 100:.0f}%" if d.get("lit_fraction") is not None else "n/a")
                       + f" over {d.get('sampled_cells', 0)} sampled cells, mean incidence {_n(d.get('incidence_deg_mean'))} deg.")
            for l in locs[:6]:
                out.append(f"- {l.get('id')}: lit {_n(None if l.get('lit_fraction') is None else l['lit_fraction'] * 100, 0)}%, "
                           f"incidence {_n(l.get('incidence_deg_mean'))} deg")
    else:
        c = d["cells"]
        out.append(f"Whole site: {d['lit_fraction'] * 100:.1f}% of known cells lit, {d['cast_shadow_fraction'] * 100:.1f}% in cast shadow, "
                   f"{d['self_shadow_fraction'] * 100:.1f}% self-shadowed ({c['unknown']} unknown cells).")
        if d.get("set_id"):
            out.append(f"Stored as {d['set_id']} ({d['set_count']} regions; attributes.illumination_class = lit | cast_shadow | self_shadow).")
    out.append("Assumptions: geometric direct sun on 2 m DEM; no scattering/twilight, no terrain beyond the site edge, sun as a point.")
    out.append("Next: find_lit_windows(target=...) for when it is lit; set_view(highlights=[set]) to show shadows.")
    return "\n".join(out)


def fmt_lit_windows(d: dict) -> str:
    """Text summary of find_lit_windows: lit time windows (UTC and LTST) with durations in minutes."""
    q = d["query"]
    cond = f"sun elevation >= {q['min_elevation_deg']:g} deg" + (
        f", incidence <= {q['max_incidence_deg']:g} deg" if q.get("max_incidence_deg") is not None else "") + \
        f", lit fraction >= {q['min_lit_fraction']:g}"
    if not d["windows"]:
        return (f"No lit window between {q['utc_from']} and {q['utc_to']} ({q['ltst_from']} to {q['ltst_to']} LTST) with {cond}; "
                f"best lit fraction {d['peak_lit_fraction']:.2f}.\nHint: {d['hint']}")
    out = [f"{d['count']} lit window(s), {d['total_lit_minutes']:.0f} min total, between {q['utc_from']} and {q['utc_to']} "
           f"with {cond} (step {q['step_min']:g} min, edges refined to about 1 s):"]
    for w in d["windows"][:8]:
        clip = " (clipped by the search range)" if w["clipped_start"] or w["clipped_end"] else ""
        out.append(f"- sol {w['mission_sol']}: {w['start_ltst']} to {w['end_ltst']} LTST = {w['start_utc']} to {w['end_utc']} UTC, "
                   f"{w['duration_min']:g} min, sun elevation {_n(w['sun_elevation_deg_start'])} to peak "
                   f"{_n(w['sun_elevation_deg_max'])} deg{clip}")
    if d["count"] > 8:
        out.append(f"... {d['count'] - 8} more in structuredContent.")
    out.append("Assumptions: geometric direct sun on 2 m DEM, no terrain beyond the site edge, no scattering/twilight; "
               "sun angle taken at the site centre. LTST = local true solar time.")
    return "\n".join(out)


def register(tool, call, coerce, location_refs: str, schema_patch: dict, loc) -> None:
    """Register sun_position, illumination_at and find_lit_windows on the MCP server using its tool decorator, request helper and shared schema fragments."""
    schema_patch["sun_position"] = {"at": loc("one place (default: the site centre)")}
    schema_patch["illumination_at"] = {"at": loc("place, footprint (with radius_m) or set to check; omit for the whole site")}
    schema_patch["find_lit_windows"] = {"target": loc("place, footprint (with radius_m), region or set to image", many=True)}

    @tool("sun_position", (
        "Sun elevation and azimuth at a place on Mars at a given time, plus local true and mean solar time, mission "
        "sol, Ls (season), equation of time, subsolar point and sunrise/sunset for that sol. Use for 'what is the "
        "local time at Jezero now', 'how high is the sun at 16:00', 'when is sunset'. Defaults: the site centre, now. "
        "No terrain: use illumination_at for shadows. " + SUN_TIME + SUN_UNITS + SUN_MODEL + " " + location_refs +
        ' Example: sun_position(sol=1200, ltst="16:00") or sun_position()'))
    async def sun_position(at: Any = None, lat: float | None = None, lon: float | None = None, utc: str | None = None,
                           sol: int | None = None, ltst: Any = None):
        body = {"at": coerce(at), "lat": lat, "lon": lon, "utc": utc, "sol": sol, "ltst": ltst}
        return await call("POST", "/sun/position", fmt_sun_position, body={k: v for k, v in body.items() if v is not None})

    @tool("illumination_at", (
        "Is a place sunlit or in shadow at a given time? Returns lit / cast-shadow / self-shadow fractions and "
        "incidence angle for a point, a footprint (radius_m, e.g. a landing pad), a region or a set, using the 2 m "
        "DEM: slope/aspect for incidence and ray-cast terrain shadows along the sun azimuth. With no `at`, "
        "computes the whole-site illumination mask and stores it as a regions set (members tagged lit, "
        "cast_shadow, self_shadow) for set_view. Use for 'is this pad in shadow at 16:00 LTST?'. "
        + SUN_TIME + SUN_UNITS + SUN_MODEL + " " + location_refs +
        ' Example: illumination_at(at="poi:base", radius_m=10, sol=1200, ltst="16:00")'))
    async def illumination_at(at: Any = None, utc: str | None = None, sol: int | None = None, ltst: Any = None,
                              radius_m: float | None = None):
        body = {"at": coerce(at), "utc": utc, "sol": sol, "ltst": ltst, "radius_m": radius_m}
        return await call("POST", "/sun/illumination", fmt_illumination, body={k: v for k, v in body.items() if v is not None})

    @tool("find_lit_windows", (
        "When is a target lit for imaging? Scans a time range at step_min and returns the windows (start/end in "
        "local true solar time and UTC, edges refined to about 1 s) when the target is in direct sun with sun "
        "elevation >= min_elevation_deg (default 10) and, optionally, incidence <= max_incidence_deg and at least "
        "min_lit_fraction of its cells lit (default 0.9 for regions/footprints, 1.0 for a point). Give either a "
        "sol with ltst_from/ltst_to (default: the whole sol; with no sol the current one) or utc_from/utc_to "
        "(default: next sol). Max 3000 steps. Targets: a point, a footprint (radius_m), a region or a set (up to "
        "400 cells sampled per member). Use for 'when is this outcrop lit for imaging?'. " + SUN_TIME + SUN_UNITS
        + SUN_MODEL + " " + location_refs +
        ' Example: find_lit_windows(target="poi:base", radius_m=20, sol=1200, min_elevation_deg=15, max_incidence_deg=60)'))
    async def find_lit_windows(target: Any, utc_from: str | None = None, utc_to: str | None = None,
                               sol: int | None = None, ltst_from: Any = None, ltst_to: Any = None,
                               step_min: float | None = None, min_elevation_deg: float | None = None,
                               max_incidence_deg: float | None = None, radius_m: float | None = None,
                               min_lit_fraction: float | None = None):
        body = {"target": coerce(target), "utc_from": utc_from, "utc_to": utc_to, "sol": sol, "ltst_from": ltst_from,
                "ltst_to": ltst_to, "step_min": step_min, "min_elevation_deg": min_elevation_deg,
                "max_incidence_deg": max_incidence_deg, "radius_m": radius_m, "min_lit_fraction": min_lit_fraction}
        return await call("POST", "/sun/lit_windows", fmt_lit_windows, body={k: v for k, v in body.items() if v is not None})
