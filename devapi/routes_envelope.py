"""POST /eva/envelope and /eva/check: EVA safety envelope and return-margin check (atlas_core.envelope)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from atlas_core import envelope

from .routes_lake import take
from .util import clean

router = APIRouter()


@router.post("/eva/envelope")
async def eva_envelope(request: Request, body: dict = Body(...)):
    b = take(body, ("base", "budget_min", "reserve_fraction", "profile", "stops_min", "havens"))
    w = request.app.state.world
    return clean(envelope.eva_envelope(w.site, w.store, b.get("base", "base"), b.get("budget_min"),
                                       b.get("reserve_fraction"), b.get("profile", "eva_walkable"),
                                       b.get("stops_min", 0.0), b.get("havens")))


@router.post("/eva/check")
async def eva_check(request: Request, body: dict = Body(...)):
    b = take(body, ("route_id", "points", "profile", "budget_min", "reserve_fraction", "stops_min", "havens"))
    w = request.app.state.world
    route = w.get_route(b["route_id"]) if b.get("route_id") else None
    return clean(envelope.check_eva_plan(w.site, w.store, route, None if route else b.get("points"),
                                         b.get("profile", "eva_walkable"), b.get("budget_min"),
                                         b.get("reserve_fraction"), b.get("stops_min", 0.0), b.get("havens")))
