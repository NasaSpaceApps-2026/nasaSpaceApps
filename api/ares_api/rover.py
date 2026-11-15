"""Rover simulator: 10 Hz loop that drives the rover along paths with slope checks and the FSD policy.

Manual mode: the viewer owns the pose and reports it (rover.pose). Auto/FSD mode: this sim owns
the pose and broadcasts rover.state. Sim time runs `speed_factor` times faster than mission time.
"""
import asyncio
import math
import time

import numpy as np

from . import config
from .hub import Hub
from .planner import Planner, RouteError
from .terrain import Terrain
from .vehicle import grade_speed, has_force_model

STOP_REASONS = ("arrived", "blocked_slope", "blocked_hazard", "out_of_bounds", "no_route",
                "stopped_by_user", "timeout")
SUBSTEP_M = 1.0
ARRIVE_M = 0.5


class Command:
    def __init__(self, kind: str, path: list, speed_factor: float, goal_label: str,
                 route_id: str = None, fsd: bool = False):
        self.kind = kind
        self.path = [tuple(p) for p in path]  # (x, y) waypoints, path[0] is the start
        self.i = 1  # index of the next waypoint
        self.speed_factor = speed_factor
        self.goal_label = goal_label
        self.route_id = route_id
        self.fsd = fsd
        self.replans = 0
        self.distance = 0.0
        self.events: list[str] = []
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()

    def remaining(self, x: float, y: float) -> float:
        if self.i >= len(self.path):
            return 0.0
        d = math.hypot(self.path[self.i][0] - x, self.path[self.i][1] - y)
        for a, b in zip(self.path[self.i:], self.path[self.i + 1:]):
            d += math.hypot(b[0] - a[0], b[1] - a[1])
        return d


class RoverSim:
    def __init__(self, terrain: Terrain, planner: Planner, hub: Hub, start: tuple = (0.0, 0.0)):
        self.t = terrain
        self.planner = planner
        self.hub = hub
        self.prof = planner.profile(planner.cfg.get("vehicle_profile") or planner.cfg["rover_profile"])
        self.cmd: Command | None = None
        self.fsd_armed = False
        self.policy = self.default_policy()
        self.last_hazard = None
        x, y = start
        self.s = {"x": x, "y": y, "z": 0.0, "heading_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0,
                  "speed_mps": 0.0, "mode": "idle", "odometer_m": 0.0, "mission_time_s": 0.0,
                  "route_id": None, "status": "ready", "last_event": None, "fsd_armed": False}
        self._settle()

    def default_policy(self) -> dict:
        return {"max_slope_deg": self.prof["nogo_slope_deg"], "caution_slope_deg": self.prof["caution_slope_deg"],
                "caution_speed_factor": 0.5, "replan": True, "max_replans": 1}

    # --- state ----------------------------------------------------------------------
    def _settle(self):
        s = self.s
        s["z"] = round(float(self.t.elevation(s["x"], s["y"])), 2)
        s["pitch_deg"], s["roll_deg"] = self.t.tilt(s["x"], s["y"], s["heading_deg"])

    def state(self) -> dict:
        s = dict(self.s)
        s["x"], s["y"] = round(s["x"], 1), round(s["y"], 1)
        s["heading_deg"] = round(s["heading_deg"], 1)
        s["lat"], s["lon"] = self.t.latlon(s["x"], s["y"])
        s["slope_deg"] = round(float(self.t.slope(s["x"], s["y"])), 1)
        s["fsd_armed"] = self.fsd_armed
        s["vehicle"] = self.prof["name"]
        s["mission_time_min"] = round(s.pop("mission_time_s") / 60, 2)
        return s

    def xy(self) -> tuple[float, float]:
        return self.s["x"], self.s["y"]

    def _event(self, text: str):
        self.s["last_event"] = text
        if self.cmd:
            self.cmd.events.append(text)

    # --- loop -----------------------------------------------------------------------------
    async def run(self):
        dt = 1.0 / config.TICK_HZ
        last = time.monotonic()
        while True:
            await asyncio.sleep(dt)
            now = time.monotonic()
            step_dt, last = now - last, now
            if self.cmd:
                try:
                    self._advance(step_dt)
                except Exception as e:  # never kill the loop
                    self._finish("blocked_hazard", f"Simulator error: {e}")
                await self.hub.broadcast("rover.state", self.state())

    def set_vehicle(self, name: str) -> dict:
        """Switch the simulated vehicle (a profile in profiles.yaml); stops any active command."""
        prof = self.planner.profile(name)
        if self.cmd:
            self._finish("stopped_by_user", f"Vehicle changed to {name}.")
        self.prof = prof
        self.policy = self.default_policy()
        return prof

    def _grade(self, x: float, y: float, nx: float, ny: float) -> float:
        """Signed grade (deg, + uphill) from (x, y) toward (nx, ny)."""
        d = math.hypot(nx - x, ny - y)
        if d < 1e-6:
            return 0.0
        dz = float(self.t.elevation(nx, ny)) - float(self.t.elevation(x, y))
        return math.degrees(math.atan2(dz, d))

    def _speed(self, slope: float, cmd: Command, grade: float = None) -> tuple[float, float, str | None]:
        """(display speed, real mission speed, stall note) in m/s.
        Vehicles with a force model use the same power/grip balance as the browser rover on the
        signed grade; others use the planner's speed curve on the slope."""
        stall = None
        if has_force_model(self.prof) and grade is not None:
            real, stall = grade_speed(self.prof, grade)
        else:
            real = float(self.planner.speed(slope, self.prof))
        if cmd.fsd and slope >= self.policy["caution_slope_deg"]:
            real *= self.policy["caution_speed_factor"]  # FSD drives cautiously on steep ground
        return real * cmd.speed_factor, real, stall

    def _advance(self, dt: float):
        cmd, s = self.cmd, self.s
        slope_here = float(self.t.slope(s["x"], s["y"]))
        tx0, ty0 = cmd.path[min(cmd.i, len(cmd.path) - 1)]
        d0 = math.hypot(tx0 - s["x"], ty0 - s["y"])
        probe = (s["x"] + (tx0 - s["x"]) / d0 * 2, s["y"] + (ty0 - s["y"]) / d0 * 2) if d0 > 1e-6 else (s["x"], s["y"])
        disp, _, _ = self._speed(slope_here, cmd, self._grade(s["x"], s["y"], *probe))
        budget = max(disp, 0.05 * cmd.speed_factor * self.prof["flat_speed_mps"]) * dt
        moved = 0.0
        while budget > 1e-6 and cmd.i < len(cmd.path):
            tx, ty = cmd.path[cmd.i]
            dx, dy = tx - s["x"], ty - s["y"]
            d = math.hypot(dx, dy)
            if d < ARRIVE_M:
                cmd.i += 1
                continue
            step = min(SUBSTEP_M, budget, d)
            nx, ny = s["x"] + dx / d * step, s["y"] + dy / d * step
            if not self.t.in_bounds(nx, ny):
                return self._finish("out_of_bounds", f"Stopped at the site edge near ({s['x']:.0f}, {s['y']:.0f}).")
            slope = float(self.t.slope(nx, ny))
            filled = bool(self.t.nearest(self.t.filled.astype(np.uint8), nx, ny))
            limit = self.policy["max_slope_deg"] if cmd.fsd else self.prof["nogo_slope_deg"]
            if slope > limit or (cmd.fsd and filled):
                reason = "blocked_hazard" if filled and slope <= limit else "blocked_slope"
                what = "unknown (interpolated) terrain" if reason == "blocked_hazard" else f"{slope:.0f} deg slope"
                self.last_hazard = {"x": round(nx), "y": round(ny), "slope_deg": round(slope, 1), "reason": reason}
                self.hub.emit("rover.hazard", self.last_hazard)
                if cmd.fsd and self.policy["replan"] and cmd.replans < self.policy["max_replans"]:
                    if self._replan(what):
                        return
                return self._finish(reason, f"Stopped before {what} at ({nx:.0f}, {ny:.0f}); "
                                            f"limit is {limit:.0f} deg.")
            _, real, stall = self._speed(slope, cmd, self._grade(s["x"], s["y"], nx, ny))
            if stall:
                self.last_hazard = {"x": round(nx), "y": round(ny), "slope_deg": round(slope, 1), "reason": "stalled"}
                self.hub.emit("rover.hazard", self.last_hazard)
                if cmd.fsd and self.policy["replan"] and cmd.replans < self.policy["max_replans"]:
                    if self._replan(stall):
                        return
                return self._finish("blocked_slope", f"Stalled at ({s['x']:.0f}, {s['y']:.0f}): {stall}.")
            s["heading_deg"] = math.degrees(math.atan2(dx, dy)) % 360
            s["x"], s["y"] = nx, ny
            s["mission_time_s"] += step / max(real, 1e-6)
            budget -= step
            moved += step
        cmd.distance += moved
        s["odometer_m"] = round(s["odometer_m"] + moved, 1)
        s["speed_mps"] = round(moved / dt, 2) if dt > 0 else 0.0
        self._settle()
        if cmd.i >= len(cmd.path):
            self._finish("arrived", f"Arrived at {cmd.goal_label}.")

    def _replan(self, what: str) -> bool:
        cmd = self.cmd
        goal = cmd.path[-1]
        cmd.replans += 1
        try:
            r = self.planner.plan(self.xy(), goal, labels=["rover", cmd.goal_label], profile=self.prof["name"],
                                  max_slope_deg=self.policy["max_slope_deg"])
        except RouteError as e:
            self._event(f"Hit {what}; replan failed: {e}")
            return False
        cmd.path = [(p["x"], p["y"]) for p in r["points"]]
        cmd.i = 1
        cmd.route_id = r["route_id"]
        self.s["route_id"] = r["route_id"]
        self._event(f"Hit {what}; replanned as {r['route_id']} ({r['distance_m']} m to go).")
        self.hub.emit("route.set", r)
        return True

    def _finish(self, reason: str, message: str):
        cmd = self.cmd
        self.cmd = None
        self.s["speed_mps"] = 0.0
        self.s["mode"] = "idle"
        self.s["status"] = reason
        self._event(message)
        self._settle()
        if cmd and not cmd.future.done():
            cmd.future.set_result({"stop_reason": reason, "message": message, "distance_m": round(cmd.distance, 1),
                                   "replans": cmd.replans, "events": cmd.events, "route_id": cmd.route_id})
        self.hub.emit("rover.state", self.state())

    # --- commands --------------------------------------------------------------------------
    def start(self, kind: str, path: list, goal_label: str, speed_factor: float = None,
              route_id: str = None) -> Command:
        if self.cmd:
            self._finish("stopped_by_user", f"Previous command replaced by {kind}.")
        sf = speed_factor or self.prof.get("display_speed_factor", 1)
        self.cmd = Command(kind, [self.xy()] + list(path), sf, goal_label, route_id, fsd=self.fsd_armed)
        self.s["mode"] = "fsd" if self.fsd_armed else "auto"
        self.s["status"] = kind
        self.s["route_id"] = route_id
        self.s["last_event"] = None
        return self.cmd

    def stop(self, reason: str = "stopped_by_user", message: str = "Stopped on request."):
        if self.cmd:
            self._finish(reason, message)
        else:
            self.s["mode"] = "idle"

    def teleport(self, x: float, y: float, heading: float = None):
        if self.cmd:
            self._finish("stopped_by_user", "Teleported.")
        self.s["x"], self.s["y"] = x, y
        if heading is not None:
            self.s["heading_deg"] = heading % 360
        self.s["mode"] = "idle"
        self.s["status"] = "teleported"
        self._settle()
        self.hub.emit("rover.state", self.state())

    def turn(self, heading: float):
        self.s["heading_deg"] = heading % 360
        self._settle()
        self.hub.emit("rover.state", self.state())

    def manual_pose(self, p: dict):
        """Viewer reports a pose from manual driving; any manual input cancels auto."""
        if self.cmd:
            self._finish("stopped_by_user", "Manual takeover from the viewer.")
        s = self.s
        moved = math.hypot(p.get("x", s["x"]) - s["x"], p.get("y", s["y"]) - s["y"])
        for k in ("x", "y", "z", "heading_deg", "pitch_deg", "roll_deg", "speed_mps"):
            if k in p and p[k] is not None:
                s[k] = float(p[k])
        s["odometer_m"] = round(s["odometer_m"] + moved, 1)
        s["mission_time_s"] += moved / self.prof["flat_speed_mps"]
        s["mode"] = "manual"
        s["status"] = "manual"

    def fsd_status(self) -> dict:
        s = self.s
        cmd = self.cmd
        return {"armed": self.fsd_armed, "mode": s["mode"], "policy": self.policy,
                "route_id": s["route_id"], "active_command": cmd.kind if cmd else None,
                "remaining_route_m": round(cmd.remaining(s["x"], s["y"])) if cmd else 0,
                "last_hazard": self.last_hazard, "last_event": s["last_event"], "status": s["status"],
                "profile": self.prof["name"], "vehicle": self.prof.get("title", self.prof["name"]),
                "limits_source": self.prof.get("source", "assumed")}
