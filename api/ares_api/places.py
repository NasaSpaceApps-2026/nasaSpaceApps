"""Points of interest, user/agent markers, and resolving any position argument to x, y."""
import itertools
import json
import math
import time

from .terrain import Terrain


class PlaceError(ValueError):
    """Bad position argument; message is written for an agent to act on."""


class Places:
    def __init__(self, terrain: Terrain):
        self.terrain = terrain
        self.pois: dict[str, dict] = {}
        path = terrain.data_dir / "data" / "pois.geojson"
        if path.exists():
            for f in json.loads(path.read_text())["features"]:
                p = dict(f["properties"])
                if p.get("x") is None or p.get("y") is None:
                    lon, lat = f["geometry"]["coordinates"][:2]
                    p["x"], p["y"] = terrain.xy_from_latlon(lat, lon)
                self.pois[p["id"]] = p
        self.markers: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # --- markers -------------------------------------------------------------
    def add_marker(self, x: float, y: float, label: str = None, created_by: str = "user") -> dict:
        if not self.terrain.in_bounds(x, y):
            raise PlaceError(f"({x:.0f}, {y:.0f}) is outside the site (|x|, |y| <= {self.terrain.half:.0f} m).")
        mid = f"m{next(self._ids)}"
        lat, lon = self.terrain.latlon(x, y)
        m = {"id": mid, "x": round(x, 1), "y": round(y, 1), "z": round(float(self.terrain.elevation(x, y)), 2),
             "lat": lat, "lon": lon, "label": label or f"Marker {mid[1:]}", "created_by": created_by,
             "created_at": time.time()}
        self.markers[mid] = m
        return m

    def remove_marker(self, mid: str) -> dict:
        mid = mid.removeprefix("marker:")
        if mid not in self.markers:
            raise PlaceError(f"No marker '{mid}'. Existing: {', '.join(self.markers) or 'none'}.")
        return self.markers.pop(mid)

    # --- POI search ----------------------------------------------------------
    def search_pois(self, type_: str = None, q: str = None, near: tuple = None,
                    radius: float = None, limit: int = 20) -> list[dict]:
        out = []
        for p in self.pois.values():
            if type_ and p.get("type") != type_:
                continue
            if q:
                hay = " ".join(str(p.get(k, "")) for k in ("id", "name", "description", "type")).lower()
                if not all(w in hay for w in q.lower().split()):
                    continue
            item = dict(p)
            if near:
                item["distance_m"] = round(math.hypot(p["x"] - near[0], p["y"] - near[1]))
                if radius and item["distance_m"] > radius:
                    continue
            out.append(item)
        if near:
            out.sort(key=lambda p: p["distance_m"])
        else:
            out.sort(key=lambda p: -(p.get("science_value") or 0))
        return out[:limit]

    # --- position resolution ---------------------------------------------------
    def resolve(self, pos, rover_xy: tuple = None) -> tuple[float, float, str]:
        """Accepts {x, y}, [x, y], "x,y", "poi:<id>", "marker:<id>", "rover", or a bare POI/marker id.
        Returns (x, y, label)."""
        if pos is None:
            raise PlaceError("Position missing. Give {x, y}, 'poi:<id>', 'marker:<id>' or 'rover'.")
        if isinstance(pos, dict):
            if "x" in pos and "y" in pos:
                return float(pos["x"]), float(pos["y"]), f'({pos["x"]:.0f}, {pos["y"]:.0f})'
            if "id" in pos:
                return self.resolve(pos["id"], rover_xy)
            if "lat" in pos and "lon" in pos:
                x, y = self.terrain.xy_from_latlon(pos["lat"], pos["lon"])
                return x, y, f'({pos["lat"]}, {pos["lon"]})'
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            return float(pos[0]), float(pos[1]), f"({pos[0]:.0f}, {pos[1]:.0f})"
        if isinstance(pos, str):
            s = pos.strip()
            if "," in s:
                try:
                    a, b = (float(v) for v in s.split(","))
                    return a, b, f"({a:.0f}, {b:.0f})"
                except ValueError:
                    pass
            if s == "rover":
                if rover_xy is None:
                    raise PlaceError("Rover position unknown.")
                return rover_xy[0], rover_xy[1], "rover"
            kind, _, ident = s.partition(":") if ":" in s else ("", "", s)
            if kind in ("", "marker") and ident in self.markers:
                m = self.markers[ident]
                return m["x"], m["y"], m["label"]
            if kind in ("", "poi") and ident in self.pois:
                p = self.pois[ident]
                return p["x"], p["y"], p["name"]
            hint = self._suggest(ident)
            raise PlaceError(f"Unknown place '{s}'.{hint} Use search_pois or list_markers to find ids.")
        raise PlaceError(f"Can't read position {pos!r}. Give {{x, y}}, 'poi:<id>', 'marker:<id>' or 'rover'.")

    def _suggest(self, s: str) -> str:
        s = s.lower()
        hits = [pid for pid, p in self.pois.items() if s in p["name"].lower() or s in pid.lower()]
        hits += [mid for mid, m in self.markers.items() if s in m["label"].lower()]
        return f" Did you mean: {', '.join(hits[:5])}?" if hits else ""
