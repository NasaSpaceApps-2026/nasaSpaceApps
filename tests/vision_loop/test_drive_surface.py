import json
from pathlib import Path

import numpy as np

from vision_loop.drive_surface import import_cesium_height


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
