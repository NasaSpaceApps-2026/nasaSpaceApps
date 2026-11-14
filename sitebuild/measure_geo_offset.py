"""Measure the registration offset between the USGS SIM 3464 base mosaic (Caltech CTX mosaic, Feb 2018, the image the
geologic units were mapped on) and OUR site frame (HiRISE/CTX elevation tied to the controlled CTX products).

Method: warp the mosaic (5 m, MTM) into the site frame at 8 m; build a hillshade from our elevation for a range of sun
azimuths; for the azimuth that correlates best with the image, cross-correlate (FFT, sub-pixel peak) image vs hillshade
(after high-pass filtering) in 4 x 4 tiles and report the median shift and its spread. The geologic polygons are drawn on this
mosaic, so the shift of the mosaic is the registration offset of the units relative to our frame.

Run:  .venv/bin/python sitebuild/measure_geo_offset.py   (needs data/raw/science/usgs_sim3464/x/.../Rasters, from the zip)
Writes data/raw/science/usgs_sim3464/offset_measurement.json (read by science.py for the catalogue text).
"""
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sitebuild"))
sys.path.insert(0, str(ROOT / "pipeline"))
from site_frame import site_crs, site_transform  # noqa: E402

RAW = ROOT / "data/raw/science/usgs_sim3464"
MOS = "sim3464_JezeroNili_FINAL/Rasters/Caltech_CTX_mosaic_feb2018/Nili_CTX_Mosaic_Beta01_Feb2018_MTM.tif"
OUTJ = RAW / "offset_measurement.json"
RES = 8.0


def hillshade(z, res, az, alt=30):
    gy, gx = np.gradient(z, res)
    sl = np.arctan(np.hypot(gx, gy)); asp = np.arctan2(-gx, gy)
    a, e = np.radians(az), np.radians(alt)
    return np.sin(e) * np.cos(sl) + np.cos(e) * np.sin(sl) * np.cos(a - asp)


def hp(a, s=6):
    return a - ndimage.gaussian_filter(a, s)


def xcorr_shift(img, ref):
    """Shift (dx_east_px, dy_north_px) that must be applied to ref to match img, via FFT cross-correlation."""
    w = np.hanning(img.shape[0])[:, None] * np.hanning(img.shape[1])[None, :]
    A, B = np.fft.fft2((img - img.mean()) * w), np.fft.fft2((ref - ref.mean()) * w)
    cc = np.fft.fftshift(np.fft.ifft2(A * np.conj(B)).real)
    cy, cx = np.array(cc.shape) // 2
    win = 40
    sub = cc[cy - win:cy + win + 1, cx - win:cx + win + 1]
    iy, ix = np.unravel_index(np.argmax(sub), sub.shape)
    # parabolic sub-pixel
    def par(m, c, p):
        d = m - 2 * c + p
        return 0.0 if d == 0 else 0.5 * (m - p) / d
    fy = par(sub[iy - 1, ix], sub[iy, ix], sub[iy + 1, ix]) if 0 < iy < sub.shape[0] - 1 else 0
    fx = par(sub[iy, ix - 1], sub[iy, ix], sub[iy, ix + 1]) if 0 < ix < sub.shape[1] - 1 else 0
    peak = sub[iy, ix] / (np.sqrt((A * np.conj(A)).real.sum() * (B * np.conj(B)).real.sum()) / img.size)
    return (ix - win + fx), -(iy - win + fy), float(peak)   # rows grow southwards


def main():
    mos = RAW / "x" / MOS
    if not mos.exists():
        with zipfile.ZipFile(RAW / "sim3464_database.zip") as z:
            for n in z.namelist():
                if "Caltech_CTX_mosaic_feb2018" in n and not n.startswith("__MACOSX"):
                    z.extract(n, RAW / "x")
    n = int(8192 / RES)
    tr = site_transform(n, RES)
    img = np.zeros((n, n), np.float32)
    with rasterio.open(mos) as d:
        reproject(rasterio.band(d, 1), img, dst_transform=tr, dst_crs=site_crs(), resampling=Resampling.average)
    with rasterio.open(ROOT / "data/build/site/jezero/elevation.tif") as d:
        z = np.zeros((n, n), np.float32)
        reproject(rasterio.band(d, 1), z, dst_transform=tr, dst_crs=site_crs(), resampling=Resampling.average,
                  src_nodata=d.nodata, dst_nodata=np.nan)
    ok = np.isfinite(z) & (img > 0)
    z = np.where(np.isfinite(z), z, np.nanmean(z))
    imh = hp(img.astype(float))
    best = None
    for az in range(0, 360, 15):      # azimuth whose hillshade gives the strongest correlation PEAK (at any shift)
        hs = hp(hillshade(z, RES, az))
        pk = xcorr_shift(imh, hs)[2]
        if best is None or pk > best[1]:
            best = (az, pk)
    az = best[0]
    hs = hp(hillshade(z, RES, az))
    shifts = []
    T = 4
    ts = n // T
    for i in range(T):
        for j in range(T):
            sl = (slice(i * ts, (i + 1) * ts), slice(j * ts, (j + 1) * ts))
            if ok[sl].mean() < 0.9:
                continue
            dx, dy, pk = xcorr_shift(imh[sl], hs[sl])
            shifts.append((dx * RES, dy * RES, pk))
    whole = xcorr_shift(imh, hs)
    s = np.array(shifts)
    good = s[s[:, 2] > 0.05] if len(s) else s
    res = dict(best_sun_azimuth_deg=az, best_azimuth_peak=round(float(best[1]), 3),
               whole_site=dict(dx_east_m=round(whole[0] * RES, 1), dy_north_m=round(whole[1] * RES, 1), peak=round(whole[2], 3)),
               tiles=[dict(dx_east_m=round(a, 1), dy_north_m=round(b, 1), peak=round(c, 3)) for a, b, c in s.tolist()],
               n_tiles_used=int(len(good)))
    if len(good):
        mag = np.hypot(good[:, 0], good[:, 1])
        res["median_dx_east_m"] = round(float(np.median(good[:, 0])), 1)
        res["median_dy_north_m"] = round(float(np.median(good[:, 1])), 1)
        res["median_magnitude_m"] = round(float(np.median(mag)), 1)
        res["tile_magnitude_range_m"] = [round(float(mag.min()), 1), round(float(mag.max()), 1)]
    res["summary"] = (f"cross-correlating the SIM 3464 base CTX mosaic (Caltech Feb 2018) with a hillshade of our elevation (best sun azimuth {az} deg, "
                      f"best whole-site correlation peak {best[1]:.2f}) gives a whole-site shift of the mosaic of {whole[0]*RES:+.0f} m east, {whole[1]*RES:+.0f} m north "
                      f"(correlation peak {whole[2]:.2f}); per-tile (2 km) shifts of {len(good)} tiles have median magnitude "
                      f"{res.get('median_magnitude_m', float('nan')):.0f} m (range {res.get('tile_magnitude_range_m', ['n/a','n/a'])[0]}-{res.get('tile_magnitude_range_m', ['n/a','n/a'])[1]} m). "
                      "Positive east/north = features in the mosaic (and so the unit polygons) lie that far east/north of the same features in our elevation frame. "
                      "This is an image-to-hillshade correlation (illumination and albedo differ), accurate to roughly one 8 m pixel at best.")
    OUTJ.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
