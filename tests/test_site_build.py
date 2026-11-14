"""Checks on the built Jezero site (data/build/site/jezero). Skipped if not built."""
import math
import os
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "data/build/site/jezero"
pytestmark = pytest.mark.skipif(not (SITE_DIR / "site.yaml").exists(), reason="site not built")

from atlas_core import load_site  # noqa: E402

REQUIRED = ["id", "kind", "title", "units", "resolution_m", "measures", "does_not_measure",
            "quality", "coverage", "source"]


@pytest.fixture(scope="module")
def site():
    return load_site(SITE_DIR)


def test_shapes_and_layers(site):
    assert site.size_px == 4096 and site.res_m == 2.0
    for lid in ("elevation", "slope_2m", "rough_5x5", "quality_flag"):
        assert site.layers[lid].data.shape == (4096, 4096)


def test_catalogue_complete(site):
    for lid, lay in site.layers.items():
        for k in REQUIRED:
            assert k in lay.meta, (lid, k)
        for k in ("mission", "instrument", "product", "citation", "url"):
            assert lay.meta["source"].get(k), (lid, k)
    assert set(site.layers["quality_flag"].classes) == {0, 1, 2}


def test_profiles(site):
    assert {"landing_pad", "eva_walkable", "rover_scout"} <= set(site.profiles)
    conds = site.profiles["landing_pad"]["conditions"]
    q = [c for c in conds if c["layer"] == "quality_flag"][0]
    assert q["value"] == ["hirise_dtm"]
    for c in conds:
        assert c.get("source")


def test_landing_poi(site):
    poi = {p["id"]: p for p in site.pois}
    for pid in ("octavia_e_butler_landing", "base"):
        assert pid in poi
    assert "assumed" in poi["base"]["description"].lower()
    p = poi["octavia_e_butler_landing"]
    assert site.in_bounds(p["x"], p["y"])
    r, c = site.rc_index(p["x"], p["y"])
    assert site.layers["quality_flag"].data[r, c] == 0       # on clean HiRISE data


def test_slope_matches_manual_horn(site):
    z = site.layers["elevation"].data.astype(np.float64)
    s = site.layers["slope_2m"].data
    rng = np.random.default_rng(1)
    n = 0
    while n < 5:
        r, c = rng.integers(5, 4091, 2)
        w = z[r - 1:r + 2, c - 1:c + 2]
        if not np.isfinite(w).all():
            continue
        dzdx = ((w[0, 2] + 2 * w[1, 2] + w[2, 2]) - (w[0, 0] + 2 * w[1, 0] + w[2, 0])) / (8 * 2.0)
        dzdy = ((w[2, 0] + 2 * w[2, 1] + w[2, 2]) - (w[0, 0] + 2 * w[0, 1] + w[0, 2])) / (8 * 2.0)
        assert s[r, c] == pytest.approx(math.degrees(math.atan(math.hypot(dzdx, dzdy))), abs=1e-3)
        n += 1


def test_elevation_matches_source_bilinear(site):
    import rasterio
    from rasterio.windows import Window
    wins = list((ROOT / "data/raw/hirise").glob("window_*.tif"))
    assert wins, "cached HiRISE window missing"
    p = {p["id"]: p for p in site.pois}["octavia_e_butler_landing"]
    R = site.radius_m
    sx, sy = R * math.radians(p["lon"]), R * math.radians(p["lat"])      # source CRS (eqc lat_ts=0)
    with rasterio.open(wins[0]) as d:
        col = (sx - d.transform.c) / d.transform.a - 0.5
        row = (sy - d.transform.f) / d.transform.e - 0.5
        r0, c0 = int(math.floor(row)), int(math.floor(col))
        a = d.read(1, window=Window(c0, r0, 2, 2)).astype(float)
    fr, fc = row - r0, col - c0
    v = a[0, 0]*(1-fr)*(1-fc) + a[0, 1]*(1-fr)*fc + a[1, 0]*fr*(1-fc) + a[1, 1]*fr*fc
    r, c = site.xy_to_rc(p["x"], p["y"])
    from atlas_core.terrain import bilinear
    z = bilinear(site.layers["elevation"].data, float(r), float(c))
    assert abs(z - v) < 1.0, (z, v)
