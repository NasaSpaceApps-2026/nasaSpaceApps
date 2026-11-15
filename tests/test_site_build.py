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
    assert set(site.layers["quality_flag"].classes) == {0, 1, 2, 3}
    assert site.layers["quality_flag"].classes[3] == "hirise_noisy"
    assert "hirise_noisy" in site.layers["quality_flag"].meta["quality"]


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
    import sys
    sys.path.insert(0, str(ROOT / "pipeline"))
    import site_cfg
    wins = list((ROOT / "data/raw/hirise").glob(f"window_{site_cfg.LAT0}_{site_cfg.LON0}_*.tif"))
    assert wins, "cached HiRISE window for site_cfg centre missing"
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


def test_site_frame_is_site_cfg(site):
    import sys
    sys.path.insert(0, str(ROOT / "pipeline"))
    sys.path.insert(0, str(ROOT / "sitebuild"))
    import site_cfg, site_frame
    assert site.lat0 == site_cfg.LAT0 and site.lon0 == site_cfg.LON0
    assert site.size_px * site.res_m == site_cfg.SIZE
    assert site_frame.verify_frame(0.01) < 0.01          # < 1 cm vs independent frame definition
    import rasterio
    from rasterio.crs import CRS
    with rasterio.open(SITE_DIR / "elevation.tif") as d:
        assert d.crs == CRS.from_string(site_cfg.CRS)
        assert d.bounds.left == -site_cfg.SIZE / 2 and d.bounds.top == site_cfg.SIZE / 2


# ---------------------------------------------------------------------------------------------
# INTERIM science layers (sitebuild/science.py): traverse, sample POIs, geologic units, CRISM evidence
# ---------------------------------------------------------------------------------------------
import json  # noqa: E402

EVIDENCE = ["carbonate", "olivine", "fe_mg_smectite", "high_ca_pyroxene", "low_ca_pyroxene", "kaolinite", "mg_smectite_2"]
needs_science = pytest.mark.skipif(not (SITE_DIR / "geologic_unit.tif").exists(), reason="science layers not built")


@needs_science
def test_science_layers_kinds_and_encodings(site):
    g = site.layers["geologic_unit"]
    assert g.kind == "categorical" and g.data.dtype == np.int16 and g.classes
    assert set(np.unique(g.data)) <= set(g.classes) | {-1}
    assert len(g.classes) >= 4 and "Jezero floor unit (Njf)" in g.classes.values()
    assert "100-200" in g.meta["quality"] and "INTERIM" in g.meta["quality"]
    for lid in EVIDENCE:
        e = site.layers[lid]
        assert e.kind == "evidence" and e.data.dtype == np.int8
        assert e.data.shape == (4096, 4096)
        assert set(np.unique(e.data)) <= {-1, 0, 1, 2, 3, 4}, lid
        assert e.meta["resolution_m"] == 36.0
        assert "INTERIM" in e.meta["quality"] and "no_coverage" in e.meta["quality"]
        for k in REQUIRED:
            assert k in e.meta, (lid, k)
        assert e.meta["source"]["licence"].startswith("CC BY 4.0")


@needs_science
def test_crism_black_is_no_coverage_not_not_detected(site):
    """The map's black pixels (no nodata mask) must be -1 in every layer, identical masks, and never tier 0."""
    import rasterio
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    import sys
    sys.path.insert(0, str(ROOT / "pipeline")); sys.path.insert(0, str(ROOT / "sitebuild"))
    from site_frame import site_crs, site_transform
    tif = ROOT / "data/raw/science/parente/jezeroFinal.tif"
    if not tif.exists():
        pytest.skip("raw Parente map absent")
    with rasterio.open(tif) as d:
        rgb = np.zeros((3, 4096, 4096), np.uint8)
        reproject(d.read(), rgb, src_transform=d.transform, src_crs=d.crs, dst_transform=site_transform(4096, 2.0),
                  dst_crs=site_crs(), resampling=Resampling.nearest)
    black = rgb.sum(0) == 0
    assert black.mean() > 0.15                              # the inventory says about a quarter of the site
    base = site.layers["carbonate"].data
    for lid in EVIDENCE:
        t = site.layers[lid].data
        assert (t[black & (t != 4)] == -1).all(), lid       # in-situ discs (tier 4) may sit on black pixels: measured there
        assert ((t == -1) == (base == -1)).mean() > 0.999    # same coverage mask in all layers (in-situ discs aside)
        assert (t == 0).any() and (t == 2).any()
    assert (site.layers["olivine"].data == 3).any() and (site.layers["carbonate"].data == 3).any()


@needs_science
def test_sample_pois_inside_grid_and_labelled(site):
    samples = [p for p in site.pois if p.get("type") == "sample_site"]
    assert 15 <= len(samples) <= 30
    for p in samples:
        assert site.in_bounds(p["x"], p["y"])
        lat, lon = site.xy_to_latlon(p["x"], p["y"])
        assert abs(lat - p["lat"]) < 1e-4 and abs(lon - p["lon"]) < 1e-4
        assert "approximate position derived from the rover waypoint at sol" in p["description"].lower()
        assert p["position_uncertainty_m"] >= 10
        assert p.get("rock_type") and p.get("region")
    ids = {p["id"] for p in samples}
    assert any(i.startswith("sample_04_") for i in ids)


@needs_science
def test_confirmed_in_situ_only_at_documented_samples(site):
    by = {p["id"]: p for p in site.pois if p.get("type") == "sample_site"}
    marked = {lid: np.argwhere(site.layers[lid].data == 4) for lid in EVIDENCE}
    # only olivine and carbonate carry tier 4 at all
    assert all(len(v) == 0 for k, v in marked.items() if k not in ("olivine", "carbonate"))
    for lid in ("olivine", "carbonate"):
        docs = [p for p in by.values() if lid in p.get("in_situ_minerals", [])]
        assert docs
        for p in docs:                                       # the containing cell is tier 4 and the source is cited
            r, c = site.rc_index(p["x"], p["y"])
            assert site.layers[lid].data[r, c] == 4
            assert "doi" in p["description"].lower() or "NTRS" in p["description"]
        # every tier-4 cell lies within 50 m of a documented sample POI
        X, Y = site.rc_to_xy(marked[lid][:, 0], marked[lid][:, 1])
        d = np.min([np.hypot(X - p["x"], Y - p["y"]) for p in docs], axis=0)
        assert d.max() <= 50.0 + 2.0
    # samples with no source statement are not marked (e.g. 1 atmospheric, 10 Swift Run, 19 Melyn)
    for pid, p in by.items():
        if pid.startswith(("sample_01_", "sample_10_", "sample_19_")):
            assert p["in_situ_minerals"] == []


@needs_science
def test_traverse_loads_in_validation_and_landing_alignment(site):
    from fastapi.testclient import TestClient
    old = os.environ.get("ATLAS_SITE_DIR"), os.environ.get("ATLAS_SITE")
    os.environ["ATLAS_SITE_DIR"] = str(SITE_DIR)
    os.environ.pop("ATLAS_SITE", None)
    try:
        from devapi.app import create_app
        client = TestClient(create_app())
        res = client.get("/validation").json()
    finally:
        for k, v in zip(("ATLAS_SITE_DIR", "ATLAS_SITE"), old):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    assert res["available"] is True
    assert res["hazard_agreement"]["traverse_length_m"] > 5000
    assert res["hazard_agreement"]["share_safe_or_caution"] > 0.9
    tr = json.loads((SITE_DIR / "traverse.geojson").read_text())
    line = next(f for f in tr["features"] if f["geometry"]["type"] == "LineString")
    assert len(line["geometry"]["coordinates"]) == len(line["properties"]["x"])
    wps = sorted([f for f in tr["features"] if f["geometry"]["type"] == "Point"], key=lambda f: f["properties"]["sol"])
    # first waypoint (sol 13, "Site increment, no motion" = landing localisation) vs the landing POI
    land = {p["id"]: p for p in site.pois}["octavia_e_butler_landing"]
    w0 = wps[0]["properties"]
    assert math.hypot(w0["x"] - land["x"], w0["y"] - land["y"]) < 30.0
