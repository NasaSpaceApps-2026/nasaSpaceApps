"""Server state: site, location sets, markers, routes, rover, WS hub."""
from __future__ import annotations

from collections import OrderedDict

from atlas_core import AtlasError, SetStore
from atlas_core.site import Site

from .hub import Hub

MAX_ROUTES = 200


class World:
    def __init__(self, site: Site, synthetic: bool = False, traverse: dict | None = None,
                 snapshot_timeout_s: float = 5.0):
        from .rover import Rover
        self.site = site
        self.synthetic = synthetic
        self.traverse = traverse
        self.snapshot_timeout_s = snapshot_timeout_s
        self.store = SetStore()
        self.hub = Hub()
        self.markers: dict[str, dict] = site.markers          # shared with resolve_ref
        self.routes: OrderedDict[str, dict] = OrderedDict()
        self.active_route: str | None = None
        self.last_planned: str | None = None        # newest route from plan_route / science / replan / fsd_enable(route_id)
        self._route_n = 0
        self._marker_n = 0
        self.snapshots: dict[str, object] = {}                 # request id -> asyncio.Future
        self.rover = Rover(self)

    # ---- routes ---------------------------------------------------------------------------------
    def new_route_id(self) -> str:
        self._route_n += 1
        return f"route-{self._route_n}"

    async def add_route(self, route: dict) -> None:
        self.routes[route["route_id"]] = route
        while len(self.routes) > MAX_ROUTES:
            self.routes.popitem(last=False)
        await self.hub.broadcast("route.set", route)

    def get_route(self, route_id: str) -> dict:
        r = self.routes.get(route_id)
        if r is None:
            known = ", ".join(list(self.routes)[-8:]) or "none yet"
            raise AtlasError(f'Unknown route "{route_id}".', hint=f"Known routes: {known}. Call plan_route first.")
        return r

    # ---- markers --------------------------------------------------------------------------------
    async def add_marker(self, x: float, y: float, label: str | None, created_by: str) -> dict:
        from atlas_core.route import terrain_z
        if not self.site.in_bounds(x, y):
            raise AtlasError(f"Marker ({x:.0f}, {y:.0f}) is outside the site (±{self.site.half_m:.0f} m).",
                             hint="Use site-frame metres inside the site.")
        self._marker_n += 1
        mid = f"m{self._marker_n}"
        lat, lon = self.site.xy_to_latlon(x, y)
        z = terrain_z(self.site, x, y)
        m = {"id": mid, "x": round(float(x), 2), "y": round(float(y), 2),
             "z": round(z, 2) if z == z else None, "lat": round(lat, 5), "lon": round(lon, 5),
             "label": label or mid, "created_by": created_by}
        self.markers[mid] = m
        await self.hub.broadcast("marker.add", m)
        return m

    async def remove_marker(self, mid: str) -> dict:
        m = self.markers.pop(mid, None)
        if m is None:
            known = ", ".join(self.markers) or "none"
            raise AtlasError(f'Unknown marker "{mid}".', hint=f"Known markers: {known}.")
        await self.hub.broadcast("marker.remove", {"id": mid})
        return m
