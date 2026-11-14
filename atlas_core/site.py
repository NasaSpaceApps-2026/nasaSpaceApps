"""Site data model and on-disk format. FROZEN INTERFACE: change only with Stream D agreement.

On-disk site directory (written by pipeline/, read by load_site):

    <site_dir>/
      site.yaml         name, lat0, lon0, radius_m, res_m, size_px, layers: [catalogue entries + file]
      profiles.yaml     profiles per contracts/mcp-tools.md section 3.4
      pois.geojson      optional; FeatureCollection, properties per PRD contract 3
      <layer>.tif       one single-band GeoTIFF per raster layer, size_px x size_px

Grid convention (site frame, PRD contract 1):
    - Square grid of size_px x size_px cells, res_m metres per cell, centred on (lat0, lon0).
    - Row 0 is the NORTH edge, column 0 is the WEST edge.
    - Centre of cell (r, c):  x = -half + (c + 0.5) * res,  y = half - (r + 0.5) * res,
      where half = size_px * res / 2.

Raster encodings in memory:
    - continuous:   float32, NaN = unknown / no data.
    - categorical:  int16, class codes >= 0, -1 = unknown. Classes in Layer.classes {code: name}.
    - evidence:     int8 tier 0..4 (see TIERS), -1 = no_coverage.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

MARS_RADIUS_M = 3396190.0

TIERS = {
    "not_detected": 0,
    "inferred_from_unit": 1,
    "weak_orbital": 2,
    "strong_orbital": 3,
    "confirmed_in_situ": 4,
}
TIER_NAMES = {v: k for k, v in TIERS.items()}
NO_COVERAGE = -1

LAYER_KINDS = ("continuous", "categorical", "evidence")


@dataclass
class Layer:
    id: str
    kind: str                       # continuous | categorical | evidence
    data: np.ndarray                # see module docstring for encodings
    meta: dict                      # catalogue entry: title, units, resolution_m, measures,
                                    # does_not_measure, quality, coverage, source{...}
    classes: dict[int, str] | None = None   # categorical only

    @property
    def unknown(self) -> np.ndarray:
        """Boolean mask of cells with no usable data."""
        if self.kind == "continuous":
            return ~np.isfinite(self.data)
        return self.data < 0


@dataclass
class Site:
    name: str
    lat0: float
    lon0: float
    res_m: float
    size_px: int
    layers: dict[str, Layer]
    profiles: dict = field(default_factory=dict)
    pois: list[dict] = field(default_factory=list)    # each: id, name, type, x, y, ... (PRD contract 3)
    radius_m: float = MARS_RADIUS_M
    rover_xy: tuple[float, float] | None = None       # set by the server from rover state, if known
    markers: dict = field(default_factory=dict)       # id -> {x, y, label, ...}; owned by the API, read by resolve_ref

    # ---- geometry -------------------------------------------------------
    @property
    def half_m(self) -> float:
        return self.size_px * self.res_m / 2.0

    def xy_to_rc(self, x, y):
        """Site-frame metres -> fractional (row, col) where integer values are cell CENTRES.
        Use this for bilinear interpolation. Use rc_index() for the containing cell."""
        col = (np.asarray(x, dtype=float) + self.half_m) / self.res_m - 0.5
        row = (self.half_m - np.asarray(y, dtype=float)) / self.res_m - 0.5
        return row, col

    def rc_index(self, x, y):
        """Containing cell (row, col) as ints for site-frame metres; may be out of bounds."""
        row, col = self.xy_to_rc(x, y)
        return np.floor(row + 0.5).astype(int), np.floor(col + 0.5).astype(int)

    def rc_to_xy(self, row, col):
        """Cell (row, col) -> site-frame metres of the cell centre."""
        x = -self.half_m + (np.asarray(col, dtype=float) + 0.5) * self.res_m
        y = self.half_m - (np.asarray(row, dtype=float) + 0.5) * self.res_m
        return x, y

    def in_bounds(self, x, y) -> bool:
        return abs(float(x)) <= self.half_m and abs(float(y)) <= self.half_m

    def xy_to_latlon(self, x, y):
        lat = self.lat0 + math.degrees(float(y) / self.radius_m)
        lon = self.lon0 + math.degrees(float(x) / (self.radius_m * math.cos(math.radians(self.lat0))))
        return lat, lon

    def latlon_to_xy(self, lat, lon):
        x = self.radius_m * math.cos(math.radians(self.lat0)) * math.radians(float(lon) - self.lon0)
        y = self.radius_m * math.radians(float(lat) - self.lat0)
        return x, y

    def layer(self, layer_id: str) -> Layer:
        from .errors import AtlasError
        if layer_id not in self.layers:
            raise AtlasError(
                f'Unknown layer "{layer_id}".',
                hint="Available layers: " + ", ".join(sorted(self.layers)) + ". Call list_layers().",
            )
        return self.layers[layer_id]


# ---- loading -------------------------------------------------------------

def _read_raster(path: Path, kind: str) -> np.ndarray:
    import rasterio
    with rasterio.open(path) as d:
        arr = d.read(1)
        nodata = d.nodata
    if kind == "continuous":
        arr = arr.astype(np.float32)
        if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
            arr[arr == nodata] = np.nan
        return arr
    if kind == "categorical":
        return arr.astype(np.int16)
    return arr.astype(np.int8)


def load_site(site_dir: str | Path) -> Site:
    site_dir = Path(site_dir)
    cfg = yaml.safe_load((site_dir / "site.yaml").read_text())
    layers: dict[str, Layer] = {}
    for entry in cfg["layers"]:
        entry = dict(entry)
        kind = entry["kind"]
        data = _read_raster(site_dir / entry.pop("file"), kind)
        classes = entry.pop("classes", None)
        if classes is not None:
            classes = {int(k): str(v) for k, v in classes.items()}
        layers[entry["id"]] = Layer(id=entry["id"], kind=kind, data=data, meta=entry, classes=classes)
        if data.shape != (cfg["size_px"], cfg["size_px"]):
            raise ValueError(f"layer {entry['id']} has shape {data.shape}, expected size_px={cfg['size_px']}")
    profiles = {}
    if (site_dir / "profiles.yaml").exists():
        profiles = yaml.safe_load((site_dir / "profiles.yaml").read_text()) or {}
    pois = []
    if (site_dir / "pois.geojson").exists():
        fc = json.loads((site_dir / "pois.geojson").read_text())
        pois = [dict(f["properties"]) for f in fc.get("features", [])]
    return Site(
        name=cfg["name"], lat0=cfg["lat0"], lon0=cfg["lon0"], res_m=cfg["res_m"],
        size_px=cfg["size_px"], layers=layers, profiles=profiles, pois=pois,
        radius_m=cfg.get("radius_m", MARS_RADIUS_M),
    )
