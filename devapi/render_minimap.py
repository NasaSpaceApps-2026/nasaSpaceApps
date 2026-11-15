"""Render a hillshade PNG + a small JSON (extent, POIs, traverse) of one of OUR site dirs for the chat-dev minimap.

    .venv/bin/python -m devapi.render_minimap data/build/site/jezero chat-dev/public/minimap [--px 900]

Writes <out>/current.png and <out>/current.json. The image is north-up and covers the whole site square, so pixel
(col, row) <-> site metres x = -half + (col+0.5)*m_per_px, y = half - (row+0.5)*m_per_px. Hillshade is a standard
315 deg / 45 deg Lambertian shade of the elevation layer (display only, vertical exaggeration x2), tinted by height.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
import yaml
from PIL import Image


def _block_mean(a: np.ndarray, k: int) -> np.ndarray:
    n = a.shape[0] // k * k
    a = a[:n, :n].reshape(n // k, k, n // k, k)
    with np.errstate(all="ignore"):
        return np.nanmean(a, axis=(1, 3))


def render(site_dir: str | Path, out_dir: str | Path, px: int = 900) -> dict:
    site_dir, out = Path(site_dir), Path(out_dir)
    cfg = yaml.safe_load((site_dir / "site.yaml").read_text())
    res, n = float(cfg["res_m"]), int(cfg["size_px"])
    elev_file = next(e["file"] for e in cfg["layers"] if e["id"] == "elevation")
    with rasterio.open(site_dir / elev_file) as d:
        z = d.read(1).astype(np.float64)
    z[~np.isfinite(z) | (z < -1e30)] = np.nan
    k = min((d for d in range(1, n + 1) if n % d == 0), key=lambda d: abs(n / d - px))  # divisor: exact extent
    zs = _block_mean(z, k)
    cell = res * k
    fill = np.where(np.isnan(zs), np.nanmean(zs), zs)
    gy, gx = np.gradient(fill * 2.0, cell)  # rows run south, so gy is d/d(south)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)  # downslope direction measured from north, clockwise (rows south => gy sign)
    az, alt = np.radians(315), np.radians(45)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip((shade - shade.min()) / max(np.ptp(shade), 1e-9), 0, 1)
    shade = np.clip((shade - 0.25) / 0.6, 0, 1) ** 0.9
    lo, hi = np.nanpercentile(zs, 2), np.nanpercentile(zs, 98)
    t = np.clip((zs - lo) / max(hi - lo, 1e-9), 0, 1)[..., None]
    low, high = np.array([92, 70, 58.0]), np.array([232, 190, 150.0])
    rgb = (low * (1 - t) + high * t) * (0.35 + 0.75 * shade[..., None])
    rgb[np.isnan(zs)] = (28, 22, 20)
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(out / "current.png", optimize=True)

    pois = []
    if (site_dir / "pois.geojson").exists():
        for f in json.loads((site_dir / "pois.geojson").read_text())["features"]:
            p = f["properties"]
            if "x" in p and "y" in p:
                pois.append({"id": p.get("id"), "name": p.get("name"), "type": p.get("type"),
                             "x": p["x"], "y": p["y"]})
    traverse = []
    if (site_dir / "traverse.geojson").exists():
        for f in json.loads((site_dir / "traverse.geojson").read_text())["features"]:
            p = f["properties"]
            if isinstance(p.get("x"), list):
                traverse.append([[round(a, 1), round(b, 1)] for a, b in zip(p["x"], p["y"])][::3])
    info = {"name": cfg["name"], "lat0": cfg["lat0"], "lon0": cfg["lon0"], "size_m": n * res,
            "px": zs.shape[0], "elev_min_m": float(np.nanmin(zs)), "elev_max_m": float(np.nanmax(zs)),
            "source": "elevation layer of the site dir (HiRISE DTM / CTX fill); hillshade for display only",
            "pois": pois, "traverse": traverse}
    (out / "current.json").write_text(json.dumps(info))
    print(f"minimap {info['name']}: {info['px']}px for {info['size_m']:.0f} m, {len(pois)} pois -> {out}")
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("site_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--px", type=int, default=900)
    a = ap.parse_args()
    render(a.site_dir, a.out_dir, a.px)


if __name__ == "__main__":
    main()
