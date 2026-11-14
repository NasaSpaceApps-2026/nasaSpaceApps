import json
from pathlib import Path

import numpy as np
import pytest

from vision_loop.drive_surface import (
    compile_drive,
    encode_heights,
    import_cesium_height,
    sample_drive,
    sample_pixel_center,
    sample_vertex,
    slope_deg,
    write_drive,
)
from vision_loop.model import save_model
from vision_loop.workspace import init_workspace


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


def test_pixel_center_matches_the_cesium_site_formula():
    # 4x4 samples, 2 m, covering a square of 8 m centered on the origin.
    # Column 0 center is x = -3. Row 0 center is y = 3.
    values = np.zeros((4, 4), np.uint16)
    values[0, 0] = 1000  # 10.00 m above base 0
    got = sample_pixel_center(-3, 3, values=values, base=0.0, res_m=2.0, size_m=8.0, center_x=0.0, center_y=0.0)
    assert got == pytest.approx(10.0, abs=0.005)
    assert np.isnan(sample_pixel_center(20, 0, values=values, base=0.0, res_m=2.0, size_m=8.0, center_x=0.0, center_y=0.0))


def test_bump_is_visible_to_a_wheel_sample(tmp_path: Path):
    work = tmp_path / "work"
    init_workspace(Path("contracts/fixtures/bubble_source"), work)
    save_model(work / "model.json", {"ops": [
        {"op": "bump", "x": 4, "y": 2, "radius_m": 2, "height_m": 0.4, "inferred": False},
    ]})
    drive = compile_drive(work)
    assert drive["baseline_values"] is not None
    base_h = sample_vertex(
        4, 2,
        values=drive["baseline_values"],
        base=drive["baseline_base"],
        res_m=drive["res_m"],
        x0=drive["x0"],
        y0=drive["y0"],
    )
    got = sample_vertex(
        4, 2,
        values=drive["values"],
        base=drive["base"],
        res_m=drive["res_m"],
        x0=drive["x0"],
        y0=drive["y0"],
    )
    assert got > base_h + 0.3
    assert int(drive["guessed"][0, 0]) == 0


def test_inferred_bump_marks_guessed_ground(tmp_path: Path):
    work = tmp_path / "work"
    init_workspace(Path("contracts/fixtures/bubble_source"), work)
    save_model(work / "model.json", {"ops": [
        {"op": "bump", "x": 4, "y": 2, "radius_m": 2, "height_m": 0.2, "inferred": True},
    ]})
    drive = compile_drive(work)
    row = int(round((drive["y0"] - 2) / drive["res_m"]))
    col = int(round((4 - drive["x0"]) / drive["res_m"]))
    assert int(drive["guessed"][row, col]) == 1
    out = write_drive(work)
    header = json.loads((out / "height.json").read_text())
    assert header["layout"] == "vertex"
    assert header["step"] == 0.01
    raw = np.fromfile(out / "height.u16", dtype="<u2")
    assert raw.size == header["width"] * header["height"]


def test_inside_the_bubble_the_bump_wins_and_outside_uses_the_site():
    bubble = {
        "layout": "vertex",
        "values": np.array([[1000]], np.uint16),
        "base": 0.0,
        "res_m": 1.0,
        "x0": 0.0,
        "y0": 0.0,
        "width": 1,
        "height": 1,
    }
    site = {
        "layout": "pixel_center",
        "values": np.full((4, 4), 500, np.uint16),
        "base": 0.0,
        "res_m": 2.0,
        "size_m": 8.0,
        "center_x": 0.0,
        "center_y": 0.0,
        "width": 4,
        "height": 4,
    }
    assert sample_drive(0, 0, bubble, site) == pytest.approx(10.0, abs=0.005)
    assert sample_drive(-3, 3, bubble, site) == pytest.approx(5.0, abs=0.005)


def test_flat_grid_slope_is_about_zero():
    bubble = {
        "layout": "vertex",
        "values": np.full((5, 5), 1000, np.uint16),
        "base": 0.0,
        "res_m": 1.0,
        "x0": 0.0,
        "y0": 0.0,
        "width": 5,
        "height": 5,
    }
    assert slope_deg(2, -2, bubble) == pytest.approx(0.0, abs=1e-6)
    edge = {
        "layout": "vertex",
        "values": np.array([[1000]], np.uint16),
        "base": 0.0,
        "res_m": 1.0,
        "x0": 0.0,
        "y0": 0.0,
        "width": 1,
        "height": 1,
    }
    assert slope_deg(0, 0, edge) == pytest.approx(0.0, abs=1e-6)


def test_drive_cli_prints_the_output_directory(tmp_path: Path, capsys):
    from vision_loop.__main__ import main

    work = tmp_path / "work"
    init_workspace(Path("contracts/fixtures/bubble_source"), work)
    main(["drive", "--work", str(work)])
    printed = capsys.readouterr().out.strip()
    assert Path(printed) == work / "drive"
    assert (work / "drive" / "height.u16").exists()
    assert (work / "drive" / "height.json").exists()
    main(["check", "--work", str(work)])
    assert "inferred ops are only on low-coverage ground" in capsys.readouterr().out
