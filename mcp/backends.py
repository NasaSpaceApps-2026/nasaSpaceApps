"""Backend split for the MCP server: Yousef's rover API (ares_api) next to our query engine (devapi/atlas_core).

ATLAS_ROVER_API_URL  Yousef's API: rover state/commands, routes, markers, view, search/sites, pois,
                     terrain sample/profile, science traverse, validation, agent events, WS.
ATLAS_API_URL        our query engine: layers, describe, search/locations|regions|nearest, sets, visibility, sun,
                     lake, eva, overview, health.

With ATLAS_ROVER_API_URL unset everything goes to ATLAS_API_URL and `Backends.request` is a pass-through, so
the tools and their output formats are unchanged. With both set, requests for the rover side are translated
into ares_api's shapes and its answers translated back into the shapes mcp/format.py expects. Differences
are catalogued in docs/api-compat.md. Nothing is dropped silently: his original fields stay in the result,
and parameters his API cannot honour produce a note or a readable error naming the backend that lacks them.

The pure mapping functions at the top of this file need no network and are unit tested (tests/test_backends.py).
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

import httpx

MARS_RADIUS_M = 3396190.0
FRAME_TOL_M = 1.0

# our profile name -> his profile name (same limits: rover_scout 0.04 m/s, no-go 20; walker no-go 25)
PROFILE_MAP = {"eva_walkable": "astronaut_walk"}
# vehicle words a user or model may use -> profile name on both backends (limits are read from the API, never here)
VEHICLE_ALIASES = {
    "buggy": "crew_buggy", "crew_buggy": "crew_buggy", "crewed_buggy": "crew_buggy", "the_buggy": "crew_buggy",
    "performance_buggy": "crew_buggy", "lrv": "crew_buggy", "crew_vehicle": "crew_buggy",
    "scout": "rover_scout", "scout_rover": "rover_scout", "rover_scout": "rover_scout", "perseverance": "rover_scout",
    "crew_rover": "crew_rover", "pressurised_rover": "crew_rover", "pressurized_rover": "crew_rover",
}
# how fast `animate=false` drives on his (always real-time) sim: display speed in m/s
INSTANT_DISPLAY_MPS = 400.0

ROVER_PREFIXES = ("/rover/", "/fsd/", "/route", "/markers", "/view", "/search/sites", "/search/targets", "/pois",
                  "/terrain/", "/validation")


class Unreachable(Exception):
    """A backend (query engine or rover API) could not be reached; carries which one and its URL."""
    def __init__(self, which: str, url: str, msg: str = ""):
        super().__init__(msg or f"{which} backend not reachable at {url}")
        self.which, self.url = which, url


def backend_for(method: str, path: str) -> str:
    """'rover' for the endpoints Yousef's API owns, else 'atlas'."""
    if path == "/events/agent":
        return "rover"
    return "rover" if any(path == p or path.startswith(p) for p in ROVER_PREFIXES) else "atlas"


# ---- frame check (same site / same coordinate frame) -------------------------------------------------

def _site_fields(site: dict) -> dict:
    """Normalise GET /site of either backend: ours is flat, his nests the manifest."""
    m = site.get("manifest") if isinstance(site.get("manifest"), dict) else site
    size = m.get("size_m")
    if size is None and "bounds" in site:
        size = 2 * site["bounds"].get("half_size_m", 0)
    if size is None and m.get("size_px") and m.get("res_m"):
        size = m["size_px"] * m["res_m"]
    return {"name": m.get("site") or m.get("name"), "lat0": float(m["lat0"]), "lon0": float(m["lon0"]),
            "size_m": float(size) if size is not None else None,
            "radius_m": float(m.get("radius_m") or MARS_RADIUS_M)}


def compare_sites(atlas_site: dict, rover_site: dict, tol_m: float = FRAME_TOL_M) -> dict | None:
    """None when both backends use the same frame; else {offset_m, dx_m, dy_m, size_*, message}.

    The frame is the origin (lat0, lon0 = site centre) and radius. A different extent with the same origin
    keeps coordinates interchangeable (reported as a note, not a mismatch)."""
    a, r = _site_fields(atlas_site), _site_fields(rover_site)
    R = a["radius_m"]
    dy = R * math.radians(r["lat0"] - a["lat0"])
    dx = R * math.cos(math.radians(a["lat0"])) * math.radians(r["lon0"] - a["lon0"])
    off = math.hypot(dx, dy)
    radius_differs = abs(a["radius_m"] - r["radius_m"]) > 1.0
    size_differs = (a["size_m"] is not None and r["size_m"] is not None and abs(a["size_m"] - r["size_m"]) > tol_m)
    if off <= tol_m and not radius_differs and not size_differs:
        return None
    out = {"offset_m": round(off, 1), "dx_m": round(dx, 1), "dy_m": round(dy, 1),
           "atlas": a, "rover": r, "frame_mismatch": bool(off > tol_m or radius_differs),
           "extent_differs": bool(size_differs)}
    parts = []
    if out["frame_mismatch"]:
        parts.append(
            f"FRAME MISMATCH: the query engine site '{a['name']}' (centre {a['lat0']:.4f} N, {a['lon0']:.4f} E, "
            f"{a['size_m']:.0f} m) and the rover API site '{r['name']}' (centre {r['lat0']:.4f} N, {r['lon0']:.4f} E, "
            f"{r['size_m']:.0f} m) have origins {off:.0f} m apart (dx {dx:.0f}, dy {dy:.0f}). The same x,y means a "
            "different place on each: coordinates from find_*/describe tools would send the rover to the wrong "
            "place. Do not drive the rover to coordinates from the other backend until both load the same site "
            "(ARES_DATA_DIR vs ATLAS_SITE_DIR; see docs/api-compat.md).")
    elif size_differs:
        parts.append(
            f"EXTENT DIFFERS: both backends share the origin ({a['lat0']:.4f} N, {a['lon0']:.4f} E) but cover "
            f"{a['size_m']:.0f} m (query engine) and {r['size_m']:.0f} m (rover API); coordinates agree where both "
            "have data, and positions outside the smaller extent are rejected by that backend.")
    out["message"] = " ".join(parts)
    return out


# ---- request-side mapping ----------------------------------------------------------------------------

def map_vehicle(name: str | None) -> str | None:
    """'buggy' / 'the scout rover' / 'crew buggy' -> crew_buggy / rover_scout / ...; other names unchanged."""
    if not name or not isinstance(name, str):
        return name
    key = re.sub(r"[\s\-]+", "_", name.strip().lower()).removeprefix("the_")
    return VEHICLE_ALIASES.get(key, name)


def map_profile(name: str | None) -> str | None:
    """Profile name mapping for the rover API: vehicle aliases first, then PROFILE_MAP; unknown names unchanged."""
    if not name:
        return name
    name = map_vehicle(name)
    return PROFILE_MAP.get(name, name)


def vehicle_table(vehicles: dict, profiles: dict, current: str | None) -> dict:
    """His GET /rover/vehicles + the /site profiles (for the caution slope) -> one table of drivable vehicles.
    Every number is his (profiles.yaml); `source` says whether it is cited or assumed."""
    out = {}
    for name, v in (vehicles or {}).items():
        p = (profiles or {}).get(name, {})
        out[name] = {"title": v.get("title", name), "flat_speed_mps": v.get("flat_speed_mps"),
                     "max_slope_deg": v.get("nogo_slope_deg"), "caution_slope_deg": p.get("caution_slope_deg"),
                     "eva_limit_min": p.get("eva_limit_min"), "source": v.get("source", "assumed"),
                     "active": name == current}
    return {"current": current, "vehicles": out}


_SET_REF = re.compile(r"^(set:)?\d+(\.\d+)?$")
_SITE_ID = re.compile(r"^site\d+$")


def is_set_ref(s: Any) -> bool:
    """True for a location-set reference such as "set:3" or "3.1" (resolved by the query engine, not the rover API)."""
    return isinstance(s, str) and bool(_SET_REF.match(s.strip()))


def policy_to_rover(policy: dict | None) -> dict | None:
    """Our FSD policy booleans -> his policy fields. Unknown keys raise ValueError (never dropped silently)."""
    if not policy:
        return None
    out: dict = {}
    for k, v in policy.items():
        if k == "replan_on_block":
            out["replan"] = bool(v)
        elif k == "caution_slowdown":
            out["caution_speed_factor"] = 0.5 if v else 1.0
        elif k in ("max_slope_deg", "caution_slope_deg", "caution_speed_factor", "replan", "max_replans"):
            out[k] = v
        else:
            raise ValueError(f"Unknown FSD policy key {k!r}. Valid: replan_on_block, caution_slowdown "
                             "(booleans), or the rover backend's own max_slope_deg, caution_slope_deg, "
                             "caution_speed_factor, replan, max_replans.")
    return out


def command_to_rover(body: dict, info: dict, last_route: str | None, vehicle: str | None = None) -> tuple[dict, list[str]]:
    """Our /rover/command body -> his CommandReq. Returns (body, notes)."""
    b = {k: v for k, v in body.items() if v is not None}
    notes: list[str] = []
    realtime = bool(b.pop("realtime", False))
    if "x" in b and "y" in b and "target" not in b:
        b["target"] = {"x": b.pop("x"), "y": b.pop("y")}
    if b.get("cmd") == "fsd_enable" and "policy" in b:
        b["policy"] = policy_to_rover(b["policy"])
    if b.get("cmd") == "follow_route" and not b.get("route_id"):
        if not last_route:
            raise ValueError("follow_route needs a route_id: the rover backend has no 'most recent route' default "
                             "and no route was planned in this MCP session.")
        b["route_id"] = last_route
        notes.append(f"route_id was not given, so the most recently planned route {last_route} was used. "
                     "Pass route_id explicitly to avoid surprises.")
    if b.get("cmd") in ("drive", "goto", "follow_route") or (b.get("cmd") == "fsd_enable" and b.get("route_id")):
        if "speed_factor" not in b:
            prof = info["profiles"].get(vehicle or info["rover_profile"], {})   # the ACTIVE vehicle, not always the scout
            flat = float(prof.get("flat_speed_mps") or 1.0)
            if realtime:
                b["speed_factor"] = float(prof.get("display_speed_factor") or 1.0)
            else:
                b["speed_factor"] = max(1.0, INSTANT_DISPLAY_MPS / flat)
                notes.append("animate=false is approximated on the rover backend: its sim always runs in real time, "
                             f"so the drive was run about {b['speed_factor']:.0f}x faster than real speed.")
        b.setdefault("timeout_s", 280)
    return b, notes


def view_to_rover(body: dict) -> dict:
    """Our /view body -> his ViewReq. layers {id: bool} | [ids] -> [{id, visible}]. highlights must be id strings."""
    b = {k: v for k, v in body.items() if v is not None}
    lay = b.get("layers")
    if lay is not None:
        items = {k: True for k in lay} if isinstance(lay, list) else lay
        if not isinstance(items, dict):
            raise ValueError("layers must be {layer_id: visible} or a list of layer ids to show.")
        out = []
        for lid, val in items.items():
            if isinstance(val, dict):
                out.append({"id": lid, "visible": bool(val.get("visible", True)),
                            **({"opacity": val["opacity"]} if "opacity" in val else {})})
            else:
                out.append({"id": lid, "visible": bool(val)})
        b["layers"] = out
    return b


def science_to_rover(body: dict) -> tuple[dict, list[str]]:
    """Our /route/science body -> his ScienceReq; parameters he does not have become notes."""
    b = {k: v for k, v in body.items() if v is not None}
    notes = []
    if "profile" in b:
        b["profile"] = map_profile(b["profile"])
    for k, default, what in (("stop_minutes", 15.0, "stop_minutes (it uses 15 min per stop from profiles.yaml)"),
                             ("max_slope_deg", None, "max_slope_deg (it uses the profile's no-go slope)"),
                             ("return_to_start", True, "return_to_start=false (it always returns to the start)")):
        if k in b:
            v = b.pop(k)
            if v != default:
                notes.append(f"The rover backend ignores {what}; you passed {k}={v!r}.")
    if "max_stops" in b:
        b["max_stops"] = max(1, min(12, int(b["max_stops"])))
    return b, notes


def sites_to_rover(body: dict) -> tuple[dict, list[str]]:
    """Our search_sites body -> his search request; parameters he lacks (e.g. min_tier) become notes, never silently dropped."""
    b = {k: v for k, v in body.items() if v is not None}
    notes = []
    if "min_tier" in b:
        notes.append(f"The rover backend has no evidence tiers, so min_tier={b.pop('min_tier')!r} was not applied "
                     "(it ranks 0-1 layer scores).")
    b.setdefault("near", "rover")
    if "top_k" in b:
        b["top_k"] = max(1, min(20, int(b["top_k"])))
    return b, notes


# ---- response-side mapping ---------------------------------------------------------------------------

def adapt_error(status: int, data: Any) -> dict:
    """His errors are {"error"} (422) or FastAPI {"detail": [...]}; ours add "hint"."""
    if isinstance(data, dict) and "error" in data:
        return {"error": str(data["error"]), "hint": data.get("hint")}
    if isinstance(data, dict) and isinstance(data.get("detail"), list) and data["detail"]:
        first = data["detail"][0]
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return {"error": f"Invalid request to the rover backend: {loc + ': ' if loc else ''}{first.get('msg', 'bad input')}",
                "hint": "Check argument names and types against the tool description."}
    if isinstance(data, dict) and isinstance(data.get("detail"), str):
        return {"error": f"Rover backend error (HTTP {status}): {data['detail']}", "hint": None}
    return {"error": f"The rover backend answered HTTP {status}: {json.dumps(data)[:200]}", "hint": None}


def adapt_state(s: dict, info: dict, remaining_m: float = 0.0) -> dict:
    """His rover state -> our state shape (fills remaining_m, profile; trims last_event)."""
    out = dict(s)
    if isinstance(out.get("last_event"), str):
        out["last_event"] = out["last_event"].rstrip(". ") or None
    out.setdefault("remaining_m", remaining_m)
    out.setdefault("profile", out.get("vehicle") or info.get("rover_profile"))
    if "sim_elapsed_s" not in out:
        out["sim_elapsed_s"] = round(float(s.get("mission_time_min", 0.0)) * 60.0, 1)
    for k, dflt in (("pitch_deg", 0.0), ("roll_deg", 0.0), ("speed_mps", 0.0), ("odometer_m", 0.0), ("mode", "idle"),
                    ("status", "unknown"), ("last_event", None), ("fsd_armed", False), ("route_id", None)):
        out.setdefault(k, dflt)
    return out


def _hazard_detail(h: dict) -> str:
    if h.get("reason") == "blocked_hazard":
        return "unknown (interpolated) terrain"
    return f"{h.get('slope_deg', '?')} degree slope"


def adapt_fsd(f: dict, info: dict | None = None) -> dict:
    """His FSD state -> our shape: policy booleans, last hazard with type/detail, and what blocks driving."""
    pol = f.get("policy") or {}
    haz = f.get("last_hazard")
    if haz:
        haz = {**haz, "type": haz.get("reason"), "detail": _hazard_detail(haz)}
    blockers = []
    if haz and f.get("remaining_route_m"):
        blockers.append(haz["detail"])
    if f.get("armed") and not f.get("route_id"):
        blockers.append("no active route (plan_route, then follow_route)")
    return {**f, "armed": bool(f.get("armed")), "mode": f.get("mode", "idle"),
            "policy": {"replan_on_block": bool(pol.get("replan")),
                       "caution_slowdown": float(pol.get("caution_speed_factor", 0.5)) < 1.0},
            "rover_policy": pol,
            "replans_used": "unknown (the rover backend only reports replans in drive results)",
            "replans_allowed": int(pol.get("max_replans", 1)) if pol.get("replan") else 0,
            "last_hazard": haz, "remaining_route_m": f.get("remaining_route_m", 0), "route_id": f.get("route_id"),
            "blockers": blockers}


def adapt_command(res: dict, info: dict, notes: list[str] | None = None) -> dict:
    """His command result -> our shape (state adapted, command-specific fields such as teleport/route progress, notes appended)."""
    out = dict(res)
    cmd = res.get("cmd")
    if "state" in res:
        out["state"] = adapt_state(res["state"], info)
    if cmd == "teleport":
        out.setdefault("stop_reason", "arrived")
        out.setdefault("detail", f"teleported to {res.get('at')}")
    if cmd in ("fsd_enable", "fsd_disable") and "fsd" in res:
        out["fsd"] = adapt_fsd(res["fsd"], info)
        out.setdefault("state", None)
    if "stop_reason" not in out and cmd in ("turn",):
        out["stop_reason"] = "arrived"
        out.setdefault("detail", "turned")
    if res.get("message"):
        out["detail"] = res["message"]
    n = list(notes or [])
    if res.get("warnings"):
        n.append(" ".join(res["warnings"]))
    if res.get("replans"):
        n.append(f"FSD attempted {res['replans']} replan(s) during the drive (the rover backend replans in place and "
                 "never stops with stop_reason replanned). Events: " + " | ".join(res.get("events") or []))
    if out.get("stop_reason") in ("blocked_slope", "blocked_hazard") and "hint" not in out:
        out["hint"] = ("Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to the same goal "
                       "with a lower max_slope_deg, or pick another direction.")
    if out.get("stop_reason") in ("out_of_bounds", "no_route"):
        out["hint"] = out.get("hint") or "The drive ended at the site edge or no route existed; check the target."
    if n:
        out["note"] = " ".join(x for x in [out.get("note"), *n] if x)
    return out


def _cum_dist(points: list[dict]) -> list[float]:
    d, tot = [0.0], 0.0
    for a, b in zip(points, points[1:]):
        tot += math.hypot(b["x"] - a["x"], b["y"] - a["y"])
        d.append(tot)
    return d


def adapt_route(r: dict, assess: dict | None, info: dict) -> dict:
    """His feasible route (200) -> the shape fmt_route/fmt_assess expect."""
    pts = r["points"]
    prof = info["profiles"].get(r.get("profile"), {})
    eva = float((r.get("consumables") or {}).get("eva_limit_min") or prof.get("eva_limit_min") or 0)
    c = r.get("consumables") or {}
    stop_total = float(r.get("stop_minutes") or 0)
    cons = {"eva_limit_min": eva, "reserve_fraction": c.get("reserve_frac"),
            "used": {"minutes": round(c.get("used_frac", 0) * eva, 1), "fraction": c.get("used_frac")},
            "margin": {"minutes": round(c.get("margin_frac", 0) * eva, 1), "fraction": c.get("margin_frac")},
            "stops_min": stop_total, "basis": c.get("basis")}
    legs = r.get("legs") or []
    cum = _cum_dist(pts) if pts else [0.0]
    stops, run_d, run_t = [], 0.0, 0.0
    per_stop = stop_total / len(r["stops"]) if r.get("stops") else 0.0
    for i, leg in enumerate(legs[:-1]):
        run_d += leg["distance_m"]
        run_t += leg["duration_min"]
        j = min(range(len(cum)), key=lambda k: abs(cum[k] - run_d)) if pts else 0
        stops.append({"id": leg["to"], "kind": "via", "x": pts[j]["x"], "y": pts[j]["y"], "z": pts[j].get("z"),
                      "distance_m": run_d, "eta_min": round(run_t + i * per_stop, 1)})
    reasons, caution = [], []
    go = True
    if assess:
        go = bool(assess.get("go"))
        reasons = [x for x in assess.get("reasons", []) if x != "all checks pass"]
    caution = [w for w in r.get("warnings", []) if "caution" in w.lower()]
    sources = r.get("sources") or {}
    assumptions = []
    if str(sources.get("speeds", "")).startswith("assumed") or "slopes assumed" in str(sources.get("speeds", "")):
        assumptions.append(f"travel speeds and slope limits of profile {r.get('profile')} are assumed: {sources.get('speeds')}")
    first, last = (pts[0], pts[-1]) if pts else ({"x": 0, "y": 0}, {"x": 0, "y": 0})
    out = dict(r)
    out.update({
        "feasible": True, "reason": None, "nearest_reachable": None, "consumables": cons, "stops": stops,
        "assessment": {"go": go, "reasons": reasons, "caution": caution},
        "max_slope_limit_deg": r.get("slope_limit_deg"),
        "caution_slope_deg": prof.get("caution_slope_deg"),
        "start": {"id": r.get("start"), "x": first["x"], "y": first["y"]},
        "goal": {"id": r.get("goal"), "x": last["x"], "y": last["y"]},
        "assumptions": assumptions,
        "notes": ["Rover backend (ares_api) planner: 4 m grid, least cost, its own assumed speed curve."]})
    return out


_NEAR_RE = re.compile(r"Nearest reachable point is \((-?\d+), (-?\d+)\), (\d+) m from the goal")
_INFEASIBLE_RE = re.compile(r"No route from|over the \d+ deg limit|sits on terrain over the slope limit|is outside the site \(\|x\|")


def infeasible_from_error(msg: str, info: dict, profile: str | None) -> dict | None:
    """His planner raises 422 for an unreachable goal; ours returns feasible:false. Convert the former."""
    if not _INFEASIBLE_RE.search(msg):
        return None
    m = _NEAR_RE.search(msg)
    nearest = ({"x": float(m.group(1)), "y": float(m.group(2)), "distance_to_goal_m": float(m.group(3)),
                "travel_min": None} if m else None)
    prof = info["profiles"].get(profile or info.get("default_profile"), {})
    return {"route_id": None, "feasible": False, "reason": msg, "nearest_reachable": nearest, "points": [],
            "distance_m": 0.0, "duration_min": 0.0, "ascent_m": 0.0, "max_slope_deg": None, "hazard_cells": 0,
            "consumables": None, "stops": [], "warnings": [],
            "assessment": {"go": False, "reasons": [msg], "caution": []},
            "profile": profile, "max_slope_limit_deg": prof.get("nogo_slope_deg"),
            "caution_slope_deg": prof.get("caution_slope_deg"),
            "hint": "Use the nearest reachable point as the goal, pick another goal, or relax max_slope_deg."}


def adapt_science(r: dict, assess: dict | None, info: dict, body: dict, notes: list[str]) -> dict:
    """His science traverse (+ assessment) -> the shape fmt_science expects; an infeasible plan becomes an empty, explained result."""
    budget = float(body.get("time_budget_min", 0))
    if r.get("feasible") is False and "points" not in r:
        return {"stops": [], "route": None, "science_value_total": None, "science_weighting_note": None,
                "time": {"travel_min": 0, "stops_min": 0, "total_min": 0, "budget_min": budget, "return_to_start": True},
                "candidates_considered": 0, "skipped": [], "interests": body.get("interests"),
                "assumptions": [], "notes": notes, "hint": r.get("message") or "No science stops fit.",
                "rover_result": r}
    route = adapt_route(r, assess, info)
    legs = r.get("legs") or []
    stop_each = float(r.get("stop_minutes") or 0) / max(1, len(r.get("science_stops") or []))
    arrive, t = [], 0.0
    for i, leg in enumerate(legs[:-1]):
        t += leg["duration_min"]
        arrive.append(round(t + i * stop_each, 1))
    stops = []
    for i, s in enumerate(r.get("science_stops") or []):
        stops.append({"id": s.get("ref"), "name": s.get("label"), "type": None, "x": s["x"], "y": s["y"],
                      "science_value": s.get("science_value"), "value_assumed": False, "matched_by": [],
                      "arrive_min": arrive[i] if i < len(arrive) else None, "stop_min": stop_each,
                      "source": s.get("source")})
    skipped = [{"id": lab, "reason": "not selected within the time budget (the rover backend gives no per-item reason)"}
               for lab in r.get("skipped") or []]
    dur, stopm = float(r.get("duration_min", 0)), float(r.get("stop_minutes") or 0)
    return {
        "stops": stops, "route": route, "science_value_total": r.get("science_value_total"),
        "science_weighting_note": ("Values are the POIs' recorded science_value; for mapped mineral targets named in "
                                   "interests the rover backend uses its 0-1 site score as the value."),
        "time": {"travel_min": dur, "stops_min": stopm, "total_min": round(dur + stopm, 1), "budget_min": budget,
                 "return_to_start": True},
        "candidates_considered": len(stops) + len(skipped), "skipped": skipped, "interests": body.get("interests"),
        "assumptions": route["assumptions"], "notes": ["Rover backend considers at most 8 candidate stops.", *notes],
        "hint": "assess_route(route_id) for go/no-go; set_view(highlights=[...]) to show the stops.",
        "route_id": r.get("route_id")}


def adapt_markers(d: dict) -> dict:
    """His marker list -> ours (adds count)."""
    ms = d.get("markers", [])
    return {**d, "count": d.get("count", len(ms)), "markers": ms}


def adapt_view(res: dict, req: dict) -> dict:
    """His view response + our request -> our view result (camera target, per-layer visibility)."""
    cam = res.get("camera") or {}
    target = None
    if cam.get("x") is not None:
        target = {"id": cam.get("target"), "x": cam["x"], "y": cam["y"], "z": cam.get("z")}
    layers = [{"layer": l["id"], "visible": l.get("visible", True)} for l in req.get("layers") or []]
    hl = [{"id": h} for h in (res.get("highlights") or [])]
    viewers = res.get("viewers_connected", 0)
    note = None
    if not viewers:
        note = "No viewer is connected, so nothing was displayed; the request was accepted."
    return {**res, "ok": res.get("ok", True), "mode": cam.get("mode") if (req.get("mode") or target) else None,
            "target": target, "layers": layers, "highlights": hl, "viewers": viewers, "note": note}


def adapt_pois(d: dict, extra_types: list[str] | None = None, known: list[str] | None = None) -> dict:
    """His POI list -> ours: adds poi:<id> refs and a null science_value where he has none."""
    pois = []
    for p in d.get("pois", []):
        q = dict(p)
        q["ref"] = f"poi:{p['id']}"
        q.setdefault("science_value", None)
        pois.append(q)
    return {**d, "pois": pois, "count": d.get("count", len(pois)),
            "types": extra_types if extra_types is not None else sorted({p.get("type") for p in pois if p.get("type")}),
            "known_ids": known if known is not None else [p["id"] for p in pois[:25]]}


def adapt_profile(d: dict, spacing_m: float) -> dict:
    """His elevation series -> our profile shape with total ascent/descent (m) and slope statistics."""
    series = d["series"]
    zs = [s["z"] for s in series if s.get("z") is not None]
    sl = [s["slope"] for s in series if s.get("slope") is not None]
    asc = sum(max(0.0, b - a) for a, b in zip(zs, zs[1:]))
    desc = sum(max(0.0, a - b) for a, b in zip(zs, zs[1:]))
    return {**d, "distance_m": d["distance_m"], "spacing_m": spacing_m, "count": d["samples"],
            "min_z": d.get("elevation_min_m"), "max_z": d.get("elevation_max_m"),
            "ascent_m": d.get("gain_m", round(asc, 2)), "descent_m": round(desc, 2),
            "max_slope_deg": d.get("max_slope_deg"), "mean_slope_deg": round(sum(sl) / len(sl), 2) if sl else None,
            "unknown_samples": 0,
            "samples": [{"d": s["d"], "x": s["x"], "y": s["y"], "z": s.get("z"), "slope_deg": s.get("slope")}
                        for s in series]}


def adapt_sample(d: dict, info: dict, profile: str) -> dict:
    """His /terrain/sample -> the shape of our GET /terrain/sample (consumed by server._adapt_sample)."""
    prof = info["profiles"].get(profile, {})

    def val(v, units=None):
        if v is None:
            return {"unknown": True}
        return {"value": v, **({"units": units} if units else {})}
    values = {"elevation": val(d.get("elevation_m"), "m"), "slope_2m": val(d.get("slope_deg"), "degrees"),
              "rough_5x5": val(d.get("roughness_m"), "m")}
    for lid, v in (d.get("layers") or {}).items():
        values.setdefault(lid, val(v))
    q = "rover backend: elevation/slope/roughness from its analysis rasters; no per-cell quality classes"
    if d.get("filled"):
        q += "; this cell is interpolated (filled) terrain, not measured"
    return {**d, "layers": values, "quality": q, "nearest_ground_truth": None,
            "hazard_class": d.get("hazard"), "hazard_profile": profile,
            "no_go_slope_deg": prof.get("nogo_slope_deg"), "caution_slope_deg": prof.get("caution_slope_deg")}


def adapt_sites(d: dict, kind: str | None, near: dict, notes: list[str]) -> dict:
    """His candidate sites -> our site shape; evidence-tier fields he lacks are null, travel_min is kept as walk_travel_min."""
    sites = []
    for s in d.get("sites", []):
        q = dict(s)
        q.update({"best_tier": None, "best_tier_name": None, "area_by_tier_m2": None,
                  "walk_distance_m": None, "walk_travel_min": s.get("travel_min")})
        q.setdefault("distance_m", 0.0)
        sites.append(q)
    hint = ("Scores are the rover backend's 0-1 index (mean layer value and area, penalised by slope), NOT evidence "
            "tiers or abundance; cite the layer, mission and instrument in each site's evidence. "
            "Sites are not stored as a set: use their ids (e.g. site1) as locations.")
    if d.get("caveat"):
        hint += " " + d["caveat"]
    if notes:
        hint += " " + " ".join(notes)
    return {**d, "kind": "score_index" if kind in (None, "abundance") else kind, "layer": None,
            "layers": [], "resolved": None, "set_id": None, "sites": sites, "near": near,
            "ranked_by": "score (0-1 index) with unreachable sites last", "hint": hint,
            "count": d.get("count", len(sites)), "registration_notes": []}


# ---- the backend router -----------------------------------------------------------------------------

class Backends:
    """Routes the MCP server's HTTP calls. `rover` None means single-backend pass-through."""

    def __init__(self, atlas: httpx.AsyncClient, rover: httpx.AsyncClient | None = None,
                 atlas_url: str = "", rover_url: str = ""):
        self.atlas, self.rover = atlas, rover
        self.atlas_url, self.rover_url = atlas_url, rover_url
        self._info: dict | None = None
        self.last_route: str | None = None
        self.sites: dict[str, dict] = {}          # his site ids -> {x, y}
        self.frame: dict | None = None
        self._frame_checked = False

    @property
    def split(self) -> bool:
        return self.rover is not None

    # -- raw HTTP --------------------------------------------------------------------------------
    async def _raw(self, which: str, method: str, path: str, body=None, params=None, timeout=None):
        client = self.rover if which == "rover" else self.atlas
        url = self.rover_url if which == "rover" else self.atlas_url
        try:
            kw = {"timeout": timeout} if timeout is not None else {}
            r = await client.request(method, path, json=body, params=params, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError,
                httpx.NetworkError) as e:
            raise Unreachable(which, url, str(e)) from e
        try:
            data = r.json()
        except ValueError:
            data = {"error": f"The {which} backend returned a non-JSON response (HTTP {r.status_code}): {r.text[:150]}",
                    "hint": "Check its logs."}
        return r.status_code, data

    async def _rget(self, path: str, params=None):
        st, d = await self._raw("rover", "GET", path, params=params, timeout=10.0)
        if st >= 400:
            raise _Fail(adapt_error(st, d))
        return d

    async def info(self) -> dict:
        """His GET /site, cached: profiles, rover profile, manifest, targets."""
        if self._info is None:
            d = await self._rget("/site")
            self._info = {"profiles": d.get("profiles", {}), "rover_profile": d.get("rover_profile"),
                          "default_profile": d.get("default_profile"), "manifest": d.get("manifest", {}),
                          "bounds": d.get("bounds", {}), "site": d}
        return self._info

    # -- frame check ---------------------------------------------------------------------------------
    async def check_frame(self, force: bool = False) -> dict | None:
        """GET /site on both backends once; remember the mismatch (if any). Never raises."""
        if not self.split or (self._frame_checked and not force):
            return self.frame
        try:
            ast, a = await self._raw("atlas", "GET", "/site", timeout=5.0)
            rst, r = await self._raw("rover", "GET", "/site", timeout=5.0)
            if ast == 200 and rst == 200:
                self.frame = compare_sites(a, r)
                self._frame_checked = True
        except Unreachable:
            pass
        except (KeyError, TypeError, ValueError):
            self.frame = {"message": "FRAME CHECK FAILED: could not compare GET /site of the two backends "
                                     "(unexpected shape); verify they load the same site.", "frame_mismatch": True}
            self._frame_checked = True
        return self.frame

    # -- reference normalisation ---------------------------------------------------------------------
    async def _locations(self, ref: str) -> list[dict]:
        st, d = await self._raw("atlas", "POST", "/describe", {"at": ref, "radius_m": 0.0, "layers": ["elevation"]},
                                timeout=20.0)
        if st >= 400:
            raise _Fail(adapt_error(st, d))
        return d["locations"]

    async def _rover_xy(self) -> dict:
        s = await self._rget("/rover/state")
        return {"x": s["x"], "y": s["y"]}

    async def _marker_xy(self, mid: str) -> dict | None:
        d = await self._rget("/markers")
        for m in d.get("markers", []):
            if m["id"] == mid:
                return {"x": m["x"], "y": m["y"]}
        return None

    async def norm_for_rover(self, ref: Any, many: bool = False) -> Any:
        """Make one of our location references acceptable to his resolver (set refs, here/me, find_sites ids)."""
        if isinstance(ref, list) and not (len(ref) == 2 and all(isinstance(v, (int, float)) for v in ref)):
            return [await self.norm_for_rover(r) for r in ref]
        if isinstance(ref, str):
            s = ref.strip()
            if s in ("here", "me"):
                return "rover"
            if _SITE_ID.match(s) and s in self.sites:
                return dict(self.sites[s])
            if is_set_ref(s):
                locs = await self._locations(s)
                if len(locs) != 1 and not many:
                    raise _Fail({"error": f"{ref!r} resolves to {len(locs)} locations; this argument needs one.",
                                 "hint": 'Use one set member such as "3.1".'})
                pts = [{"x": l["x"], "y": l["y"]} for l in locs]
                return pts if many else pts[0]
        return ref

    async def norm_for_atlas(self, body: Any) -> Any:
        """Replace rover/marker/find_sites references inside a query-engine request with coordinates, because the
        query engine does not know his rover or his markers."""
        needs = _walk_strings(body)
        if not any(s in ("rover", "here", "me") or s.startswith("marker:") or _SITE_ID.match(s) or re.match(r"^m\d+$", s)
                   for s in needs):
            return body
        cache: dict[str, Any] = {}

        async def sub(s: str):
            if s in cache:
                return cache[s]
            v = s
            if s in ("rover", "here", "me"):
                v = await self._rover_xy()
            elif s.startswith("marker:") or re.match(r"^m\d+$", s):
                xy = await self._marker_xy(s.removeprefix("marker:"))
                if xy is None and s.startswith("marker:"):
                    raise _Fail({"error": f'Unknown marker "{s}" on the rover backend.', "hint": "Call list_markers."})
                v = xy or s
            elif _SITE_ID.match(s) and s in self.sites:
                v = dict(self.sites[s])
            cache[s] = v
            return v

        async def walk(o):
            if isinstance(o, str):
                return await sub(o)
            if isinstance(o, list):
                return [await walk(v) for v in o]
            if isinstance(o, dict):
                return {k: (await walk(v) if k not in ("layer", "evidence", "metric", "profile", "objective", "op",
                                                       "min_tier", "kind", "query", "sort") else v)
                        for k, v in o.items()}
            return o
        return await walk(body)

    # -- public entry --------------------------------------------------------------------------------
    async def request(self, method: str, path: str, *, json_body=None, params=None, timeout=None):
        """-> (status, data). Raises Unreachable(which, url) when a backend cannot be contacted."""
        if not self.split:
            return await self._raw("atlas", method, path, json_body, params, timeout)
        which = backend_for(method, path)
        try:
            if which == "atlas":
                if path == "/overview" and method == "GET":
                    return await self._overview(params, timeout)
                body = await self.norm_for_atlas(json_body) if json_body is not None else None
                return await self._raw("atlas", method, path, body, params, timeout)
            return await self._rover(method, path, json_body or {}, params or {}, timeout)
        except _Fail as f:
            return 422, f.data

    # -- overview (query engine + live rover state, markers, targets from the rover backend) -----------
    async def _overview(self, params, timeout):
        st, d = await self._raw("atlas", "GET", "/overview", None, params, timeout)
        if st >= 400:
            return st, d
        warn = []
        try:
            r = await self._rget("/rover/state")
            d["rover"] = {**(d.get("rover") or {}), "x": r["x"], "y": r["y"], "heading_deg": r.get("heading_deg"),
                          "mode": r.get("mode"), "fsd_armed": r.get("fsd_armed")}
            d["markers"] = (await self._rget("/markers")).get("markers", [])
            tg = (await self._rget("/search/targets")).get("targets", [])
            d["search_targets"] = [t for t in tg if t.get("available", True)]
        except (Unreachable, _Fail) as e:
            warn.append("The rover backend could not be read, so the rover position, markers and search targets "
                        f"shown here come from the query engine's stand-in and may be wrong ({_why(e)}).")
        frame = await self.check_frame()
        if frame:
            warn.insert(0, frame["message"])
        if warn:
            d["frame_warning"] = frame["message"] if frame else None
            d["next"] = " ".join(warn + [d.get("next") or ""]).strip()
        return st, d

    # -- rover side -------------------------------------------------------------------------------------
    async def _rover(self, method: str, path: str, body: dict, params: dict, timeout):
        info = await self.info()
        raw = self._raw

        if path == "/events/agent":
            b = {k: body.get(k) for k in ("tool", "args_summary", "result_summary") if body.get(k) is not None}
            b["status"] = {"started": "started", "done": "ok", "ok": "ok", "error": "error"}.get(body.get("status"), "ok")
            if body.get("is_error"):
                b["status"] = "error"
            return await raw("rover", "POST", "/agent/event", b, None, timeout)

        if path == "/rover/state":
            s = await self._rget("/rover/state")
            f = await self._rget("/fsd/status")
            return 200, adapt_state(s, info, f.get("remaining_route_m", 0.0))

        if path == "/rover/vehicle":
            if method == "GET":
                return 200, await self._vehicles()
            name = map_vehicle(body.get("profile") or body.get("vehicle"))
            st, d = await raw("rover", "POST", "/rover/vehicle", {"profile": name}, None, timeout or 20.0)
            if st >= 400:
                tbl = await self._vehicles()
                e = adapt_error(st, d)
                e["hint"] = "Valid vehicles: " + ", ".join(tbl["vehicles"]) + "."
                return 422, e
            tbl = await self._vehicles()
            return 200, {**tbl, "ok": True, "vehicle": d["vehicle"], "title": d.get("title"),
                         "state": adapt_state(d["state"], info)}

        if path == "/fsd/status":
            return 200, adapt_fsd(await self._rget("/fsd/status"), info)

        if path == "/rover/command":
            b = dict(body)
            if "x" in b and "y" in b and "target" not in b:
                b["target"] = {"x": b.pop("x"), "y": b.pop("y")}
            if "target" in b:
                b["target"] = await self.norm_for_rover(b["target"])
            if b.get("cmd") == "goto" and b.get("target") is None:
                raise _Fail({"error": "goto needs a target.", "hint": 'Example: goto_location(target="poi:landing")'})
            try:
                b, notes = command_to_rover(b, info, self.last_route, await self._vehicle())
            except ValueError as e:
                raise _Fail({"error": str(e), "hint": "Plan a route first with plan_route."})
            st, d = await raw("rover", "POST", "/rover/command", b, None, timeout or 330.0)
            if st >= 400:
                return 422, adapt_error(st, d)
            return 200, adapt_command(d, info, notes)

        if path == "/route/science":
            b = dict(body)
            if "start" in b:
                b["start"] = await self.norm_for_rover(b["start"])
            b, notes = science_to_rover(b)
            st, d = await raw("rover", "POST", "/route/science", b, None, timeout)
            if st >= 400:
                return 422, adapt_error(st, d)
            assess = None
            if d.get("route_id"):
                self.last_route = d["route_id"]
                ast, a = await raw("rover", "GET", f"/route/{d['route_id']}/assess", None, None, 20.0)
                assess = a if ast < 400 else None
            return 200, adapt_science(d, assess, info, body, notes)

        if path == "/route" and method == "POST":
            b = dict(body)
            for k in ("start", "goal"):
                if k in b:
                    b[k] = await self.norm_for_rover(b[k])
            if "via" in b:
                b["via"] = await self.norm_for_rover(b["via"], many=False)
            # no profile given: plan for the vehicle that will drive it (his default would be the walker)
            b["profile"] = map_profile(b.get("profile")) or await self._vehicle()
            st, d = await raw("rover", "POST", "/route", b, None, timeout)
            if st >= 400:
                e = adapt_error(st, d)
                inf = infeasible_from_error(e["error"], info, b.get("profile"))
                return (200, inf) if inf else (422, e)
            self.last_route = d.get("route_id", self.last_route)
            ast, a = await raw("rover", "GET", f"/route/{d['route_id']}/assess", None, None, 20.0)
            return 200, adapt_route(d, a if ast < 400 else None, info)

        if path.startswith("/route/") and method == "GET":      # assess_route: stored route + his assessment
            rid = path.split("/", 2)[2]
            st, d = await raw("rover", "GET", f"/route/{rid}", None, None, timeout)
            if st >= 400:
                return 422, adapt_error(st, d)
            ast, a = await raw("rover", "GET", f"/route/{rid}/assess", None, None, timeout)
            return 200, adapt_route(d, a if ast < 400 else None, info)

        if path == "/markers":
            if method == "GET":
                st, d = await raw("rover", "GET", "/markers", None, None, timeout)
                return (st, adapt_markers(d)) if st < 400 else (422, adapt_error(st, d))
            b = dict(body)
            if "at" in b:
                b["at"] = await self.norm_for_rover(b["at"])
                if isinstance(b["at"], dict) and "x" in b["at"] and "y" in b["at"]:
                    at = b.pop("at")
                    b["x"], b["y"] = at["x"], at["y"]
            st, d = await raw("rover", "POST", "/markers", b, None, timeout)
            return (st, d) if st < 400 else (422, adapt_error(st, d))

        if path == "/view":
            b = dict(body)
            if "target" in b:
                b["target"] = await self.norm_for_rover(b["target"])
            if b.get("highlights") is not None:
                b["highlights"] = await self._highlights(b["highlights"])
            try:
                b = view_to_rover(b)
            except ValueError as e:
                raise _Fail({"error": str(e), "hint": None})
            st, d = await raw("rover", "POST", "/view", b, None, timeout)
            return (200, adapt_view(d, b)) if st < 400 else (422, adapt_error(st, d))

        if path == "/search/sites":
            b = dict(body)
            if "near" in b:
                b["near"] = await self.norm_for_rover(b["near"])
            b, notes = sites_to_rover(b)
            b["profile"] = map_profile(b.get("profile")) or await self._vehicle() or "rover_scout"
            st, d = await raw("rover", "POST", "/search/sites", b, None, timeout or 60.0)
            if st >= 400:
                return 422, adapt_error(st, d)
            tg = (await self._rget("/search/targets")).get("targets", [])
            kind = next((t["kind"] for t in tg if t["target"] == d.get("target")), None)
            near = await self._near_point(b["near"], d.get("near"))
            for s in d.get("sites", []):
                self.sites[s["id"]] = {"x": s["x"], "y": s["y"]}
            return 200, adapt_sites(d, kind, near, notes)

        if path == "/search/targets":
            st, d = await raw("rover", "GET", "/search/targets", None, None, timeout)
            return (st, d) if st < 400 else (422, adapt_error(st, d))

        if path == "/pois":
            p = dict(params)
            if "radius_m" in p:
                p["radius"] = p.pop("radius_m")
            if "near" in p:
                p["near"] = await self._poi_near(p["near"])
            st, d = await raw("rover", "GET", "/pois", None, p, timeout)
            if st >= 400:
                return 422, adapt_error(st, d)
            types = known = None
            if not d.get("pois"):
                _, allp = await raw("rover", "GET", "/pois", None, {"limit": 200}, 10.0)
                types = sorted({q.get("type") for q in allp.get("pois", []) if q.get("type")})
                known = [q["id"] for q in allp.get("pois", [])[:25]]
            return 200, adapt_pois(d, types, known)

        if path == "/terrain/sample":
            p = dict(params)
            p.setdefault("profile", info["rover_profile"] or "rover_scout")
            st, d = await raw("rover", "GET", "/terrain/sample", None, p, timeout)
            return (200, adapt_sample(d, info, p["profile"])) if st < 400 else (422, adapt_error(st, d))

        if path == "/terrain/profile":
            b = dict(body)
            b["points"] = await self.norm_for_rover(b.get("points"))
            b["points"] = _flatten_points(b["points"])
            if "profile" in b:
                b["profile"] = map_profile(b["profile"])
            st, d = await raw("rover", "POST", "/terrain/profile", b, None, timeout)
            return (200, adapt_profile(d, b.get("spacing_m", 10.0))) if st < 400 else (422, adapt_error(st, d))

        if path == "/validation":
            st, d = await raw("rover", "GET", "/validation", None, None, timeout)
            return (st, d) if st < 400 else (422, adapt_error(st, d))

        return 422, {"error": f"The rover backend has no equivalent of {method} {path}.",
                     "hint": "Unset ATLAS_ROVER_API_URL to use the query engine's stand-in for this call."}

    async def _vehicle(self) -> str | None:
        """Name of the vehicle his sim is driving right now (live: POST /rover/vehicle can change it)."""
        try:
            return (await self._rget("/rover/state")).get("vehicle")
        except (_Fail, Unreachable):
            return None

    async def _vehicles(self) -> dict:
        info = await self.info()
        d = await self._rget("/rover/vehicles")
        return vehicle_table(d.get("vehicles", {}), info["profiles"], d.get("current"))

    async def _highlights(self, hl: Any) -> list[str]:
        """His highlights are id strings. Expand our set refs into member ids; refuse raw coordinates."""
        items = hl if isinstance(hl, list) else [hl]
        out: list[str] = []
        for h in items:
            if isinstance(h, dict):
                raise _Fail({"error": "The rover backend can only highlight ids (POI, marker or site ids), not raw "
                                      "coordinates.", "hint": 'Use "poi:<id>", "marker:<id>" or a find_sites id.'})
            s = str(h).strip()
            if s.startswith("set:") or (is_set_ref(s) and "." not in s):
                sid = s.removeprefix("set:")
                st, d = await self._raw("atlas", "GET", f"/sets/{sid}", None, {"limit": 50}, 20.0)
                if st >= 400:
                    raise _Fail(adapt_error(st, d))
                out += [str(m["id"]) for m in d.get("members", d.get("results", []))]
            else:
                out.append(s)
        return out[:50]

    async def _poi_near(self, near: Any) -> Any:
        v = await self.norm_for_rover(near)
        if isinstance(v, dict):
            return f"{v['x']},{v['y']}"
        return v

    async def _near_point(self, near: Any, label: Any) -> dict:
        """{id, x, y} of the search origin for fmt_sites (his response only returns a label)."""
        if isinstance(near, dict) and "x" in near:
            return {"id": label or "point", "x": near["x"], "y": near["y"]}
        try:
            d = await self._rget("/terrain/sample", {"at": near if isinstance(near, str) else json.dumps(near)})
            return {"id": label or str(near), "x": d["x"], "y": d["y"]}
        except (_Fail, Unreachable, KeyError):
            return {"id": label or str(near), "x": 0.0, "y": 0.0}


class _Fail(Exception):
    def __init__(self, data: dict):
        super().__init__(data.get("error"))
        self.data = data


def _why(e: Exception) -> str:
    return e.data.get("error", "error") if isinstance(e, _Fail) else str(e)


def _walk_strings(o: Any) -> list[str]:
    if isinstance(o, str):
        return [o]
    if isinstance(o, list):
        return [s for v in o for s in _walk_strings(v)]
    if isinstance(o, dict):
        return [s for k, v in o.items() if k not in ("layer", "evidence", "metric", "profile", "objective", "op",
                                                     "min_tier", "kind", "query", "sort") for s in _walk_strings(v)]
    return []


def _flatten_points(pts: Any) -> list:
    out: list = []
    for p in pts if isinstance(pts, list) else [pts]:
        if isinstance(p, list) and p and isinstance(p[0], (dict, list, str)):
            out += _flatten_points(p)
        else:
            out.append(p)
    return out
