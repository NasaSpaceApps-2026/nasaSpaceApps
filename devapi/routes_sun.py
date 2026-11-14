"""Sun-position and terrain-illumination endpoints (POST /sun/position, /sun/illumination, /sun/lit_windows).

make_router(site, store) -> APIRouter; registered from devapi/app.py. Logic lives in atlas_core.sun / illumination.
Times: ISO-8601 UTC, "now", or a mission `sol` plus local true solar time `ltst` ("HH:MM" or decimal hours).
"""
from __future__ import annotations

from fastapi import APIRouter, Body

from atlas_core import AtlasError, illumination as I, resolve_ref, sun as S

from .util import clean


def _take(body, allowed, required=()):
    if not isinstance(body, dict):
        raise AtlasError("The request body must be a JSON object.")
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise AtlasError(f"Unknown field(s): {', '.join(unknown)}.", hint="Valid fields: " + ", ".join(allowed) + ".")
    missing = [k for k in required if body.get(k) is None]
    if missing:
        raise AtlasError(f"Missing required field(s): {', '.join(missing)}.", hint="Required: " + ", ".join(required) + ".")
    return body


def make_router(site, store) -> APIRouter:
    router = APIRouter(tags=["sun"])

    @router.post("/sun/position")
    async def sun_position(body: dict = Body(default={})):
        b = _take(body or {}, ("at", "lat", "lon", "utc", "sol", "ltst", "sunrise_sunset", "min_elevation_deg"))
        if b.get("at") is not None:
            pts = resolve_ref(site, b["at"], store)
            if len(pts) != 1:
                raise AtlasError("`at` must be one location.", hint='Use a point, POI id or one set member such as "2.1".')
            lat, lon = pts[0]["lat"], pts[0]["lon"]
        elif b.get("lat") is not None and b.get("lon") is not None:
            lat, lon = float(b["lat"]), float(b["lon"])
            if not -90 <= lat <= 90:
                raise AtlasError("lat must be within -90..90 (planetocentric degrees).")
        else:
            lat, lon = site.lat0, site.lon0
        t = I.resolve_time(b.get("utc"), b.get("sol"), b.get("ltst"), lon)
        res = S.sun_position(t, lat, lon)
        if b.get("sunrise_sunset", True):
            res["sunrise_sunset"] = S.sunrise_sunset(t, lat, lon, float(b.get("min_elevation_deg") or 0.0))
        res["site"] = site.name
        res["assumptions"] = [
            {"what": "Mars24 algorithm (Allison & McEwen 2000), planetocentric latitude converted to planetographic; "
                     "geometric sun-centre elevation, no refraction or dust scattering", "source": "cited: NASA GISS Mars24"},
            {"what": f"mission sol 0 = touchdown sol ({S.MARS2020_LANDING_UTC.isoformat().replace('+00:00', 'Z')}), sols "
                     f"start at local mean midnight at {S.LANDING_LON_E} E", "source": "assumed"}]
        return clean(res)

    @router.post("/sun/illumination")
    async def sun_illumination(body: dict = Body(default={})):
        b = _take(body or {}, ("at", "utc", "sol", "ltst", "radius_m", "make_set", "min_region_area_m2"))
        res = I.illumination_at(site, store, b.get("at"), b.get("utc"), b.get("sol"), b.get("ltst"),
                                b.get("radius_m"), bool(b.get("make_set", True)),
                                float(b.get("min_region_area_m2") or 100.0))
        res["site"] = site.name
        return clean(res)

    @router.post("/sun/lit_windows")
    async def sun_lit_windows(body: dict = Body(...)):
        b = _take(body, ("target", "utc_from", "utc_to", "sol", "ltst_from", "ltst_to", "step_min", "min_elevation_deg",
                         "max_incidence_deg", "radius_m", "min_lit_fraction"), ("target",))
        res = I.find_lit_windows(site, store, b["target"], b.get("utc_from"), b.get("utc_to"), b.get("sol"),
                                 b.get("ltst_from"), b.get("ltst_to"), float(b.get("step_min") or 10.0),
                                 float(b.get("min_elevation_deg") if b.get("min_elevation_deg") is not None else 10.0),
                                 b.get("max_incidence_deg"), b.get("radius_m"), b.get("min_lit_fraction"))
        res["site"] = site.name
        return clean(res)

    return router
