"""Terrain service: analysis rasters on the contract 3 grid, sampled in site-frame metres."""
import json
import math
from pathlib import Path

import numpy as np
import rasterio

from . import config

R_MARS = 3396190.0
CORE = ("dem", "slope", "rough")


class Terrain:
    def __init__(self, data_dir: Path = None, extra_layer_dir: Path = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self.manifest = json.loads((self.data_dir / "manifest.json").read_text())
        self.lat0 = float(self.manifest["lat0"])
        self.lon0 = float(self.manifest["lon0"])
        self.radius = float(self.manifest.get("radius_m", R_MARS))

        analysis = self.data_dir / "analysis"
        self.grids: dict[str, np.ndarray] = {}
        with rasterio.open(analysis / "dem.tif") as d:
            t = d.transform
            self.res = float(t.a)
            self.west, self.north = float(t.c), float(t.f)
            self.shape = d.shape
        self.size = self.res * self.shape[1]
        for name in CORE:
            self.grids[name] = self._read(analysis / f"{name}.tif")
        filled = analysis / "filled.tif"
        self.filled = self._read(filled) > 0.5 if filled.exists() else np.zeros(self.shape, bool)
        # Cells with no elevation are unknown terrain: count them as filled (planner cost, hazard "unknown").
        self.filled |= np.isnan(self.grids["dem"]) | np.isnan(self.grids["slope"])

        # Science layers: layer_<id>.tif from the data dir, then extras (data dir wins).
        self.layers: dict[str, np.ndarray] = {}
        self.layer_source: dict[str, str] = {}
        dirs = [(analysis, "data")]
        extra = Path(extra_layer_dir or config.EXTRA_LAYER_DIR)
        if extra.exists():
            dirs.append((extra, "synthetic"))
        for d, src in dirs:
            for p in sorted(d.glob("layer_*.tif")):
                lid = p.stem[len("layer_"):]
                if lid in self.layers:
                    continue
                arr = self._read(p)
                if arr.shape != self.shape:
                    continue
                self.layers[lid] = arr
                # Anything in a fixture site is synthetic, wherever the file sits.
                self.layer_source[lid] = "synthetic" if self.manifest.get("fixture") else src

    def _read(self, path: Path) -> np.ndarray:
        with rasterio.open(path) as d:
            a = d.read(1).astype(np.float32)
            if d.nodata is not None and not np.isnan(d.nodata):
                a[a == d.nodata] = np.nan
            a[~np.isfinite(a) | (a < -1e30)] = np.nan  # untagged float nodata (e.g. -3.4e38)
        return a

    # --- coordinates -------------------------------------------------------
    @property
    def half(self) -> float:
        return self.size / 2

    def in_bounds(self, x: float, y: float) -> bool:
        return abs(x) <= self.half and abs(y) <= self.half

    def latlon(self, x: float, y: float) -> tuple[float, float]:
        lat = self.lat0 + math.degrees(y / self.radius)
        lon = self.lon0 + math.degrees(x / (self.radius * math.cos(math.radians(self.lat0))))
        return round(lat, 6), round(lon, 6)

    def xy_from_latlon(self, lat: float, lon: float) -> tuple[float, float]:
        x = self.radius * math.cos(math.radians(self.lat0)) * math.radians(lon - self.lon0)
        y = self.radius * math.radians(lat - self.lat0)
        return x, y

    def to_cell(self, x, y, res: float = None):
        """Fractional (row, col) of pixel centres on a grid of the given resolution."""
        res = res or self.res
        col = (np.asarray(x) - self.west) / res - 0.5
        row = (self.north - np.asarray(y)) / res - 0.5
        return row, col

    def cell_xy(self, row, col, res: float = None):
        res = res or self.res
        return self.west + (np.asarray(col) + 0.5) * res, self.north - (np.asarray(row) + 0.5) * res

    # --- sampling ------------------------------------------------------------
    def bilinear(self, grid: np.ndarray, x, y):
        row, col = self.to_cell(x, y)
        h, w = grid.shape
        row = np.clip(row, 0, h - 1)
        col = np.clip(col, 0, w - 1)
        r0 = np.clip(np.floor(row).astype(int), 0, h - 2)
        c0 = np.clip(np.floor(col).astype(int), 0, w - 2)
        fr, fc = row - r0, col - c0
        return (grid[r0, c0] * (1 - fr) * (1 - fc) + grid[r0, c0 + 1] * (1 - fr) * fc
                + grid[r0 + 1, c0] * fr * (1 - fc) + grid[r0 + 1, c0 + 1] * fr * fc)

    def nearest(self, grid: np.ndarray, x, y):
        row, col = self.to_cell(x, y)
        h, w = grid.shape
        r = np.clip(np.rint(row).astype(int), 0, h - 1)
        c = np.clip(np.rint(col).astype(int), 0, w - 1)
        return grid[r, c]

    def elevation(self, x, y):
        return self.bilinear(self.grids["dem"], x, y)

    def slope(self, x, y):
        return self.bilinear(self.grids["slope"], x, y)

    def hazard_class(self, slope_deg: float, filled: bool, profile: dict) -> str:
        if filled:
            return "unknown"
        if slope_deg >= profile["nogo_slope_deg"]:
            return "no-go"
        if slope_deg >= profile["caution_slope_deg"]:
            return "caution"
        return "safe"

    def tilt(self, x: float, y: float, heading_deg: float) -> tuple[float, float]:
        """Pitch (nose up +) and roll (right side down +) in degrees from the DEM gradient."""
        d = 2.0
        gx = (self.elevation(x + d, y) - self.elevation(x - d, y)) / (2 * d)
        gy = (self.elevation(x, y + d) - self.elevation(x, y - d)) / (2 * d)
        h = math.radians(heading_deg)
        fwd = (math.sin(h), math.cos(h))
        right = (math.cos(h), -math.sin(h))
        pitch = math.degrees(math.atan(gx * fwd[0] + gy * fwd[1]))
        roll = -math.degrees(math.atan(gx * right[0] + gy * right[1]))
        return round(pitch, 1), round(roll, 1)

    def layer_value(self, lid: str, x: float, y: float):
        grid = self.layers[lid]
        if lid == "geology" or np.allclose(grid[::17, ::17], np.round(grid[::17, ::17])):
            v = self.nearest(grid, x, y)  # class rasters: never interpolate
        else:
            v = self.bilinear(grid, x, y)
        v = float(v)
        return None if math.isnan(v) else round(v, 3)

    def sample(self, x: float, y: float, profile: dict) -> dict:
        elev = float(self.elevation(x, y))
        slope = float(self.slope(x, y))
        rough = float(self.bilinear(self.grids["rough"], x, y))
        num = lambda v, nd: round(v, nd) if math.isfinite(v) else None  # noqa: E731 (NaN is not JSON)
        filled = bool(self.nearest(self.filled.astype(np.uint8), x, y))
        lat, lon = self.latlon(x, y)
        return {
            "x": round(x), "y": round(y), "lat": lat, "lon": lon,
            "elevation_m": num(elev, 2),
            "slope_deg": num(slope, 1),
            "roughness_m": num(rough, 2),
            "hazard": self.hazard_class(slope, filled or not math.isfinite(slope), profile),
            "filled": filled,
            "layers": {lid: self.layer_value(lid, x, y) for lid in self.layers},
            "sources": {"elevation": self.source_label("dem"), "slope": self.source_label("slope"),
                        "roughness": self.source_label("rough")},
        }

    def _dtm_citation(self) -> str:
        """Cite the elevation source from the manifest (mission, instrument, DOI) when the build says so."""
        for layer in self.manifest.get("layers", []):
            if "DTM" in str(layer.get("instrument", "")):
                url = str(layer.get("url") or "")
                doi = url.split("doi.org/")[-1] if "doi.org/" in url else url
                cite = f'{layer.get("mission", "MRO")} {layer["instrument"]}'
                return f"{cite} (USGS, doi:{doi})" if "doi.org/" in url else (f"{cite} ({url})" if url else cite)
        return "MRO HiRISE DTM"

    def source_label(self, lid: str) -> str:
        """Mission/instrument string for citing a layer."""
        if self.manifest.get("fixture"):
            base = "synthetic fixture"
        else:
            base = self._dtm_citation()
        if lid == "dem":
            return base
        if lid in ("slope", "rough"):
            return base if self.manifest.get("fixture") else f"derived from {base}, {self.res:g} m grid"
        for layer in self.manifest.get("layers", []):
            if layer["id"] == lid:
                return f'{layer.get("mission")} {layer.get("instrument")}'
        if self.layer_source.get(lid) == "synthetic":
            return "synthetic placeholder (not Mars data)"
        return base
