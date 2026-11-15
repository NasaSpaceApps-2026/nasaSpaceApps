"""FastAPI app: every endpoint in contracts/mcp-api-needs.md, backed by atlas_core.

create_app(site) -> FastAPI. All positions are site-frame metres; location references accept everything
atlas_core.refs.resolve_ref does, including "marker:<id>". AtlasError -> HTTP 422 {"error", "hint"}.
WebSocket envelope: {"type": str, "ts": float, "payload": object}.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import uuid

from fastapi import Body, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from atlas_core import AtlasError, catalog, describe, load_site, resolve_ref, route as R, search, travel
from atlas_core.site import Site

from .rover import Rover  # noqa: F401  (re-export for tests)
from .search_sites import search_sites, search_targets
from .util import clean
from .validation import run_validation
from .world import World

MAX_HIGHLIGHTS = 50
VIEW_MODES = ("globe", "orbit", "chase", "ground")


def _g(v) -> str:
    return f"{float(v):.4g}"


class CleanJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return super().render(clean(content))


def load_configured_site() -> tuple[Site, bool]:
    d = os.environ.get("ATLAS_SITE_DIR")
    if d and os.environ.get("ATLAS_SITE", "").lower() != "synthetic":
        return load_site(d), False
    from atlas_core.testing import make_site
    return make_site(), True


def _traverse_from_env() -> dict | None:
    d = os.environ.get("ATLAS_SITE_DIR")
    if d and os.environ.get("ATLAS_SITE", "").lower() != "synthetic":
        p = os.path.join(d, "traverse.geojson")
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    return None


def take(body, allowed: tuple[str, ...], required: tuple[str, ...] = ()) -> dict:
    if not isinstance(body, dict):
        raise AtlasError("The request body must be a JSON object.")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise AtlasError(f"Unknown field(s): {', '.join(unknown)}.", hint="Valid fields: " + ", ".join(allowed) + ".")
    missing = [k for k in required if body.get(k) is None]
    if missing:
        raise AtlasError(f"Missing required field(s): {', '.join(missing)}.", hint="Required: " + ", ".join(required) + ".")
    return body


def create_app(site: Site | None = None, synthetic: bool | None = None, traverse: dict | None = None,
               snapshot_timeout_s: float = 5.0) -> FastAPI:
    if site is None:
        site, synthetic = load_configured_site()
        traverse = traverse or _traverse_from_env()
    if synthetic is None:
        synthetic = site.name == "synthetic"
    from .vehicles import add_vehicle_profiles
    add_vehicle_profiles(site)      # crew_buggy etc. from api/profiles.yaml when the site has no such profile
    world = World(site, bool(synthetic), traverse, snapshot_timeout_s)
    app = FastAPI(title="Ares Atlas dev API", version="0.1", default_response_class=CleanJSONResponse,
                  description="Stand-in for api/ (Yousef). Reference implementation over atlas_core.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.world = world
    from .routes_visibility import visibility_router
    app.include_router(visibility_router(site, world.store))
    hub, store, rover = world.hub, world.store, world.rover
    from .routes_sun import make_router as _sun_router
    app.include_router(_sun_router(site, store))   # /sun/* (sun position, illumination)
    from .routes_lake import router as _lake_router
    from .routes_envelope import router as _eva_router
    app.include_router(_lake_router)    # /lake/*
    app.include_router(_eva_router)     # /eva/*

    # ---- errors ---------------------------------------------------------------------------------
    @app.exception_handler(AtlasError)
    async def _atlas_error(_: Request, e: AtlasError):
        return CleanJSONResponse(status_code=422, content=e.to_dict())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return CleanJSONResponse(status_code=422, content={
            "error": f"Invalid request: {loc + ': ' if loc else ''}{first.get('msg', 'bad input')}",
            "hint": "Check field names and types against the endpoint description."})

    # ---- basics ---------------------------------------------------------------------------------
    @app.get("/health")
    async def health():
        return {"ok": True, "synthetic": world.synthetic, "site": site.name, "viewers": hub.count}

    def travel_profiles() -> dict:
        out = {}
        for name, p in site.profiles.items():
            if isinstance(p, dict) and p.get("travel"):
                params, assumed = R.route_params(site, name)
                out[name] = {k: params[k] for k in ("flat_speed_mps", "max_slope_deg", "caution_slope_deg",
                                                    "eva_limit_min", "reserve_fraction")}
                out[name]["assumed"] = assumed
        return out

    @app.get("/site")
    async def get_site():
        return {"name": site.name, "lat0": site.lat0, "lon0": site.lon0, "radius_m": site.radius_m,
                "res_m": site.res_m, "size_px": site.size_px, "size_m": site.size_px * site.res_m,
                "half_m": site.half_m, "synthetic": world.synthetic,
                "frame": "Site frame: metres, x east, y north, origin at site centre (lat0, lon0). "
                         "Headings are degrees clockwise from north.",
                "layers": sorted(site.layers), "profiles": site.profiles, "limits": travel_profiles(),
                "poi_count": len(site.pois), "marker_count": len(world.markers)}

    @app.get("/layers")
    async def layers(kind: str | None = None, query: str | None = None):
        return catalog.list_layers(site, kind, query)

    @app.get("/layers/{layer_id}")
    async def layer(layer_id: str):
        return catalog.describe_layer(site, layer_id)

    @app.get("/overview")
    async def overview():
        d = catalog.site_overview(site)
        d["rover"] = {**(d["rover"] or {}), **{k: v for k, v in rover.state().items()
                                                if k in ("x", "y", "heading_deg", "mode", "fsd_armed")}}
        d["markers"] = list(world.markers.values())
        order = {t: i for i, t in enumerate(("base", "landmark"))}
        d["pois"] = [{"id": p.get("id"), "name": p.get("name"), "type": p.get("type"), "x": p.get("x"),
                      "y": p.get("y"), "science_value": p.get("science_value")}
                     for p in sorted(site.pois, key=lambda p: order.get(p.get("type"), 9))]
        d["search_targets"] = search_targets(site)
        d["synthetic"] = world.synthetic
        return d

    @app.post("/describe")
    async def describe_(body: dict = Body(...)):
        b = take(body, ("at", "radius_m", "layers"), ("at",))
        return describe.describe_location(site, store, b["at"], b.get("radius_m") or 0.0, b.get("layers"))

    # ---- search ---------------------------------------------------------------------------------
    async def _announce(source: str, res: dict) -> None:
        await hub.broadcast("search.results", {"source": source, "set_id": res.get("set_id"),
                                               "sites": res.get("sites", res.get("results", []))})

    def _add_range_hint(res: dict) -> None:
        """Empty search: append the real range of the layers behind the failing conditions to the hint."""
        if res.get("hint"):
            res["hint"] = res["hint"].replace("nearest(from=", "nearest(from_ref=").replace("to=<pad set>", "to_ref=<pad set>")
        if res.get("count") or not res.get("diagnostics"):
            return
        notes, seen = [], set()
        steps = sorted((d for d in res["diagnostics"] if d["step"] != "all conditions"
                        and not d["step"].startswith("+")), key=lambda d: d["pass_fraction"])
        for d in steps:
            lid = d["step"].split(" ")[0]
            if lid in seen or lid not in site.layers:
                continue
            seen.add(lid)
            try:
                info = catalog.describe_layer(site, lid)
            except AtlasError:
                continue
            if info["kind"] == "continuous" and info.get("min") is not None:
                notes.append(f'Layer {lid} on this site: range {_g(info["min"])} to {_g(info["max"])}, median '
                             f'{_g(info["median"])}{(" " + info["units"]) if info.get("units") else ""}; '
                             f'"{d["step"]}" passes {d["pass_fraction"] * 100:.2g}% of cells. Pick a threshold inside '
                             "that range.")
            elif info.get("classes"):
                notes.append(f'Layer {lid} classes on this site: {", ".join(info["classes"])}'
                             + (f'; "{d["step"]}" passes {d["pass_fraction"] * 100:.2g}% of cells, so that class '
                                "is absent or tiny here." if d["pass_fraction"] < 0.001 else "."))
            if len(notes) >= 2:
                break
        if notes:
            res["hint"] = ((res.get("hint") or "").replace(" Relax that condition.", "") + " " + " ".join(notes)).strip()

    @app.post("/search/locations")
    async def search_locations(body: dict = Body(...)):
        b = take(body, ("conditions", "profile", "footprint_radius_m", "within", "objective", "spacing_m", "limit"))
        res = search.find_locations(site, store, b.get("conditions"), b.get("profile"), b.get("footprint_radius_m"),
                                    b.get("within"), b.get("objective", "flattest"), b.get("spacing_m"),
                                    b.get("limit", 5))
        _add_range_hint(res)
        await _announce("find_locations", res)
        return res

    @app.post("/search/regions")
    async def search_regions(body: dict = Body(...)):
        b = take(body, ("conditions", "profile", "min_area_m2", "within", "objective", "limit"))
        res = search.find_regions(site, store, b.get("conditions"), b.get("profile"), b.get("min_area_m2", 400.0),
                                  b.get("within"), b.get("objective", "largest"), b.get("limit", 5))
        _add_range_hint(res)
        await _announce("find_regions", res)
        return res

    @app.post("/search/nearest")
    async def search_nearest(body: dict = Body(...)):
        b = take(body, ("from", "to", "metric", "profile", "limit"), ("from", "to"))
        return travel.nearest(site, store, b["from"], b["to"], b.get("metric", "walk_time_min"),
                              b.get("profile", "eva_walkable"), b.get("limit", 5))

    @app.get("/sets/{set_id}")
    async def get_set(set_id: str, offset: int = 0, limit: int = 10, sort: str | None = None,
                      descending: bool = False):
        if offset < 0 or not 1 <= limit <= 50:
            raise AtlasError("offset must be >= 0 and limit within 1..50.", hint="Try offset=0, limit=10.")
        page = store.page(set_id, offset, limit, sort, descending)
        page["query"] = store.get(set_id).query
        return page

    @app.get("/search/targets")
    async def get_search_targets():
        return {"targets": search_targets(site)}

    @app.post("/search/sites")
    async def post_search_sites(body: dict = Body(...)):
        res = search_sites(site, store, body)
        await _announce("find_sites", res)
        return res

    # ---- terrain, POIs ----------------------------------------------------------------------
    @app.get("/terrain/sample")
    async def terrain_sample(x: float, y: float, profile: str = "rover_scout"):
        loc = describe.describe_location(site, store, {"x": x, "y": y}, 0.0, None)["locations"][0]
        vals = loc["values"]
        params, _ = R.route_params(site, profile)

        def v(k):
            d = vals.get(k) or {}
            return d.get("value")
        slope = v("slope_2m")
        return {"x": x, "y": y, "lat": loc["lat"], "lon": loc["lon"], "elevation_m": v("elevation"),
                "slope_deg": slope, "roughness_m": v("rough_5x5"),
                "hazard_class": R.hazard_class(float("nan") if slope is None else slope, params),
                "hazard_profile": profile, "caution_slope_deg": params["caution_slope_deg"],
                "no_go_slope_deg": params["max_slope_deg"], "layers": vals, "quality": loc["quality"],
                "nearest_ground_truth": loc["nearest_ground_truth"]}

    @app.post("/terrain/profile")
    async def terrain_profile(body: dict = Body(...)):
        b = take(body, ("points", "spacing_m"), ("points",))
        return R.elevation_profile(site, store, b["points"], b.get("spacing_m"))

    def poi_out(p: dict, near: dict | None) -> dict:
        z = R.terrain_z(site, p["x"], p["y"])
        lat, lon = site.xy_to_latlon(p["x"], p["y"])
        d = {k: p.get(k) for k in ("id", "name", "type", "description", "mission", "science_value", "sol",
                                   "source_url", "region", "rock_type", "in_situ_minerals",
                                   "position_method", "position_uncertainty_m")}
        d.update({"id": p.get("id"), "x": p["x"], "y": p["y"], "z": round(z, 2) if math.isfinite(z) else None,
                  "lat": round(lat, 5), "lon": round(lon, 5), "ref": f"poi:{p.get('id')}"})
        if near is not None:
            d["distance_m"] = round(math.hypot(p["x"] - near["x"], p["y"] - near["y"]), 1)
        return {k: v for k, v in d.items() if v is not None or k in ("science_value",)}

    @app.get("/pois")
    async def pois(type: str | None = None, q: str | None = None, near: str | None = None,
                   radius: float | None = None, limit: int = 25):
        if not 1 <= limit <= 100:
            raise AtlasError("limit must be within 1..100.")
        npt = None
        if near:
            parts = near.split(",")
            try:
                if len(parts) == 2:
                    npt = {"x": float(parts[0]), "y": float(parts[1])}
            except ValueError:
                npt = None
            if npt is None:
                npt = resolve_ref(site, near, store)[0]
        items = site.pois
        if type:
            items = [p for p in items if p.get("type") == type]
        if q:
            ql = q.lower()
            items = [p for p in items if ql in " ".join(str(p.get(k) or "") for k in ("id", "name", "type", "description")).lower()]
        if radius is not None:
            if npt is None:
                raise AtlasError("radius needs near.", hint="Pass near=x,y or near=poi:<id>.")
            items = [p for p in items if math.hypot(p["x"] - npt["x"], p["y"] - npt["y"]) <= radius]
        out = [poi_out(p, npt) for p in items]
        if npt is not None:
            out.sort(key=lambda d: d["distance_m"])
        else:
            out.sort(key=lambda d: (-(d.get("science_value") if d.get("science_value") is not None else -1), str(d["id"])))
        types = sorted({p.get("type") for p in site.pois if p.get("type")})
        known = [str(p.get("id")) for p in site.pois[:25]]
        return {"count": len(out), "pois": out[:limit], "types": types, "known_ids": known,
                "note": "science_value is null where the POI has none recorded."}

    # ---- routes ---------------------------------------------------------------------------------
    @app.post("/route")
    async def post_route(body: dict = Body(...)):
        b = take(body, ("start", "goal", "via", "profile", "max_slope_deg", "time_budget_min"), ("goal",))
        res = R.plan_route(site, store, b.get("start", "rover"), b["goal"], b.get("via"),
                           b.get("profile") or rover.profile, b.get("max_slope_deg"), b.get("time_budget_min"))
        if res["feasible"]:
            res["route_id"] = world.new_route_id()
            await world.add_route(res)
            world.last_planned = res["route_id"]
        return res

    @app.get("/route/{route_id}")
    async def get_route(route_id: str):
        return world.get_route(route_id)

    @app.post("/route/science")
    async def post_route_science(body: dict = Body(...)):
        b = take(body, ("start", "time_budget_min", "interests", "profile", "max_slope_deg", "stop_minutes",
                        "return_to_start", "max_stops"), ("time_budget_min",))
        res = R.science_traverse(site, store, b.get("start", "rover"), b["time_budget_min"], b.get("interests"),
                                 b.get("profile", "eva_walkable"), b.get("max_slope_deg"),
                                 b.get("stop_minutes", 15.0), b.get("return_to_start", True), b.get("max_stops"))
        r = res["route"]
        if r and r["feasible"] and r["points"] and res["stops"]:
            r["route_id"] = world.new_route_id()
            await world.add_route(r)
            world.last_planned = r["route_id"]
        return res

    # ---- rover ----------------------------------------------------------------------------------
    @app.get("/rover/state")
    async def rover_state():
        return rover.state()

    def vehicle_table() -> dict:
        lim = travel_profiles()
        out = {}
        for name, v in lim.items():
            prof = site.profiles.get(name) or {}
            fs = (prof.get("travel") or {}).get("flat_speed_mps")
            out[name] = {"title": prof.get("description") or name, "flat_speed_mps": v["flat_speed_mps"],
                         "max_slope_deg": v["max_slope_deg"], "caution_slope_deg": v["caution_slope_deg"],
                         "eva_limit_min": v["eva_limit_min"],
                         "source": (fs.get("source") if isinstance(fs, dict) else None) or "assumed",
                         "active": name == rover.profile}
        return {"current": rover.profile, "vehicles": out}

    @app.get("/rover/vehicle")
    async def get_vehicle():
        return vehicle_table()

    @app.post("/rover/vehicle")
    async def set_vehicle(body: dict = Body(...)):
        b = take(body, ("profile",), ("profile",))
        tbl = vehicle_table()
        if b["profile"] not in tbl["vehicles"]:
            raise AtlasError(f'Unknown vehicle "{b["profile"]}".', hint="Valid vehicles: " + ", ".join(tbl["vehicles"]) + ".")
        await rover.set_profile(b["profile"])
        tbl = vehicle_table()
        return {**tbl, "ok": True, "vehicle": rover.profile, "title": tbl["vehicles"][rover.profile]["title"],
                "state": rover.state()}

    @app.post("/rover/command")
    async def rover_command(body: dict = Body(...)):
        return await rover.command(body)

    @app.get("/fsd/status")
    async def fsd_status():
        return rover.fsd_status()

    # ---- markers --------------------------------------------------------------------------------
    @app.post("/markers")
    async def post_marker(body: dict = Body(...)):
        b = take(body, ("x", "y", "at", "label", "created_by"))
        if b.get("at") is not None:
            pts = resolve_ref(site, b["at"], store)
            if len(pts) != 1:
                raise AtlasError("`at` must be one location.")
            x, y = pts[0]["x"], pts[0]["y"]
        elif b.get("x") is not None and b.get("y") is not None:
            x, y = float(b["x"]), float(b["y"])
        else:
            raise AtlasError("A marker needs x and y (or `at`).", hint='Example: {"x": 120, "y": -40, "label": "camp"}.')
        who = b.get("created_by", "agent")
        if who not in ("user", "agent"):
            raise AtlasError('created_by must be "user" or "agent".')
        return await world.add_marker(x, y, b.get("label"), who)

    @app.get("/markers")
    async def get_markers():
        return {"count": len(world.markers), "markers": list(world.markers.values())}

    @app.delete("/markers/{marker_id}")
    async def delete_marker(marker_id: str):
        return {"removed": await world.remove_marker(marker_id)}

    # ---- view -----------------------------------------------------------------------------------
    def one_point(ref, what="target") -> dict:
        pts = resolve_ref(site, ref, store)
        if len(pts) != 1:
            raise AtlasError(f"`{what}` must be one location, got {len(pts)}.", hint='Use a point, "poi:<id>" or one set member.')
        p = pts[0]
        z = R.terrain_z(site, p["x"], p["y"])
        return {"id": p.get("id"), "name": p.get("name"), "x": p["x"], "y": p["y"],
                "z": round(z, 2) if math.isfinite(z) else None, "lat": p["lat"], "lon": p["lon"]}

    @app.post("/view")
    async def post_view(body: dict = Body(...)):
        b = take(body, ("mode", "target", "layers", "highlights", "distance_m", "heading_deg", "pitch_deg"))
        if not any(b.get(k) is not None for k in ("mode", "target", "layers", "highlights")):
            raise AtlasError("Nothing to do.", hint="Pass mode, target, layers or highlights.")
        mode = b.get("mode")
        if mode is not None and mode not in VIEW_MODES:
            raise AtlasError(f'Unknown view mode "{mode}".', hint="Use one of: " + ", ".join(VIEW_MODES) + ".")
        target = one_point(b["target"]) if b.get("target") is not None else None
        layer_changes = []
        lay = b.get("layers")
        if lay is not None:
            items = {k: True for k in lay} if isinstance(lay, list) else lay
            if not isinstance(items, dict):
                raise AtlasError("layers must be {layer_id: visible} or a list of layer ids to show.")
            for lid, val in items.items():
                if lid not in site.layers:
                    raise AtlasError(f'Unknown layer "{lid}".', hint="Layers: " + ", ".join(sorted(site.layers)) + ".")
                vis = val.get("visible", True) if isinstance(val, dict) else bool(val)
                ch = {"layer": lid, "visible": vis}
                if isinstance(val, dict) and "opacity" in val:
                    ch["opacity"] = float(val["opacity"])
                layer_changes.append(ch)
        highlights, capped = [], None
        if b.get("highlights") is not None:
            for r in (b["highlights"] if isinstance(b["highlights"], list) else [b["highlights"]]):
                for m in resolve_ref(site, r, store):
                    highlights.append({"id": m.get("id"), "name": m.get("name"), "x": m["x"], "y": m["y"]})
            if len(highlights) > MAX_HIGHLIGHTS:
                capped = len(highlights)
                highlights = highlights[:MAX_HIGHLIGHTS]
        if mode is not None or target is not None:
            await hub.broadcast("view.camera", {"mode": mode, "target": target, "heading_deg": b.get("heading_deg"),
                                                "pitch_deg": b.get("pitch_deg"), "distance_m": b.get("distance_m")})
        for ch in layer_changes:
            await hub.broadcast("view.layer", ch)
        if b.get("highlights") is not None:
            await hub.broadcast("view.highlight", {"ids": [h["id"] for h in highlights], "points": highlights})
        res = {"ok": True, "mode": mode, "target": target, "layers": layer_changes, "highlights": highlights,
               "viewers": hub.count}
        if b.get("distance_m") is not None:
            res["distance_m"] = b["distance_m"]
        if capped:
            res["note"] = (f"Only the first {MAX_HIGHLIGHTS} of {capped} highlights (best-ranked first) were shown; "
                           "pass fewer or specific members such as \"2.1\" to choose which.")
        if hub.count == 0:
            res["note"] = (res.get("note", "") + " No viewer is connected, so nothing was displayed; the request was accepted.").strip()
        return res

    @app.post("/view/snapshot")
    async def view_snapshot(body: dict = Body(default={})):
        b = take(body or {}, ("direction",))
        if hub.count == 0:
            return CleanJSONResponse(status_code=504, content={
                "error": "No viewer is connected, so no snapshot can be taken.",
                "hint": "Open the web app, then retry."})
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        world.snapshots[rid] = fut
        try:
            await hub.broadcast("view.snapshot_request", {"request_id": rid, "direction": b.get("direction")})
            img = await asyncio.wait_for(fut, world.snapshot_timeout_s)
            return {"request_id": rid, "content_type": "image/jpeg", "image_base64": img,
                    "bytes_base64": len(img)}
        except asyncio.TimeoutError:
            return CleanJSONResponse(status_code=504, content={
                "error": f"The viewer did not send a snapshot within {world.snapshot_timeout_s:g} s.",
                "hint": "Check the viewer is open and rendering, then retry."})
        finally:
            world.snapshots.pop(rid, None)

    # ---- events, validation ---------------------------------------------------------------------
    @app.post("/events/agent")
    async def agent_event(body: dict = Body(...)):
        b = take(body, ("tool", "args_summary", "result_summary", "status", "is_error"), ("tool",))
        await hub.broadcast("agent.event", b)
        return {"ok": True, "viewers": hub.count}

    @app.get("/validation")
    async def validation():
        return run_validation(world)

    # ---- websocket ------------------------------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await hub.connect(ws)
        try:
            await ws.send_json({"type": "rover.state", "ts": 0, "payload": clean(rover.state())})
            while True:
                msg = await ws.receive_json()
                t = msg.get("type") if isinstance(msg, dict) else None
                p = msg.get("payload", msg) if isinstance(msg, dict) else {}
                try:
                    if t == "rover.pose":
                        rover.set_manual_pose(p)
                    elif t == "view.snapshot":
                        fut = world.snapshots.get(p.get("request_id"))
                        img = p.get("image_base64") or p.get("jpeg_base64")
                        if fut is not None and not fut.done() and img:
                            fut.set_result(img)
                    elif t == "marker.click":
                        await world.add_marker(float(p["x"]), float(p["y"]), p.get("label"), "user")
                    elif t == "ping":
                        await ws.send_json({"type": "pong", "ts": 0, "payload": {}})
                except (AtlasError, KeyError, TypeError, ValueError) as e:
                    await ws.send_json({"type": "error", "ts": 0, "payload": {
                        "error": e.message if isinstance(e, AtlasError) else f"bad {t} message: {e}"}})
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(ws)

    return app
