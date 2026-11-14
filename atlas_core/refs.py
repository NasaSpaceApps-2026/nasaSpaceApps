"""Location references (contracts/mcp-tools.md section 3.1). FROZEN INTERFACE.

resolve_ref(site, ref, store) -> list of members, each {id, name?, x, y, lat, lon}.

Accepted forms:
    {"x": 120, "y": -340}            site-frame point
    {"lat": 18.44, "lon": 77.45}     planetocentric lat, east lon
    "poi:<id>"  or a bare POI id     named point of interest
    "base" / "rover"                 keywords (base = POI with type "base"; rover = site.rover_xy)
    "set:<n>"                        every member of a location set
    "<n>.<k>"                        one member of a location set
    a list of any of the above       concatenated
"""
from __future__ import annotations

from .errors import AtlasError
from .sets import SetStore
from .site import Site


def _pt(site: Site, x: float, y: float, **extra) -> dict:
    lat, lon = site.xy_to_latlon(x, y)
    d = {"x": float(x), "y": float(y), "lat": round(lat, 5), "lon": round(lon, 5)}
    d.update(extra)
    return d


def _check_bounds(site: Site, p: dict) -> dict:
    if not site.in_bounds(p["x"], p["y"]):
        raise AtlasError(
            f"Location ({p['x']:.0f}, {p['y']:.0f}) m is outside the site, which spans "
            f"±{site.half_m:.0f} m in x and y.",
            hint="Use site-frame metres from get_site_overview, or a POI id.",
        )
    return p


def resolve_ref(site: Site, ref, store: SetStore | None = None) -> list[dict]:
    if isinstance(ref, list):
        out: list[dict] = []
        for r in ref:
            out.extend(resolve_ref(site, r, store))
        return out
    if isinstance(ref, dict):
        if "x" in ref and "y" in ref:
            return [_check_bounds(site, _pt(site, ref["x"], ref["y"], id=ref.get("id", "point")))]
        if "lat" in ref and "lon" in ref:
            x, y = site.latlon_to_xy(ref["lat"], ref["lon"])
            return [_check_bounds(site, _pt(site, x, y, id=ref.get("id", "point")))]
        raise AtlasError(f"Cannot read location {ref!r}.", hint='Use {"x":..,"y":..}, {"lat":..,"lon":..}, a POI id or a set id.')
    if not isinstance(ref, str):
        raise AtlasError(f"Cannot read location {ref!r}.", hint="Use a point, POI id or set id.")

    s = ref.strip()
    if s.startswith("set:"):
        if store is None:
            raise AtlasError("Set references are not available here.")
        return [dict(m) for m in store.get(s).members]
    if store is not None and "." in s and s.split(".")[0].isdigit():
        m = store.find_member(s)
        if m is None:
            raise AtlasError(f'Unknown set member "{s}".', hint="Call get_set to list members.")
        return [dict(m)]
    if s == "rover":
        xy = getattr(site, "rover_xy", None)
        if xy is None:
            raise AtlasError("Rover position is not known to this server.", hint="Pass the rover's x, y explicitly.")
        return [_pt(site, xy[0], xy[1], id="rover", name="rover")]
    if s == "base":
        bases = [p for p in site.pois if p.get("type") == "base"]
        if not bases:
            raise AtlasError("No base is defined for this site.", hint="Pass a point or POI id instead.")
        p = bases[0]
        return [_pt(site, p["x"], p["y"], id=f"poi:{p['id']}", name=p.get("name"))]
    poi_id = s[4:] if s.startswith("poi:") else s
    for p in site.pois:
        if p.get("id") == poi_id:
            return [_check_bounds(site, _pt(site, p["x"], p["y"], id=f"poi:{poi_id}", name=p.get("name")))]
    names = ", ".join(p["id"] for p in site.pois[:15])
    raise AtlasError(f'Unknown location "{s}".', hint=f"Known POIs include: {names}. Or pass {{\"x\":..,\"y\":..}}.")
