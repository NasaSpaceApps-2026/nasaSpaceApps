"""Ares Atlas API: terrain, routes, search, markers, rover sim and the viewer WebSocket.

Run: api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000
"""
import asyncio
import contextlib
import itertools
import json
import math
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config
from .hub import Hub
from .places import PlaceError, Places
from .planner import Planner, RouteError
from .rover import RoverSim
from .science import Validation, plan_science_traverse
from .search import SearchError, SiteSearch
from .terrain import Terrain

Position = Any  # {x, y} | [x, y] | "x,y" | "poi:<id>" | "marker:<id>" | "rover" | bare id


class World:
    """Everything the API serves, built once per app."""

    def __init__(self, data_dir: Path = None, extra_layer_dir: Path = None, state_file: Path | None = None):
        self.cfg = config.load_yaml(config.PROFILES_PATH)
        self.state_file = state_file
        self.terrain = Terrain(data_dir, extra_layer_dir)
        self.places = Places(self.terrain)
        self.planner = Planner(self.terrain, self.cfg)
        self.search = SiteSearch(self.terrain, self.planner, config.load_yaml(config.TARGETS_PATH))
        self.validation = Validation(self.terrain, self.planner)
        self.hub = Hub()
        # Start at the base POI, else the earliest rover waypoint (the landing site on real data), else the centre.
        base = self.places.pois.get("base") or min(
            (p for p in self.places.pois.values() if p.get("type") == "rover_waypoint" and self.terrain.in_bounds(p["x"], p["y"])),
            key=lambda p: p.get("sol") if p.get("sol") is not None else 1e9, default=None)
        self.rover = RoverSim(self.terrain, self.planner, self.hub, (base["x"], base["y"]) if base else (0.0, 0.0))
        self.view = {"mode": "orbit", "camera": None, "layers": {}, "highlights": []}
        self.snapshots: dict[str, asyncio.Future] = {}
        self._snap_ids = itertools.count(1)
        self.load_state()

    # --- persistence: markers and routes survive restarts (per data site) ------------------------
    def load_state(self):
        if not self.state_file or not self.state_file.exists():
            return
        try:
            st = json.loads(self.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if st.get("site") != self.terrain.manifest.get("site"):
            return  # saved for a different site
        self.places.markers.update({m["id"]: m for m in st.get("markers", [])})
        self.planner.routes.update(st.get("routes", {}))
        num = lambda ids, p: max([int(i[len(p):]) for i in ids if i[len(p):].isdigit()] or [0])
        self.places._ids = itertools.count(num(self.places.markers, "m") + 1)
        self.planner._ids = itertools.count(num(self.planner.routes, "r") + 1)

    def save_state(self):
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"site": self.terrain.manifest.get("site"),
                                       "markers": list(self.places.markers.values()),
                                       "routes": dict(list(self.planner.routes.items())[-200:])}))
            tmp.replace(self.state_file)
        except OSError:
            pass

    def resolve(self, pos) -> tuple[float, float, str]:
        return self.places.resolve(pos, self.rover.xy())


# --- request bodies ------------------------------------------------------------------------------
class ProfileReq(BaseModel):
    points: list[Position] = Field(..., min_length=2)
    spacing_m: float = Field(10.0, gt=0)
    profile: str | None = None


class RouteReq(BaseModel):
    start: Position = "rover"
    goal: Position
    via: list[Position] = []
    profile: str | None = None
    max_slope_deg: float | None = Field(None, gt=0, lt=90)
    time_budget_min: float | None = Field(None, gt=0)


class ScienceReq(BaseModel):
    start: Position = "rover"
    time_budget_min: float = Field(..., gt=0)
    interests: list[str] = []
    profile: str | None = None
    max_stops: int = Field(6, ge=1, le=12)


class CommandReq(BaseModel):
    cmd: Literal["drive", "turn", "goto", "follow_route", "stop", "teleport", "fsd_enable", "fsd_disable"]
    distance_m: float | None = None
    heading_deg: float | None = None
    delta_deg: float | None = None
    target: Position = None
    route_id: str | None = None
    speed_factor: float | None = Field(None, gt=0)
    policy: dict | None = None
    wait: bool = True
    timeout_s: float = Field(120.0, gt=0, le=600)


class LayerView(BaseModel):
    id: str
    visible: bool = True
    opacity: float | None = Field(None, ge=0, le=1)


class ViewReq(BaseModel):
    mode: Literal["globe", "orbit", "chase", "ground"] | None = None
    target: Position = None
    heading_deg: float | None = None
    pitch_deg: float | None = None
    distance_m: float | None = None
    layers: list[LayerView] = []
    highlights: list[str] | None = None


class SnapshotReq(BaseModel):
    direction: str | None = None


class MarkerReq(BaseModel):
    x: float | None = None
    y: float | None = None
    at: Position = None
    label: str | None = None
    created_by: Literal["user", "agent"] = "user"


class SiteSearchReq(BaseModel):
    target: str
    near: Position = None
    radius_m: float | None = Field(None, gt=0)
    max_slope_deg: float | None = Field(None, gt=0, lt=90)
    top_k: int = Field(5, ge=1, le=20)
    profile: str | None = None


class VehicleReq(BaseModel):
    profile: str


class AgentEvent(BaseModel):
    tool: str
    args_summary: str = ""
    result_summary: str = ""
    status: Literal["started", "ok", "error"] = "ok"


# --- app -------------------------------------------------------------------------------------------
DEFAULT_STATE = os.environ.get("ARES_STATE_FILE", str(config.API_DIR / ".state" / "state.json"))


def create_app(data_dir: Path = None, extra_layer_dir: Path = None, state_file: Path | str | None = DEFAULT_STATE) -> FastAPI:
    """state_file: where markers/routes persist (None or "" disables; tests pass None)."""
    world = World(data_dir, extra_layer_dir, Path(state_file) if state_file else None)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        world.hub.loop = asyncio.get_running_loop()
        task = asyncio.create_task(world.rover.run())
        yield
        task.cancel()

    app = FastAPI(title="Ares Atlas API", version="0.1.0", lifespan=lifespan,
                  description="Terrain, routes, science search, markers and rover sim for Jezero. "
                              "All positions are site-frame metres (x east, y north) and also accept "
                              "'poi:<id>', 'marker:<id>' or 'rover'.")
    app.state.world = world
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(PlaceError)
    @app.exception_handler(RouteError)
    @app.exception_handler(SearchError)
    async def readable_error(_, exc):
        return JSONResponse(status_code=422, content={"error": str(exc)})

    t, pl, places = world.terrain, world.planner, world.places

    @app.get("/health")
    def health():
        return {"ok": True, "site": t.manifest.get("site"), "fixture": bool(t.manifest.get("fixture")),
                "vehicle": world.rover.prof["name"], "viewers": len(world.hub.clients),
                "markers": len(places.markers), "routes": len(pl.routes)}

    @app.get("/site")
    def site():
        return {"manifest": t.manifest,
                "bounds": {"half_size_m": t.half, "res_m": t.res, "planning_res_m": pl.res},
                "profiles": world.cfg["profiles"], "default_profile": world.cfg["default_profile"],
                "rover_profile": world.cfg["rover_profile"], "vehicle_profile": world.rover.prof["name"],
                "speed_curve": world.cfg["speed_curve"],
                "stop_minutes": world.cfg["stop_minutes"],
                "layers_loaded": {lid: t.layer_source[lid] for lid in t.layers},
                "search_targets": [s["target"] for s in world.search.available() if s["available"]],
                "rover": world.rover.state(), "markers": list(places.markers.values()),
                "poi_count": len(places.pois)}

    # --- terrain --------------------------------------------------------------------------------------
    @app.get("/terrain/sample")
    def terrain_sample(x: float | None = None, y: float | None = None, at: str | None = None,
                       profile: str | None = None):
        if at is not None:
            x, y, _ = world.resolve(at)
        if x is None or y is None:
            raise PlaceError("Give x and y in site metres, or at='poi:<id>' / 'marker:<id>' / 'rover'.")
        if not t.in_bounds(x, y):
            raise PlaceError(f"({x:.0f}, {y:.0f}) is outside the site (|x|, |y| <= {t.half:.0f} m).")
        return t.sample(x, y, pl.profile(profile))

    @app.post("/terrain/profile")
    async def terrain_profile(req: ProfileReq):
        return await run_in_threadpool(_terrain_profile, req)

    def _terrain_profile(req: ProfileReq):
        pts = [world.resolve(p)[:2] for p in req.points]
        xs, ys, dist = [], [], []
        total = 0.0
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            d = math.hypot(bx - ax, by - ay)
            n = max(1, int(math.ceil(d / req.spacing_m)))
            for i in range(0 if not xs else 1, n + 1):
                xs.append(ax + (bx - ax) * i / n)
                ys.append(ay + (by - ay) * i / n)
                dist.append(total + d * i / n)
            total += d
        z = t.elevation(xs, ys)
        s = t.slope(xs, ys)
        prof = pl.profile(req.profile)
        return {"distance_m": round(total), "samples": len(xs),
                "elevation_min_m": round(float(z.min()), 1), "elevation_max_m": round(float(z.max()), 1),
                "gain_m": round(float(sum(max(0.0, b - a) for a, b in zip(z, z[1:]))), 1),
                "max_slope_deg": round(float(s.max()), 1),
                "nogo_samples": int((s >= prof["nogo_slope_deg"]).sum()),
                "series": [{"d": round(d, 1), "x": round(x, 1), "y": round(y, 1), "z": round(float(zz), 2),
                            "slope": round(float(ss), 1)} for d, x, y, zz, ss in zip(dist, xs, ys, z, s)],
                "source": t.source_label("dem")}

    @app.get("/pois")
    def pois(type: str | None = None, q: str | None = None, near: str | None = None,
             radius: float | None = None, limit: int = Query(20, ge=1, le=200)):
        nxy = world.resolve(near)[:2] if near else None
        items = places.search_pois(type, q, nxy, radius, limit)
        return {"count": len(items), "pois": items}

    # --- routes -------------------------------------------------------------------------------------
    @app.post("/route")
    async def route(req: RouteReq):
        pts = [world.resolve(p) for p in [req.start, *req.via, req.goal]]
        r = await run_in_threadpool(pl.plan, pts[0][:2], pts[-1][:2], via=[p[:2] for p in pts[1:-1]],
                                    labels=[p[2] for p in pts], profile=req.profile,
                                    max_slope_deg=req.max_slope_deg, time_budget_min=req.time_budget_min,
                                    stops=len(req.via))
        world.hub.emit("route.set", r)
        world.save_state()
        return r

    @app.get("/route/{route_id}")
    def get_route(route_id: str):
        return pl.get(route_id)

    @app.get("/route/{route_id}/assess")
    def assess_route(route_id: str):
        return pl.assess(route_id)

    @app.post("/route/science")
    async def route_science(req: ScienceReq):
        sx, sy, label = world.resolve(req.start)
        r = await run_in_threadpool(plan_science_traverse, pl, places, world.search, (sx, sy), label,
                                    req.time_budget_min, req.interests, req.profile, req.max_stops)
        if r.get("route_id"):
            world.hub.emit("route.set", r)
            world.save_state()
        return r

    # --- search + markers -----------------------------------------------------------------------------
    @app.get("/search/targets")
    def search_targets():
        return {"targets": world.search.available()}

    @app.post("/search/sites")
    async def search_sites(req: SiteSearchReq):
        near, label = (None, None)
        if req.near is not None:
            nx, ny, label = world.resolve(req.near)
            near = (nx, ny)
        res = await run_in_threadpool(world.search.find, req.target, near, label, req.radius_m,
                                      req.max_slope_deg, req.top_k, req.profile)
        world.hub.emit("search.results", res)
        return res

    @app.get("/markers")
    def list_markers():
        return {"markers": list(places.markers.values())}

    @app.post("/markers")
    def add_marker(req: MarkerReq):
        if req.at is not None:
            x, y, _ = world.resolve(req.at)
        elif req.x is not None and req.y is not None:
            x, y = req.x, req.y
        else:
            raise PlaceError("Give x and y, or at='poi:<id>' / 'rover'.")
        m = places.add_marker(x, y, req.label, req.created_by)
        world.hub.emit("marker.add", m)
        world.save_state()
        return m

    @app.delete("/markers/{marker_id}")
    def delete_marker(marker_id: str):
        m = places.remove_marker(marker_id)
        world.hub.emit("marker.remove", {"id": m["id"]})
        world.save_state()
        return {"removed": m["id"]}

    # --- rover ----------------------------------------------------------------------------------------
    @app.get("/rover/state")
    def rover_state():
        return world.rover.state()

    @app.get("/fsd/status")
    def fsd_status():
        return world.rover.fsd_status()

    @app.get("/rover/vehicles")
    def rover_vehicles():
        return {"current": world.rover.prof["name"],
                "vehicles": {k: {"title": v.get("title", k), "flat_speed_mps": v["flat_speed_mps"],
                                 "nogo_slope_deg": v["nogo_slope_deg"], "source": v.get("source", "assumed")}
                             for k, v in world.cfg["profiles"].items()}}

    @app.post("/rover/vehicle")
    def rover_vehicle(req: VehicleReq):
        prof = world.rover.set_vehicle(req.profile)
        world.hub.emit("rover.state", world.rover.state())
        return {"ok": True, "vehicle": prof["name"], "title": prof.get("title"), "state": world.rover.state()}

    @app.post("/rover/command")
    async def rover_command(req: CommandReq):
        rv = world.rover
        c = req.cmd
        if c == "stop":
            rv.stop()
            return {"ok": True, "cmd": c, "stop_reason": "stopped_by_user", "state": rv.state()}
        if c == "fsd_disable":
            rv.fsd_armed = False
            rv.policy = rv.default_policy()
            return {"ok": True, "cmd": c, "fsd": rv.fsd_status()}
        if c == "turn":
            if req.heading_deg is None and req.delta_deg is None:
                raise PlaceError("turn needs heading_deg (clockwise from north) or delta_deg.")
            h = req.heading_deg if req.heading_deg is not None else rv.s["heading_deg"] + req.delta_deg
            rv.turn(h)
            return {"ok": True, "cmd": c, "state": rv.state()}
        if c == "teleport":
            x, y, label = world.resolve(req.target)
            if not t.in_bounds(x, y):
                raise PlaceError(f"({x:.0f}, {y:.0f}) is outside the site.")
            rv.teleport(x, y, req.heading_deg)
            slope = float(t.slope(x, y))
            warn = [f"Landed on a {slope:.0f} deg slope."] if slope >= rv.prof["caution_slope_deg"] else []
            return {"ok": True, "cmd": c, "at": label, "state": rv.state(), "warnings": warn}

        if c == "fsd_enable":
            rv.fsd_armed = True
            rv.policy = {**rv.default_policy(), **(req.policy or {})}
            if not req.route_id:
                return {"ok": True, "cmd": c, "fsd": rv.fsd_status(),
                        "hint": "FSD armed. Call follow_route with a route_id to drive under the policy."}
            c = "follow_route"

        if c == "drive":
            if not req.distance_m:
                raise PlaceError("drive needs distance_m (metres) and optional heading_deg.")
            h = math.radians(req.heading_deg if req.heading_deg is not None else rv.s["heading_deg"])
            x0, y0 = rv.xy()
            path = [(x0 + math.sin(h) * req.distance_m, y0 + math.cos(h) * req.distance_m)]
            cmd = rv.start("drive", path, f"{req.distance_m:.0f} m at {math.degrees(h) % 360:.0f} deg", req.speed_factor)
        elif c == "goto":
            x, y, label = world.resolve(req.target)
            r = await run_in_threadpool(pl.plan, rv.xy(), (x, y), labels=["rover", label], profile=rv.prof["name"],
                                        max_slope_deg=rv.policy["max_slope_deg"] if rv.fsd_armed else None)
            world.save_state()
            world.hub.emit("route.set", r)
            cmd = rv.start("goto", [(p["x"], p["y"]) for p in r["points"][1:]], label, req.speed_factor, r["route_id"])
        elif c == "follow_route":
            if not req.route_id:
                raise PlaceError("follow_route needs route_id from plan_route.")
            r = pl.get(req.route_id)
            path = [(p["x"], p["y"]) for p in r["points"]]
            x0, y0 = rv.xy()
            if math.hypot(path[0][0] - x0, path[0][1] - y0) > 10:
                approach = await run_in_threadpool(pl.plan, (x0, y0), path[0], labels=["rover", r["start"]],
                                                   profile=rv.prof["name"])
                path = [(p["x"], p["y"]) for p in approach["points"][1:]] + path[1:]
            cmd = rv.start("follow_route", path, r["goal"], req.speed_factor, req.route_id)
        else:
            raise PlaceError(f"Unknown command {c}.")

        if not req.wait:
            return {"ok": True, "cmd": c, "started": True, "state": rv.state()}
        try:
            res = await asyncio.wait_for(asyncio.shield(cmd.future), req.timeout_s)
        except asyncio.TimeoutError:
            return {"ok": True, "cmd": c, "stop_reason": "timeout",
                    "message": f"Still driving after {req.timeout_s:.0f} s; check fsd_status or rover state.",
                    "remaining_m": round(cmd.remaining(*rv.xy())), "state": rv.state()}
        return {"ok": res["stop_reason"] == "arrived", "cmd": c, **res, "state": rv.state(),
                "fsd": rv.fsd_status() if rv.fsd_armed else None}

    # --- view ---------------------------------------------------------------------------------------------
    @app.get("/view")
    def get_view():
        return world.view

    @app.post("/view")
    def set_view(req: ViewReq):
        out = {}
        if req.mode or req.target is not None:
            cam = {"mode": req.mode or world.view["mode"], "heading": req.heading_deg, "pitch": req.pitch_deg,
                   "distance": req.distance_m}
            if req.target is not None:
                x, y, label = world.resolve(req.target)
                cam.update(x=round(x, 1), y=round(y, 1), z=round(float(t.elevation(x, y)), 1), target=label)
            world.view["mode"] = cam["mode"]
            world.hub.emit("view.camera", cam)
            out["camera"] = cam
        for lv in req.layers:
            payload = lv.model_dump()
            world.view["layers"][lv.id] = payload
            world.hub.emit("view.layer", payload)
        if req.highlights is not None:
            world.view["highlights"] = req.highlights
            world.hub.emit("view.highlight", {"ids": req.highlights})
            out["highlights"] = req.highlights
        out["layers"] = [lv.id for lv in req.layers]
        out["viewers_connected"] = len(world.hub.clients)
        return {"ok": True, **out}

    @app.post("/view/snapshot")
    async def snapshot(req: SnapshotReq):
        if not world.hub.clients:
            return JSONResponse(status_code=503, content={"error": "No viewer is connected to take a snapshot."})
        rid = f"s{next(world._snap_ids)}"
        fut = asyncio.get_running_loop().create_future()
        world.snapshots[rid] = fut
        await world.hub.broadcast("view.snapshot_request", {"request_id": rid, "direction": req.direction})
        try:
            img = await asyncio.wait_for(fut, config.SNAPSHOT_TIMEOUT_S)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=504, content={"error": "Viewer did not send a snapshot within 5 s."})
        finally:
            world.snapshots.pop(rid, None)
        return {"request_id": rid, "image_base64": img, "mime": "image/jpeg", "rover": world.rover.state()}

    # --- misc ---------------------------------------------------------------------------------------------
    @app.get("/bubble/status")
    def bubble_status():
        b = t.manifest.get("bubble")
        if not b:
            return {"available": False, "message": "No photoreal bubble in this data build yet (Stream A)."}
        metrics_path = t.data_dir / "bubble" / "metrics.json"
        metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else b.get("metrics")
        return {"available": True, "bubble": b, "metrics": metrics}

    @app.get("/validation")
    async def validation():
        return await run_in_threadpool(world.validation.run)

    @app.post("/agent/event")
    def agent_event(ev: AgentEvent):
        world.hub.emit("agent.event", ev.model_dump())
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await world.hub.connect(sock)
        await sock.send_text(json.dumps({"type": "rover.state", **world.rover.state()}))
        try:
            while True:
                try:
                    msg = json.loads(await sock.receive_text())
                except json.JSONDecodeError:
                    continue
                kind = msg.get("type")
                if kind == "rover.pose":
                    world.rover.manual_pose(msg)
                elif kind == "view.snapshot":
                    fut = world.snapshots.get(msg.get("request_id"))
                    if fut and not fut.done():
                        fut.set_result(msg.get("image_base64") or msg.get("image"))
                elif kind == "view.state":
                    world.view["camera"] = {k: msg.get(k) for k in ("mode", "x", "y", "z", "heading", "pitch")}
                elif kind == "marker.click":
                    try:
                        m = places.add_marker(float(msg["x"]), float(msg["y"]), msg.get("label"), "user")
                        await world.hub.broadcast("marker.add", m)
                    except (PlaceError, KeyError, TypeError, ValueError) as e:
                        await sock.send_text(json.dumps({"type": "error", "error": str(e)}))
        except WebSocketDisconnect:
            pass
        finally:
            world.hub.disconnect(sock)

    for mount, sub in (("/tiles", "tiles"), ("/data", "data"), ("/bubble", "bubble")):
        d = t.data_dir / sub
        if d.exists():
            app.mount(mount, StaticFiles(directory=d), name=sub)
    return app


app = create_app()
