"""Crop one of OUR site dirs to a smaller square in a re-centred frame (pure array slicing, no resampling).

Why: the teammates' rover sandbox worlds are small squares centred on their own origin (world `crater` = 1800 m,
centred at site metres center_x/center_y of contracts/fixtures/rover_worlds/crater/manifest.json). The sandbox draws
the API rover pose with no frame transform, so API + query engine + sandbox only agree if the API's frame IS the world's
frame. This writes a copy of our real site whose origin is the world centre, so devapi and (via export_ares_dir) the
API can run in that frame without touching any teammate file.

    .venv/bin/python -m devapi.crop_site data/build/site/jezero OUT_DIR --world crater
    .venv/bin/python -m devapi.crop_site data/build/site/jezero OUT_DIR --center 1000.85 1766.39 --size 1800

Cells are not resampled, so the crop centre snaps to the 2 m grid (<= 1 m from the requested centre; reported).
pois and the traverse are shifted into the new frame and dropped when outside it.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin

REPO = Path(__file__).resolve().parents[1]
WORLDS = REPO / "contracts" / "fixtures" / "rover_worlds"


def world_center(name: str) -> tuple[float, float, float]:
    m = json.loads((WORLDS / name / "manifest.json").read_text())
    return float(m["center_x"]), float(m["center_y"]), float(m["size_m"])


def crop(site_dir: str | Path, out_dir: str | Path, cx: float, cy: float, size_m: float) -> dict:
    site_dir, out = Path(site_dir), Path(out_dir)
    cfg = yaml.safe_load((site_dir / "site.yaml").read_text())
    res, n = float(cfg["res_m"]), int(cfg["size_px"])
    half = n * res / 2
    k = int(round(size_m / res))
    col0 = int(round((cx - k * res / 2 + half) / res))
    row0 = int(round((half - (cy + k * res / 2)) / res))
    if col0 < 0 or row0 < 0 or col0 + k > n or row0 + k > n:
        raise SystemExit(f"crop {size_m:.0f} m around ({cx:.1f}, {cy:.1f}) does not fit inside the {n * res:.0f} m site")
    ncx, ncy = -half + col0 * res + k * res / 2, half - row0 * res - k * res / 2  # snapped centre, old frame
    R = float(cfg.get("radius_m", 3396190.0))
    lat0 = cfg["lat0"] + math.degrees(ncy / R)
    lon0 = cfg["lon0"] + math.degrees(ncx / (R * math.cos(math.radians(cfg["lat0"]))))
    out.mkdir(parents=True, exist_ok=True)
    new_half = k * res / 2
    for e in cfg["layers"]:
        with rasterio.open(site_dir / e["file"]) as d:
            a = d.read(1)[row0:row0 + k, col0:col0 + k]
            prof = d.profile.copy()
        prof.update(width=k, height=k, transform=from_origin(-new_half, new_half, res, res), crs=None)
        with rasterio.open(out / e["file"], "w", **prof) as w:
            w.write(a, 1)
    new = dict(cfg, name=f"{cfg['name']}_crop{int(size_m)}", lat0=round(lat0, 8), lon0=round(lon0, 8), size_px=k)
    new.pop("crs", None)
    (out / "site.yaml").write_text(yaml.safe_dump(new, sort_keys=False))
    if (site_dir / "profiles.yaml").exists():
        (out / "profiles.yaml").write_text((site_dir / "profiles.yaml").read_text())

    def shift(x, y):
        return x - ncx, y - ncy

    def inside(x, y):
        return abs(x) <= new_half and abs(y) <= new_half

    if (site_dir / "pois.geojson").exists():
        fc = json.loads((site_dir / "pois.geojson").read_text())
        feats = []
        for f in fc["features"]:
            p = f["properties"]
            if "x" in p and "y" in p:
                x, y = shift(p["x"], p["y"])
                if inside(x, y):
                    f["properties"] = dict(p, x=round(x, 2), y=round(y, 2))
                    feats.append(f)
        (out / "pois.geojson").write_text(json.dumps(dict(fc, features=feats), indent=1))
    if (site_dir / "traverse.geojson").exists():
        fc = json.loads((site_dir / "traverse.geojson").read_text())
        feats = []
        for f in fc["features"]:
            p = f["properties"]
            if isinstance(p.get("x"), list):
                pts = [shift(x, y) for x, y in zip(p["x"], p["y"])]
                if any(inside(x, y) for x, y in pts):
                    f["properties"] = dict(p, x=[round(x, 1) for x, _ in pts], y=[round(y, 1) for _, y in pts])
                    feats.append(f)
        (out / "traverse.geojson").write_text(json.dumps(dict(fc, features=feats)))
    info = {"out": str(out), "size_m": k * res, "snapped_centre_old_frame": [ncx, ncy], "requested": [cx, cy],
            "snap_error_m": math.hypot(ncx - cx, ncy - cy), "lat0": new["lat0"], "lon0": new["lon0"]}
    print(json.dumps(info))
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("site_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--world", help="a contracts/fixtures/rover_worlds/<name> whose centre and size to use")
    ap.add_argument("--center", nargs=2, type=float, metavar=("X", "Y"))
    ap.add_argument("--size", type=float)
    a = ap.parse_args()
    if a.world:
        cx, cy, size = world_center(a.world)
    elif a.center and a.size:
        (cx, cy), size = a.center, a.size
    else:
        ap.error("give --world NAME or --center X Y --size M")
    crop(a.site_dir, a.out_dir, cx, cy, a.size or size)


if __name__ == "__main__":
    main()
