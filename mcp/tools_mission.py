"""MCP tools for the astronaut in the middle of an EVA: eva_start, eva_end, eva_status, return_route, nearest_haven,
check_path_ahead, comm_check, log_sample, list_samples, bearing_to, rendezvous, daylight_check.

Registered from mcp/server.py with `tools_mission.register(...)`. Each tool is one call to /mission/* on the query API
(devapi/routes_mission.py, logic in atlas_core/mission.py). Results are plain language, headline first with the key numbers,
assumptions labelled, and the full numbers in structuredContent. "me"/"here" in any place argument is the crew position.
See docs/mission-tools.md.
"""
from __future__ import annotations

import re
from typing import Any

from format import dist_text, dur_text, num

CREW = ('Positions: "me"/"here" = where the crew is now; also "base", "the rover" (= the vehicle left at the start of the EVA, '
        'NOT the crew), "marker:<id>", a place name ("the delta", "Seitah") or {x, y} in site metres.')
UNITS = "Units: minutes, metres, degrees; headings are degrees clockwise from north (0 north, 90 east)."
CLOCK = ("The EVA clock is the simulated mission clock kept by the server (started by eva_start); walking is not clocked "
         "automatically, so elapsed time is what the clock says.")

_COMPASS = {"n": 0, "north": 0, "nne": 22.5, "ne": 45, "northeast": 45, "ene": 67.5, "e": 90, "east": 90, "ese": 112.5,
            "se": 135, "southeast": 135, "sse": 157.5, "s": 180, "south": 180, "ssw": 202.5, "sw": 225, "southwest": 225,
            "wsw": 247.5, "w": 270, "west": 270, "wnw": 292.5, "nw": 315, "northwest": 315, "nnw": 337.5}


def _m(v) -> str:
    """Minutes, for text: 1 decimal below 10, whole numbers above."""
    if v is None:
        return "n/a"
    return f"{v:.1f} min" if abs(v) < 10 else f"{v:.0f} min"


def _loose_heading(h) -> tuple[float | None, str | None]:
    if h is None:
        return None, None
    if isinstance(h, (int, float)) and not isinstance(h, bool):
        return float(h), None
    key = re.sub(r"[^a-z]", "", str(h).lower())
    if key in _COMPASS:
        return float(_COMPASS[key]), None
    try:
        return float(str(h).replace("°", "").replace("deg", "").strip()), None
    except ValueError:
        return None, f'I could not read the heading "{h}". Use degrees (0 = north, 90 = east) or a compass point like "north-east".'


def _loose_dist(d) -> tuple[float | None, str | None]:
    if d is None:
        return None, None
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        return float(d), None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*(km|kilomet\w*|m|met\w*)?\s*$", str(d), re.I)
    if not m:
        return None, f'I could not read the distance "{d}". Use metres (300) or "0.3 km".'
    return float(m.group(1)) * (1000.0 if (m.group(2) or "").lower().startswith("k") else 1.0), None


def _haven_line(h: dict) -> str:
    wt = "no walkable path" if h["walk_min"] is None else f"{_m(h['walk_min'])} walk"
    return f"{h['name']}: {wt}, {dist_text(h['straight_m'])} {h['direction']} (heading {num(h['heading_deg'], 0)} deg)"


def _daylight_text(d: dict) -> str:
    if not d.get("sun_up"):
        sr = d.get("minutes_to_sunrise")
        return (f"The sun is down (elevation {num(d['sun_elevation_deg'])} deg)" + (f", back up in about {_m(sr)}" if sr else "")
                + ". Mars keeps some dusty twilight glow, but there is no direct sun.")
    bits = [f"Sun {num(d['sun_elevation_deg'])} deg above the horizon"]
    if d.get("minutes_to_sunset") is not None:
        bits.append(f"sunset in {_m(d['minutes_to_sunset'])} (at {d['sunset_ltst'][:5]} local solar time)")
    if d.get("minutes_until_shaded") is not None:
        if d["minutes_until_shaded"] <= 0:
            bits.append("this spot is already in shadow")
        else:
            bits.append(f"this spot falls into shade in {_m(d['minutes_until_shaded'])} ({d['shade_cause']})")
    return ", ".join(bits) + "."


# ---- formatters -----------------------------------------------------------------------------------

def fmt_eva_start(d: dict) -> str:
    hv = "; ".join(f"{h['name']} at ({h['x']:.0f}, {h['y']:.0f})" for h in d["havens"])
    out = [f"EVA started: budget {dur_text(d['budget_min'])}, reserve {d['reserve_fraction'] * 100:.0f}%, so {dur_text(d['usable_min'])} "
           f"usable before you must be back at a haven."
           + (f" Counting {_m(d['already_out_min'])} already spent outside." if d.get("already_out_min") else ""),
           ("Note: an EVA was already running; this replaced it and restarted the clock (use eva_status to read the running one)."
            if d.get("replaced_running_eva") else ""),
           f"Havens (safe places to retreat to): {hv}.",
           f"Crew at ({d['crew']['x']:.0f}, {d['crew']['y']:.0f})" + (" (moved there)." if d.get("moved_crew") else "."),
           "Assumptions: " + d["assumptions"][0] + " " + CLOCK,
           "Next: eva_status for time left and walk-back margin."]
    return "\n".join(x for x in out if x)


def fmt_eva_end(d: dict) -> str:
    return (f"EVA ended after {_m(d['elapsed_min'])} of a {dur_text(d['budget_min'])} budget "
            f"({'within' if d['within_budget'] else 'OVER'} budget, {'inside' if d['within_reserve'] else 'into'} the reserve). "
            f"Nearest haven: {_haven_line(d['nearest_haven'])}{'; you are at it' if d['at_haven'] else '; you are NOT at a haven'}. "
            f"{d['samples_logged']} sample(s) logged.")


def fmt_status(d: dict) -> str:
    nh = d["nearest_haven"]
    wb = d["walk_back_min"]
    place = d.get("nearest_place")
    where = (f"about {dist_text(place['distance_m'])} {place['direction_from_place']} of {place['name']}" if place
             else f"at ({d['crew']['x']:.0f}, {d['crew']['y']:.0f})")
    head = (f"{d['status']}: {d['status_reason']}. "
            + (f"Walk back to {nh['name']} takes {_m(wb)} ({dist_text(nh['straight_m'])} {nh['direction']}); " if wb is not None
               else f"No walkable path back to {nh['name']}; ")
            + (f"margin above the reserve {_m(d['margin_min'])}." if d["margin_min"] is not None else ""))
    out = [head,
           f"Time: {_m(d['elapsed_min'])} out of {dur_text(d['budget_min'])}; {_m(d['remaining_min'])} left on the clock, "
           f"{_m(d['remaining_usable_min'])} of that before the {d['reserve_fraction'] * 100:.0f}% reserve starts."
           + (f" You could walk about {_m(d['turn_back_in_min'])} farther out before you must turn back." if d["margin_min"] and d["margin_min"] > 0 else ""),
           f"You are {where}.",
           "Light: " + _daylight_text(d["daylight"])]
    if len(d["havens"]) > 1:
        out.append("Havens: " + "; ".join(_haven_line(h) for h in d["havens"]) + ".")
    out.append("Assumptions: " + d["assumptions"][0])
    return "\n".join(out)


def fmt_havens(d: dict) -> str:
    hv = d["havens"]
    place = d.get("nearest_place")
    out = [f"Nearest haven: {_haven_line(hv[0])}."]
    if len(hv) > 1:
        out.append("Others: " + "; ".join(_haven_line(h) for h in hv[1:]) + ".")
    if place:
        out.append(f"You are about {dist_text(place['distance_m'])} {place['direction_from_place']} of {place['name']}.")
    if not d.get("eva_active"):
        out.append("No EVA clock is running, so only the base is counted as a haven (call eva_start to add the rover and track time).")
    out.append("Assumptions: " + d["assumptions"][0])
    return "\n".join(out)


def _leg_text(legs: list[dict]) -> str:
    parts = []
    for i, l in enumerate(legs):
        t = l.get("turn_deg")
        turn = "" if t is None or abs(t) < 10 else (f"turn {'right' if t > 0 else 'left'} {abs(t):.0f} deg, " if i else "")
        parts.append(f"{turn}{'head' if i == 0 else 'go'} {l['direction']} (heading {l['heading_deg']:.0f} deg) for {dist_text(l['length_m'])}")
    return "; then ".join(parts)


def fmt_return_route(d: dict) -> str:
    if not d.get("feasible"):
        extra = f" Nearest reachable ground: ({d['nearest_reachable']['x']:.0f}, {d['nearest_reachable']['y']:.0f})." if d.get("nearest_reachable") else ""
        return f"No safe walking route: {d.get('reason')}{extra}\nTry: {d.get('hint') or 'check the ground around you with check_path_ahead.'}"
    out = [f"Route to {d['goal_name']}: {dur_text(d['duration_min'])} on foot, {dist_text(d['distance_m'])} of walking, steepest slope "
           f"{num(d['max_slope_deg'])} deg (walking limit {num(d['slope_limit_deg'])} deg).",
           "Legs: " + _leg_text(d["legs"]) + "."]
    if "margin_at_arrival_min" in d:
        out.append(f"Clock: {_m(d['elapsed_min'])} out; you would arrive at {_m(d['arrive_elapsed_min'])}, "
                   f"{_m(abs(d['margin_at_arrival_min']))} {'before' if d['margin_at_arrival_min'] >= 0 else 'AFTER'} the reserve line "
                   f"({_m(d['usable_min'])}). Point of no return: {d['point_of_no_return']}.")
    else:
        out.append("No EVA clock is running (eva_start), so no budget check.")
    for w in d.get("warnings") or []:
        out.append("Warning: " + w)
    out.append("Assumptions: " + d["assumptions"][0])
    return "\n".join(out)


def fmt_path_ahead(d: dict) -> str:
    out = [f"Heading {d['direction']} ({d['heading_deg']:.0f} deg) for {dist_text(d['distance_m'])}: walk time about {_m(d['walk_min'])}, "
           f"max slope {num(d['slope']['max_deg'])} deg (mean {num(d['slope']['mean_deg'])} deg; caution above "
           f"{num(d['slope']['caution_deg'])}, no-go above {num(d['slope']['no_go_deg'])})."]
    fb, fh = d.get("first_blocker"), d.get("first_hazard")
    if fb:
        out.append(f"BLOCKED at {dist_text(fb['distance_m'])}: {fb['kind']}" + (f" ({num(fb['slope_deg'])} deg)" if fb.get("slope_deg") is not None else "")
                   + ". You cannot walk this line past that point.")
    if fh and fh is not fb and (not fb or fh["distance_m"] < fb["distance_m"]):
        out.append(f"First hazard at {dist_text(fh['distance_m'])}: {fh['kind']}" + (f" ({num(fh['slope_deg'])} deg)" if fh.get("slope_deg") is not None else "") + ".")
    if not fh:
        out.append("No hazard found along the line (slope, roughness and data coverage).")
    if d.get("roughness_max_m") is not None:
        out.append(f"Roughness up to {num(d['roughness_max_m'], 2)} m (rough above {num(d['roughness_caution_m'], 1)} m). "
                   f"Unknown ground: {d['unknown_fraction'] * 100:.0f}% of the line.")
    if d["sun_up"]:
        sh = d["shaded_fraction"] or 0.0
        out.append(f"Light: {sh * 100:.0f}% of the line is in shadow now" + (f", first shadow at {dist_text(d['first_shadow_at_m'])}" if d.get("first_shadow_at_m") is not None else "") + ".")
    else:
        out.append("Light: the sun is down, so the whole line is dark.")
    if d.get("edge_of_map_at_m") is not None:
        out.append(f"The mapped area ends at {dist_text(d['edge_of_map_at_m'])}.")
    if "margin_at_end_min" in d:
        pn = d.get("point_of_no_return_m")
        out.append((f"EVA margin: {_m(d['margin_at_end_min'])} at the end of this line (negative = short of the reserve)"
                    + (f"; you pass the point of no return at {dist_text(pn)} along it." if pn is not None else "; you stay above the reserve all the way.")))
    out.extend(d.get("notes") or [])
    out.append("Assumptions: " + d["assumptions"][0])
    return "\n".join(out)


def fmt_comm(d: dict) -> str:
    base = f"{d['to_name']}, {dist_text(d['distance_m'])} {d['direction_to']}"
    if d["clear"]:
        return (f"Radio line of sight to {base}: CLEAR (tightest clearance {num(d.get('min_clearance_m'), 1)} m).\n"
                "Assumptions: " + d["assumptions"][0])
    bp = d.get("blocking_point") or {}
    out = [f"Radio line of sight to {base}: BLOCKED by terrain about {dist_text(bp.get('distance_from_a_m'))} from you "
           f"(ground {num(d.get('clearance_deficit_m'), 1)} m above the sight line)."]
    r = d.get("restore")
    if r:
        out.append(f"Nearest spot that restores line of sight: {dist_text(r['walk_m'])} of walking {r['direction']} (heading {r['heading_deg']:.0f} deg), "
                   f"about {_m(r['walk_min'])}.")
    else:
        out.append(d.get("hint") or "No nearby spot restores line of sight.")
    out.append("Assumptions: " + d["assumptions"][0])
    return "\n".join(out)


def fmt_sample(d: dict) -> str:
    mins = [m for m in d["minerals"] if m["tier"] and m["tier"] >= 1]
    ev = ", ".join(f"{m['mineral']}: {m['tier_name'].replace('_', ' ')}" for m in mins) or "nothing detected from orbit here"
    return (f"Logged sample {d['n']}: \"{d['note']}\" at ({d['x']:.0f}, {d['y']:.0f}), "
            + (f"{_m(d['elapsed_min'])} into the EVA, " if d.get("elapsed_min") is not None else "")
            + f"local solar time {d['ltst'][:5]}. Map unit: {d['unit'] or 'unknown'}; mineral evidence: {ev}. Marker {d['marker_id']} placed.\n"
            "Assumptions: " + d["assumptions"][0])


def fmt_samples(d: dict) -> str:
    if not d["count"]:
        return "No samples logged yet. Use log_sample(note=...) at the spot."
    lines = [f"{d['count']} sample(s) logged:"]
    for s in d["samples"]:
        mins = ", ".join(m["mineral"] for m in s["minerals"] if m["tier"] and m["tier"] >= 1) or "no mineral evidence"
        lines.append(f"- Sample {s['n']}: \"{s['note']}\" at ({s['x']:.0f}, {s['y']:.0f}), unit {s['unit'] or 'unknown'}, {mins}"
                     + (f", {_m(s['elapsed_min'])} into the EVA" if s.get("elapsed_min") is not None else ""))
    return "\n".join(lines)


def fmt_bearing(d: dict) -> str:
    walk = (f"walking about {_m(d['walk_min'])} ({dist_text(d['walk_m'])} of path, steepest {num(d['max_slope_deg'])} deg)"
            if d.get("walk_min") is not None else f"no safe walking path ({d.get('walk_problem')})")
    return (f"{d['target_name']} is {d['direction']}, heading {d['heading_deg']:.0f} deg, {dist_text(d['straight_m'])} in a straight line; {walk}.\n"
            "Assumptions: " + d["assumptions"][0])


def fmt_rendezvous(d: dict) -> str:
    if not d.get("feasible"):
        return f"No meeting point: {d.get('reason')}"
    m = d["meeting_point"]
    out = [f"Best meeting point: ({m['x']:.0f}, {m['y']:.0f}), {dist_text(m['straight_m_from_crew'])} {m['direction']} of you "
           f"(heading {m['heading_from_crew_deg']:.0f} deg). You walk {_m(m['crew_walk_min'])}, the rover drives {_m(m['rover_drive_min'])}; "
           f"whoever arrives first waits about {_m(m['wait_min'])}."]
    for c in d["candidates"][1:]:
        out.append(f"Alternative, {c['label']}: you {_m(c['crew_walk_min'])}, rover {_m(c['rover_drive_min'])}.")
    out.append(f"Rover assumed to drive as the {d['rover_profile'].replace('_', ' ')} (speed from its profile). Method: " + d["method"])
    return "\n".join(out)


def fmt_daylight(d: dict) -> str:
    return _daylight_text(d) + f" Local solar time {d['ltst'][:5]}.\nAssumptions: " + d["note"]


# ---- registration ---------------------------------------------------------------------------------

def register(tool, call, coerce, schema_patch: dict, loc, act, err) -> None:
    """Register the mid-mission tools on the MCP server (tool decorator, request helper, shared schema fragments)."""
    schema_patch["eva_start"] = {"base": loc("the base / home haven (default: the site base)"),
                                 "crew_at": loc("where the crew actually is now (moves the crew there; default: where they are)"),
                                 "havens": loc("extra safe places (shelters) besides the base and the rover", many=True)}
    schema_patch["return_route"] = {"to": loc("where to go (default: the nearest haven)")}
    schema_patch["check_path_ahead"] = {"toward": loc("a place to look toward instead of a heading")}
    schema_patch["comm_check"] = {"to": loc("who to talk to (default: the base)")}
    schema_patch["log_sample"] = {"at": loc("where the sample is (default: where the crew is)")}
    schema_patch["bearing_to"] = {"target": loc("place to head for")}
    schema_patch["rendezvous"] = {"rover": loc("where the rover is (default: where it was left at the start of the EVA)")}
    schema_patch["daylight_check"] = {"at": loc("spot to check (default: where the crew is)")}

    @tool("eva_start", (
        "Start the EVA clock and safety budget for the crew (an astronaut outside on Mars). Havens = the base + the rover where "
        "it was left + any shelters. Defaults: base, 480 min budget and 20% reserve from the walking profile. Set already_out_min "
        "if the crew has been out for a while. crew_at moves the crew (simulation). Call it once at the start; the other mission "
        "tools work without it but give no time budget. " + CLOCK + " " + CREW + " " + UNITS +
        " Example: eva_start(budget_min=480, reserve=0.2)"), act)
    async def eva_start(base: Any = None, budget_min: float | None = None, reserve: float | None = None, crew_at: Any = None,
                        havens: Any = None, already_out_min: float | None = None):
        body = {"base": coerce(base), "budget_min": budget_min, "reserve": reserve, "crew_at": coerce(crew_at),
                "havens": coerce(havens), "already_out_min": already_out_min}
        return await call("POST", "/mission/eva/start", fmt_eva_start, body={k: v for k, v in body.items() if v is not None})

    @tool("eva_end", ("End the EVA: elapsed time against the budget, whether the crew is at a haven, samples logged. "
                      "Example: eva_end()"), act)
    async def eva_end():
        return await call("POST", "/mission/eva/end", fmt_eva_end, body={})

    @tool("eva_status", (
        "The astronaut's situation right now: GO / CAUTION / RETURN NOW, time elapsed and left, the walk-back time to the nearest "
        "haven and the margin above the reserve, the nearest named place, minutes to sunset and until this spot is shaded. "
        "Use for 'how much time do I have', 'am I OK', 'status', 'status check'. Needs eva_start (call it first with defaults if no EVA is "
        "running; the clock then starts at zero, so say so, or ask for already_out_min). Rule: margin = budget x (1 - reserve) - "
        "elapsed - walk back; RETURN NOW at 0 or less, CAUTION under 30 min (assumed). The budget is TIME only: oxygen and power are not "
        "modelled or tracked, and the limit is a safety limit that the assistant does not raise to let someone keep going. " + CLOCK + " " + UNITS + " Example: eva_status()"))
    async def eva_status():
        return await call("POST", "/mission/status", fmt_status, body={})

    @tool("return_route", (
        "Fastest safe walking route from the crew to the nearest haven (or to `to`): time, distance, steepest slope, turn-by-turn "
        "legs (heading and distance), and the point-of-no-return check against the EVA budget. Use for 'get me back', 'how do I get "
        "to base', 'emergency, go home'. Routes avoid ground steeper than the walking limit and unknown ground. " + CREW + " " + UNITS +
        " Example: return_route()  or  return_route(to=\"the rover\")"))
    async def return_route(to: Any = None):
        return await call("POST", "/mission/return_route", fmt_return_route, body={"to": coerce(to)} if to is not None else {})

    @tool("nearest_haven", (
        "Safe places to retreat to (base, the parked rover, shelters), ranked by walking time from where the crew is, with "
        "distance and direction. Use for 'nearest shelter', 'where can I get out of here', emergencies. Works with or without "
        "eva_start (without it only the base counts). " + CREW + " " + UNITS + " Example: nearest_haven()"))
    async def nearest_haven():
        return await call("POST", "/mission/havens", fmt_havens, body={})

    @tool("check_path_ahead", (
        "What is ahead on a straight line from the crew: slope, roughness, unknown ground and shadow along it, the first hazard "
        "and its distance, walking time, and the EVA margin at the end (with the point of no return along the line). Give "
        "heading_deg (degrees, or 'north', 'north-east') OR toward (a place). Default distance 200 m, max 3000 m. Use for 'what's "
        "ahead if I keep going north 300 m', 'can I go 2 km that way'. " + CREW + " " + UNITS +
        " Example: check_path_ahead(heading_deg=0, distance_m=300)"))
    async def check_path_ahead(heading_deg: Any = None, toward: Any = None, distance_m: Any = None):
        h, e1 = _loose_heading(heading_deg)
        d, e2 = _loose_dist(distance_m)
        if e1 or e2:
            return await err(e1 or e2)
        body = {"heading_deg": h, "toward": coerce(toward), "distance_m": d if d is not None else (200.0 if toward is None else None)}
        return await call("POST", "/mission/path_ahead", fmt_path_ahead, body={k: v for k, v in body.items() if v is not None})

    @tool("comm_check", (
        "Is there radio line of sight from the crew to the base (or `to`)? If terrain blocks it, the nearest spot within about "
        "300 m of walking that restores it, with direction, distance and time. Geometric line of sight only (no Fresnel zone or "
        "link budget). antenna_h = antenna height above the ground in metres (default 1.7). Use for 'am I in radio contact', 'can "
        "base hear me'. " + CREW + " " + UNITS + " Example: comm_check(to=\"base\")"))
    async def comm_check(to: Any = "base", antenna_h: float | None = None):
        body = {"to": coerce(to), "antenna_h": antenna_h}
        return await call("POST", "/mission/comm_check", fmt_comm, body={k: v for k, v in body.items() if v is not None})

    @tool("log_sample", (
        "Log a rock/soil sample at the crew's spot (or `at`): saves a numbered marker with the note, the EVA time, the position, "
        "the map unit and the mineral evidence from orbit at that point (orbital tiers, not a measurement of this rock; 'not "
        "detected' is absence of evidence). Use for 'log this rock, looks layered'. " + CREW +
        " Example: log_sample(note=\"layered rock, rusty band\")"), act)
    async def log_sample(note: str, at: Any = None):
        body = {"note": note, "at": coerce(at)}
        return await call("POST", "/mission/sample", fmt_sample, body={k: v for k, v in body.items() if v is not None})

    @tool("list_samples", "List the samples logged so far (note, position, EVA time, unit, minerals). Example: list_samples()")
    async def list_samples():
        return await call("GET", "/mission/samples", fmt_samples)

    @tool("bearing_to", (
        "Which way to a place: heading (degrees and compass point), straight-line distance, and walking time/distance on a safe "
        "route from the crew. Use for 'which way is the rover', 'how far to the ridge', 'which direction is base'. " + CREW + " " + UNITS +
        " Example: bearing_to(target=\"base\")"))
    async def bearing_to(target: Any):
        return await call("POST", "/mission/bearing", fmt_bearing, body={"target": coerce(target)})

    @tool("rendezvous", (
        "Where the crew and the rover should meet: the point minimising the longer of the crew's walking time and the rover's "
        "driving time (every reachable cell is scored), plus the simple alternatives (walk to the rover, rover comes to you). "
        "By default the rover is where it was left when the EVA started. Use for 'where should I meet the rover', 'call the rover "
        "to me'. " + CREW + " " + UNITS + " Example: rendezvous()"))
    async def rendezvous(rover: Any = None):
        r = coerce(rover)
        if isinstance(r, str) and r.strip().lower() == "default":
            r = None
        return await call("POST", "/mission/rendezvous", fmt_rendezvous, body={"rover": r} if r is not None else {})

    @tool("daylight_check", (
        "Light at the crew's spot (or `at`) on the EVA clock: sun elevation, minutes to sunset, and minutes until this spot is in "
        "shade (terrain shadow or sunset). Geometric sun, no dust twilight (Mars stays dimly lit ~2 h after sunset). Use for 'when do "
        "I lose the light', 'how long until sunset'. " + CREW + " " + UNITS + " Example: daylight_check()"))
    async def daylight_check(at: Any = None):
        return await call("POST", "/mission/daylight", fmt_daylight, body={"at": coerce(at)} if at is not None else {})
