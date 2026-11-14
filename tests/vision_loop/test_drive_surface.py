import json
from pathlib import Path

import numpy as np
import pytest

from vision_loop.drive_surface import encode_heights, import_cesium_height, sample_vertex


def test_cesium_u16_becomes_an_editable_model(tmp_path: Path):
    values = np.arange(16, dtype="<u2").reshape(4, 4)
    u16 = tmp_path / "dem2m.u16"
    values.tofile(u16)
    header = tmp_path / "dem2m.json"
    header.write_text(json.dumps({
        "width": 4, "height": 4, "res_m": 2.0, "base": -2400.0, "step": 0.01,
    }))
    work = tmp_path / "work"
    model = import_cesium_height(u16, header, work)
    assert model["source"] == "cesium_dem2m"
    assert model["layout"] == "pixel_center"
    assert model["ops"] == []
    assert model["size_m"] == 8.0
    on_disk = json.loads((work / "model.json").read_text())
    assert on_disk["ops"] == []
    copied = np.fromfile(work / "baseline" / "height.u16", dtype="<u2").reshape(4, 4)
    assert np.array_equal(copied, values)


def test_uint16_roundtrip_is_within_half_a_centimetre():
    elev = np.array([[-2400.004], [-2399.5], [-2000.0]], np.float64)
    values, base = encode_heights(elev)
    back = base + 0.01 * values.astype(np.float64)
    assert np.max(np.abs(back - elev)) <= 0.005
    assert values.dtype == np.uint16
    assert base == pytest.approx(np.floor(elev.min()))


def test_vertex_sample_hits_the_northwest_sample_and_rejects_outside():
    elev = np.arange(9, dtype=np.float64).reshape(3, 3) + 10
    values, base = encode_heights(elev)
    assert sample_vertex(-2, 2, values=values, base=base, res_m=1, x0=-2, y0=2) == pytest.approx(10, abs=0.005)
    assert np.isnan(sample_vertex(-3, 2, values=values, base=base, res_m=1, x0=-2, y0=2))
