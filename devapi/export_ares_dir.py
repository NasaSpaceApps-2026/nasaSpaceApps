"""Export one of OUR site dirs (site.yaml + <layer>.tif) to the data-dir layout Yousef's API reads
(ARES_DATA_DIR: manifest.json, analysis/{dem,slope,rough,filled}.tif, analysis/layer_*.tif, data/pois.geojson).

Why: his API cannot read data/build/site/jezero directly (different layout, no manifest.json). Exporting guarantees
both backends use the SAME frame (same lat0/lon0/size/grid), which the MCP frame check verifies.

    .venv/bin/python -m devapi.export_ares_dir data/build/site/jezero OUT_DIR
    ARES_DATA_DIR=OUT_DIR api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000

Mapping (TEST ONLY, not a science product): dem <- elevation, slope <- slope_2m, rough <- rough_5x5, filled <- cells
whose quality_flag is not hirise_dtm (his planner treats them as unknown terrain). His search targets expect a 0-1
abundance layer, but our minerals are ordinal evidence tiers (detections, not abundance), so
layer_min_<target> = tier / 4 where tier -1 (no coverage) is NaN. Targets: carbonate, olivine, clay (= max of the
smectite and kaolinite layers). His find_sites scores built on them are therefore tier indices, not abundances.
Large rasters that need no change are symlinked, not copied.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import numpy as np
import rasterio
import yaml

TARGET_LAYERS = {"min_carbonate": ["carbonate"], "min_olivine": ["olivine"],
                 "min_clay": ["fe_mg_smectite", "kaolinite", "mg_smectite_2"]}


def _tiers(path: Path) -> np.ndarray:
    with rasterio.open(path) as d:
        return d.read(1).astype(np.int16)


def export(site_dir: str | Path, out_dir: str | Path) -> Path:
    """Write the ARES_DATA_DIR layout (manifest, analysis rasters, pois) from one of our site directories; returns out_dir."""
    site_dir, out = Path(site_dir), Path(out_dir)
    cfg = yaml.safe_load((site_dir / "site.yaml").read_text())
    files = {e["id"]: e for e in cfg["layers"]}
    (out / "analysis").mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)

    def link(src: Path, dst: Path) -> None:
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        os.symlink(src.resolve(), dst)

    for dst, lid in (("dem", "elevation"), ("slope", "slope_2m"), ("rough", "rough_5x5")):
        link(site_dir / files[lid]["file"], out / "analysis" / f"{dst}.tif")

    ref = site_dir / files["elevation"]["file"]
    with rasterio.open(ref) as d:
        prof = d.profile.copy()
    prof.update(dtype="float32", nodata=float("nan"), count=1, compress="deflate")

    if "quality_flag" in files:
        q = _tiers(site_dir / files["quality_flag"]["file"])
        classes = files["quality_flag"].get("classes") or {0: "hirise_dtm"}
        good = [int(k) for k, v in classes.items() if v == "hirise_dtm"]
        filled = (~np.isin(q, good)).astype(np.float32)
        with rasterio.open(out / "analysis" / "filled.tif", "w", **prof) as w:
            w.write(filled, 1)

    made = []
    for target, srcs in TARGET_LAYERS.items():
        present = [s for s in srcs if s in files]
        if not present:
            continue
        best = None
        for s in present:
            t = _tiers(site_dir / files[s]["file"]).astype(np.float32)
            t = np.where(t < 0, np.nan, np.clip(t, 0, 4) / 4.0)
            best = t if best is None else np.fmax(best, t)
        with rasterio.open(out / "analysis" / f"layer_{target}.tif", "w", **prof) as w:
            w.write(best.astype(np.float32), 1)
        made.append(f"layer_{target}")

    size_m = cfg["size_px"] * cfg["res_m"]
    manifest = {"site": cfg["name"], "fixture": False, "lat0": cfg["lat0"], "lon0": cfg["lon0"],
                "radius_m": cfg.get("radius_m", 3396190.0), "size_m": size_m, "layers": [],
                "exported_from": str(site_dir), "export_note": "devapi.export_ares_dir (test mapping, see its docstring)"}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for name in ("pois.geojson", "traverse.geojson"):
        if (site_dir / name).exists():
            shutil.copyfile(site_dir / name, out / "data" / name)
    print(f"exported {site_dir} -> {out} ({size_m:.0f} m, lat0 {cfg['lat0']}, lon0 {cfg['lon0']}); "
          f"layers: {', '.join(made) or 'none'}")
    return out


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("site_dir")
    ap.add_argument("out_dir")
    a = ap.parse_args()
    export(a.site_dir, a.out_dir)


if __name__ == "__main__":
    main()
