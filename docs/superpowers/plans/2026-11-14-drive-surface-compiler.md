# Drive-Surface Compiler Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Rithvik's Cesium height grid into the JSON file the vision agent edits, then write that JSON back to a height grid the rover and Cesium both sample.

**Architecture:** Import reads `dem2m.u16` plus `dem2m.json` and writes `model.json`. That JSON is the model file. It records the grid (layout, size, spacing, base, step) and starts with `"ops": []`. The original uint16 bytes are copied beside it so the agent does not rewrite hundreds of thousands of height numbers by hand. The agent appends bump, paint, and smooth ops to `model.json`. Export applies those ops and writes a new uint16 grid in the same encoding. `sample_drive` reads the edited grid inside the bubble and the original Cesium grid outside it. The `.glb` stays a picture. It is not the collision surface.

**Tech Stack:** Python 3.11, numpy, Pillow, pytest. A small TypeScript twin of the sampler lives in `contracts/drive/sampleHeight.ts` for Rithvik and Yousef to paste. Do not edit `web/` or `api/`.

## Global Constraints

- Height encoding matches Rithvik's viewer and `pipeline/09_export_web.py` on `main`: `elev_m = base + 0.01 * uint16`, little-endian, row 0 = north, `base = floor(min height)`.
- His site grid is pixel-centered. From `web/src/site.ts`: `cf = (x + half) / res_m - 0.5`, `rf = (half - y) / res_m - 0.5`, with `half = size_m / 2` and the grid centered on the site origin. Column 0's center is at `x = -half + 0.5 * res_m`. Row 0's center is at `y = half - 0.5 * res_m`.
- The bubble window is vertex-sampled, matching `grid.json` (`x = x0 + col * res_m`, `y = y0 - row * res_m`). The bubble sampler uses that formula. The site sampler uses his pixel-center formula. `sample_drive` picks one.
- A bump in `model.json` must show up in `sample_drive` at that `(x, y)`. Outside the bubble, the returned height is the site grid and ignores the ops.
- Texture is written for the viewer to drape. It does not move the suspension. Suspension, pitch, and roll come from the height under the wheels. A `guessed.u8` mask is 1 where an op with `"inferred": true` has falloff above 0.05, so the rover can treat that ground as caution.
- Do not edit `pipeline/bubble`, `web/`, or `api/`. Do not download imagery.
- Round-trip error of a finite height through uint16 is at most 0.005 m.
- Import does not put every height inside `model.json`. A 512×512 site is a quarter million numbers. The JSON names the copied `baseline/height.u16` and holds the editable `ops` list. That is the file the agent modifies.

---

### Task 0: Import the Cesium height grid into model.json

**Files:**
- Create: `pipeline/vision_loop/drive_surface.py`
- Test: `tests/vision_loop/test_drive_surface.py`

**Interfaces:**
- Consumes: a `dem2m.u16` and `dem2m.json` in Rithvik's format (`width`, `height`, `res_m`, `base`, `step`)
- Produces: `import_cesium_height(u16_path, json_path, work: Path, size_m: float | None = None) -> dict`
  - Writes `work/model.json`, `work/baseline/height.u16`, `work/baseline/height.json`
  - `size_m` defaults to `width * res_m` when omitted

`model.json` shape:

```json
{
  "source": "cesium_dem2m",
  "layout": "pixel_center",
  "width": 4,
  "height": 4,
  "res_m": 2.0,
  "size_m": 8.0,
  "center_x": 0.0,
  "center_y": 0.0,
  "base": -2400.0,
  "step": 0.01,
  "heights": "baseline/height.u16",
  "ops": []
}
```

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py::test_cesium_u16_becomes_an_editable_model -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'vision_loop.drive_surface'`

- [ ] **Step 3: Implement the import**

Read the JSON header, memory-map or `np.fromfile` the uint16 as little-endian, check `width * height` matches the file, and write the model above with an empty `ops` list. Copy the bytes to `baseline/height.u16` unchanged. Copy the header fields into `baseline/height.json` and add `layout`, `size_m`, `center_x`, and `center_y`.

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py::test_cesium_u16_becomes_an_editable_model -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/vision_loop/drive_surface.py tests/vision_loop/test_drive_surface.py
git commit -m "$(cat <<'EOF'
Import the Cesium height grid into the JSON the agent edits.

The uint16 bytes stay next to model.json. The agent changes the ops list.
EOF
)"
```

---

### Task 1: Encode heights and sample a vertex grid

**Files:**
- Modify: `pipeline/vision_loop/drive_surface.py`
- Modify: `tests/vision_loop/test_drive_surface.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `STEP = 0.01`
  - `encode_heights(elev: np.ndarray) -> tuple[np.ndarray, float]`  # uint16 array, base
  - `sample_vertex(x, y, *, values, base, res_m, x0, y0) -> float`  # NaN outside

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pytest

from vision_loop.drive_surface import encode_heights, sample_vertex


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
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'vision_loop.drive_surface'`

- [ ] **Step 3: Write `pipeline/vision_loop/drive_surface.py`**

```python
from __future__ import annotations

import numpy as np

from vision_loop.errors import VisionError

STEP = 0.01


def encode_heights(elev: np.ndarray) -> tuple[np.ndarray, float]:
    elev = np.asarray(elev, dtype=np.float64)
    finite = elev[np.isfinite(elev)]
    if finite.size == 0:
        raise VisionError("no finite heights to encode")
    base = float(np.floor(finite.min()))
    scaled = np.rint((elev - base) / STEP)
    if float(np.nanmax(scaled)) > 65535 or float(np.nanmin(scaled)) < 0:
        raise VisionError("relief does not fit uint16 at 0.01 m")
    values = np.zeros(elev.shape, dtype=np.uint16)
    good = np.isfinite(elev)
    values[good] = scaled[good].astype(np.uint16)
    return values, base


def _bilinear(values: np.ndarray, cf: float, rf: float) -> float:
    rows, cols = values.shape
    if cf < 0 or rf < 0 or cf > cols - 1 or rf > rows - 1:
        return float("nan")
    c0 = int(np.floor(cf))
    r0 = int(np.floor(rf))
    c1 = min(c0 + 1, cols - 1)
    r1 = min(r0 + 1, rows - 1)
    fc = cf - c0
    fr = rf - r0
    v00 = float(values[r0, c0])
    v01 = float(values[r0, c1])
    v10 = float(values[r1, c0])
    v11 = float(values[r1, c1])
    return (v00 * (1 - fc) + v01 * fc) * (1 - fr) + (v10 * (1 - fc) + v11 * fc) * fr


def sample_vertex(x: float, y: float, *, values, base: float, res_m: float, x0: float, y0: float) -> float:
    cf = (x - x0) / res_m
    rf = (y0 - y) / res_m
    value = _bilinear(values, cf, rf)
    if value != value:
        return value
    return base + STEP * value
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/vision_loop/drive_surface.py tests/vision_loop/test_drive_surface.py
git commit -m "$(cat <<'EOF'
Encode bubble heights in the viewer's uint16 grid.

A vertex sample round-trips through the same centimetre step the Cesium terrain uses.
EOF
)"
```

---

### Task 2: Sample Rithvik's pixel-centered site grid

**Files:**
- Modify: `pipeline/vision_loop/drive_surface.py`
- Modify: `tests/vision_loop/test_drive_surface.py`

**Interfaces:**
- Consumes: `STEP`, `_bilinear`
- Produces: `sample_pixel_center(x, y, *, values, base, res_m, size_m, center_x, center_y) -> float`

This is the formula in `web/src/site.ts` `Site.elevation`, shifted so the grid center is `(center_x, center_y)`. For the site, both centers are 0.

- [ ] **Step 1: Add the failing test**

```python
def test_pixel_center_matches_the_cesium_site_formula():
    # 4x4 samples, 2 m, covering a square of 8 m centered on the origin.
    # Column 0 center is x = -3. Row 0 center is y = 3.
    values = np.zeros((4, 4), np.uint16)
    values[0, 0] = 1000  # 10.00 m above base 0
    got = sample_pixel_center(-3, 3, values=values, base=0.0, res_m=2.0, size_m=8.0, center_x=0.0, center_y=0.0)
    assert got == pytest.approx(10.0, abs=0.005)
    assert np.isnan(sample_pixel_center(20, 0, values=values, base=0.0, res_m=2.0, size_m=8.0, center_x=0.0, center_y=0.0))
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py::test_pixel_center_matches_the_cesium_site_formula -v`

Expected: FAIL, `ImportError` or `NameError` for `sample_pixel_center`

- [ ] **Step 3: Add the function**

```python
def sample_pixel_center(
    x: float,
    y: float,
    *,
    values,
    base: float,
    res_m: float,
    size_m: float,
    center_x: float,
    center_y: float,
) -> float:
    half = size_m / 2.0
    cf = ((x - center_x) + half) / res_m - 0.5
    rf = (half - (y - center_y)) / res_m - 0.5
    value = _bilinear(values, cf, rf)
    if value != value:
        return value
    return base + STEP * value
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/vision_loop/drive_surface.py tests/vision_loop/test_drive_surface.py
git commit -m "$(cat <<'EOF'
Sample the site height grid with the Cesium pixel-center formula.

Column zero sits half a pixel in from the west edge, matching Site.elevation.
EOF
)"
```

---

### Task 3: Compile model.json into the drive window

**Files:**
- Modify: `pipeline/vision_loop/drive_surface.py`
- Modify: `tests/vision_loop/test_drive_surface.py`

**Interfaces:**
- Consumes: `compile_model` from `vision_loop.model`, `encode_heights`, `sample_vertex`
- Produces:
  - `compile_drive(work: Path) -> dict` with keys `values`, `base`, `res_m`, `x0`, `y0`, `texture`, `guessed`
  - `write_drive(work: Path) -> Path` writing `work/drive/height.u16`, `height.json`, `texture.png`, `guessed.u8`

`guessed` is uint8, 1 where any op with `"inferred": true` has falloff above 0.05. Paint and bump without that flag leave it 0.

`height.json` fields: `layout` = `"vertex"`, `width`, `height`, `res_m`, `x0`, `y0`, `base`, `step`.

- [ ] **Step 1: Add the failing test**

```python
import json
from pathlib import Path

from vision_loop.model import save_model
from vision_loop.workspace import init_workspace
from vision_loop.drive_surface import compile_drive, sample_vertex, write_drive


def test_bump_is_visible_to_a_wheel_sample(tmp_path: Path):
    work = tmp_path / "work"
    init_workspace(Path("contracts/fixtures/bubble_source"), work)
    save_model(work / "model.json", {"ops": [
        {"op": "bump", "x": 4, "y": 2, "radius_m": 2, "height_m": 0.4, "inferred": False},
    ]})
    drive = compile_drive(work)
    base_h = sample_vertex(4, 2, values=encode_only_baseline(work), base=drive["baseline_base"], res_m=drive["res_m"], x0=drive["x0"], y0=drive["y0"])
    got = sample_vertex(4, 2, values=drive["values"], base=drive["base"], res_m=drive["res_m"], x0=drive["x0"], y0=drive["y0"])
    assert got > base_h + 0.3
    assert int(drive["guessed"][0, 0]) == 0


def encode_only_baseline(work: Path):
    from vision_loop.model import load_baseline
    from vision_loop.drive_surface import encode_heights
    dem, _tex, _grid = load_baseline(work)
    values, _base = encode_heights(dem)
    return values
```

Replace that helper with a direct comparison inside the test after `compile_drive` also returns `baseline_values` and `baseline_base`, so the test does not need a second encoder call. Assert `drive["baseline_values"]` exists.

Also assert an inferred bump sets `guessed` at its center to 1.

```python
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
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py -v`

Expected: FAIL, `ImportError` for `compile_drive`

- [ ] **Step 3: Implement compile and write**

Use `compile_model` for the edited height and texture. Encode that height. Build `guessed` with the same falloff rule as `model._falloff` by calling a new `guess_mask(dem_shape, grid, ops)` in `drive_surface.py` that duplicates the falloff math (importing `_falloff` from `vision_loop.model` is fine). Write little-endian bytes with `values.astype("<u2").tofile(...)`. Save the texture with Pillow. `height.json` includes `x0`, `y0`, `res_m`, `width`, `height`, `base`, `step`, `layout`.

`compile_drive` returns the arrays plus `baseline_values` and `baseline_base` from encoding the baseline dem before ops, so the wheel test can compare them.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/vision_loop/drive_surface.py tests/vision_loop/test_drive_surface.py
git commit -m "$(cat <<'EOF'
Compile model.json into the height grid the wheels sample.

Inferred bumps mark those cells as guessed ground.
EOF
)"
```

---

### Task 4: One drive sample, the TypeScript twin, and the CLI

**Files:**
- Modify: `pipeline/vision_loop/drive_surface.py`
- Modify: `pipeline/vision_loop/__main__.py`
- Create: `contracts/drive/sampleHeight.ts`
- Modify: `tests/vision_loop/test_drive_surface.py`

**Interfaces:**
- Consumes: `sample_vertex`, `sample_pixel_center`
- Produces:
  - `sample_drive(x, y, bubble, site=None) -> float`
  - CLI: `PYTHONPATH=pipeline python3 -m vision_loop drive --work DIR`
  - `contracts/drive/sampleHeight.ts` exporting `sampleVertex`, `samplePixelCenter`, and `sampleDrive` with the same formulas

`bubble` and `site` are dicts loaded from their `height.json` plus the uint16 array. `sample_drive` returns the bubble height when the vertex sample is finite, otherwise the site pixel-center sample, otherwise NaN.

- [ ] **Step 1: Add the failing test**

```python
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
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m pytest tests/vision_loop/test_drive_surface.py::test_inside_the_bubble_the_bump_wins_and_outside_uses_the_site -v`

Expected: FAIL, `sample_drive` is not defined

- [ ] **Step 3: Implement `sample_drive`, the CLI, and the TypeScript twin**

Python:

```python
def sample_drive(x: float, y: float, bubble: dict, site: dict | None = None) -> float:
    got = sample_vertex(
        x, y,
        values=bubble["values"], base=bubble["base"], res_m=bubble["res_m"],
        x0=bubble["x0"], y0=bubble["y0"],
    )
    if got == got:
        return got
    if site is None:
        return float("nan")
    return sample_pixel_center(
        x, y,
        values=site["values"], base=site["base"], res_m=site["res_m"],
        size_m=site["size_m"], center_x=site["center_x"], center_y=site["center_y"],
    )
```

Add `drive` subcommand in `vision_loop/__main__.py` that calls `write_drive` and prints the output directory.

TypeScript in `contracts/drive/sampleHeight.ts` repeats the two formulas and `sampleDrive`. Header comment, verbatim:

```typescript
// Paste into Rithvik's Site.elevation and Yousef's sampleHeight.
// Inside the bubble, use the vertex grid written by `vision_loop drive`.
// Outside it, use the existing pixel-center dem2m.u16 grid.
// Wheel height, pitch, roll, and later suspension all read this number.
// Texture does not enter this function. guessed.u8 is a separate caution flag.
```

The TypeScript functions take plain numbers and a `Uint16Array` plus width. No Cesium imports.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/vision_loop -q`

Expected: PASS, including the existing vision-loop tests

- [ ] **Step 5: Commit**

```bash
git add pipeline/vision_loop/drive_surface.py pipeline/vision_loop/__main__.py contracts/drive/sampleHeight.ts tests/vision_loop/test_drive_surface.py
git commit -m "$(cat <<'EOF'
Sample edited bubble heights first and the site grid outside them.

The rover and the Cesium terrain provider can share this one height function.
EOF
)"
```

## Fit with the Cesium viewer and the rover

Checked against `origin/main` after the merge.

- Cesium (`web/src/site.ts`) and the API (`api/ares_api/terrain.py` `to_cell`) use the same pixel-center grid. `west = -size_m/2`, `north = size_m/2`, column 0 at `x = west + 0.5 * res_m`. `dem2m.u16` is `base + 0.01 * value`. The API rover's `z`, pitch, and roll come from `Terrain.elevation` and `tilt()`.
- The browser rover (`web/src/rover/physics.ts`) does not read `dem2m.u16`. It calls `HeightSampler.sampleHeight(x, y)`. The sandbox implementation reads RGB height tiles (`elev_m = -10000 + 0.01 * (R*65536+G*256+B)`), which are shared-edge posts, not pixel centers. Wheelbase is 2.7 m, track 2.3 m.
- The API refuses a drive using the precomputed `slope` raster, not the live height gradient. A bump that only changes elevation will lift the body and change pitch, and will not by itself trip `blocked_slope`.
- Site frame matches everywhere that matters: x east, y north, origin at the region centre. three.js and our glTF are both `(x, z, -y)`.

Rules for this compiler:

- An imported Cesium grid is stored so its first sample is `x0 = -size_m/2 + res_m/2`, `y0 = size_m/2 - res_m/2`. `sample_vertex` and `sample_pixel_center` then return the same height. Do not invent a second offset.
- `contracts/drive/sampleHeight.ts` exports `sampleHeight(x, y)` with the `HeightSampler` shape. Inside the bubble it reads the edited grid. The caller passes the tile sampler for everywhere else.
- Also export `slopeDeg(x, y)` from a 2 m finite difference of `sample_drive`. The API's no-go check has to use that inside the bubble, because `slope.tif` does not contain the edits.
- Do not modify `web/src/rover/physics.ts` or `api/ares_api/rover.py`. They already take a height function. The compiler only has to feed that function.

## Direction

1. Read his `dem2m.u16` and `dem2m.json`.
2. Write `model.json` with an empty `ops` list. That is the file the vision agent modifies.
3. After the agent edits, write a new uint16 grid from `model.json`.
4. The rover and Cesium sample that grid inside the bubble and the original grid outside it.

## Handoff, not done in this plan

Rithvik, in `web/src/site.ts` `elevation`: if `sampleDrive` returns a finite bubble height, use it; otherwise keep the current `dem2m.u16` read. Drape `drive/texture.png` inside the bubble rectangle. Do not also draw `mesh.glb` as a solid ground mesh.

Yousef, in `sampleHeight(x, y)`: call the same function for each wheel. Pitch and roll come from the four heights. Slope limits use those heights. When suspension arrives, the spring sees this height, not the glTF. Read `guessed.u8` at the wheel as caution, not as a soil model.

## Out of scope

- Editing `web/` or `api/`
- Wheel friction from texture color
- Replacing the Cesium ion planet tileset
- Downloading HiRISE or rover frames
