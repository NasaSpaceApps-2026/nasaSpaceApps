"""Mid-mission astronaut endpoints (/mission/*): EVA clock and state, status, return route, havens, path ahead, comms,
samples, bearing, rendezvous, daylight. Logic lives in atlas_core.mission; this file holds the one EVA in progress
(app.state.eva), the simulated mission clock (`/mission/advance`, or an explicit `now` per call) and the sample log.

The crew position is the rover position of the world ("me"/"here" in every location reference). The rover the crew
left is a haven too (parked where the EVA began; `crew_at` moves the crew away from it).
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool

from atlas_core import AtlasError, mission as M, resolve_ref

from .routes_lake import take
from .util import clean

router = APIRouter(tags=["mission"])


def _w(request: Request):
    return request.app.state.world


def _crew(w) -> dict:
    return {"x": float(w.rover.x), "y": float(w.rover.y)}


def _eva(request: Request):
    return getattr(request.app.state, "eva", None)


def _need_eva(request: Request) -> M.EvaState:
    st = _eva(request)
    if st is None:
        raise AtlasError("No EVA is in progress, so there is no clock or budget to report on.",
                         hint="Call eva_start first (defaults: base, 8 h budget, 20% reserve).")
    return st


def _places(w) -> list[dict]:
    gaz = getattr(w, "gazetteer", None)
    return [{"name": e.name, "x": e.x, "y": e.y} for e in (gaz.entries if gaz else []) if e.kind != "alias"]


VEHICLE_WORDS = ("rover", "the rover", "my rover", "vehicle", "the vehicle", "buggy", "the buggy", "the rover vehicle")


def _vref(request: Request, ref):
    """In the mission tools "the rover" is the vehicle parked at the start of the EVA (not the crew, who is "me"/"here")."""
    if isinstance(ref, str) and ref.strip().lower() in VEHICLE_WORDS:
        st = _eva(request)
        if st is None or not st.rover_parked:
            raise AtlasError("The rover's parking spot is not known because no EVA has been started.",
                             hint='Call eva_start first, or give the rover\'s position as {"x":..,"y":..}.')
        return {"x": st.rover_parked["x"], "y": st.rover_parked["y"], "id": "rover"}
    return ref


def _opt_one(w, ref, what):
    pts = resolve_ref(w.site, ref, w.store)
    if len(pts) != 1:
        raise AtlasError(f"`{what}` must be one place, got {len(pts)}.")
    return pts[0]


@router.post("/mission/eva/start")
async def eva_start(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("base", "budget_min", "reserve", "crew_at", "havens", "now", "already_out_min"))
    w = _w(request)
    parked = (float(w.rover.x), float(w.rover.y))
    if b.get("crew_at") is not None:
        p = _opt_one(w, b["crew_at"], "crew_at")
        await w.rover.command({"cmd": "teleport", "x": p["x"], "y": p["y"]})
    crew = _crew(w)
    replaced = _eva(request) is not None
    st = await run_in_threadpool(M.start_eva, w.site, w.store, crew, b.get("base"), b.get("budget_min"), b.get("reserve"),
                                 b.get("havens"), b.get("now"), b.get("already_out_min") or 0.0, parked)
    request.app.state.eva = st
    return clean({"started": True, "base": st.base, "budget_min": st.budget_min, "reserve_fraction": st.reserve,
                  "usable_min": round(st.usable_min, 1), "start_utc": M.iso(st.start_utc), "already_out_min": st.already_out_min,
                  "havens": st.havens, "crew": crew, "moved_crew": b.get("crew_at") is not None, "replaced_running_eva": replaced,
                  "assumptions": ["Budget and reserve are the profile's EVA limit and reserve unless given (cited or assumed per "
                                  "profile); the clock is the simulated mission clock, 1 s per real second plus any advance.",
                                  M.ASSUMPTIONS]})


@router.post("/mission/eva/end")
async def eva_end(request: Request, body: dict = Body(default={})):
    take(body or {}, ("now",))
    st = _need_eva(request)
    w = _w(request)
    t = st.now((body or {}).get("now"))
    crew = _crew(w)
    hv = await run_in_threadpool(M.rank_havens, w.site, st, crew, w.store)
    at_haven = hv[0]["straight_m"] is not None and hv[0]["straight_m"] <= M.HAVEN_MERGE_M
    elapsed = st.elapsed_min(t)
    samples = [m for m in w.markers.values() if m.get("sample")]
    request.app.state.eva = None
    return clean({"ended": True, "elapsed_min": round(elapsed, 1), "budget_min": st.budget_min,
                  "within_budget": elapsed <= st.budget_min, "within_reserve": elapsed <= st.usable_min,
                  "nearest_haven": hv[0], "at_haven": at_haven, "samples_logged": len(samples)})


@router.post("/mission/advance")
async def mission_advance(request: Request, body: dict = Body(...)):
    """Move the simulated mission clock forward `minutes` (tests, demos, evals; walking is not clocked automatically)."""
    b = take(body, ("minutes",), ("minutes",))
    st = _need_eva(request)
    st.advance(float(b["minutes"]))
    return clean({"advanced_min": float(b["minutes"]), "elapsed_min": round(st.elapsed_min(st.now()), 1)})


@router.post("/mission/status")
async def mission_status(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("now",))
    st, w = _need_eva(request), _w(request)
    return clean(await run_in_threadpool(M.eva_status, w.site, w.store, st, _crew(w), b.get("now"), _places(w)))


@router.post("/mission/havens")
async def mission_havens(request: Request, body: dict = Body(default={})):
    take(body or {}, ())
    w = _w(request)
    crew = _crew(w)
    hv = await run_in_threadpool(M.rank_havens, w.site, _eva(request), crew, w.store)
    return clean({"crew": crew, "havens": hv, "nearest_place": M.nearest_place(_places(w), crew["x"], crew["y"]),
                  "eva_active": _eva(request) is not None, "assumptions": [M.ASSUMPTIONS]})


@router.post("/mission/return_route")
async def mission_return_route(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("to", "now"))
    w = _w(request)
    return clean(await run_in_threadpool(M.return_route, w.site, w.store, _eva(request), _crew(w), _vref(request, b.get("to")), b.get("now")))


@router.post("/mission/path_ahead")
async def mission_path_ahead(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("heading_deg", "toward", "distance_m", "now"))
    w = _w(request)
    return clean(await run_in_threadpool(M.check_path_ahead, w.site, w.store, _eva(request), _crew(w), b.get("heading_deg"),
                                         _vref(request, b.get("toward")), b.get("distance_m"), b.get("now")))


@router.post("/mission/comm_check")
async def mission_comm_check(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("to", "antenna_h", "to_antenna_h"))
    w = _w(request)
    return clean(await run_in_threadpool(M.comm_check, w.site, w.store, _crew(w), _vref(request, b.get("to") or "base"),
                                         float(b.get("antenna_h") or 1.7), b.get("to_antenna_h")))


@router.post("/mission/sample")
async def log_sample(request: Request, body: dict = Body(...)):
    b = take(body, ("note", "at", "now"), ("note",))
    w = _w(request)
    st = _eva(request)
    p = _opt_one(w, b["at"], "at") if b.get("at") is not None else {**_crew(w), "id": "me"}
    ev = await run_in_threadpool(M.sample_evidence, w.site, w.store, p["x"], p["y"])
    n = 1 + sum(1 for m in w.markers.values() if m.get("sample"))
    t = st.now(b.get("now")) if st else M.S.parse_utc(b.get("now"))
    note = str(b["note"]).strip()[:200]
    m = await w.add_marker(p["x"], p["y"], f"Sample {n}: {note}", "agent")
    rec = {"n": n, "note": note, "utc": M.iso(t), "ltst": M.S.sun_position(t, w.site.lat0, w.site.lon0)["ltst"],
           "elapsed_min": round(st.elapsed_min(t), 1) if st else None, **ev}
    m["sample"] = rec
    return clean({"logged": True, "marker_id": m["id"], "x": m["x"], "y": m["y"], "eva_active": st is not None, **rec,
                  "assumptions": ["Unit and mineral tiers are from orbital maps at this 2 m cell, not a measurement of this rock "
                                  "(a rover-confirmed tier is the only in-situ one); 'not detected' is absence of evidence."]})


@router.get("/mission/samples")
async def list_samples(request: Request):
    w = _w(request)
    out = [{"marker_id": m["id"], "x": m["x"], "y": m["y"], **m["sample"]} for m in w.markers.values() if m.get("sample")]
    return clean({"count": len(out), "samples": out})


@router.post("/mission/bearing")
async def mission_bearing(request: Request, body: dict = Body(...)):
    b = take(body, ("target",), ("target",))
    w = _w(request)
    return clean(await run_in_threadpool(M.bearing_to, w.site, w.store, _crew(w), _vref(request, b["target"])))


@router.post("/mission/rendezvous")
async def mission_rendezvous(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("rover",))
    w = _w(request)
    st = _eva(request)
    if b.get("rover") is not None:
        p = _opt_one(w, _vref(request, b["rover"]), "rover")
        rxy = (p["x"], p["y"])
    elif st is not None and st.rover_parked:
        rxy = (st.rover_parked["x"], st.rover_parked["y"])
    else:
        bp = _opt_one(w, "base", "rover")
        rxy = (bp["x"], bp["y"])
    return clean(await run_in_threadpool(M.rendezvous, w.site, w.store, _crew(w), rxy, 0.25, w.rover.profile))


@router.post("/mission/daylight")
async def mission_daylight(request: Request, body: dict = Body(default={})):
    b = take(body or {}, ("at", "now"))
    w = _w(request)
    st = _eva(request)
    p = _opt_one(w, _vref(request, b["at"]), "at") if b.get("at") is not None else _crew(w)
    t = st.now(b.get("now")) if st else M.S.parse_utc(b.get("now"))
    return clean(await run_in_threadpool(M.daylight, w.site, p["x"], p["y"], t))
