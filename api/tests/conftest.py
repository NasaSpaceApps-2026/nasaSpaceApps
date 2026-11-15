"""Test terrain: a flat 1,024 m site with an east-west ridge (~22 deg flanks) that has a gap
at the east end, and a 40 deg wall block. Between rover (20) and walker (25) no-go limits on
purpose, so a walking route crosses the ridge and the rover's FSD must replan through the gap."""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))
sys.path.insert(0, str(API / "scripts"))

SIZE, RES, LAT0, LON0, R = 1024.0, 2.0, 18.46, 77.41, 3396190.0
CRS = f"+proj=eqc +lat_ts={LAT0} +lat_0=0 +lon_0={LON0} +x_0=0 +y_0={-R * math.radians(LAT0):.3f} +R={R} +units=m +no_defs"
GAP_X = 300.0


def elevation(x, y):
    z = -2400.0 + 0 * x
    ridge = 9.4 * np.exp(-(y / 20.0) ** 2) / (1 + np.exp((x - GAP_X) / 10.0))
    wall = 30.0 / (1 + np.exp(-(np.maximum(np.abs(x + 300), np.abs(y - 300)) - 60) / -4.0))
    return z + ridge + wall


def latlon(x, y):
    return LAT0 + math.degrees(y / R), LON0 + math.degrees(x / (R * math.cos(math.radians(LAT0))))


def build(out: Path):
    n = int(SIZE / RES)
    xs = -SIZE / 2 + (np.arange(n) + 0.5) * RES
    ys = SIZE / 2 - (np.arange(n) + 0.5) * RES
    xx, yy = np.meshgrid(xs, ys)
    dem = elevation(xx, yy)
    gy, gx = np.gradient(dem, RES)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    pad = np.pad(dem, 2, mode="edge")
    rough = np.lib.stride_tricks.sliding_window_view(pad, (5, 5)).std(axis=(-1, -2))
    meta = dict(driver="GTiff", height=n, width=n, count=1, dtype="float32", crs=CRS,
                transform=from_origin(-SIZE / 2, SIZE / 2, RES, RES))
    (out / "analysis").mkdir(parents=True)
    for name, arr in (("dem", dem), ("slope", slope), ("rough", rough)):
        with rasterio.open(out / "analysis" / f"{name}.tif", "w", **meta) as d:
            d.write(arr.astype(np.float32), 1)
    carb = np.exp(-((np.hypot(xx + 200, yy + 300) / 40) ** 2)) * 0.9 + np.exp(-((np.hypot(xx - 200, yy + 300) / 25) ** 2)) * 0.7
    with rasterio.open(out / "analysis" / "layer_min_carbonate.tif", "w", **meta) as d:
        d.write(carb.astype(np.float32), 1)

    def poi(pid, name, typ, x, y, value=0.0, sol=None):
        lat, lon = latlon(x, y)
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"id": pid, "name": name, "type": typ, "x": x, "y": y, "z": float(elevation(x, y)),
                               "lat": lat, "lon": lon, "mission": "test", "source_url": "", "description": name,
                               "science_value": value, "sol": sol, "image_urls": []}}
    pois = [poi("base", "Base", "base", -200, -200), poi("north", "North target", "science_target", -200, 200, 0.9),
            poi("east", "East target", "science_target", 400, -100, 0.6),
            poi("wpA", "WP A", "rover_waypoint", -400, -400, 0.1, 1), poi("wpB", "WP B", "rover_waypoint", -100, -400, 0.1, 2)]
    (out / "data").mkdir()
    (out / "data" / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pois}))
    line = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [pois[3]["geometry"]["coordinates"],
                                                                                    pois[4]["geometry"]["coordinates"]]},
            "properties": {}}
    (out / "data" / "traverse.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [line, pois[3], pois[4]]}))
    (out / "manifest.json").write_text(json.dumps({"site": "test", "fixture": True, "lat0": LAT0, "lon0": LON0,
                                                   "radius_m": R, "size_m": SIZE, "layers": []}))


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("site")
    build(d)
    return d


@pytest.fixture()
def client(data_dir, tmp_path):
    from fastapi.testclient import TestClient
    from ares_api.main import create_app
    app = create_app(data_dir, extra_layer_dir=tmp_path / "none", state_file=None)
    with TestClient(app) as c:
        c.post("/rover/vehicle", json={"profile": "rover_scout"})  # existing tests drive the scout
        yield c
