"""Line-of-sight endpoints: POST /visibility/viewshed, /visibility/line_of_sight, /visibility/vantage.

Registered by devapi.app with `app.include_router(visibility_router(site, store))`. AtlasError -> 422
{error, hint} through the app-wide handler. Handlers are plain `def` so FastAPI runs the (CPU-bound) work in
its thread pool and the event loop stays responsive.
"""
from __future__ import annotations

from fastapi import APIRouter, Body

from atlas_core import AtlasError, visibility as V


def _take(body, allowed: tuple[str, ...], required: tuple[str, ...] = ()) -> dict:
    if not isinstance(body, dict):
        raise AtlasError("The request body must be a JSON object.")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise AtlasError(f"Unknown field(s): {', '.join(unknown)}.", hint="Valid fields: " + ", ".join(allowed) + ".")
    missing = [k for k in required if body.get(k) is None]
    if missing:
        raise AtlasError(f"Missing required field(s): {', '.join(missing)}.", hint="Required: " + ", ".join(required) + ".")
    return body


def visibility_router(site, store) -> APIRouter:
    """Router for POST /visibility/viewshed, /line_of_sight and /vantage over atlas_core.visibility."""
    router = APIRouter(prefix="/visibility", tags=["visibility"])

    @router.post("/viewshed")
    def viewshed(body: dict = Body(...)):
        b = _take(body, ("at", "observer_height_m", "target_height_m", "max_radius_m", "curvature", "check", "coarse"),
                  ("at",))
        return V.viewshed(site, store, b["at"], b.get("observer_height_m", 1.7), b.get("target_height_m", 0.0),
                          b.get("max_radius_m", 3000.0), b.get("curvature", True), b.get("check"), b.get("coarse"))

    @router.post("/line_of_sight")
    def line_of_sight(body: dict = Body(...)):
        b = _take(body, ("a", "b", "height_a", "height_b", "curvature"), ("a", "b"))
        return V.line_of_sight(site, b["a"], b["b"], b.get("height_a", 1.7), b.get("height_b", 1.7), store,
                               b.get("curvature", True))

    @router.post("/vantage")
    def vantage(body: dict = Body(...)):
        b = _take(body, ("target", "observer_height_m", "within", "conditions", "limit", "target_height_m",
                         "max_distance_m", "min_distance_m", "spacing_m"), ("target",))
        return V.find_vantage_points(site, store, b["target"], b.get("observer_height_m", 1.7), b.get("within"),
                                     b.get("conditions"), b.get("limit", 5), b.get("target_height_m", 0.0),
                                     b.get("max_distance_m", 1500.0), b.get("spacing_m"),
                                     min_distance_m=b.get("min_distance_m", 0.0))

    return router
