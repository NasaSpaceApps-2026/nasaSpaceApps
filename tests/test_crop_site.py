"""devapi.crop_site: a crop is a pure slice in a re-centred frame (lat0/lon0 and POIs move, heights do not)."""
import json
import math

import numpy as np
import rasterio
import yaml
from rasterio.transform import from_origin

from devapi.crop_site import crop

R = 3396190.0


def _site(tmp_path, n=100, res=2.0):
    d = tmp_path / "site"
    d.mkdir()
    z = (np.arange(n)[None, :] * 1.0 + np.arange(n)[:, None] * 100.0).astype("float32")  # unique value per cell
    with rasterio.open(d / "elevation.tif", "w", driver="GTiff", width=n, height=n, count=1, dtype="float32",
                       transform=from_origin(-n * res / 2, n * res / 2, res, res)) as w:
        w.write(z, 1)
    (d / "site.yaml").write_text(yaml.safe_dump({
        "name": "t", "lat0": 18.0, "lon0": 77.0, "radius_m": R, "res_m": res, "size_px": n,
        "layers": [{"id": "elevation", "kind": "continuous", "file": "elevation.tif"}]}))
    (d / "pois.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"id": "in", "x": 30.0, "y": 20.0}},
        {"type": "Feature", "geometry": None, "properties": {"id": "out", "x": -90.0, "y": -90.0}}]}))
    return d, z


def test_crop_is_a_slice_in_a_shifted_frame(tmp_path):
    d, z = _site(tmp_path)
    info = crop(d, tmp_path / "out", cx=30.0, cy=20.0, size_m=40.0)
    assert info["snap_error_m"] < 1.0
    cfg = yaml.safe_load((tmp_path / "out" / "site.yaml").read_text())
    assert cfg["size_px"] == 20
    ncx, ncy = info["snapped_centre_old_frame"]
    assert cfg["lat0"] == round(18.0 + math.degrees(ncy / R), 8)
    with rasterio.open(tmp_path / "out" / "elevation.tif") as r:
        a = r.read(1)
    # old cell (row, col) of the crop centre equals new cell (10, 10) offset
    col0 = int(round((ncx - 20 + 100) / 2))
    row0 = int(round((100 - (ncy + 20)) / 2))
    assert np.array_equal(a, z[row0:row0 + 20, col0:col0 + 20])
    pois = json.loads((tmp_path / "out" / "pois.geojson").read_text())["features"]
    assert [p["properties"]["id"] for p in pois] == ["in"]
    assert abs(pois[0]["properties"]["x"] - (30.0 - ncx)) < 0.01


def test_crop_outside_site_exits(tmp_path):
    import pytest
    d, _ = _site(tmp_path)
    with pytest.raises(SystemExit):
        crop(d, tmp_path / "o2", cx=90.0, cy=0.0, size_m=60.0)
