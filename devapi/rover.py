"""Rover simulator (PRD "Rover simulator", scope doc FSD policy).

Terrain-following v1: pose (z, pitch, roll) from four wheel samples of the site DEM, speed from the
profile's slope curve, no-go slope enforced step by step. Blocking commands simulate the whole motion before
returning one {state, stop_reason} result. By default the motion is stepped (1 m steps, no waiting) so tests
and agents get an immediate answer; `realtime: true` runs at 10 Hz wall-clock with a `speed_factor` so the
viewer sees the rover move.

Stop reasons: arrived, blocked_slope, blocked_hazard, replanned, stopped_by_user, timeout.
FSD: when armed, caution slopes halve the speed, and a blocked route-following command is replanned once
(stop_reason `replanned`, new route_id returned, rover stopped; call follow_route again).
"""
from __future__ import annotations

import asyncio
import math
import time
from contextlib import asynccontextmanager

from atlas_core import AtlasError, resolve_ref, route as R

STEP_M = 1.0
BROADCAST_EVERY_M = 5.0
ARRIVE_TOL_M = 3.0
REPLAN_SLOPE_FACTOR = 0.9
DISPLAY_SPEED_MPS = 8.0
MAX_DRIVE_M = 5000.0


def _bearing(a, b) -> float:
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0


class Rover:
    """Terrain-following rover simulator (pose from four wheel samples of the DEM, slope-limited speed, no-go enforcement); see the module docstring for stop reasons and FSD."""
    def __init__(self, world):
        self.w = world
        site = world.site
        base = next((p for p in site.pois if p.get("type") == "base"), None)
        self.x, self.y = (float(base["x"]), float(base["y"])) if base else (0.0, 0.0)
        self.heading = 0.0
        self.z = self.pitch = self.roll = 0.0
        self.speed = 0.0
        self.odometer = 0.0
        self.route_id: str | None = None
        self.status = "idle"
        self.last_event = "rover spawned"
        self.remaining_m = 0.0
        self.sim_s = 0.0
        self.profile = "rover_scout"
        self.fsd_armed = False
        self.policy = {"replan_on_block": True, "caution_slowdown": True}
        self.filled_policy = "caution"   # stand-in terrain has no interpolated cells; accepted for API parity
        self.replans_used = 0
        self.last_hazard: dict | None = None
        self.running = False
        self.manual = False
        self._cancel = False
        self._lock = asyncio.Lock()
        self._refresh_pose()

    # ---- state ----------------------------------------------------------------------------------
    def _refresh_pose(self) -> None:
        z, p, r = R.pose_at(self.w.site, self.x, self.y, self.heading)
        self.z, self.pitch, self.roll = z, p, r
        self.w.site.rover_xy = (self.x, self.y)

    @property
    def mode(self) -> str:
        if self.manual:
            return "manual"
        if self.fsd_armed:
            return "fsd"
        return "auto" if self.running else "idle"

    def state(self) -> dict:
        lat, lon = self.w.site.xy_to_latlon(self.x, self.y)
        return {
            "x": round(self.x, 2), "y": round(self.y, 2),
            "z": round(self.z, 2) if math.isfinite(self.z) else None,
            "lat": round(lat, 5), "lon": round(lon, 5),
            "heading_deg": round(self.heading % 360.0, 1), "pitch_deg": round(self.pitch, 1),
            "roll_deg": round(self.roll, 1), "speed_mps": round(self.speed, 3), "mode": self.mode,
            "odometer_m": round(self.odometer, 1), "route_id": self.route_id, "status": self.status,
            "last_event": self.last_event, "fsd_armed": self.fsd_armed,
            "remaining_m": round(self.remaining_m, 1), "profile": self.profile,
            "sim_elapsed_s": round(self.sim_s, 1), **self._limits(),
        }

    def _limits(self) -> dict:
        """Active vehicle's caution / no-go slopes (the real API reports both in /rover/state and /fsd/status)."""
        try:
            prm, _ = R.route_params(self.w.site, self.profile)
            return {"caution_slope_deg": prm["caution_slope_deg"], "nogo_slope_deg": prm["max_slope_deg"]}
        except Exception:  # noqa: BLE001
            return {}

    async def _emit(self) -> None:
        await self.w.hub.broadcast("rover.state", self.state())

    def fsd_status(self) -> dict:
        blockers = []
        if self.remaining_m > 0 and self.last_hazard:
            blockers.append(self.last_hazard["detail"])
        if self.fsd_armed and not self.route_id:
            blockers.append("no active route (plan_route, then follow_route)")
        return {
            "armed": self.fsd_armed, "mode": self.mode, "policy": {**self.policy, "filled": self.filled_policy},
            **self._limits(), "replans_used": self.replans_used,
            "replans_allowed": 1 if self.policy.get("replan_on_block") else 0,
            "last_hazard": self.last_hazard, "remaining_route_m": round(self.remaining_m, 1),
            "route_id": self.route_id, "latest_planned_route": self.w.last_planned,
            "rover_profile": self.profile, "blockers": blockers,
        }

    # ---- viewer-driven pose --------------------------------------------------------------------
    def set_manual_pose(self, msg: dict) -> None:
        """rover.pose from the viewer (manual driving): the viewer owns the pose; cancel any command."""
        self._cancel = True
        self.manual = True
        self.x, self.y = float(msg["x"]), float(msg["y"])
        if "heading_deg" in msg:
            self.heading = float(msg["heading_deg"])
        self._refresh_pose()
        for src, dst in (("z", "z"), ("pitch_deg", "pitch"), ("roll_deg", "roll")):
            if isinstance(msg.get(src), (int, float)):
                setattr(self, dst, float(msg[src]))
        self.speed = float(msg.get("speed_mps", 0.0) or 0.0)
        self.status = "manual driving"

    # ---- motion ---------------------------------------------------------------------------------
    @asynccontextmanager
    async def _motion(self):
        if self.running:
            self._cancel = True
        async with self._lock:
            self._cancel = False
            self.manual = False
            yield

    async def set_profile(self, profile: str) -> None:
        """Switch the driven vehicle (any profile with a travel block); stops a drive in progress."""
        R.route_params(self.w.site, profile)      # validates
        if self.running:
            self._cancel = True
        self.profile = profile
        self.last_event = f"vehicle changed to {profile}"
        await self._emit()

    def _params(self, profile: str | None = None) -> dict:
        prof = profile or self.profile
        params, _ = R.route_params(self.w.site, prof)
        return params

    def _hazard(self, x: float, y: float, params: dict):
        if not self.w.site.in_bounds(x, y):
            return "blocked_hazard", "reached the edge of the site"
        sl = R.ground_slope(self.w.site, x, y)
        if not math.isfinite(sl):
            return "blocked_hazard", f"unknown terrain ahead at ({x:.0f}, {y:.0f}) (no data)"
        if sl > params["max_slope_deg"]:
            return "blocked_slope", (f"{sl:.0f} degree slope ahead at ({x:.0f}, {y:.0f}) exceeds the "
                                     f"{params['max_slope_deg']:g} degree no-go limit")
        return None

    async def _drive_path(self, pts, params, realtime: bool, factor: float, timeout_s: float):
        """Follow a polyline from the current position. Returns (stop_reason, detail, travelled_m)."""
        t0 = time.monotonic()
        last_bc_m = 0.0
        last_bc_t = t0
        travelled = 0.0
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))
        done_before = 0.0
        self.running = True
        self.status = "driving"
        try:
            for a, b in zip(pts[:-1], pts[1:]):
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                if L < 1e-9:
                    continue
                self.heading = _bearing(a, b)
                s = 0.0
                while s < L - 1e-9:
                    if self._cancel:
                        return self._stop("stopped_by_user", "stopped by user", travelled, total - done_before - s)
                    sl = R.ground_slope(self.w.site, self.x, self.y)
                    sl_eff = params["max_slope_deg"] if not math.isfinite(sl) else sl
                    v = float(R.speed_mps(params, sl_eff))
                    if self.fsd_armed and self.policy.get("caution_slowdown") and sl_eff > params["caution_slope_deg"]:
                        v *= 0.5
                    step = STEP_M
                    if realtime:
                        step = min(STEP_M, v * factor * 0.1)
                    step = min(step, L - s)
                    s2 = s + step
                    nx, ny = a[0] + (b[0] - a[0]) * s2 / L, a[1] + (b[1] - a[1]) * s2 / L
                    hz = self._hazard(nx, ny, params)
                    if hz:
                        return self._stop(hz[0], hz[1], travelled, total - done_before - s)
                    s = s2
                    self.x, self.y = nx, ny
                    self.odometer += step
                    travelled += step
                    self.sim_s += step / v
                    self.speed = v * (factor if realtime else 1.0)
                    self._refresh_pose()
                    self.remaining_m = max(0.0, total - done_before - s)
                    now = time.monotonic()
                    if realtime:
                        await asyncio.sleep(step / (v * factor))
                        if now - last_bc_t >= 0.099:
                            await self._emit()
                            last_bc_t = now
                        if time.monotonic() - t0 > timeout_s:
                            return self._stop("timeout", f"command timed out after {timeout_s:g} s", travelled,
                                              total - done_before - s)
                    elif travelled - last_bc_m >= BROADCAST_EVERY_M:
                        last_bc_m = travelled
                        await self._emit()
                        await asyncio.sleep(0)       # let other requests (stop) interleave
                done_before += L
            self.x, self.y = pts[-1]
            self._refresh_pose()
            self.remaining_m = 0.0
            self.speed = 0.0
            self.status = "arrived"
            return "arrived", "reached the target", travelled
        finally:
            self.running = False
            if self.speed:
                self.speed = 0.0

    def _stop(self, reason: str, detail: str, travelled: float, remaining: float):
        self.speed = 0.0
        self.remaining_m = max(0.0, remaining)
        self.status = f"stopped: {reason}"
        if reason.startswith("blocked"):
            self.last_hazard = {"type": reason, "x": round(self.x, 1), "y": round(self.y, 1), "detail": detail}
        return reason, detail, travelled

    async def _finish(self, reason: str, detail: str, travelled: float, sim_s0: float, extra: dict | None = None) -> dict:
        self.last_event = f"{reason}: {detail}"
        if reason == "arrived":
            self.replans_used = 0
        self._refresh_pose()
        await self._emit()
        res = {"state": self.state(), "stop_reason": reason, "detail": detail,
               "distance_m": round(travelled, 1), "elapsed_sim_min": round((self.sim_s - sim_s0) / 60.0, 1),
               "route_id": self.route_id, "filled_crossed_m": 0.0}
        if reason.startswith("blocked"):
            res["hint"] = ("Arm FSD (fsd_enable) so a blocked route is replanned once, or plan_route to the same "
                           "goal with a lower max_slope_deg, or pick another direction.")
        if extra:
            res.update(extra)
        return res

    async def _follow(self, route: dict, params: dict, realtime: bool, factor: float, timeout_s: float,
                      goal) -> dict:
        pts = [(p["x"], p["y"]) for p in route["points"]]
        self.route_id = route["route_id"]
        self.w.active_route = route["route_id"]
        sim0 = self.sim_s
        self.remaining_m = route["distance_m"]
        self.last_event = f"following {route['route_id']}"
        await self._emit()
        reason, detail, travelled = await self._drive_path(pts, params, realtime, factor, timeout_s)
        extra: dict = {}
        if reason in ("blocked_slope", "blocked_hazard"):
            await self.w.hub.broadcast("rover.hazard", {"type": reason, "detail": detail,
                                                       "x": round(self.x, 1), "y": round(self.y, 1)})
            if self.fsd_armed and self.policy.get("replan_on_block") and self.replans_used < 1 and goal is not None:
                self.replans_used += 1
                rid = self.w.new_route_id()
                limit = params["max_slope_deg"] * REPLAN_SLOPE_FACTOR
                new = R.plan_route(self.w.site, self.w.store, {"x": self.x, "y": self.y}, goal, None,
                                   self.profile, limit, None, rid)
                if new["feasible"]:
                    new["replan_of"] = route["route_id"]
                    await self.w.add_route(new)
                    self.route_id = rid
                    self.w.last_planned = rid
                    self.remaining_m = new["distance_m"]
                    self.last_hazard = dict(self.last_hazard or {}, replanned_as=rid)
                    reason = "replanned"
                    detail = (f"{detail}. FSD replanned from ({self.x:.0f}, {self.y:.0f}) with a {limit:g} degree "
                              f"slope limit: {new['distance_m']:.0f} m, {new['duration_min']:.0f} min as {rid}. "
                              f"The new route plans straight from here to the original final goal and does not revisit "
                              f"the original route's intermediate stops; about {max(route['distance_m'] - travelled, 0):.0f} m "
                              f"of {route['route_id']} were left. "
                              "The rover has stopped; call follow_route to continue.")
                    extra = {"new_route": _brief(new)}
                else:
                    detail += f". FSD replan failed: {new['reason']}"
                    extra = {"replan_failed": new["reason"], "nearest_reachable": new.get("nearest_reachable")}
        return await self._finish(reason, detail, travelled, sim0, extra)

    # ---- commands -------------------------------------------------------------------------------
    def _target_xy(self, body: dict) -> dict:
        ref = body.get("target", body.get("to", body.get("at")))
        if ref is None and "x" in body and "y" in body:
            ref = {"x": body["x"], "y": body["y"]}
        if ref is None:
            raise AtlasError("This command needs a target.",
                             hint='Pass x and y, or target: a POI id ("poi:<id>"), "marker:<id>" or {"x":..,"y":..}.')
        pts = resolve_ref(self.w.site, ref, self.w.store)
        if len(pts) != 1:
            raise AtlasError(f"target must be one location, got {len(pts)}.", hint='Use one set member such as "3.1".')
        return pts[0]

    async def command(self, body: dict) -> dict:
        """Run a rover command. Motion commands block until a stop reason by default; `wait: false` starts the drive
        as a background task and returns the current state at once (`started: true`); errors that surface within the
        first moments (bad target, infeasible route) are still raised, later outcomes are in state().status/last_event."""
        if body.get("wait") is False and body.get("cmd") in ("drive", "goto", "follow_route"):
            task = asyncio.ensure_future(self._command({**body, "wait": True}))
            self._task = task
            done, _ = await asyncio.wait({task}, timeout=0.25)
            if done:
                return task.result()
            task.add_done_callback(self._background_done)
            return {"state": self.state(), "started": True, "stop_reason": None, "cmd": body.get("cmd")}
        return await self._command(body)

    def _background_done(self, task) -> None:
        try:
            task.result()
        except Exception as e:  # noqa: BLE001  (a background drive must never raise into the event loop)
            self.running = False
            self.speed = 0.0
            self.status = "stopped: error"
            self.last_event = f"drive failed: {e}"

    async def _command(self, body: dict) -> dict:
        cmd = body.get("cmd")
        known = ("drive", "turn", "goto", "follow_route", "stop", "teleport", "fsd_enable", "fsd_disable")
        if cmd not in known:
            raise AtlasError(f"Unknown rover command {cmd!r}.", hint="Use one of: " + ", ".join(known) + ".")
        realtime = bool(body.get("realtime", False))
        timeout_s = float(body.get("timeout_s", 120.0))
        profile = body.get("profile")
        if profile and profile != self.profile:
            R.route_params(self.w.site, profile)      # validates
            self.profile = profile

        if cmd == "stop":
            self._cancel = True
            was = self.running
            await asyncio.sleep(0)
            self.last_event = "stop requested by user" if was else "stop (rover was already idle)"
            if not was:
                self.speed = 0.0
                self.status = "idle"
            await self._emit()
            return {"state": self.state(), "stop_reason": "stopped_by_user", "detail": self.last_event}

        if cmd == "fsd_enable":
            self.fsd_armed = True
            self.replans_used = 0
            pol = body.get("policy")
            if isinstance(pol, dict):
                pol = dict(pol)
                if "filled" in pol:
                    f = pol.pop("filled")
                    if f not in ("block", "caution", "allow"):
                        raise AtlasError("policy.filled must be one of block, caution, allow.")
                    self.filled_policy = f
                unknown = set(pol) - set(self.policy)
                if unknown:
                    raise AtlasError(f"Unknown FSD policy key(s): {', '.join(sorted(unknown))}.",
                                     hint="Valid: " + ", ".join(self.policy) + " (booleans).")
                self.policy.update({k: bool(v) for k, v in pol.items()})
            rid = body.get("route_id")
            if rid:
                self.w.get_route(rid)
                self.route_id = rid
                self.w.active_route = rid
                self.w.last_planned = rid
            self.last_event = "FSD armed"
            await self._emit()
            return {"state": self.state(), "fsd": self.fsd_status(), "stop_reason": None}
        if cmd == "fsd_disable":
            self.fsd_armed = False
            self.last_event = "FSD disarmed"
            await self._emit()
            return {"state": self.state(), "fsd": self.fsd_status(), "stop_reason": None}

        if cmd == "teleport":
            tgt = self._target_xy(body)
            params = self._params()
            hz = self._hazard(tgt["x"], tgt["y"], params)
            if hz:
                plan = R._travel._Plan(self.w.site, params)
                snap = plan.snap(tgt["x"], tgt["y"])
                near = ""
                if snap:
                    cx, cy = plan.cell_centre_xy(snap[0], snap[1])
                    near = f" Nearest walkable ground: ({cx:.0f}, {cy:.0f})."
                raise AtlasError(f"Cannot teleport to ({tgt['x']:.0f}, {tgt['y']:.0f}): {hz[1]}.{near}",
                                 hint="Teleport to walkable ground (check with sample_terrain).")
            async with self._motion():
                self.x, self.y = float(tgt["x"]), float(tgt["y"])
                if body.get("heading_deg") is not None:
                    self.heading = float(body["heading_deg"])
                self.route_id = None
                self.remaining_m = 0.0
                self.speed = 0.0
                self.status = "arrived"
                self.last_hazard = None
                self._refresh_pose()
                self.last_event = f"teleported to ({self.x:.0f}, {self.y:.0f})"
                await self._emit()
            return {"state": self.state(), "stop_reason": "arrived", "detail": self.last_event}

        if cmd == "turn":
            if body.get("heading_deg") is None and body.get("delta_deg") is None:
                raise AtlasError("turn needs heading_deg (absolute, clockwise from north) or delta_deg.")
            async with self._motion():
                h = float(body["heading_deg"]) if body.get("heading_deg") is not None else self.heading + float(body["delta_deg"])
                self.heading = h % 360.0
                self._refresh_pose()
                self.status = "arrived"
                self.last_event = f"turned to heading {self.heading:.0f}"
                await self._emit()
            return {"state": self.state(), "stop_reason": "arrived", "detail": self.last_event}

        # ---- motion commands -----------------------------------------------------------------
        params = self._params()
        factor = body.get("speed_factor")
        factor = float(factor) if factor is not None else (
            max(1.0, DISPLAY_SPEED_MPS / params["flat_speed_mps"]) if realtime else 1.0)
        if not factor > 0:
            raise AtlasError("speed_factor must be > 0.")

        if cmd == "drive":
            try:
                dist = float(body["distance_m"])
            except (KeyError, TypeError, ValueError):
                raise AtlasError("drive needs distance_m (metres, > 0).",
                                 hint="Example: {cmd: 'drive', distance_m: 50, heading_deg: 90}.") from None
            if not 0 < dist <= MAX_DRIVE_M:
                raise AtlasError(f"distance_m must be between 0 and {MAX_DRIVE_M:g}.")
            async with self._motion():
                hdg = float(body["heading_deg"]) if body.get("heading_deg") is not None else self.heading
                h = math.radians(hdg)
                dest = (self.x + dist * math.sin(h), self.y + dist * math.cos(h))
                self.route_id = None
                sim0 = self.sim_s
                self.heading = hdg % 360.0
                reason, detail, travelled = await self._drive_path([(self.x, self.y), dest], params, realtime,
                                                                   factor, timeout_s)
                return await self._finish(reason, detail, travelled, sim0)

        if cmd == "goto":
            tgt = self._target_xy(body)
            rid = self.w.new_route_id()
            route = R.plan_route(self.w.site, self.w.store, {"x": self.x, "y": self.y}, {"x": tgt["x"], "y": tgt["y"]},
                                 None, self.profile, body.get("max_slope_deg"), None, rid)
            if not route["feasible"]:
                reason = "blocked_slope" if "degree ground" in (route["reason"] or "") else "blocked_hazard"
                self.last_event = f"goto refused: {route['reason']}"
                return {"state": self.state(), "stop_reason": reason, "detail": route["reason"],
                        "feasible": False, "nearest_reachable": route["nearest_reachable"],
                        "hint": route["hint"], "distance_m": 0.0, "route_id": self.route_id}
            await self.w.add_route(route)
            async with self._motion():
                return await self._follow(route, params, realtime, factor, timeout_s,
                                          {"x": tgt["x"], "y": tgt["y"]})

        if cmd == "follow_route":
            rid = body.get("route_id")
            defaulted = not rid
            if defaulted:
                rid = self.w.last_planned or self.w.active_route
            if not rid:
                raise AtlasError("No route to follow.", hint="Call plan_route first and pass its route_id.")
            route = self.w.get_route(rid)
            if not route["feasible"]:
                raise AtlasError(f"Route {rid} is not feasible: {route['reason']}", hint="Plan a feasible route first.")
            p0 = route["points"][0]
            d0 = math.hypot(p0["x"] - self.x, p0["y"] - self.y)
            if d0 > ARRIVE_TOL_M:
                raise AtlasError(
                    f"The rover is {d0:.0f} m from the start of {rid} ({p0['x']:.0f}, {p0['y']:.0f})"
                    + (" (route_id was not given, so the most recently planned route was used)." if defaulted else "."),
                    hint=(f"Move the rover to the route start first: teleport_rover(x={p0['x']:.0f}, y={p0['y']:.0f}) "
                          f"(instant) or goto_location(x={p0['x']:.0f}, y={p0['y']:.0f}) (drives), or plan the route "
                          'again with start="rover".'))
            goal = {"x": route["goal"]["x"], "y": route["goal"]["y"]}
            async with self._motion():
                res = await self._follow(route, params, realtime, factor, timeout_s, goal)
            res["route_used"] = rid
            if route.get("profile") and route["profile"] != self.profile:
                rp, _ = R.route_params(self.w.site, route["profile"])
                res["profile_warning"] = (
                    f"Route {rid} was planned and assessed for profile {route['profile']} (no-go above "
                    f"{rp['max_slope_deg']:g} deg) but the rover drives with profile {self.profile} (no-go above "
                    f"{params['max_slope_deg']:g} deg), so a GO assessment does not guarantee the rover can drive it.")
                res["note"] = ((res.get("note") + " ") if res.get("note") else "") + res["profile_warning"]
            if defaulted:
                res["note"] = ((res.get("note") + " ") if res.get("note") else "") + (
                    f"route_id was not given, so the most recently planned route {rid} was used. "
                    "Pass route_id explicitly to avoid surprises.")
            return res
        raise AtlasError(f"Unhandled command {cmd}.")  # pragma: no cover


def _brief(route: dict) -> dict:
    keys = ("route_id", "feasible", "distance_m", "duration_min", "max_slope_deg", "ascent_m", "replan_of")
    return {k: route.get(k) for k in keys}
