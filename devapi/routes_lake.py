"""POST /lake/flood, GET /lake/levels: ancient-lake reconstruction (atlas_core.lake)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from atlas_core import AtlasError, lake

from .util import clean

router = APIRouter()


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


@router.get("/lake/levels")
async def lake_levels():
    return clean({"levels": [{"named_level": k, **v} for k, v in lake.NAMED_LEVELS.items()],
                  "datum_note": lake.DATUM_NOTE, "caveat": lake.CAVEAT})


@router.post("/lake/flood")
async def lake_flood(request: Request, body: dict = Body(...)):
    b = take(body, ("level_m", "named_level", "connected_to", "check"))
    w = request.app.state.world
    return clean(lake.flood_at_level(w.site, w.store, b.get("level_m"), b.get("named_level"),
                                     b.get("connected_to"), b.get("check")))
