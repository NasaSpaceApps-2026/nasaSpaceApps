"""Site frame geometry (PRD contract 1) shared by build_site.py."""
import math
from rasterio.transform import from_origin

R = 3396190.0
SRC_CRS = "+proj=eqc +lat_ts=0 +lon_0=0 +R=3396190 +units=m"   # source mosaics (lat_ts=0, lon_0=0)


def site_crs(lat0, lon0):
    # northing shifted (+y_0) so that (lat0, lon0) maps to (0, 0), PRD contract 1
    y0 = -R * math.radians(lat0)
    return f"+proj=eqc +lat_ts={lat0} +lon_0={lon0} +y_0={y0:.4f} +R={R} +units=m"


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
