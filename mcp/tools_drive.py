"""MCP tools: select_vehicle and drive_to (vehicle-aware driving of the crewed buggy or the scout rover).

Registered from mcp/server.py with `tools_drive.register(ctx)`. Every call goes through the same `request` the
other tools use, so it works against Yousef's API (split mode, mcp/backends.py) and the devapi stand-in alike.

drive_to composes existing endpoints: POST /rover/vehicle (select), POST /search/locations (highest point),
POST /route (plan with the vehicle's profile), POST /rover/command (teleport, fsd_enable, follow_route). No
vehicle number is typed here: speed and slope limits are read from the API (`GET /rover/vehicle`) and printed with
the API's own source label (cited or assumed).
"""
from __future__ import annotations

import math
from typing import Any

from format import dist_text, dur_text, num, xy

MAX_CANDIDATES = 8          # highest-point candidates tried (each needs one route plan)
REPOSITION_MIN_M = 10.0     # start closer than this to the rover counts as "already there"
MAX_REPLAN_ROUNDS = 3       # follow_route calls when the backend stops with `replanned` (stand-in behaviour)


class DriveFail(Exception):
    """A drive_to precondition failed; carries a message and a hint, rendered as a readable error result."""
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message, self.hint = message, hint


def short_src(src) -> str:
    """Source label shortened to 60 characters ("assumed" when missing) for one-line output."""
    s = str(src or "assumed").split(";")[0].strip()
    return s if len(s) <= 60 else s[:57].rstrip() + "..."


def vehicle_line(name: str, v: dict) -> str:
    """One line for a vehicle: speed, slope limits and their (cited or assumed) source, all read from the API."""
    cau = v.get("caution_slope_deg")
    return (f"{name} ({v.get('title', name)}): flat speed {num(v.get('flat_speed_mps'), 2)} m/s"
            f"{', caution above ' + num(cau) + ' deg' if cau is not None else ''}, no-go above "
            f"{num(v.get('max_slope_deg'))} deg [{short_src(v.get('source'))}]")


def fmt_vehicle(d: dict) -> str:
    """Text summary of select_vehicle / the vehicle table."""
    vs = d["vehicles"]
    cur = d["current"]
    head = (f"Active vehicle: {vehicle_line(cur, vs[cur])}." if cur in vs else f"Active vehicle: {cur}.")
    if d.get("switched"):
        head = f"Switched to {vehicle_line(cur, vs[cur])}." if cur in vs else f"Switched to {cur}."
    others = [vehicle_line(n, v) for n, v in vs.items() if n != cur]
    return "\n".join([head] + (["Other vehicles: " + "; ".join(others) + "."] if others else []) +
                     ["Limits are the API's profile values (cited or assumed as labelled), not typed in the MCP server."])


def fmt_drive_to(d: dict) -> str:
    """Text summary of drive_to: target, route length and time, outcome (stop reason) and final state."""
    lim = d.get("limits") or {}
    veh = f"{d['vehicle']} (no-go above {num(lim.get('max_slope_deg'))} deg [{short_src(lim.get('source'))}])"
    lines: list[str] = []
    g = d.get("goal") or {}
    if not d.get("feasible"):
        lines.append(f"NOT DRIVEN: no drivable route for {veh}. {d.get('reason')}")
        nr = d.get("nearest_reachable")
        if nr:
            lines.append(f"Nearest reachable point: {xy(nr['x'], nr['y'])} m, {num(nr.get('distance_to_goal_m'), 0)} m "
                         "from the goal.")
    elif not d.get("driven"):
        lines.append(f"NOT DRIVEN: {d.get('reason')}")
    else:
        lines.append(f"{(d.get('stop_reason') or '?').upper()} with {veh}: "
                     f"{d.get('detail') or ''}".rstrip())
    hs = d.get("highest")
    if hs and d.get("reason") != hs["text"]:
        lines.append(hs["text"])
    rt = d.get("route")
    if rt:
        lines.append(f"Route {rt['route_id']}: {dist_text(rt['distance_m'])}, {dur_text(rt['duration_min'])} planned, "
                     f"ascent {num(rt.get('ascent_m'), 0)} m, max slope {num(rt.get('max_slope_deg'))} deg"
                     f" (limit {num(rt.get('limit_deg'))} deg)"
                     f"{', ' + rt['verdict'] if rt.get('verdict') else ''}.")
        if rt.get("filled_warning"):
            lines.append("Warning: " + rt["filled_warning"])
        elif rt.get("filled_length_m"):
            lines.append(f"Route crosses {num(rt['filled_length_m'], 0)} m of interpolated (filled) terrain "
                         f"(policy {rt.get('filled_policy')}).")
    if d.get("driven"):
        st = d.get("state") or {}
        el = d.get("elapsed_sim_min")
        if d.get("filled_crossed_m"):
            lines.append(f"Crossed {num(d['filled_crossed_m'], 0)} m of interpolated (filled, not measured) terrain "
                         f"(FSD {'on' if d.get('fsd') else 'off'}, policy {d.get('filled_policy')}).")
        lines.append(f"Drove {dist_text(d.get('distance_driven_m'))}"
                     f"{' in ' + dur_text(el) + ' of simulated time' if el is not None else ''}; rover now at "
                     f"{xy(st.get('x'), st.get('y'))} m, z {num(st.get('z'), 0)} m, vehicle {d['vehicle']}.")
    if g and g.get("x") is not None and d.get("feasible"):
        lines.append(f"Goal {g.get('label') or ''} {xy(g['x'], g['y'])} m"
                     f"{', z ' + num(g['z'], 0) + ' m' if g.get('z') is not None else ''}.".replace("  ", " "))
    for n in d.get("notes", []):
        lines.append("Note: " + n)
    if d.get("hint"):
        lines.append("Hint: " + d["hint"])
    return "\n".join(lines)


def register(ctx: dict) -> None:
    """ctx: tool, act, act_idem, request, ok, err, Unreachable, coerce, location_refs, units, drive_note, schema_patch,
    loc, backends."""
    tool, act, act_idem = ctx["tool"], ctx["act"], ctx["act_idem"]
    request, ok, err, Unreachable = ctx["request"], ctx["ok"], ctx["err"], ctx["Unreachable"]
    coerce, LOC, UNITS, loc = ctx["coerce"], ctx["location_refs"], ctx["units"], ctx["loc"]
    bk = ctx["backends"]
    ctx["schema_patch"]["drive_to"] = {
        "target": loc("destination, or the CENTRE of the search when highest_within_m is given"),
        "start": loc("where the trip starts (default: the rover's current position)")}

    async def rq(method: str, path: str, body=None, params=None, timeout=None) -> Any:
        try:
            st, d = await request(method, path, json_body=body, params=params, timeout=timeout)
        except Unreachable as u:
            raise DriveFail(f"The {u.which} backend is not reachable at {u.url}.", "Start it, then retry.")
        if st >= 400:
            raise DriveFail(str(d.get("error", d)) if isinstance(d, dict) else str(d),
                            d.get("hint") if isinstance(d, dict) else None)
        return d

    async def vehicles() -> dict:
        return await rq("GET", "/rover/vehicle")

    async def pick_vehicle(name: str | None, tbl: dict) -> tuple[str, bool]:
        """-> (active vehicle name, switched). Switches the simulator when `name` differs from the active one."""
        cur = tbl["current"]
        if not name:
            return cur, False
        want = bk.map_vehicle(name)
        if want not in tbl["vehicles"]:
            alt = bk.map_profile(name)
            if alt in tbl["vehicles"]:
                want = alt
            else:
                raise DriveFail(f'Unknown vehicle "{name}".', "Valid vehicles: " + ", ".join(tbl["vehicles"]) +
                                '. Words such as "buggy" and "scout" are understood.')
        if want == cur:
            return cur, False
        await rq("POST", "/rover/vehicle", {"profile": want})
        return want, True

    @tool("select_vehicle", (
        "Choose which vehicle the simulator drives: crew_buggy (the crewed buggy, fast, no-go slope per its profile), "
        "rover_scout (the Perseverance-like scout rover, very slow), crew_rover or the walker; call with no argument "
        "to list the vehicles with their speed and slope limits (read from the API, labelled cited or assumed) and "
        "the active one. 'buggy', 'the scout rover' and similar words are accepted. Switching cancels a drive in "
        "progress and keeps the position. plan_route without a profile, goto_location, follow_route and drive_to "
        "all use the active vehicle. " + UNITS + " Example: select_vehicle(vehicle=\"buggy\")"), act_idem)
    async def select_vehicle(vehicle: str | None = None):
        try:
            tbl = await vehicles()
            switched = False
            if vehicle:
                _, switched = await pick_vehicle(vehicle, tbl)
                tbl = await vehicles()
        except DriveFail as e:
            return await err(e.message, e.hint)
        tbl["switched"] = switched
        return await ok(fmt_vehicle(tbl), tbl)

    @tool("drive_to", (
        "ONE call for 'drive the buggy from A to B', 'drive to the top of the hill' and 'drive the scout to marker X': "
        "optionally selects the vehicle (vehicle=\"buggy\"|\"scout\"|...), plans a route with THAT vehicle's profile, "
        "checks it is drivable within the vehicle's slope limit, then drives it and returns the stop reason (arrived, "
        "blocked_slope, blocked_hazard, ...), distance, simulated time and the route's max slope. If there is no "
        "drivable route it does not move and returns the reason and the nearest reachable point. "
        "target is the destination (any location reference). start (optional) is where the trip begins: if the "
        "rover is elsewhere it is first moved there instantly by teleport (no driving, no simulated time, said in "
        "the result), or driven there with reposition=\"drive\". "
        "HIGHEST POINT: pass highest_within_m=<radius metres> and target = the centre of the search (a POI such as the "
        "feature the user named, a marker, or omit it to search around the start/rover); it finds the highest ground "
        "within that radius that the vehicle can reach under its slope limit, plans to it and drives. When the true "
        "summit of that radius is steeper than the limit or cut off, the result says so, gives the summit's "
        "coordinates and elevation and drives to the highest REACHABLE point instead: tell the user that plainly. "
        "max_slope_deg can only tighten the vehicle's limit. fsd (default true) arms the autopilot for this "
        "drive (and disarms it again): caution slowdown on steep or interpolated ground and one replan; fsd=false "
        "checks slopes only. filled = block|caution|allow (default caution) is the policy for interpolated (filled, "
        "not measured) terrain: caution crosses it at half speed and the result reports filled_crossed_m and the "
        "route's filled warning (tell the user how much was interpolated); block avoids it and stops FSD at it "
        "(blocked_hazard); allow ignores it. Use plan_route + assess_route + follow_route instead when the user "
        "wants to review first. " + ctx["drive_note"] + " " + LOC + " " + UNITS + " "
        "Examples: drive_to(target=\"poi:region_delta_front\", start=\"base\", vehicle=\"buggy\"); "
        "drive_to(vehicle=\"buggy\", highest_within_m=1000); drive_to(target=\"marker:m1\", vehicle=\"scout\")"), act)
    async def drive_to(target: Any = None, vehicle: str | None = None, start: Any = None,
                       highest_within_m: float | None = None, reposition: str = "teleport",
                       max_slope_deg: float | None = None, fsd: bool = True, animate: bool = False,
                       speed_factor: float | None = None, filled: str = "caution"):
        notes: list[str] = []
        try:
            data = await _drive_to(target, vehicle, start, highest_within_m, reposition, max_slope_deg, fsd,
                                   animate, speed_factor, notes, filled)
        except DriveFail as e:
            return await err(e.message, e.hint)
        data["notes"] = notes
        return await ok(fmt_drive_to(data), data)

    # ---- the orchestration -----------------------------------------------------------------------
    async def _drive_to(target, vehicle, start, highest_m, reposition, max_slope, use_fsd, animate, speed_factor,
                        notes: list[str], filled: str = "caution") -> dict:
        if filled not in ("block", "caution", "allow"):
            raise DriveFail(f'filled must be "block", "caution" or "allow", got {filled!r}.',
                            "block avoids interpolated terrain, caution (default) crosses it slowly and reports it, allow ignores it.")
        if reposition not in ("teleport", "drive", "none"):
            raise DriveFail(f'reposition must be "teleport", "drive" or "none", got {reposition!r}.')
        target, start = coerce(target), coerce(start)
        if target is None and highest_m is None:
            raise DriveFail("drive_to needs a target, or highest_within_m (radius in metres) for the highest point.",
                            'Example: drive_to(target="poi:base") or drive_to(vehicle="buggy", highest_within_m=1000).')
        if highest_m is not None and not (isinstance(highest_m, (int, float)) and highest_m > 0):
            raise DriveFail("highest_within_m must be a radius in metres greater than 0.")

        tbl = await vehicles()
        name, switched = await pick_vehicle(vehicle, tbl)
        if switched:
            tbl = await vehicles()
        lim = tbl["vehicles"][name]
        eff = lim.get("max_slope_deg")
        if max_slope is not None:
            if eff is not None and max_slope > eff:
                notes.append(f"max_slope_deg={max_slope:g} is looser than the {name} limit ({eff:g} deg [{lim.get('source')}]); "
                             f"the limit was used.")
            else:
                eff = max_slope
        out: dict = {"vehicle": name, "vehicle_title": lim.get("title"), "vehicle_switched": switched,
                     "limits": {k: lim.get(k) for k in ("max_slope_deg", "caution_slope_deg", "flat_speed_mps", "source")},
                     "slope_limit_used_deg": eff, "fsd": bool(use_fsd), "filled_policy": filled, "feasible": False, "driven": False}
        if switched:
            notes.append(f"Vehicle switched to {name}.")

        rv0 = await rq("GET", "/rover/state")
        origin_ref = start if start is not None else "rover"

        # 1. destination
        highest = None
        if highest_m is not None:
            center = target if target is not None else origin_ref
            route, goal_ref, highest = await _highest_reachable(center, origin_ref, float(highest_m), name, eff, notes, filled)
            out["highest"] = highest
            if route is None:
                out["reason"] = highest["text"]
                out["hint"] = "Try a larger highest_within_m, another centre, or the scout/walker if the ground is too steep."
                return out
        else:
            goal_ref = target
            body = {"start": origin_ref, "goal": goal_ref, "profile": name, "filled": filled}
            if max_slope is not None:
                body["max_slope_deg"] = eff
            route = await rq("POST", "/route", body, timeout=120.0)

        if not route.get("feasible", True):
            out.update(feasible=False, reason=route.get("reason"), nearest_reachable=route.get("nearest_reachable"),
                       hint=route.get("hint") or "Pick a goal on drivable ground or the nearest reachable point.")
            return out
        pts = route.get("points") or []
        a = route.get("assessment") or {}
        out["route"] = {"route_id": route["route_id"], "distance_m": route.get("distance_m"),
                        "duration_min": route.get("duration_min"), "ascent_m": route.get("ascent_m"),
                        "max_slope_deg": route.get("max_slope_deg"), "limit_deg": route.get("max_slope_limit_deg", eff),
                        "verdict": ("GO" if a.get("go") else "NO-GO") if "go" in a else None,
                        "reasons": a.get("reasons"),
                        "filled_policy": route.get("filled_policy"), "filled_cells": route.get("filled_cells"),
                        "filled_length_m": route.get("filled_length_m"),
                        "filled_warning": next((w for w in route.get("warnings") or [] if "interpolated" in w), None)}
        out["feasible"] = True
        if pts:
            out["start"] = {"x": pts[0]["x"], "y": pts[0]["y"]}
            out["goal"] = {"x": pts[-1]["x"], "y": pts[-1]["y"], "z": pts[-1].get("z"),
                           "label": "top" if highest else None}
        if a.get("go") is False:
            out["reason"] = ("the route assessment is NO-GO: " + "; ".join(a.get("reasons") or ["no reason given"]))
            out["hint"] = f'Review it with assess_route("{route["route_id"]}"); follow_route drives it anyway.'
            return out

        # 2. get to the start
        if pts:
            d0 = math.hypot(pts[0]["x"] - rv0["x"], pts[0]["y"] - rv0["y"])
            if start is not None and d0 > REPOSITION_MIN_M and reposition != "none":
                if reposition == "teleport":
                    await rq("POST", "/rover/command", {"cmd": "teleport", "x": pts[0]["x"], "y": pts[0]["y"]})
                    notes.append(f"The rover was {dist_text(d0)} from the requested start, so it was placed at "
                                 f"{xy(pts[0]['x'], pts[0]['y'])} m instantly (teleport: no driving, no simulated time, "
                                 "odometer unchanged), then driven from there.")
                    out["repositioned"] = "teleport"
                else:
                    res = await rq("POST", "/rover/command",
                                   {"cmd": "goto", "x": pts[0]["x"], "y": pts[0]["y"], **_speed(animate, speed_factor)})
                    out["repositioned"] = "drive"
                    if res.get("stop_reason") != "arrived":
                        out.update(reason=f"could not reach the start: {res.get('stop_reason')} - {res.get('detail')}",
                                   stop_reason=res.get("stop_reason"), state=res.get("state"))
                        return out
                    notes.append(f"Drove {dist_text(res.get('distance_m'))} to the start first.")
            elif start is not None and d0 > REPOSITION_MIN_M:
                notes.append(f"reposition=none: the rover is {dist_text(d0)} from the start and drives there first "
                             "(the backend plans an approach leg, or follow_route fails).")

        # 3. drive
        armed_before = bool((await rq("GET", "/fsd/status")).get("armed"))
        if use_fsd:     # (re)arm with the filled policy for this drive; disarmed again below unless it was armed before
            await rq("POST", "/rover/command", {"cmd": "fsd_enable", "policy": {"filled": filled}})
        sim0 = (await rq("GET", "/rover/state")).get("sim_elapsed_s") or 0.0
        rid, total, res, rounds, filled_m = route["route_id"], 0.0, {}, 0, 0.0
        try:
            while True:
                res = await rq("POST", "/rover/command", {"cmd": "follow_route", "route_id": rid,
                                                          **_speed(animate, speed_factor)}, timeout=330.0)
                total += float(res.get("distance_m") or 0.0)
                filled_m += float(res.get("filled_crossed_m") or 0.0)
                rounds += 1
                nr = res.get("new_route")
                if res.get("stop_reason") == "replanned" and nr and rounds < MAX_REPLAN_ROUNDS:
                    rid = nr["route_id"]
                    notes.append(f"FSD replanned around a hazard ({nr['route_id']}); continued.")
                    continue
                break
        finally:
            if use_fsd and not armed_before:
                try:
                    await rq("POST", "/rover/command", {"cmd": "fsd_disable"})
                except DriveFail:
                    pass
        st = res.get("state") or await rq("GET", "/rover/state")
        out.update(driven=True, stop_reason=res.get("stop_reason"), detail=res.get("detail"),
                   distance_driven_m=round(total, 1), filled_crossed_m=round(filled_m, 1), state=st, final_route_id=rid,
                   elapsed_sim_min=round(((st.get("sim_elapsed_s") or 0.0) - sim0) / 60.0, 2))
        if res.get("note"):
            notes.append(res["note"])
        if res.get("stop_reason") not in ("arrived", None):
            out["hint"] = ("Interpolated (filled, not measured) terrain or no-data ground stopped FSD; retry with "
                           "filled=\"caution\" (cross it slowly), fsd=false (slope checks only), or choose another goal."
                           if use_fsd and res.get("stop_reason") == "blocked_hazard" else res.get("hint"))
        return out

    def _speed(animate: bool, speed_factor: float | None) -> dict:
        b: dict = {}
        if animate:
            b["realtime"] = True
        if speed_factor is not None:
            b["speed_factor"] = speed_factor
        return b

    async def _highest_reachable(center, origin_ref, radius: float, name: str, eff: float | None,
                                 notes: list[str], filled: str = "caution") -> tuple[dict | None, Any, dict]:
        """Highest ground within `radius` of `center` that a route for `name` reaches from `origin_ref`."""
        within = {"from": center, "max": radius, "metric": "straight_m"}
        spacing = max(50.0, radius / 10.0)
        base = {"footprint_radius_m": 0, "within": within, "objective": "highest", "spacing_m": spacing}
        summit_body = {**base, "limit": 1, "conditions": [{"layer": "elevation", "op": ">", "value": -1.0e6}]}
        summit = (await rq("POST", "/search/locations", summit_body)).get("results") or []
        cand_body = {**base, "limit": MAX_CANDIDATES}
        if eff is not None:
            cand_body["conditions"] = [{"layer": "slope_2m", "op": "<=", "value": eff}]
        cands = (await rq("POST", "/search/locations", cand_body)).get("results") or []
        info: dict = {"radius_m": radius, "limit_deg": eff, "summit": None, "chosen": None, "tried": 0}
        if summit:
            s = summit[0]
            info["summit"] = {"x": s["x"], "y": s["y"], "elevation_m": _elev(s),
                              "slope_deg": (s.get("attributes") or {}).get("slope_2m_mean")}
        if not cands:
            info["text"] = (f"No cell within {dist_text(radius)} has a slope at or under {num(eff)} deg for {name}, "
                            "so there is no drivable high point there.")
            return None, None, info
        last = None
        for c in cands:
            info["tried"] += 1
            goal = {"x": c["x"], "y": c["y"]}
            body = {"start": origin_ref, "goal": goal, "profile": name, "filled": filled}
            if eff is not None:
                body["max_slope_deg"] = eff
            r = await rq("POST", "/route", body, timeout=120.0)
            last = r
            if r.get("feasible", True) and r.get("points"):
                ce = _elev(c)
                info["chosen"] = {"x": c["x"], "y": c["y"], "elevation_m": ce,
                                  "straight_m_from_centre": (c.get("attributes") or {}).get("straight_m")}
                sm = info["summit"]
                if sm and ce is not None and sm["elevation_m"] is not None and ce >= sm["elevation_m"] - 0.5:
                    info["true_summit_reached"] = True
                    info["text"] = (f"Highest point within {dist_text(radius)}: {xy(c['x'], c['y'])} m at "
                                    f"{num(ce, 1)} m elevation (this is the true highest cell of that radius, and it is "
                                    f"within the {name} slope limit).")
                else:
                    info["true_summit_reached"] = False
                    why = ""
                    if sm:
                        sl = sm.get("slope_deg")
                        reason = (f"its own slope is above the {num(eff)} deg limit" if sl is not None and eff is not None
                                  and sl > eff else f"steeper ground over the {num(eff)} deg limit cuts it off from the start")
                        why = (f" The true summit of that radius is {xy(sm['x'], sm['y'])} m at "
                               f"{num(sm['elevation_m'], 1)} m"
                               f"{' (local slope ' + num(sl) + ' deg)' if sl is not None else ''}; it is not reachable "
                               f"for {name}: {reason}; it is "
                               f"{num(sm['elevation_m'] - ce, 1)} m higher than the reachable high point.")
                    info["text"] = (f"Highest REACHABLE point within {dist_text(radius)}: {xy(c['x'], c['y'])} m at "
                                    f"{num(ce, 1)} m elevation.{why}")
                st_d = info["chosen"]["straight_m_from_centre"]
                if st_d is not None and st_d > 0.9 * radius:
                    notes.append("The chosen point is near the edge of the search radius; higher ground may exist "
                                 "outside it. Try a larger highest_within_m.")
                return r, goal, info
        info["text"] = (f"None of the {info['tried']} highest candidate points within {dist_text(radius)} is reachable "
                        f"for {name} from the start ({(last or {}).get('reason') or 'no route'}).")
        return None, None, info

    def _elev(r: dict) -> float | None:
        a = r.get("attributes") or {}
        if a.get("elevation_mean") is not None:
            return float(a["elevation_mean"])
        s = r.get("score")
        return -float(s) if s is not None else None
