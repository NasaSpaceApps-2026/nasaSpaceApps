"""Site frame geometry (PRD contract 1) shared by build_site.py."""
import math
import sys
from pathlib import Path

from rasterio.transform import from_origin

# Shared frame lives in Caden's pipeline/site_cfg.py (read-only import by path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import site_cfg  # team single source of truth for centre, size and CRS
from site_cfg import CRS as SITE_CRS, LAT0, LON0, R, SIZE  # noqa: F401
SRC_CRS = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=m"   # source mosaics (lat_ts=0, lon_0=0)


def site_crs():
    """The CRS the build writes: exactly site_cfg.CRS (no local re-definition)."""
    return SITE_CRS


def reference_crs(lat0=LAT0, lon0=LON0):
    """Independent re-statement of the contract-1 frame (eqc, lat_ts=lat0, northing shifted so
    (lat0, lon0) -> (0, 0)). Used ONLY by verify_frame() to cross-check site_cfg.CRS."""
    y0 = -R * math.radians(lat0)
    return f"+proj=eqc +lat_ts={lat0} +lat_0=0 +lon_0={lon0} +x_0=0 +y_0={y0:.6f} +R={R} +units=m +no_defs"


def verify_frame(tol_m=0.01):
    """Transform test points lon/lat -> xy and back with both definitions; return max
    disagreement in metres (asserts < tol_m). Also checks the centre maps to (0, 0)."""
    from rasterio.crs import CRS
    from rasterio.warp import transform as wtransform
    geo = CRS.from_string(f"+proj=longlat +R={R} +no_defs")
    ca, cb = CRS.from_string(SITE_CRS), CRS.from_string(reference_crs())
    h = SIZE / 2 / (R * math.radians(1))
    pts = [(LON0, LAT0), (77.4508, 18.4447), (LON0 - h, LAT0 - h), (LON0 + h, LAT0 + h),
           (LON0 - h, LAT0 + h), (LON0 + h, LAT0 - h), (77.35, 18.40)]
    lons, lats = zip(*pts)
    xa, ya = wtransform(geo, ca, lons, lats)
    xb, yb = wtransform(geo, cb, lons, lats)
    worst = max(math.hypot(p - q, r - t) for p, q, r, t in zip(xa, xb, ya, yb))
    # round trip through each definition, cross-checked on the ground (metres)
    lo, la = wtransform(ca, geo, xb, yb)
    m = R * math.radians(1)
    worst = max(worst, max(math.hypot((u - v) * m * math.cos(math.radians(LAT0)), (w - z) * m)
                           for u, v, w, z in zip(lo, lons, la, lats)))
    cx, cy = xa[0], ya[0]
    assert math.hypot(cx, cy) < tol_m, f"centre maps to ({cx}, {cy}), not origin"
    assert worst < tol_m, f"site_cfg.CRS and reference frame disagree by {worst} m"
    return worst


def site_transform(size_px, res):
    half = size_px * res / 2
    return from_origin(-half, half, res, res)     # origin at centre, row 0 north


def src_window_bounds(lat0, lon0, half, pad=1.04):
    """Bounds in the source CRS (lat_ts=0) covering the site square (+pad). E-W metres are
    stretched by 1/cos(lat) in the source, so the x span is wider than the y span."""
    cx, cy = R * math.radians(lon0), R * math.radians(lat0)
    kx = 1 / math.cos(math.radians(lat0))
    # site-frame x metres -> source metres: x_src = x / cos(lat0) (since site eqc has lat_ts=lat0);
    # y: a site y metre is a source y metre (both R*dlat).  Corner latitude differs from lat0 by
    # up to half/R rad (0.07 deg), negligible vs. the pad.
    return (cx - half * kx * pad, cy - half * pad, cx + half * kx * pad, cy + half * pad)
