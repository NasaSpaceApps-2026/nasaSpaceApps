"""Mid-mission astronaut tools: pure functions over a Site (no server state except the small EvaState value object).

The crew position is the rover position of the stand-in world ("me"/"here"); the caller passes it as `crew` {x, y}.
Everything reuses the existing engines: atlas_core.travel (walking-time fields), route.plan_route (routes), envelope (EVA
budget rule), visibility (line of sight and viewshed sweep), illumination + sun (sun and terrain shadow), describe.

EVA budget rule (same as atlas_core.envelope): usable = budget x (1 - reserve); at any moment
margin = usable - elapsed - walk_back_to_nearest_haven. margin <= 0 means RETURN NOW.
Walking times: isotropic placeholder Tobler-shaped speed on a 4 m planning grid (approximate, not measured suit data).
Mission clock: EvaState.now(); an explicit `now` (UTC ISO string, or minutes since the EVA started) overrides it for tests.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from . import envelope as E
from . import illumination as I
from . import route as R
from . import sun as S
from . import travel as T
from . import visibility as V
from .errors import AtlasError
from .refs import resolve_ref
from .sets import SetStore
from .site import Site, tier_label

PROFILE = "eva_walkable"
CAUTION_MARGIN_MIN = 30.0          # assumed: below this many spare minutes the status is CAUTION
HAVEN_MERGE_M = 30.0               # havens closer than this to an existing one are the same place
ROUGH_CAUTION_M = 0.5              # assumed: rough_5x5 above this is flagged as rough underfoot
COMM_SEARCH_M = 300.0              # comm_check looks this far (walking) for a spot with line of sight
PATH_STEP_M = 4.0
MAX_PATH_M = 3000.0
COMPASS16 = ["north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast",
             "south-southeast", "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest",
             "northwest", "north-northwest"]
ASSUMPTIONS = ("Walking times use a placeholder speed model (isotropic, 4 m planning grid), not measured suit data. "
               "Sun and shadow are geometric (no dust twilight, no boulders below the 2 m grid).")


# ---- small helpers --------------------------------------------------------------------------------

def compass16(deg: float) -> str:
    """16-point compass word for a heading in degrees clockwise from north."""
    return COMPASS16[int(((deg % 360.0) + 11.25) // 22.5) % 16]


def heading_deg(dx: float, dy: float) -> float:
    """Heading in degrees clockwise from north of a displacement (x east, y north)."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _r(v, nd=1):
    return None if v is None or not math.isfinite(v) else round(float(v), nd)


def _xy(p) -> tuple[float, float]:
    return float(p["x"]), float(p["y"])


def _one(site: Site, store: SetStore | None, ref, what: str) -> dict:
    pts = resolve_ref(site, ref, store)
    if len(pts) != 1:
        raise AtlasError(f"`{what}` must be one place, but it resolved to {len(pts)}.",
                         hint='Use "base", "me", a POI id, a point {x, y} or one set member.')
    return pts[0]


def field_at(site: Site, field: np.ndarray, x: float, y: float, win: int = 3) -> float:
    """Value of a travel field at (x, y); an unreachable cell falls back to the best finite neighbour within `win` cells."""
    r, c = site.rc_index(x, y)
    r, c = int(r), int(c)
    n = field.shape[0]
    if not (0 <= r < n and 0 <= c < field.shape[1]):
        return math.inf
    v = float(field[r, c])
    if math.isfinite(v):
        return v
    sub = field[max(r - win, 0):r + win + 1, max(c - win, 0):c + win + 1]
    sub = sub[np.isfinite(sub)]
    return float(sub.min()) if sub.size else math.inf


# ---- EVA state and clock --------------------------------------------------------------------------

class EvaState:
    """One EVA in progress: base, budget, reserve, havens (base + parked rover + extras) and the simulated mission clock."""

    def __init__(self, base: dict, budget_min: float, reserve: float, start_utc: datetime, havens: list[dict],
                 rover_parked: dict | None, clock_offset_s: float = 0.0, already_out_min: float = 0.0):
        self.base, self.budget_min, self.reserve = base, float(budget_min), float(reserve)
        self.start_utc, self.havens, self.rover_parked = start_utc, havens, rover_parked
        self.clock_offset_s = clock_offset_s          # sim now = real now + offset (set by `advance` and by a `now` at start)
        self.already_out_min = float(already_out_min)  # time out before the clock started (astronaut told us)

    @property
    def usable_min(self) -> float:
        return self.budget_min * (1.0 - self.reserve)

    def now(self, override=None) -> datetime:
        """Simulated mission UTC time. override: None, a UTC ISO string, or minutes since the EVA started (number)."""
        if override is None:
            return datetime.now(timezone.utc) + timedelta(seconds=self.clock_offset_s)
        if isinstance(override, (int, float)) and not isinstance(override, bool):
            return self.start_utc + timedelta(minutes=float(override) - self.already_out_min)
        return S.parse_utc(override)

    def elapsed_min(self, now: datetime) -> float:
        return self.already_out_min + (now - self.start_utc).total_seconds() / 60.0

    def advance(self, minutes: float) -> None:
        self.clock_offset_s += float(minutes) * 60.0


def default_havens(site: Site, store: SetStore | None) -> list[dict]:
    try:
        b = _one(site, store, "base", "base")
        return [{"id": "base", "name": b.get("name") or "base", "x": b["x"], "y": b["y"], "kind": "base"}]
    except AtlasError:
        return []


def _merge_haven(havens: list[dict], h: dict) -> None:
    if all(math.hypot(h["x"] - o["x"], h["y"] - o["y"]) > HAVEN_MERGE_M for o in havens):
        havens.append(h)


def start_eva(site: Site, store: SetStore | None, crew: dict, base=None, budget_min: float | None = None,
              reserve: float | None = None, extra_havens=None, now=None, already_out_min: float = 0.0,
              rover_xy: tuple[float, float] | None = None) -> EvaState:
    """Start the EVA clock. `crew` is where the crew is now; `rover_xy` where the vehicle is parked (default: the crew
    position, i.e. they stepped out there). Havens = base + parked vehicle + `extra_havens`."""
    params, _ = R.route_params(site, PROFILE)
    budget = float(budget_min if budget_min is not None else params.get("eva_limit_min") or E.DEFAULT_EVA_LIMIT_MIN)
    res = float(reserve if reserve is not None else params.get("reserve_fraction") or E.DEFAULT_RESERVE)
    if not math.isfinite(budget) or budget <= 0:
        raise AtlasError("budget_min must be a number > 0 (minutes).", hint="Use e.g. 480 for an 8 hour EVA.")
    if not (0 <= res < 1):
        raise AtlasError("reserve must be a fraction from 0 up to (not including) 1.", hint="Use e.g. 0.2 for a 20% reserve.")
    if already_out_min is None or already_out_min < 0 or already_out_min >= budget:
        raise AtlasError("already_out_min must be between 0 and the budget (minutes).")
    b = _one(site, store, base if base is not None else "base", "base")
    real_now = datetime.now(timezone.utc)
    start = real_now if now is None else S.parse_utc(now)
    havens = default_havens(site, store) if base is None else [
        {"id": b.get("id") or "base", "name": b.get("name") or "base", "x": b["x"], "y": b["y"], "kind": "base"}]
    rover_p = {"id": "rover", "name": "the rover", "x": float(rover_xy[0] if rover_xy else crew["x"]),
               "y": float(rover_xy[1] if rover_xy else crew["y"]), "kind": "rover"}
    _merge_haven(havens, rover_p)
    for k, ref in enumerate(extra_havens or []):
        p = _one(site, store, ref, "havens")
        _merge_haven(havens, {"id": f"haven{k + 1}", "name": p.get("name") or f"shelter {k + 1}",
                              "x": p["x"], "y": p["y"], "kind": "shelter"})
    return EvaState(base={"id": b.get("id"), "name": b.get("name"), "x": b["x"], "y": b["y"]}, budget_min=budget,
                    reserve=res, start_utc=start, havens=havens, rover_parked=rover_p,
                    clock_offset_s=(start - real_now).total_seconds(), already_out_min=already_out_min)


# ---- named places ---------------------------------------------------------------------------------

def nearest_place(places: list[dict] | None, x: float, y: float) -> dict | None:
    """Nearest named place {name, distance_m, direction, heading_deg} from a list of {name, x, y}; None if none."""
    best = None
    for p in places or []:
        d = math.hypot(p["x"] - x, p["y"] - y)
        if best is None or d < best[0]:
            best = (d, p)
    if best is None:
        return None
    d, p = best
    h = heading_deg(p["x"] - x, p["y"] - y)
    return {"name": p["name"], "distance_m": _r(d, 0), "direction_from_place": compass16((h + 180) % 360),
            "heading_to_place_deg": _r(h, 0)}


# ---- sun / daylight -------------------------------------------------------------------------------

def _sun(site: Site, t: datetime) -> tuple[float, float]:
    sp = S.sun_position(t, site.lat0, site.lon0)
    return sp["sun_elevation_deg"], sp["sun_azimuth_deg"]


def _lit(site, zfill, row, col, t) -> bool:
    el, az = _sun(site, t)
    return el > 0 and bool(I.points_lit(zfill, site.res_m, np.array([row]), np.array([col]), el, az)[0])


def daylight(site: Site, x: float, y: float, t: datetime, horizon_min: float = 1600.0, step_min: float = 10.0) -> dict:
    """Sun at (x, y) at time t: elevation, minutes to sunset (sun centre at the geometric horizon) and minutes until this
    spot is in shade (terrain shadow or sunset, whichever is first). Scans up to `horizon_min` ahead."""
    el0, az0 = _sun(site, t)
    r, c = site.rc_index(x, y)
    r, c = int(r), int(c)
    zfill = I._filled(site)
    n = zfill.shape[0]
    if not (0 <= r < n and 0 <= c < n):
        raise AtlasError("That spot is outside the mapped site.", hint="Use a place inside the site.")
    sp = S.sun_position(t, site.lat0, site.lon0)
    out = {"utc": iso(t), "ltst": sp["ltst"], "mission_sol": sp["mission_sol"], "sun_elevation_deg": _r(el0, 1),
           "sun_azimuth_deg": _r(az0, 0), "sun_up": el0 > 0, "minutes_to_sunset": None, "sunset_ltst": None,
           "minutes_until_shaded": None, "shade_cause": None, "lit_now": False, "minutes_to_sunrise": None,
           "note": ("Geometric sun on the 2 m terrain: no dust twilight (on Mars it stays light-ish for roughly 2 h after "
                    "sunset), no boulders. 'Shaded' means no direct sun.")}
    lit0 = _lit(site, zfill, r, c, t)
    out["lit_now"] = lit0
    if el0 <= 0:                       # night: when does the sun come back
        k, prev = 1, t
        while k * step_min <= horizon_min:
            tt = t + timedelta(minutes=k * step_min)
            if _sun(site, tt)[0] > 0:
                a, b = prev, tt
                for _ in range(6):
                    m = a + (b - a) / 2
                    a, b = (m, b) if _sun(site, m)[0] <= 0 else (a, m)
                out["minutes_to_sunrise"] = _r((b - t).total_seconds() / 60.0, 0)
                break
            prev = tt
            k += 1
        out["shade_cause"] = "night"
        out["minutes_until_shaded"] = 0.0
        return out
    sunset = shade = None
    prev, prev_lit = t, lit0
    if not lit0:
        shade = (0.0, "terrain shadow")
    k = 1
    while k * step_min <= horizon_min and sunset is None:
        tt = t + timedelta(minutes=k * step_min)
        el, _az = _sun(site, tt)
        if el <= 0:
            a, b = prev, tt
            for _ in range(7):
                m = a + (b - a) / 2
                a, b = (m, b) if _sun(site, m)[0] > 0 else (a, m)
            sunset = b
            if shade is None:
                shade = ((b - t).total_seconds() / 60.0, "sunset")
            break
        if shade is None:
            lit = _lit(site, zfill, r, c, tt)
            if not lit:
                a, b = prev, tt
                for _ in range(6):
                    m = a + (b - a) / 2
                    a, b = (m, b) if _lit(site, zfill, r, c, m) else (a, m)
                shade = ((b - t).total_seconds() / 60.0, "terrain shadow")
        prev = tt
        k += 1
    if sunset is not None:
        out["minutes_to_sunset"] = _r((sunset - t).total_seconds() / 60.0, 0)
        out["sunset_ltst"] = S.hms(S.ltst_hours(sunset, site.lon0))
        out["sunset_utc"] = iso(sunset)
    if shade is not None:
        out["minutes_until_shaded"] = _r(shade[0], 0)
        out["shade_cause"] = shade[1]
    return out


# ---- havens and status ----------------------------------------------------------------------------

def _crew_field(site, crew, metric="walk_time_min", profile=PROFILE):
    return T.travel_field(site, [{"x": float(crew["x"]), "y": float(crew["y"])}], metric, profile)


def rank_havens(site: Site, state: EvaState | None, crew: dict, store: SetStore | None = None,
                field: np.ndarray | None = None) -> list[dict]:
    """Havens ranked by walking time from the crew (nearest first). Each: name, kind, x, y, walk_min, straight_m,
    heading_deg, direction. walk_min is None when there is no walkable path."""
    havens = state.havens if state else default_havens(site, store)
    if not havens:
        raise AtlasError("No havens known (no base on this site).", hint="Start an EVA with a base, or pass a point.")
    f = field if field is not None else _crew_field(site, crew)
    out = []
    for h in havens:
        t = field_at(site, f, h["x"], h["y"])
        dx, dy = h["x"] - crew["x"], h["y"] - crew["y"]
        hd = heading_deg(dx, dy)
        out.append({"id": h["id"], "name": h["name"], "kind": h["kind"], "x": _r(h["x"]), "y": _r(h["y"]),
                    "walk_min": _r(t) if math.isfinite(t) else None, "straight_m": _r(math.hypot(dx, dy), 0),
                    "heading_deg": _r(hd, 0), "direction": compass16(hd)})
    out.sort(key=lambda d: (d["walk_min"] is None, d["walk_min"] or 0.0))
    return out


def _verdict(margin: float | None, walk_back: float | None) -> tuple[str, str]:
    if walk_back is None:
        return "RETURN NOW", "there is no walkable path back from here"
    if margin <= 0:
        return "RETURN NOW", "walking back now already uses up the reserve"
    if margin < CAUTION_MARGIN_MIN:
        return "CAUTION", f"under {CAUTION_MARGIN_MIN:g} min of spare time above the reserve"
    return "GO", "enough time above the reserve"


def eva_status(site: Site, store: SetStore | None, state: EvaState, crew: dict, now=None,
               places: list[dict] | None = None) -> dict:
    t = state.now(now)
    elapsed = state.elapsed_min(t)
    usable = state.usable_min
    havens = rank_havens(site, state, crew, store)
    nh = havens[0]
    wb = nh["walk_min"]
    margin = None if wb is None else usable - elapsed - wb
    status, why = _verdict(margin, wb)
    dl = daylight(site, crew["x"], crew["y"], t)
    out = {"crew": {"x": _r(crew["x"]), "y": _r(crew["y"])}, "nearest_place": nearest_place(places, crew["x"], crew["y"]),
           "utc": iso(t), "elapsed_min": _r(elapsed), "budget_min": state.budget_min, "reserve_fraction": state.reserve,
           "usable_min": _r(usable), "remaining_min": _r(state.budget_min - elapsed),
           "remaining_usable_min": _r(usable - elapsed), "nearest_haven": nh, "havens": havens,
           "walk_back_min": wb, "margin_min": _r(margin), "status": status, "status_reason": why,
           "turn_back_in_min": _r(max(margin, 0.0) / 2.0) if margin is not None else None,
           "daylight": dl, "caution_margin_min": CAUTION_MARGIN_MIN,
           "assumptions": ["margin = budget x (1 - reserve) - elapsed - walk back to the nearest haven; "
                           f"CAUTION below {CAUTION_MARGIN_MIN:g} min margin (assumed threshold). " + ASSUMPTIONS]}
    return out


# ---- routes ---------------------------------------------------------------------------------------

def _simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker polyline simplification (tolerance in metres)."""
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy) or 1e-9
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * (pts[i][0] - x1) - dx * (pts[i][1] - y1)) / L
        if d > dmax:
            dmax, idx = d, i
    if dmax <= tol:
        return [pts[0], pts[-1]]
    return _simplify(pts[:idx + 1], tol)[:-1] + _simplify(pts[idx:], tol)


def turn_summary(points: list[dict], tol: float = 15.0, max_legs: int = 8) -> list[dict]:
    """Route -> legs {heading_deg, direction, length_m, turn} (turn: signed degrees from the previous leg, + = right)."""
    pts = _simplify([(p["x"], p["y"]) for p in points], tol)
    while len(pts) - 1 > max_legs:
        tol *= 1.6
        pts = _simplify([(p["x"], p["y"]) for p in points], tol)
    legs, prev = [], None
    for a, b in zip(pts, pts[1:]):
        hd = heading_deg(b[0] - a[0], b[1] - a[1])
        turn = None if prev is None else ((hd - prev + 180.0) % 360.0) - 180.0
        legs.append({"heading_deg": _r(hd, 0), "direction": compass16(hd), "length_m": _r(math.hypot(b[0] - a[0], b[1] - a[1]), 0),
                     "turn_deg": None if turn is None else _r(turn, 0)})
        prev = hd
    return legs


def return_route(site: Site, store: SetStore | None, state: EvaState | None, crew: dict, to=None, now=None,
                 budget_min: float | None = None, reserve: float | None = None) -> dict:
    """Fastest safe walking route (profile eva_walkable: slope limit and unknown ground respected) to the nearest haven,
    or to `to`. Time, distance, steepest slope, legs, and the point-of-no-return check against the EVA budget."""
    if to is not None:
        g = _one(site, store, to, "to")
        goal = {"x": g["x"], "y": g["y"]}
        goal_name = g.get("name") or g.get("id") or "the given spot"
    else:
        hv = rank_havens(site, state, crew, store)
        if hv[0]["walk_min"] is None:
            return {"feasible": False, "reason": "No walkable path to any known haven from here.",
                    "hint": "Check the ground around you (check_path_ahead) or call for help.", "havens": hv}
        goal = {"x": hv[0]["x"], "y": hv[0]["y"]}
        goal_name = hv[0]["name"]
    rt = R.plan_route(site, store, {"x": crew["x"], "y": crew["y"]}, goal, None, PROFILE)
    if not rt.get("feasible"):
        return {"feasible": False, "reason": rt.get("reason"), "hint": rt.get("hint"),
                "nearest_reachable": rt.get("nearest_reachable"), "goal_name": goal_name}
    out = {"feasible": True, "goal_name": goal_name, "goal": {"x": _r(goal["x"]), "y": _r(goal["y"])},
           "duration_min": rt["duration_min"], "distance_m": rt["distance_m"], "max_slope_deg": rt["max_slope_deg"],
           "ascent_m": rt["ascent_m"], "legs": turn_summary(rt["points"]), "warnings": rt.get("warnings", []),
           "caution_slope_deg": rt.get("caution_slope_deg"), "slope_limit_deg": rt.get("max_slope_limit_deg"),
           "assumptions": [ASSUMPTIONS]}
    h0 = rt["points"][0]
    out["start"] = {"x": h0["x"], "y": h0["y"]}
    if state is not None:
        t = state.now(now)
        elapsed = state.elapsed_min(t)
        usable = state.usable_min
        arrive = elapsed + rt["duration_min"]
        margin = usable - arrive
        out.update({"elapsed_min": _r(elapsed), "usable_min": _r(usable), "arrive_elapsed_min": _r(arrive),
                    "margin_at_arrival_min": _r(margin), "within_reserve": margin >= 0,
                    "point_of_no_return": ("PAST: leave now, the walk back already eats into the reserve"
                                           if margin < 0 else
                                           f"about {max(margin, 0) / 2.0:.0f} min of walking farther out before you must turn back")})
    return out


# ---- the path ahead -------------------------------------------------------------------------------

def check_path_ahead(site: Site, store: SetStore | None, state: EvaState | None, crew: dict, heading=None, toward=None,
                     distance_m: float | None = None, now=None) -> dict:
    """Slope / roughness / data / shadow profile along a straight line from the crew, first hazard and distance, and the
    EVA margin along it. Give `heading` (degrees clockwise from north) or `toward` (a place)."""
    if toward is not None:
        tp = _one(site, store, toward, "toward")
        dx, dy = tp["x"] - crew["x"], tp["y"] - crew["y"]
        hd = heading_deg(dx, dy)
        dist = float(distance_m) if distance_m is not None else min(math.hypot(dx, dy), MAX_PATH_M)
    elif heading is not None:
        hd = float(heading) % 360.0
        dist = float(distance_m if distance_m is not None else 200.0)
    else:
        raise AtlasError("Give a heading (degrees) or a place to head toward.", hint='e.g. heading_deg=0 for north, or toward="the delta".')
    if not math.isfinite(dist) or dist <= 0:
        raise AtlasError("distance_m must be a positive number of metres.")
    notes = []
    if dist > MAX_PATH_M:
        notes.append(f"Checked the first {MAX_PATH_M:.0f} m only (you asked for {dist:.0f} m).")
        dist = MAX_PATH_M
    params, _ = R.route_params(site, PROFILE)
    caution, nogo = params["caution_slope_deg"], params["max_slope_deg"]
    step = max(PATH_STEP_M, dist / 700.0)
    ds = np.arange(0.0, dist + 1e-6, step)
    if ds[-1] < dist - 1e-6:
        ds = np.append(ds, dist)
    ux, uy = math.sin(math.radians(hd)), math.cos(math.radians(hd))
    xs, ys = crew["x"] + ux * ds, crew["y"] + uy * ds
    half = site.half_m
    inside = (np.abs(xs) < half) & (np.abs(ys) < half)
    edge_at = None if inside.all() else float(ds[int(np.argmin(inside))])
    rr, cc = site.rc_index(np.where(inside, xs, 0.0), np.where(inside, ys, 0.0))
    rr, cc = np.asarray(rr, int), np.asarray(cc, int)
    slope = np.where(inside, site.layer("slope_2m").data[rr, cc], np.nan).astype(float)
    rough_l = site.layers.get("rough_5x5")
    rough = np.where(inside, rough_l.data[rr, cc], np.nan).astype(float) if rough_l is not None else np.full(ds.shape, np.nan)
    t = state.now(now) if state else (datetime.now(timezone.utc) if now is None else S.parse_utc(now))
    el, az = _sun(site, t)
    if el > 0:
        lit = np.where(inside, I.points_lit(I._filled(site), site.res_m, rr, cc, el, az), False)
    else:
        lit = np.zeros(ds.shape, bool)
    unknown = ~np.isfinite(slope)
    sev = np.zeros(ds.shape, int)                      # 0 ok, 1 caution, 2 blocked
    kind = np.array([""] * len(ds), dtype=object)
    for mask, s, k in ((np.isfinite(rough) & (rough > ROUGH_CAUTION_M), 1, "rough ground"),
                       (np.isfinite(slope) & (slope > caution), 1, "steep slope"),
                       (np.isfinite(slope) & (slope > nogo), 2, "too steep to walk"),
                       (unknown & inside, 2, "no map data (unknown ground)"),
                       (~inside, 2, "edge of the mapped area")):
        sev[mask] = s
        kind[mask] = k
    hazards = np.flatnonzero(sev > 0)
    blockers = np.flatnonzero(sev == 2)

    def hz(i):
        return {"distance_m": _r(ds[i], 0), "kind": kind[i], "severity": "blocked" if sev[i] == 2 else "caution",
                "slope_deg": _r(slope[i]), "x": _r(xs[i]), "y": _r(ys[i])}
    pts = [{"x": float(a), "y": float(b)} for a, b in zip(xs[inside], ys[inside])]
    tmin = E._elapsed_minutes(site, pts, params) if len(pts) > 1 else np.zeros(len(pts))
    shade_i = np.flatnonzero(~lit & inside)
    seg = max(step, 50.0)
    segs = []
    for a in np.arange(0.0, ds[-1] + 1e-6, seg):
        m = (ds >= a) & (ds < a + seg) & inside
        if m.any():
            sl = slope[m]
            segs.append({"from_m": _r(a, 0), "to_m": _r(min(a + seg, ds[-1]), 0),
                         "mean_slope_deg": _r(np.nanmean(sl)) if np.isfinite(sl).any() else None,
                         "max_slope_deg": _r(np.nanmax(sl)) if np.isfinite(sl).any() else None,
                         "shaded_fraction": _r(1.0 - float(lit[m].mean()), 2)})
    out = {"heading_deg": _r(hd, 0), "direction": compass16(hd), "distance_m": _r(ds[-1], 0), "step_m": _r(step, 1),
           "slope": {"max_deg": _r(np.nanmax(slope)) if np.isfinite(slope).any() else None,
                     "mean_deg": _r(np.nanmean(slope)) if np.isfinite(slope).any() else None,
                     "caution_deg": caution, "no_go_deg": nogo},
           "roughness_max_m": _r(np.nanmax(rough), 2) if np.isfinite(rough).any() else None,
           "roughness_caution_m": ROUGH_CAUTION_M, "unknown_fraction": _r(float((unknown & inside).mean()), 2),
           "sun_elevation_deg": _r(el), "shaded_fraction": _r(1.0 - float(lit[inside].mean()), 2) if inside.any() else None,
           "first_shadow_at_m": _r(ds[shade_i[0]], 0) if len(shade_i) and el > 0 else None, "sun_up": el > 0,
           "walk_min": _r(tmin[-1]) if len(tmin) else None, "segments": segs,
           "first_hazard": hz(hazards[0]) if len(hazards) else None,
           "first_blocker": hz(blockers[0]) if len(blockers) else None,
           "hazard_count": int(len(hazards)), "edge_of_map_at_m": _r(edge_at, 0), "notes": notes,
           "assumptions": ["Straight line sampled on the 2 m slope map (rocks under about 3 m are not resolved); "
                           f"caution slope {caution:g} deg and no-go slope {nogo:g} deg come from the walking profile; "
                           f"rough ground = 5x5 roughness above {ROUGH_CAUTION_M:g} m (assumed). " + ASSUMPTIONS]}
    if state is not None and len(pts) > 1:
        back_f = T.travel_field(site, [{"x": h["x"], "y": h["y"]} for h in state.havens], "walk_time_min", PROFILE)
        elapsed = state.elapsed_min(t)
        back = np.array([field_at(site, back_f, p["x"], p["y"], 1) for p in pts])
        margin = state.usable_min - (elapsed + tmin + back)
        bad = np.flatnonzero(~np.isfinite(margin) | (margin < 0))
        dist_in = ds[inside]
        out["margin_at_end_min"] = _r(margin[-1]) if np.isfinite(margin[-1]) else None
        out["margin_start_min"] = _r(margin[0]) if np.isfinite(margin[0]) else None
        out["point_of_no_return_m"] = _r(dist_in[bad[0]], 0) if len(bad) else None
        out["return_margin_note"] = ("margin = usable budget - elapsed - walk out - walk back from there to the nearest haven; "
                                     "negative means past the point of no return.")
    return out


# ---- comms ----------------------------------------------------------------------------------------

def comm_check(site: Site, store: SetStore | None, crew: dict, to="base", antenna_h: float = 1.7,
               to_antenna_h: float | None = None, search_m: float = COMM_SEARCH_M) -> dict:
    """Geometric line of sight crew -> `to`. If blocked, the nearest spot within `search_m` of walking that restores it."""
    tgt = _one(site, store, to, "to")
    toh = antenna_h if to_antenna_h is None else to_antenna_h
    los = V.line_of_sight(site, {"x": crew["x"], "y": crew["y"]}, {"x": tgt["x"], "y": tgt["y"]}, antenna_h, toh)
    dist = math.hypot(tgt["x"] - crew["x"], tgt["y"] - crew["y"])
    hd = heading_deg(tgt["x"] - crew["x"], tgt["y"] - crew["y"])
    out = {"to_name": tgt.get("name") or tgt.get("id") or "the target", "to": {"x": _r(tgt["x"]), "y": _r(tgt["y"])},
           "distance_m": _r(dist, 0), "heading_to_deg": _r(hd, 0), "direction_to": compass16(hd), "clear": bool(los["clear"]),
           "antenna_h_m": antenna_h, "to_antenna_h_m": toh,
           "blocking_point": los.get("blocking_point"), "min_clearance_m": los.get("min_clearance_m"),
           "clearance_deficit_m": los.get("clearance_deficit_m"),
           "assumptions": ["Geometric line of sight only (terrain between the two antennas at "
                           f"{antenna_h:g} m and {toh:g} m height); no radio model (Fresnel zone, power, frequency)."]}
    if los["clear"]:
        return out
    dem, (x0, y0), res = V._dem(site), V._geom(site), site.res_m
    z_t = V._elevation_at(site, tgt["x"], tgt["y"])
    radius = min(dist + search_m + 100.0, 6000.0)
    vis, r_lo, c_lo, _st = V._sweep(dem, res, x0, y0, tgt["x"], tgt["y"], z_t + toh, antenna_h, radius)
    wm = _crew_field(site, crew, "walk_m")
    n = wm.shape[0]
    h, w = vis.shape
    sub = wm[r_lo:r_lo + h, c_lo:c_lo + w]
    cand = vis[:sub.shape[0], :sub.shape[1]] & np.isfinite(sub) & (sub <= search_m)
    if not cand.any():
        out["restore"] = None
        out["hint"] = (f"No spot within {search_m:.0f} m of walking has line of sight to {out['to_name']}. Try a higher antenna "
                       "or relay through the rover, or head back toward base.")
        return out
    idx = int(np.argmin(np.where(cand, sub, np.inf)))
    rr, cc = divmod(idx, sub.shape[1])
    row, col = r_lo + rr, c_lo + cc
    xs, ys = site.rc_to_xy(row, col)
    px, py = float(xs), float(ys)
    wt = field_at(site, _crew_field(site, crew), px, py)
    d2 = math.hypot(px - crew["x"], py - crew["y"])
    h2 = heading_deg(px - crew["x"], py - crew["y"])
    out["restore"] = {"x": _r(px), "y": _r(py), "walk_m": _r(float(sub[rr, cc]), 0), "straight_m": _r(d2, 0),
                      "walk_min": _r(wt) if math.isfinite(wt) else None, "heading_deg": _r(h2, 0), "direction": compass16(h2)}
    return out


# ---- samples --------------------------------------------------------------------------------------

def sample_evidence(site: Site, store: SetStore | None, x: float, y: float) -> dict:
    """Geologic unit and mineral evidence (tiers) at a point, from describe_location; tiers are orbital unless confirmed."""
    from . import describe as D
    loc = D.describe_location(site, store or SetStore(), {"x": x, "y": y}, 0.0)["locations"][0]
    unit, minerals = None, []
    for lid, v in loc["values"].items():
        lay = site.layers[lid]
        if lay.kind == "categorical" and "class" in v and unit is None and "unit" in lid:
            unit = v["class"]
        elif lay.kind == "evidence" and "tier" in v:
            minerals.append({"mineral": lid, "tier": v["tier"], "tier_name": v["tier_name"],
                             "tier_label": v.get("tier_label") or tier_label(v["tier_name"])})
        elif lay.kind == "evidence":
            minerals.append({"mineral": lid, "tier": None, "tier_name": "no_coverage",
                             "tier_label": tier_label(None)})
    return {"unit": unit, "minerals": minerals, "quality": loc.get("quality"),
            "elevation_m": (loc["values"].get("elevation") or {}).get("value")}


# ---- bearing / rendezvous -------------------------------------------------------------------------

def bearing_to(site: Site, store: SetStore | None, crew: dict, target) -> dict:
    """Heading (degrees + compass point), straight distance and walking time/distance to a place."""
    tg = _one(site, store, target, "target")
    dx, dy = tg["x"] - crew["x"], tg["y"] - crew["y"]
    hd = heading_deg(dx, dy)
    out = {"target_name": tg.get("name") or tg.get("id") or "the target", "target": {"x": _r(tg["x"]), "y": _r(tg["y"])},
           "heading_deg": _r(hd, 0), "direction": compass16(hd), "straight_m": _r(math.hypot(dx, dy), 0),
           "assumptions": [ASSUMPTIONS]}
    rt = R.plan_route(site, store, {"x": crew["x"], "y": crew["y"]}, {"x": tg["x"], "y": tg["y"]}, None, PROFILE)
    if rt.get("feasible"):
        out.update({"walk_min": rt["duration_min"], "walk_m": rt["distance_m"], "max_slope_deg": rt["max_slope_deg"]})
    else:
        out.update({"walk_min": None, "walk_m": None, "walk_problem": rt.get("reason")})
    return out


def rover_profile(site: Site, prefer: str | None = None) -> str:
    """The vehicle that comes to meet the crew: the active vehicle if it is a crewed one, else crew_rover, crew_buggy, rover_scout."""
    order = ([prefer] if prefer in ("crew_buggy", "crew_rover") else []) + ["crew_rover", "crew_buggy", "rover_scout"]
    for p in order:
        if isinstance(site.profiles.get(p), dict) and site.profiles[p].get("travel"):
            return p
    raise AtlasError("No vehicle profile with a speed is defined for this site.")


def rendezvous(site: Site, store: SetStore | None, crew: dict, rover_xy: tuple[float, float], tol_min: float = 0.25,
               vehicle: str | None = None) -> dict:
    """Meeting point minimising max(crew walk time, rover drive time) over every reachable cell; compares meeting at the
    rover and the rover coming to the crew."""
    rp = rover_profile(site, vehicle)
    tw = _crew_field(site, crew)
    td = T.travel_field(site, [{"x": rover_xy[0], "y": rover_xy[1]}], "walk_time_min", rp)
    cost = np.fmax(tw, td)
    cost = np.where(np.isfinite(tw) & np.isfinite(td), cost, np.inf)
    if not np.isfinite(cost).any():
        return {"feasible": False, "reason": "No point is reachable by both the crew on foot and the rover.",
                "rover_profile": rp}
    best = float(cost.min())
    pick = np.where(cost <= best + tol_min, tw, np.inf)       # among the best, the one that needs the least walking
    i = int(np.argmin(pick))
    r, c = divmod(i, cost.shape[1])
    x, y = (float(v) for v in site.rc_to_xy(r, c))

    def cand(label, px, py):
        a, b = field_at(site, tw, px, py), field_at(site, td, px, py)
        return {"label": label, "x": _r(px), "y": _r(py), "crew_walk_min": _r(a) if math.isfinite(a) else None,
                "rover_drive_min": _r(b) if math.isfinite(b) else None,
                "worst_min": _r(max(a, b)) if math.isfinite(max(a, b)) else None}
    opt = cand("best meeting point", x, y)
    cands = [opt, cand("meet at the rover (you walk to it)", rover_xy[0], rover_xy[1]),
             cand("rover comes to you", crew["x"], crew["y"])]
    hd = heading_deg(x - crew["x"], y - crew["y"])
    opt.update({"heading_from_crew_deg": _r(hd, 0), "direction": compass16(hd),
                "straight_m_from_crew": _r(math.hypot(x - crew["x"], y - crew["y"]), 0),
                "wait_min": _r(abs((opt["crew_walk_min"] or 0) - (opt["rover_drive_min"] or 0)))})
    return {"feasible": True, "meeting_point": opt, "candidates": cands, "rover_profile": rp,
            "rover": {"x": _r(rover_xy[0]), "y": _r(rover_xy[1])},
            "method": ("every reachable cell scored by max(crew walk time, rover drive time); the best is chosen, ties "
                       "(within 0.25 min) broken by the least walking for the crew. The rover is assumed to drive on its own "
                       "speed profile; " + ASSUMPTIONS),
            "assumptions": [ASSUMPTIONS]}
