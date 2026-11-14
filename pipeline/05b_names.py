"""Official IAU/USGS Mars place names near the site -> data/build/data/names.geojson (site-frame x, y included).

Source: USGS Gazetteer of Planetary Nomenclature, Mars centre points (public domain):
  https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/MARS_nomenclature_center_pts.zip
Latitude is planetocentric, longitude east-positive (per the shapefile .prj, GCS_Mars_2000).
Note: the bulk file does not list rover-era informal names (e.g. Octavia E. Butler Landing).
Run: .venv/bin/python pipeline/05b_names.py
"""
import io
import json
import math
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from site_cfg import LAT0, LON0, R  # noqa: E402

URL = "https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/MARS_nomenclature_center_pts.zip"
ROOT = Path(__file__).resolve().parent.parent
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "build" / "data"
BBOX = (16.5, 20.5, 75.5, 79.5)  # lat min/max, lon min/max

RAW.mkdir(parents=True, exist_ok=True)
zpath = RAW / "MARS_nomenclature_center_pts.zip"
if not zpath.exists():
    zpath.write_bytes(urllib.request.urlopen(URL, timeout=60).read())
z = zipfile.ZipFile(zpath)
d = z.read(next(n for n in z.namelist() if n.lower().endswith(".dbf")))
n, hl, rl = struct.unpack("<xxxxIHH", d[:12])
fields, o = [], 32
while d[o] != 0x0D:
    fields.append((d[o:o + 11].split(b"\0")[0].decode(), d[o + 16]))
    o += 32
feats = []
for i in range(n):
    r, p, rec = d[hl + i * rl: hl + (i + 1) * rl], 1, {}
    for name, ln in fields:
        rec[name] = r[p:p + ln].decode("latin1").strip()
        p += ln
    try:
        lat, lon = float(rec["center_lat"]), float(rec["center_lon"])
    except ValueError:
        continue
    if not (BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]):
        continue
    x, y = R * math.cos(math.radians(LAT0)) * math.radians(lon - LON0), R * math.radians(lat - LAT0)
    feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
        "id": "iau_" + rec["clean_name"].lower().replace(" ", "_"), "name": rec["clean_name"],
        "type": "landmark", "feature_type": rec["type"], "x": round(x, 1), "y": round(y, 1),
        "lat": lat, "lon": lon, "diameter_km": float(rec["diameter"] or 0), "mission": "IAU/USGS Gazetteer",
        "source_url": rec.get("link") or "https://planetarynames.wr.usgs.gov/", "description": rec["origin"],
        "approved": rec["approvaldt"][:10]}})
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "names.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
print(len(feats), "official names:", ", ".join(f["properties"]["name"] for f in feats))
