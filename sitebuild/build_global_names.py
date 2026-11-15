"""Global Mars place-name list (for fuzzy 'Mount Sons' -> Olympus Mons) -> atlas_core/data/mars_global_names.json.

Source: USGS Gazetteer of Planetary Nomenclature, Mars centre points (IAU-approved names; public domain):
  https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/MARS_nomenclature_center_pts.zip
Latitude is planetocentric, longitude east-positive (GCS_Mars_2000 sphere, R = 3396190 m, the same sphere as our site frame).
Kept: every Mons/Tholus/Patera/Planitia/Planum/Chasma/Vallis/Fossa/Terra/Mensa with diameter >= 100 km, craters >= 150 km,
and any feature of the extra names below. Each record keeps the gazetteer link as its source.
Run: .venv/bin/python sitebuild/build_global_names.py   (downloads into data/raw once)
"""
import json
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/MARS_nomenclature_center_pts.zip"
ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT / "data" / "raw" / "MARS_nomenclature_center_pts.zip"
OUT = ROOT / "atlas_core" / "data" / "mars_global_names.json"
BIG_TYPES = ("Mons", "Tholus", "Patera", "Planitia", "Planum", "Chasma", "Vallis", "Fossa", "Terra", "Mensa", "Labyrinthus")
ALWAYS = {"Jezero", "Gale", "Olympus Mons", "Aeolis Mons", "Nili Fossae", "Isidis Planitia", "Eberswalde", "Holden", "Columbia Hills"}


def main() -> None:
    ZIP.parent.mkdir(parents=True, exist_ok=True)
    if not ZIP.exists():
        ZIP.write_bytes(urllib.request.urlopen(URL, timeout=90).read())
    z = zipfile.ZipFile(ZIP)
    d = z.read(next(n for n in z.namelist() if n.lower().endswith(".dbf")))
    n, hl, rl = struct.unpack("<xxxxIHH", d[:12])
    fields, o = [], 32
    while d[o] != 0x0D:
        fields.append((d[o:o + 11].split(b"\0")[0].decode(), d[o + 16]))
        o += 32
    out = []
    for i in range(n):
        r, p, rec = d[hl + i * rl: hl + (i + 1) * rl], 1, {}
        for name, ln in fields:
            rec[name] = r[p:p + ln].decode("latin1").strip()
            p += ln
        try:
            lat, lon, diam = float(rec["center_lat"]), float(rec["center_lon"]), float(rec["diameter"] or 0)
        except ValueError:
            continue
        typ = rec["type"].split(",")[0]
        name = rec["clean_name"]
        keep = name in ALWAYS or (typ in BIG_TYPES and diam >= 100) or (typ == "Crater" and diam >= 150)
        if keep:
            out.append({"name": name, "feature_type": rec["type"], "lat": round(lat, 4), "lon": round(lon % 360, 4),
                        "diameter_km": diam, "source_url": rec.get("link") or "https://planetarynames.wr.usgs.gov/",
                        "approved": rec["approvaldt"][:10]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "USGS Gazetteer of Planetary Nomenclature, Mars centre points (IAU)",
                               "url": URL, "radius_m": 3396190.0, "names": out}, indent=0))
    print(len(out), "names ->", OUT.relative_to(ROOT))


if __name__ == "__main__":
    sys.exit(main())
