"""Stream A step 5: real Perseverance traverse and waypoints -> contract-3 GeoJSON in the site frame.

Sources (NASA/JPL, cached in data/raw by hand or by the curl lines in data/SOURCES.md):
  https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_traverse.json   (LineString per drive)
  https://mars.nasa.gov/mmgis-maps/M20/Layers/json/M20_waypoints.json  (Point per localisation)
Writes data/build/data/pois.geojson and traverse.geojson. Only features near the 8 km site are kept.
Run: .venv/bin/python pipeline/05_vectors.py
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).parent))
from site_cfg import LAT0, LON0, R, SIZE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "build" / "data"
MARGIN = 300.0
IMG_BASE = "https://mars.nasa.gov/mmgis-maps/M20/"
SRC_URL = "https://mars.nasa.gov/mmgis-maps/M20/Layers/json/"


def to_xy(lon, lat):
    return (R * math.cos(math.radians(LAT0)) * math.radians(lon - LON0), R * math.radians(lat - LAT0))


with rasterio.open(ROOT / "data" / "build" / "analysis" / "dem.tif") as d:
    dem, res = d.read(1), d.res[0]
half = dem.shape[0] * res / 2


def dem_z(x, y):
    c, r = int((x + half) / res), int((half - y) / res)
    return float(dem[r, c]) if 0 <= r < dem.shape[0] and 0 <= c < dem.shape[1] else None


def inside(x, y):
    return abs(x) <= SIZE / 2 + MARGIN and abs(y) <= SIZE / 2 + MARGIN


wp = json.loads((RAW / "m20_waypoints.json").read_text())["features"]
tv = json.loads((RAW / "m20_traverse.json").read_text())["features"]

pois = []
for f in wp:
    p, (lon, lat, *_) = f["properties"], f["geometry"]["coordinates"]
    x, y = to_xy(lon, lat)
    if not inside(x, y):
        continue
    imgs = [IMG_BASE + i["url"] for i in p.get("images", []) if i.get("url")]
    pois.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
        "id": f"m20_{p['RMC']}", "name": f"Perseverance sol {p['sol']} (RMC {p['RMC']})", "type": "rover_waypoint",
        "x": round(x, 2), "y": round(y, 2), "z": dem_z(x, y), "lat": lat, "lon": lon, "mission": "Mars 2020",
        "source_url": SRC_URL + "M20_waypoints.json", "description": p.get("Note") or "Rover localisation",
        "science_value": 0.0, "sol": p["sol"], "image_urls": imgs,
        "rover_elev_m": p.get("elev_geoid"), "rover_pitch_deg": p.get("pitch"), "rover_roll_deg": p.get("roll"),
        "rover_yaw_deg": p.get("yaw"), "dist_total_m": p.get("dist_total_m")}})

segs = []
for f in tv:
    g = f["geometry"]
    lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    for line in lines:
        xy = [to_xy(c[0], c[1]) for c in line]
        if any(inside(x, y) for x, y in xy):
            segs.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [c[:2] for c in line]},
                         "properties": {"kind": "traverse", "sol": f["properties"].get("sol"),
                                        "length_m": f["properties"].get("length"),
                                        "xy": [[round(x, 2), round(y, 2)] for x, y in xy]}})

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": pois}))
(OUT / "traverse.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": segs}))
sols = sorted(p["properties"]["sol"] for p in pois)
print(f"{len(pois)} waypoints in site (sols {sols[0]}..{sols[-1]}), {len(segs)} traverse segments")
xs = [p["properties"]["x"] for p in pois]; ys = [p["properties"]["y"] for p in pois]
print(f"waypoint extent x {min(xs):.0f}..{max(xs):.0f}  y {min(ys):.0f}..{max(ys):.0f}")
