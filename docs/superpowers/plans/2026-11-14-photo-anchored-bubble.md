# Photo-Anchored Bubble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline loop that drapes posed photos onto a frozen heightfield and fades back to the ortho, so a rover-eye render shows the photo while the mesh heights stay the DEM.

**Architecture:** A synthetic mound proves the loop without NASA downloads. `bake_texture` is the only edit: it composites fit-camera photos onto the ortho. A software rasterizer renders fixed cameras to PNGs. Each tick changes one parameter, re-renders, and reverts when fit or holdout scores get worse. `judge` is the only way a real bundle becomes the active mesh. Baseline and refined GLBs share positions.

**Tech Stack:** Python 3.11, numpy, Pillow, pytest. No GDAL, OpenCV, PyTorch, or GPU.

## Global Constraints

- Approach A only. The DEM is frozen. `geometry_delta_m_max` must be 0. Do not add relief exaggeration, photoclinometry, splats, or generative gap-fill.
- Do not build the globe, the 8 km tile pipeline, the rover, or MCP.
- Do not edit `PRD.md`.
- Site frame is meters, x east, y north, z up. Mesh samples are row 0 = north. glTF is Y-up: `(x - center_x, z, -(y - center_y))`. East is +X, up is +Y, north is -Z. Origin is the bubble center. Do not pre-rotate to Z-up.
- Default `angle_power` is **0**. Weight is a front-face cutoff (`facing > 0.05`) times distance fade. A cosine falloff hides the photo on a rover-like oblique view; a scratch run of this algorithm moved the mound peak from ortho distance 111 to 7 only after the cosine was removed, and fit SSIM went from about 0.03 to about 0.88. `set_angle_power` exists and is not in the scripted sequence.
- Recorded `threshold_ssim` is 0.75 because Contract 6 names that field. `decide_pass` does not read it. Pass is fit SSIM `>= baseline + 0.05`, holdout drop `<= 0.02`, geometry delta 0, and texels with zero photo weight unchanged.
- Fit SSIM checks that the projected photo reappears from its own camera. It does not prove the pose sits on Mars. Fixture sets `requires_human` false. A real bundle sets `requires_human` true and stays `active: baseline` until `judge --verdict accept`.
- Holdout cameras are scored and never enabled.
- Max 5 ticks. Scripted sequence enables only the first fit camera.
- `lpips` is JSON null. Do not add a neural metric.
- No clock and no hour-26 check in code. The human stops.
- Bundle and work directories must be separate. `run_scripted` deletes the work directory.
- Dependencies, exact pins: `numpy>=1.26,<3`, `Pillow>=10,<12`, `pytest>=8,<9`.

---

## File map

| File | Responsibility |
|---|---|
| `pipeline/bubble/errors.py` | `BubbleError` |
| `pipeline/bubble/gates.py` | Pass/revert thresholds and `Scores` |
| `pipeline/bubble/geom.py` | Cameras, grid, projection, normals, visibility |
| `pipeline/bubble/compose.py` | Bake the ortho + photo texture |
| `pipeline/bubble/render.py` | Heightfield rasterizer |
| `pipeline/bubble/score.py` | Masked SSIM, compare strips |
| `pipeline/bubble/gltf.py` | GLB write/read, position delta |
| `pipeline/bubble/params.py` | One-field operators |
| `pipeline/bubble/bundle.py` | Load `dem.npy` + `ortho.png` + viewpoints |
| `pipeline/bubble/fixture.py` | Synthetic mound and its photos |
| `pipeline/bubble/tick.py` | Init, one tick, revert, state log |
| `pipeline/bubble/scripted.py` | Three-tick run and `judge` |
| `pipeline/bubble/__main__.py` | CLI |
| `tests/bubble/` | One test module per task |
| `contracts/fixtures/bubble_source/` | Committed synthetic input |
| `contracts/fixtures/bubble/` | Committed baseline/refined output |

Scratch run this plan is written against (33×33 mound, cameras below): fit SSIM 0.030 → 0.878, holdout 0.026 → 0.720, northwest corner texel unchanged, peak color distance to albedo 111 → 7.

## Fixture constants (every task uses these)

Grid: 33×33, `x0 = -16`, `y0 = 16`, `res_m = 1`, center `(0, 0)`. Peak sample `(row=14, col=20)`, site `(4, 2)`, height about 1.22 m.

| Camera | xyz | heading | pitch | fov | size | role |
|---|---|---|---|---|---|---|
| `nadir` | `(0, 0, 40)` | 0 | -90 | 50 | 64×64 | nadir, no image |
| `fit_0` | `(1, -1, 2.4)` | 45 | -20 | 60 | 96×72 | fit |
| `holdout_0` | `(2.5, -2.5, 2.6)` | 18 | -22 | 60 | 96×72 | holdout |

Scripted operators, in order: `set_opacity 1`, `enable_frame fit_0`, `set_fade_end_m 20`.

---

### Task 1: Gates and camera geometry

**Files:**
- Create: `pipeline/requirements.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `pipeline/bubble/__init__.py`
- Create: `pipeline/bubble/errors.py`
- Create: `pipeline/bubble/gates.py`
- Create: `pipeline/bubble/geom.py`
- Test: `tests/bubble/test_gates.py`
- Test: `tests/bubble/test_geom.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `BubbleError`
  - `Scores(fit_ssim: float, holdout_ssim: float)`
  - `decide_pass(baseline: Scores, best: Scores, geometry_delta_m: float, outside_unchanged: bool) -> bool`
  - `verdict(prev: Scores, new: Scores) -> str`  # `"better" | "same" | "worse"`
  - `mean_or_default(values: list[float], default: float) -> float`
  - constants `MAX_TICKS = 5`, `FIT_IMPROVE = 0.05`, `FIT_WORSE = 0.01`, `HOLDOUT_DROP = 0.02`, `SSIM_MIN_PIXELS = 16`, `THRESHOLD_SSIM_RECORDED = 0.75`
  - `Camera` fields: `name, role, xyz, heading_deg, pitch_deg, fov_deg, width, height, image`
  - `Grid` fields: `x0, y0, res_m, center_x=0, center_y=0`
  - `project_points(cam, x, y, z) -> (u, v, depth, valid)`
  - `grid_xy(grid, height, width) -> (x, y)` full `(height, width)` arrays
  - `dem_normals(dem, res) -> (nx, ny, nz)`
  - `sample_rgb(image, u, v) -> float array`
  - `sample_dem(dem, row, col) -> float array`
  - `visibility_mask(cam, x, y, z, dem, grid, steps=32, eps=0.05) -> bool array`
  - `distance_fade(dist, start, end) -> float array`

- [ ] **Step 1: Write the failing tests**

```python
# tests/bubble/test_gates.py
from bubble.gates import (
    FIT_IMPROVE,
    HOLDOUT_DROP,
    THRESHOLD_SSIM_RECORDED,
    Scores,
    decide_pass,
    verdict,
)


def test_pass_ignores_recorded_ssim_threshold():
    base = Scores(0.20, 0.40)
    best = Scores(0.30, 0.39)
    assert best.fit_ssim < THRESHOLD_SSIM_RECORDED
    assert best.fit_ssim - base.fit_ssim >= FIT_IMPROVE
    assert base.holdout_ssim - best.holdout_ssim <= HOLDOUT_DROP
    assert decide_pass(base, best, 0.0, True) is True


def test_geometry_or_footprint_or_small_gain_fails():
    base = Scores(0.20, 0.40)
    assert decide_pass(base, Scores(0.90, 0.40), 0.01, True) is False
    assert decide_pass(base, Scores(0.90, 0.40), 0.0, False) is False
    assert decide_pass(base, Scores(0.90, 0.37), 0.0, True) is False
    assert decide_pass(base, Scores(0.24, 0.40), 0.0, True) is False


def test_verdict_labels():
    prev = Scores(0.50, 0.50)
    assert verdict(prev, Scores(0.52, 0.50)) == "better"
    assert verdict(prev, Scores(0.50, 0.50)) == "same"
    assert verdict(prev, Scores(0.48, 0.50)) == "worse"
    assert verdict(prev, Scores(0.50, 0.47)) == "worse"
```

```python
# tests/bubble/test_geom.py
import numpy as np
import pytest

from bubble.geom import Camera, Grid, dem_normals, project_points, visibility_mask, grid_xy


def _cam(**kwargs):
    base = dict(name="c", role="fit", xyz=(0.0, 0.0, 0.0), heading_deg=0.0,
                pitch_deg=0.0, fov_deg=90.0, width=100, height=80, image=None)
    base.update(kwargs)
    return Camera(**base)


def test_optical_axis_and_right_and_nadir():
    cam = _cam()
    u, v, depth, valid = project_points(cam, 0, 10, 0)
    assert bool(valid)
    assert float(depth) == pytest.approx(10)
    assert float(u) == pytest.approx(50)
    assert float(v) == pytest.approx(40)
    u, v, depth, valid = project_points(cam, 2, 10, 0)
    assert float(u) == pytest.approx(60)

    nadir = _cam(xyz=(0, 0, 10), pitch_deg=-90)
    u, v, depth, valid = project_points(nadir, 0, 0, 0)
    assert float(u) == pytest.approx(50)
    assert float(v) == pytest.approx(40)
    assert float(depth) == pytest.approx(10)
    u, v, depth, valid = project_points(nadir, 0, 2, 0)
    assert float(v) == pytest.approx(30)


def test_normals_flat_and_east_slope():
    nx, ny, nz = dem_normals(np.zeros((5, 5)), 1.0)
    assert np.allclose(nx, 0) and np.allclose(ny, 0) and np.allclose(nz, 1)
    slope = np.broadcast_to(0.5 * np.arange(7), (5, 7)).copy()
    nx, ny, nz = dem_normals(slope, 1.0)
    expect = -0.5 / np.hypot(0.5, 1.0)
    assert nx[2, 3] == pytest.approx(expect)
    assert ny[2, 3] == pytest.approx(0)


def test_grid_xy_is_dense_and_north_is_row_zero():
    grid = Grid(x0=-16, y0=16, res_m=1)
    x, y = grid_xy(grid, 33, 33)
    assert x.shape == (33, 33) and y.shape == (33, 33)
    assert x[0, 0] == pytest.approx(-16)
    assert y[0, 0] == pytest.approx(16)
    assert x[14, 20] == pytest.approx(4)
    assert y[14, 20] == pytest.approx(2)


def test_wall_occludes_far_texel_only():
    dem = np.zeros((21, 21))
    dem[:, 7:10] = 10
    grid = Grid(x0=-10, y0=10, res_m=1)
    x, y = grid_xy(grid, 21, 21)
    cam = _cam(xyz=(-8, 0, 1), heading_deg=90, pitch_deg=-5, fov_deg=40, width=32, height=32)
    vis = visibility_mask(cam, x, y, dem, dem, grid)
    assert bool(vis[10, 2]) is True
    assert bool(vis[10, 14]) is False
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pip install -r pipeline/requirements.txt && pytest tests/bubble/test_gates.py tests/bubble/test_geom.py -v`

Expected: collection error, `ModuleNotFoundError: No module named 'bubble'`

- [ ] **Step 3: Write the implementation**

`pipeline/requirements.txt`

```
numpy>=1.26,<3
Pillow>=10,<12
pytest>=8,<9
```

`pytest.ini`

```
[pytest]
pythonpath = pipeline
testpaths = tests
```

`.gitignore`

```
data/
__pycache__/
.pytest_cache/
*.pyc
```

`pipeline/bubble/__init__.py` is an empty file.

`pipeline/bubble/errors.py`

```python
class BubbleError(ValueError):
    pass
```

`pipeline/bubble/gates.py`

```python
from __future__ import annotations

from dataclasses import dataclass

MAX_TICKS = 5
FIT_IMPROVE = 0.05
FIT_WORSE = 0.01
HOLDOUT_DROP = 0.02
SSIM_MIN_PIXELS = 16
THRESHOLD_SSIM_RECORDED = 0.75


@dataclass(frozen=True)
class Scores:
    fit_ssim: float
    holdout_ssim: float


def mean_or_default(values: list[float], default: float) -> float:
    if not values:
        return default
    return float(sum(values) / len(values))


def decide_pass(
    baseline: Scores,
    best: Scores,
    geometry_delta_m: float,
    outside_unchanged: bool,
) -> bool:
    if geometry_delta_m != 0.0 or not outside_unchanged:
        return False
    if best.fit_ssim < baseline.fit_ssim + FIT_IMPROVE:
        return False
    if best.holdout_ssim < baseline.holdout_ssim - HOLDOUT_DROP:
        return False
    return True


def verdict(prev: Scores, new: Scores) -> str:
    if new.fit_ssim < prev.fit_ssim - FIT_WORSE:
        return "worse"
    if new.holdout_ssim < prev.holdout_ssim - HOLDOUT_DROP:
        return "worse"
    if new.fit_ssim > prev.fit_ssim + FIT_WORSE:
        return "better"
    return "same"
```

`pipeline/bubble/geom.py`

```python
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bubble.errors import BubbleError


@dataclass(frozen=True)
class Camera:
    name: str
    role: str
    xyz: tuple[float, float, float]
    heading_deg: float
    pitch_deg: float
    fov_deg: float
    width: int
    height: int
    image: str | None


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    res_m: float
    center_x: float = 0.0
    center_y: float = 0.0


def camera_basis(cam: Camera):
    yaw = np.deg2rad(cam.heading_deg)
    pitch = np.deg2rad(cam.pitch_deg)
    forward = np.array(
        [
            np.sin(yaw) * np.cos(pitch),
            np.cos(yaw) * np.cos(pitch),
            np.sin(pitch),
        ],
        dtype=np.float64,
    )
    right = np.array([np.cos(yaw), -np.sin(yaw), 0.0], dtype=np.float64)
    forward = forward / np.linalg.norm(forward)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    down = down / np.linalg.norm(down)
    return right, down, forward


def project_points(cam: Camera, x, y, z):
    """Pixel x right, y down, depth forward. heading 0 looks north. pitch < 0 looks down."""
    right, down, forward = camera_basis(cam)
    px = np.asarray(x, dtype=np.float64) - cam.xyz[0]
    py = np.asarray(y, dtype=np.float64) - cam.xyz[1]
    pz = np.asarray(z, dtype=np.float64) - cam.xyz[2]
    depth = px * forward[0] + py * forward[1] + pz * forward[2]
    xr = px * right[0] + py * right[1] + pz * right[2]
    yd = px * down[0] + py * down[1] + pz * down[2]
    fx = (cam.width / 2.0) / np.tan(np.deg2rad(cam.fov_deg) / 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = cam.width / 2.0 + fx * xr / depth
        v = cam.height / 2.0 + fx * yd / depth
    valid = np.isfinite(u) & np.isfinite(v) & (depth > 0.05)
    return u, v, depth, valid


def grid_xy(grid: Grid, height: int, width: int):
    cols = np.arange(width, dtype=np.float64)
    rows = np.arange(height, dtype=np.float64)
    x = grid.x0 + cols[None, :] * grid.res_m
    y = grid.y0 - rows[:, None] * grid.res_m
    x = np.broadcast_to(x, (height, width)).copy()
    y = np.broadcast_to(y, (height, width)).copy()
    return x, y


def dem_normals(dem: np.ndarray, res: float):
    dem = np.asarray(dem, dtype=np.float64)
    dzdx = np.empty_like(dem)
    dzdy = np.empty_like(dem)
    dzdx[:, 1:-1] = (dem[:, 2:] - dem[:, :-2]) / (2 * res)
    dzdx[:, 0] = (dem[:, 1] - dem[:, 0]) / res
    dzdx[:, -1] = (dem[:, -1] - dem[:, -2]) / res
    dzdy[1:-1, :] = (dem[:-2, :] - dem[2:, :]) / (2 * res)
    dzdy[0, :] = (dem[0, :] - dem[1, :]) / res
    dzdy[-1, :] = (dem[-2, :] - dem[-1, :]) / res
    nx = -dzdx
    ny = -dzdy
    nz = np.ones_like(dem)
    nrm = np.sqrt(nx * nx + ny * ny + nz * nz)
    return nx / nrm, ny / nrm, nz / nrm


def sample_rgb(image: np.ndarray, u, v):
    rgb = image
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[..., None], 3, axis=2)
    ih, iw = rgb.shape[:2]
    x = np.clip(np.asarray(u, dtype=np.float64), 0, iw - 1.001)
    y = np.clip(np.asarray(v, dtype=np.float64), 0, ih - 1.001)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, iw - 1)
    y1 = np.clip(y0 + 1, 0, ih - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    c00 = rgb[y0, x0].astype(np.float64)
    c10 = rgb[y0, x1].astype(np.float64)
    c01 = rgb[y1, x0].astype(np.float64)
    c11 = rgb[y1, x1].astype(np.float64)
    return (
        c00 * (1 - fx) * (1 - fy)
        + c10 * fx * (1 - fy)
        + c01 * (1 - fx) * fy
        + c11 * fx * fy
    )


def sample_dem(dem, row, col):
    height, width = dem.shape
    r = np.clip(row, 0, height - 1.001)
    c = np.clip(col, 0, width - 1.001)
    r0 = np.floor(r).astype(np.int32)
    c0 = np.floor(c).astype(np.int32)
    r1 = np.clip(r0 + 1, 0, height - 1)
    c1 = np.clip(c0 + 1, 0, width - 1)
    fr = r - r0
    fc = c - c0
    return (
        dem[r0, c0] * (1 - fr) * (1 - fc)
        + dem[r0, c1] * (1 - fr) * fc
        + dem[r1, c0] * fr * (1 - fc)
        + dem[r1, c1] * fr * fc
    )


def visibility_mask(cam: Camera, x, y, z, dem, grid: Grid, steps: int = 32, eps: float = 0.05):
    height, width = dem.shape
    ox, oy, oz = cam.xyz
    ts = np.linspace(0.05, 0.95, steps, dtype=np.float64)
    sx = ox + ts[:, None, None] * (x[None, :, :] - ox)
    sy = oy + ts[:, None, None] * (y[None, :, :] - oy)
    sz = oz + ts[:, None, None] * (z[None, :, :] - oz)
    col = (sx - grid.x0) / grid.res_m
    row = (grid.y0 - sy) / grid.res_m
    inside = (row >= 0) & (row <= height - 1) & (col >= 0) & (col <= width - 1)
    terrain = sample_dem(dem, row, col)
    blocked = inside & (terrain > sz + eps)
    return ~blocked.any(axis=0)


def distance_fade(dist, start: float, end: float):
    if end <= start:
        raise BubbleError("fade_end_m must be greater than fade_start_m")
    t = np.clip((dist - start) / (end - start), 0, 1)
    s = t * t * (3 - 2 * t)
    return 1 - s
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/bubble/test_gates.py tests/bubble/test_geom.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/requirements.txt pytest.ini .gitignore pipeline/bubble tests/bubble
git commit -m "$(cat <<'EOF'
Add bubble camera geometry and the pass gate.

The recorded SSIM threshold stays in the metrics file and is not the pass rule.

EOF
)"
```

---

### Task 2: Texture bake

**Files:**
- Create: `pipeline/bubble/compose.py`
- Test: `tests/bubble/test_compose.py`

**Interfaces:**
- Consumes: `Camera`, `Grid`, `RefineParams` is defined in this task's test via a local stand-in until Task 5. `bake_texture` takes a structural object with `opacity, fade_start_m, fade_end_m, angle_power, luminance_gain, luminance_bias, enabled_frames`.
- Produces:
  - `FRONT_CUTOFF = 0.05`
  - `as_rgb(image) -> uint8 HxWx3`
  - `bake_texture(dem, ortho, grid, cameras, images, params, poses_trusted) -> (uint8 texture, bool touched)`

- [ ] **Step 1: Write the failing test**

```python
# tests/bubble/test_compose.py
from dataclasses import dataclass

import numpy as np

from bubble.compose import bake_texture
from bubble.errors import BubbleError
from bubble.geom import Camera, Grid


@dataclass(frozen=True)
class P:
    opacity: float = 1.0
    fade_start_m: float = 30.0
    fade_end_m: float = 40.0
    angle_power: float = 0.0
    luminance_gain: float = 1.0
    luminance_bias: float = 0.0
    enabled_frames: tuple = ("fit_0",)


def test_nadir_photo_paints_center_and_leaves_corner():
    dem = np.zeros((9, 9), np.float32)
    grid = Grid(-4, 4, 1, 0, 0)
    ortho = np.full((9, 9, 3), 100, np.uint8)
    cam = Camera("fit_0", "fit", (0, 0, 20), 0, -90, 20, 32, 32, "refs/fit_0.png")
    red = np.zeros((32, 32, 3), np.uint8)
    red[:] = (255, 0, 0)
    tex, touched = bake_texture(dem, ortho, grid, (cam,), {"fit_0": red}, P(), True)
    assert tex[4, 4].tolist() == [255, 0, 0]
    assert tex[0, 0].tolist() == [100, 100, 100]
    assert bool(touched[4, 4]) is True
    assert bool(touched[0, 0]) is False


def test_camera_under_the_mesh_paints_nothing():
    dem = np.zeros((9, 9), np.float32)
    grid = Grid(-4, 4, 1, 0, 0)
    ortho = np.full((9, 9, 3), 100, np.uint8)
    cam = Camera("fit_0", "fit", (0, 0, -5), 0, 90, 40, 32, 32, "x")
    red = np.zeros((32, 32, 3), np.uint8)
    red[:] = (255, 0, 0)
    params = P(fade_start_m=0.1, fade_end_m=30)
    tex, touched = bake_texture(dem, ortho, grid, (cam,), {"fit_0": red}, params, True)
    assert int(touched.sum()) == 0
    assert np.array_equal(tex, ortho)


def test_disabled_frames_match_ortho_bytes():
    dem = np.zeros((4, 4), np.float32)
    grid = Grid(0, 3, 1)
    ortho = np.full((4, 4, 3), 40, np.uint8)
    tex, touched = bake_texture(dem, ortho, grid, (), {}, P(enabled_frames=()), True)
    assert int(touched.sum()) == 0
    assert np.array_equal(tex, ortho)


def test_untrusted_poses_refuse_to_project():
    dem = np.zeros((4, 4), np.float32)
    grid = Grid(0, 3, 1)
    ortho = np.full((4, 4, 3), 40, np.uint8)
    cam = Camera("fit_0", "fit", (0, 0, 10), 0, -90, 40, 8, 8, "x")
    try:
        bake_texture(dem, ortho, grid, (cam,), {"fit_0": np.zeros((8, 8, 3), np.uint8)}, P(), False)
        raised = False
    except BubbleError as exc:
        raised = True
        assert "poses_trusted" in str(exc)
    assert raised
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `pytest tests/bubble/test_compose.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'bubble.compose'`

- [ ] **Step 3: Write `pipeline/bubble/compose.py`**

```python
from __future__ import annotations

import numpy as np

from bubble.errors import BubbleError
from bubble.geom import distance_fade, dem_normals, grid_xy, project_points, sample_rgb, visibility_mask

FRONT_CUTOFF = 0.05


def as_rgb(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return np.repeat(image[..., None], 3, axis=2)
    if image.shape[2] != 3:
        raise BubbleError("ortho must be gray or RGB")
    return image


def bake_texture(dem, ortho, grid, cameras, images, params, poses_trusted: bool):
    height, width = dem.shape
    if ortho.shape[:2] != (height, width):
        raise BubbleError(f"ortho shape {ortho.shape[:2]} != dem {(height, width)}")
    base = as_rgb(ortho)
    out = base.astype(np.float64).copy()
    x, y = grid_xy(grid, height, width)
    z = np.asarray(dem, dtype=np.float64)
    nx, ny, nz = dem_normals(dem, grid.res_m)
    touched = np.zeros((height, width), dtype=bool)
    if params.enabled_frames and not poses_trusted:
        raise BubbleError("poses_trusted is false; refusing to project")
    enabled = [cam for cam in cameras if cam.name in params.enabled_frames]
    enabled.sort(key=lambda cam: cam.name)
    for cam in enabled:
        if cam.role != "fit":
            raise BubbleError(f"cannot enable {cam.role} camera '{cam.name}'")
        if cam.name not in images:
            raise BubbleError(f"missing image for '{cam.name}'")
        u, v, _depth, valid = project_points(cam, x, y, z)
        in_image = valid & (u >= 0) & (v >= 0) & (u <= cam.width - 1) & (v <= cam.height - 1)
        visible = visibility_mask(cam, x, y, z, dem, grid) & in_image
        to_x = cam.xyz[0] - x
        to_y = cam.xyz[1] - y
        to_z = cam.xyz[2] - z
        dist = np.maximum(np.sqrt(to_x * to_x + to_y * to_y + to_z * to_z), 1e-6)
        facing = np.clip((nx * to_x + ny * to_y + nz * to_z) / dist, 0, 1)
        if params.angle_power == 0:
            angle = np.where(facing > FRONT_CUTOFF, 1.0, 0.0)
        else:
            angle = np.where(facing > FRONT_CUTOFF, facing ** params.angle_power, 0.0)
        fade = distance_fade(dist, params.fade_start_m, params.fade_end_m)
        weight = params.opacity * angle * fade
        weight = np.where(visible, weight, 0.0)
        sampled = sample_rgb(images[cam.name], u, v)
        sampled = np.clip(sampled * params.luminance_gain + params.luminance_bias, 0, 255)
        use = weight >= 1e-6
        mixed = out * (1 - weight[..., None]) + sampled * weight[..., None]
        out = np.where(use[..., None], mixed, out)
        touched |= use
    texture = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    return texture, touched
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/bubble/test_compose.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/bubble/compose.py tests/bubble/test_compose.py
git commit -m "$(cat <<'EOF'
Bake posed photos onto a frozen ortho with distance fade.

Front faces inside the fade get the photo. Back faces and untrusted poses stay on the ortho.

EOF
)"
```

---

### Task 3: Rasterizer, SSIM, compare strips

**Files:**
- Create: `pipeline/bubble/render.py`
- Create: `pipeline/bubble/score.py`
- Test: `tests/bubble/test_render.py`
- Test: `tests/bubble/test_score.py`

**Interfaces:**
- Consumes: `Camera`, `Grid`, `project_points`, `grid_xy`, `sample_rgb`, `Scores`, `mean_or_default`, `SSIM_MIN_PIXELS`
- Produces:
  - `MAGENTA = (255, 0, 255)`
  - `render_mesh(dem, texture, cam, grid) -> uint8 HxWx3`
  - `ssim_masked(a, b) -> float`
  - `score_views(per_camera: dict[str, float], cameras) -> Scores`
  - `write_compare(path, ref, baseline, refined) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/bubble/test_render.py
import numpy as np

from bubble.geom import Camera, Grid
from bubble.render import MAGENTA, render_mesh


def test_nadir_center_reads_the_red_patch():
    dem = np.zeros((17, 17), np.float32)
    grid = Grid(-8, 8, 1)
    texture = np.zeros((17, 17, 3), np.uint8)
    texture[:] = (0, 255, 0)
    texture[6:11, 6:11] = (255, 0, 0)
    cam = Camera("n", "nadir", (0, 0, 30), 0, -90, 30, 48, 48, None)
    image = render_mesh(dem, texture, cam, grid)
    assert image[24, 24, 0] > 200
    assert image[24, 24, 1] < 40
```

```python
# tests/bubble/test_score.py
from pathlib import Path

import numpy as np
from PIL import Image

from bubble.geom import Camera
from bubble.score import score_views, ssim_masked, write_compare


def test_ssim_identical_is_one_and_black_white_is_low():
    rgb = np.zeros((32, 32, 3), np.uint8)
    rgb[:] = (20, 40, 60)
    assert ssim_masked(rgb, rgb.copy()) == 1.0
    white = np.full_like(rgb, 255)
    assert ssim_masked(np.zeros_like(rgb), white) < 0.1


def test_ssim_tiny_mask_is_zero():
    a = np.zeros((8, 8, 3), np.uint8)
    a[:] = (255, 0, 255)
    b = a.copy()
    a[0, 0] = (1, 2, 3)
    b[0, 0] = (1, 2, 3)
    assert ssim_masked(a, b) == 0.0


def test_score_views_skips_nadir_and_defaults_empty_holdout():
    cameras = (
        Camera("nadir", "nadir", (0, 0, 1), 0, -90, 40, 8, 8, None),
        Camera("fit_0", "fit", (0, 0, 1), 0, -20, 40, 8, 8, "a.png"),
    )
    scores = score_views({"fit_0": 0.4, "nadir": 0.0}, cameras)
    assert scores.fit_ssim == 0.4
    assert scores.holdout_ssim == 1.0


def test_compare_strip_is_three_panels(tmp_path: Path):
    panel = np.zeros((4, 5, 3), np.uint8)
    panel[:] = (1, 2, 3)
    other = panel.copy()
    other[:] = (4, 5, 6)
    path = tmp_path / "c.png"
    write_compare(path, panel, other, panel)
    strip = np.asarray(Image.open(path).convert("RGB"))
    assert strip.shape == (4, 15, 3)
    assert np.array_equal(strip[:, :5], panel)
    assert np.array_equal(strip[:, 5:10], other)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `pytest tests/bubble/test_render.py tests/bubble/test_score.py -v`

Expected: FAIL, `ModuleNotFoundError` for `bubble.render` or `bubble.score`

- [ ] **Step 3: Write the renderer and scorer**

`pipeline/bubble/render.py`

```python
from __future__ import annotations

import numpy as np

from bubble.geom import grid_xy, project_points, sample_rgb

MAGENTA = (255, 0, 255)


def sample_texture_uv(texture, u, v):
    height, width = texture.shape[:2]
    col = np.asarray(u, dtype=np.float64) * (width - 1)
    row = (1.0 - np.asarray(v, dtype=np.float64)) * (height - 1)
    return sample_rgb(texture, col, row)


def render_mesh(dem, texture, cam, grid):
    height, width = dem.shape
    xs, ys = grid_xy(grid, height, width)
    u, v, depth, valid = project_points(cam, xs, ys, dem)
    image = np.zeros((cam.height, cam.width, 3), dtype=np.float64)
    image[:] = MAGENTA
    zbuf = np.full((cam.height, cam.width), np.inf)
    for r in range(height - 1):
        for c in range(width - 1):
            tris = (
                ((r, c), (r + 1, c), (r, c + 1)),
                ((r, c + 1), (r + 1, c), (r + 1, c + 1)),
            )
            for tri in tris:
                if any((not bool(valid[rr, cc])) or float(depth[rr, cc]) <= 0.05 for rr, cc in tri):
                    continue
                points = [
                    (float(u[rr, cc]), float(v[rr, cc]), float(depth[rr, cc]))
                    for rr, cc in tri
                ]
                uvs = [(cc / (width - 1), 1 - rr / (height - 1)) for rr, cc in tri]
                _shade(image, zbuf, points[0], points[1], points[2], uvs[0], uvs[1], uvs[2], texture)
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


def _shade(image, zbuf, p0, p1, p2, uv0, uv1, uv2, texture):
    h, w = image.shape[:2]
    minx = max(int(np.floor(min(p0[0], p1[0], p2[0]))), 0)
    maxx = min(int(np.ceil(max(p0[0], p1[0], p2[0]))), w - 1)
    miny = max(int(np.floor(min(p0[1], p1[1], p2[1]))), 0)
    maxy = min(int(np.ceil(max(p0[1], p1[1], p2[1]))), h - 1)
    if minx > maxx or miny > maxy:
        return
    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(denom) < 1e-12:
        return
    gx = np.arange(minx, maxx + 1, dtype=np.float64) + 0.5
    gy = np.arange(miny, maxy + 1, dtype=np.float64) + 0.5
    xx, yy = np.meshgrid(gx, gy)
    w0 = ((p1[1] - p2[1]) * (xx - p2[0]) + (p2[0] - p1[0]) * (yy - p2[1])) / denom
    w1 = ((p2[1] - p0[1]) * (xx - p2[0]) + (p0[0] - p2[0]) * (yy - p2[1])) / denom
    w2 = 1.0 - w0 - w1
    depth = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    mask = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & (depth > 0.05)
    sub_z = zbuf[miny : maxy + 1, minx : maxx + 1]
    mask &= depth < sub_z
    if not np.any(mask):
        return
    uu = w0 * uv0[0] + w1 * uv1[0] + w2 * uv2[0]
    vv = w0 * uv0[1] + w1 * uv1[1] + w2 * uv2[1]
    colors = sample_texture_uv(texture, uu, vv)
    sub = image[miny : maxy + 1, minx : maxx + 1]
    sub[mask] = colors[mask]
    sub_z[mask] = depth[mask]
```

`pipeline/bubble/score.py`

```python
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from bubble.errors import BubbleError
from bubble.gates import SSIM_MIN_PIXELS, Scores, mean_or_default
from bubble.render import MAGENTA

C1 = 0.01 ** 2
C2 = 0.03 ** 2


def luminance(rgb: np.ndarray) -> np.ndarray:
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255.0


def ssim_masked(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.uint8)
    b = np.asarray(b, dtype=np.uint8)
    mask = ~np.all(a == MAGENTA, axis=-1) & ~np.all(b == MAGENTA, axis=-1)
    if int(mask.sum()) < SSIM_MIN_PIXELS:
        return 0.0
    aa = luminance(a)[mask]
    bb = luminance(b)[mask]
    mu_a = float(aa.mean())
    mu_b = float(bb.mean())
    var_a = float(aa.var())
    var_b = float(bb.var())
    cov = float(((aa - mu_a) * (bb - mu_b)).mean())
    num = (2 * mu_a * mu_b + C1) * (2 * cov + C2)
    den = (mu_a ** 2 + mu_b ** 2 + C1) * (var_a + var_b + C2)
    return float(num / den)


def score_views(per_camera: dict[str, float], cameras) -> Scores:
    fit = [per_camera[cam.name] for cam in cameras if cam.role == "fit"]
    hold = [per_camera[cam.name] for cam in cameras if cam.role == "holdout"]
    return Scores(
        fit_ssim=mean_or_default(fit, 0.0),
        holdout_ssim=mean_or_default(hold, 1.0),
    )


def write_compare(path: Path, ref: np.ndarray, baseline: np.ndarray, refined: np.ndarray) -> None:
    if ref.shape != baseline.shape or ref.shape != refined.shape:
        raise BubbleError("compare panels must share a shape")
    strip = np.concatenate([ref, baseline, refined], axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(strip, dtype=np.uint8), mode="RGB").save(path)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/bubble/test_render.py tests/bubble/test_score.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/bubble/render.py pipeline/bubble/score.py tests/bubble/test_render.py tests/bubble/test_score.py
git commit -m "$(cat <<'EOF'
Render the heightfield and score views with masked SSIM.

Compare strips are the photo, the ortho render, and the refined render.

EOF
)"
```

---

### Task 4: glTF mesh with identical geometry

**Files:**
- Create: `pipeline/bubble/gltf.py`
- Test: `tests/bubble/test_gltf.py`

**Interfaces:**
- Consumes: `Grid`, `grid_xy`, `dem_normals`
- Produces:
  - `write_glb(path: Path, dem, texture, grid) -> None`
  - `glb_positions(path: Path) -> float32 (N, 3)`
  - `glb_png(path: Path) -> bytes`
  - `position_delta(path_a: Path, path_b: Path) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/bubble/test_gltf.py
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from bubble.geom import Grid
from bubble.gltf import glb_png, glb_positions, position_delta, write_glb


def test_positions_are_y_up_around_the_bubble_center(tmp_path: Path):
    dem = np.full((2, 2), 5, np.float32)
    grid = Grid(x0=10, y0=20, res_m=1, center_x=10, center_y=20)
    texture = np.zeros((2, 2, 3), np.uint8)
    texture[0, 0] = (255, 0, 0)
    path = tmp_path / "mesh.glb"
    write_glb(path, dem, texture, grid)
    pos = glb_positions(path)
    assert np.allclose(pos[0], [0, 5, 0])
    assert np.allclose(pos[1], [1, 5, 0])
    assert np.allclose(pos[2], [0, 5, 1])
    assert np.allclose(pos[3], [1, 5, 1])
    image = np.asarray(Image.open(BytesIO(glb_png(path))).convert("RGB"))
    assert image[0, 0].tolist() == [255, 0, 0]
    assert path.read_bytes()[:4] == b"glTF"


def test_position_delta_is_zero_for_the_same_heights(tmp_path: Path):
    dem = np.arange(16, dtype=np.float32).reshape(4, 4)
    grid = Grid(0, 3, 1, 0, 0)
    gray = np.full((4, 4, 3), 8, np.uint8)
    red = gray.copy()
    red[:] = (200, 0, 0)
    a = tmp_path / "a.glb"
    b = tmp_path / "b.glb"
    write_glb(a, dem, gray, grid)
    write_glb(b, dem, red, grid)
    assert position_delta(a, b) == 0.0
    assert glb_png(a) != glb_png(b)
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `pytest tests/bubble/test_gltf.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'bubble.gltf'`

- [ ] **Step 3: Write `pipeline/bubble/gltf.py`**

```python
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import json
import struct

import numpy as np
from PIL import Image

from bubble.geom import dem_normals, grid_xy


def _png_bytes(texture: np.ndarray) -> bytes:
    buf = BytesIO()
    Image.fromarray(np.asarray(texture, dtype=np.uint8), mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def write_glb(path: Path, dem, texture, grid) -> None:
    height, width = dem.shape
    xs, ys = grid_xy(grid, height, width)
    pos = np.stack(
        [xs - grid.center_x, np.asarray(dem, dtype=np.float64), -(ys - grid.center_y)],
        axis=-1,
    ).reshape(-1, 3).astype(np.float32)
    nx, ny, nz = dem_normals(dem, grid.res_m)
    nrm = np.stack([nx, nz, -ny], axis=-1).reshape(-1, 3).astype(np.float32)
    nlen = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = (nrm / np.maximum(nlen, 1e-8)).astype(np.float32)
    uu = np.broadcast_to(np.linspace(0, 1, width, dtype=np.float32), (height, width)).copy()
    vv = np.broadcast_to(np.linspace(1, 0, height, dtype=np.float32)[:, None], (height, width)).copy()
    uv = np.stack([uu, vv], axis=-1).reshape(-1, 2).astype(np.float32)
    idx = []
    for r in range(height - 1):
        for c in range(width - 1):
            i00 = r * width + c
            i10 = (r + 1) * width + c
            i01 = r * width + c + 1
            i11 = (r + 1) * width + c + 1
            idx.extend([i00, i10, i01, i01, i10, i11])
    indices = np.asarray(idx, dtype=np.uint32)
    png = _png_bytes(texture)
    blobs = [pos.tobytes(), nrm.tobytes(), uv.tobytes(), indices.tobytes(), png]
    parts = []
    offsets = []
    lengths = []
    cursor = 0
    for blob in blobs:
        offsets.append(cursor)
        lengths.append(len(blob))
        parts.append(blob)
        cursor += len(blob)
        pad = (4 - (len(blob) % 4)) % 4
        parts.append(b"\x00" * pad)
        cursor += pad
    bin_blob = b"".join(parts)
    accessors = [
        {
            "bufferView": 0,
            "componentType": 5126,
            "count": int(pos.shape[0]),
            "type": "VEC3",
            "min": pos.min(axis=0).astype(float).tolist(),
            "max": pos.max(axis=0).astype(float).tolist(),
        },
        {"bufferView": 1, "componentType": 5126, "count": int(pos.shape[0]), "type": "VEC3"},
        {"bufferView": 2, "componentType": 5126, "count": int(pos.shape[0]), "type": "VEC2"},
        {"bufferView": 3, "componentType": 5125, "count": int(indices.size), "type": "SCALAR"},
    ]
    targets = [34962, 34962, 34962, 34963, None]
    views = []
    for i, _blob in enumerate(blobs):
        view = {"buffer": 0, "byteOffset": offsets[i], "byteLength": lengths[i]}
        if targets[i] is not None:
            view["target"] = targets[i]
        views.append(view)
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": views,
        "accessors": accessors,
        "images": [{"bufferView": 4, "mimeType": "image/png"}],
        "samplers": [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}],
        "textures": [{"sampler": 0, "source": 0}],
        "materials": [
            {
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
                        "indices": 3,
                        "material": 0,
                    }
                ]
            }
        ],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - (len(js) % 4)) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_blob)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(js), 0x4E4F534A)
    out += js
    out += struct.pack("<II", len(bin_blob), 0x004E4942)
    out += bin_blob
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def _chunks(path: Path):
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("not a glb")
    offset = 12
    chunks = []
    while offset < len(data):
        length, ctype = struct.unpack_from("<II", data, offset)
        chunks.append((ctype, data[offset + 8 : offset + 8 + length]))
        offset += 8 + length
    meta = json.loads(chunks[0][1].decode("utf-8").rstrip())
    return meta, chunks[1][1]


def glb_positions(path: Path) -> np.ndarray:
    meta, blob = _chunks(path)
    acc = meta["accessors"][meta["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    view = meta["bufferViews"][acc["bufferView"]]
    count = acc["count"] * 3
    arr = np.frombuffer(blob, dtype="<f4", count=count, offset=view["byteOffset"])
    return arr.reshape(-1, 3).copy()


def glb_png(path: Path) -> bytes:
    meta, blob = _chunks(path)
    view = meta["bufferViews"][meta["images"][0]["bufferView"]]
    start = view["byteOffset"]
    return bytes(blob[start : start + view["byteLength"]])


def position_delta(path_a: Path, path_b: Path) -> float:
    a = glb_positions(path_a).astype(np.float64)
    b = glb_positions(path_b).astype(np.float64)
    if a.shape != b.shape:
        return float("inf")
    return float(np.max(np.abs(a - b)))
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/bubble/test_gltf.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/bubble/gltf.py tests/bubble/test_gltf.py
git commit -m "$(cat <<'EOF'
Export the bubble as a Y-up glTF mesh with an embedded PNG.

Two textures of the same DEM produce identical vertex positions.

EOF
)"
```

---

### Task 5: Operators and bundle loader

**Files:**
- Create: `pipeline/bubble/params.py`
- Create: `pipeline/bubble/bundle.py`
- Test: `tests/bubble/test_params.py`
- Test: `tests/bubble/test_bundle.py`

**Interfaces:**
- Consumes: `Camera`, `Grid`, `BubbleError`
- Produces:
  - `RefineParams` with defaults `opacity=0`, `fade_start_m=4`, `fade_end_m=18`, `angle_power=0`, `luminance_gain=1`, `luminance_bias=0`, `enabled_frames=()`
  - `apply_operator(params, name, value, cameras) -> RefineParams`
  - `save_params(path, params)`, `load_params(path) -> RefineParams`
  - `Bundle` fields: `root, dem, ortho, grid, cameras, images, poses_trusted, requires_human, registration_rms_m, bubble_id, size_m`
  - `load_bundle(path: Path) -> Bundle`

Operator names and ranges:

| Operator | Value | Rule |
|---|---|---|
| `set_opacity` | float | `[0, 1]` |
| `set_fade_start_m` | float | `>= 0` and `< fade_end_m` |
| `set_fade_end_m` | float | `> fade_start_m` and `<= 500` |
| `set_angle_power` | float | `[0, 4]` |
| `set_luminance_gain` | float | `[0, 4]` |
| `set_luminance_bias` | float | `[-255, 255]` |
| `enable_frame` | camera name | role must be `fit`; appending an existing name returns the same params |
| `disable_frame` | camera name | drops that name if present |

Any other name raises `BubbleError` matching `unknown operator`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/bubble/test_params.py
import pytest

from bubble.errors import BubbleError
from bubble.geom import Camera
from bubble.params import RefineParams, apply_operator


def _cams():
    return (
        Camera("nadir", "nadir", (0, 0, 1), 0, -90, 40, 8, 8, None),
        Camera("fit_0", "fit", (0, 0, 1), 0, -20, 40, 8, 8, "refs/fit_0.png"),
        Camera("holdout_0", "holdout", (0, 0, 1), 0, -20, 40, 8, 8, "refs/holdout_0.png"),
    )


def test_one_field_changes():
    params = RefineParams()
    nxt = apply_operator(params, "set_opacity", "0.4", _cams())
    assert nxt.opacity == 0.4
    assert nxt.enabled_frames == params.enabled_frames
    assert nxt.fade_end_m == params.fade_end_m
    assert nxt.angle_power == 0.0


def test_enable_fit_and_reject_holdout():
    params = apply_operator(RefineParams(), "enable_frame", "fit_0", _cams())
    assert params.enabled_frames == ("fit_0",)
    again = apply_operator(params, "enable_frame", "fit_0", _cams())
    assert again == params
    with pytest.raises(BubbleError, match="cannot enable holdout"):
        apply_operator(params, "enable_frame", "holdout_0", _cams())


def test_disable_and_unknown_and_range():
    params = apply_operator(RefineParams(enabled_frames=("fit_0", "fit_1")), "disable_frame", "fit_0", _cams())
    assert params.enabled_frames == ("fit_1",)
    with pytest.raises(BubbleError, match="unknown operator"):
        apply_operator(RefineParams(), "sculpt_mesh", "1", _cams())
    with pytest.raises(BubbleError, match="opacity"):
        apply_operator(RefineParams(), "set_opacity", "1.5", _cams())
```

```python
# tests/bubble/test_bundle.py
from pathlib import Path
import json

import numpy as np
import pytest
from PIL import Image

from bubble.bundle import load_bundle
from bubble.errors import BubbleError


def _write(path: Path, cameras: list[dict]) -> None:
    path.mkdir(parents=True)
    np.save(path / "dem.npy", np.zeros((4, 4), np.float32))
    Image.fromarray(np.full((4, 4, 3), 80, np.uint8)).save(path / "ortho.png")
    (path / "grid.json").write_text(json.dumps({
        "x0": -1.5, "y0": 1.5, "res_m": 1, "center_x": 0, "center_y": 0
    }))
    (path / "refs").mkdir()
    Image.fromarray(np.full((8, 8, 3), 10, np.uint8)).save(path / "refs" / "fit_0.png")
    (path / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": False,
        "registration_rms_m": 0.0,
        "bubble_id": "tiny",
        "cameras": cameras,
    }))


def _fit():
    return {
        "name": "fit_0", "role": "fit", "xyz": [0, 0, 2], "heading_deg": 0,
        "pitch_deg": -20, "fov_deg": 50, "width": 8, "height": 8, "image": "refs/fit_0.png",
    }


def _nadir():
    return {
        "name": "nadir", "role": "nadir", "xyz": [0, 0, 20], "heading_deg": 0,
        "pitch_deg": -90, "fov_deg": 40, "width": 8, "height": 8, "image": None,
    }


def test_loads_tiny_bundle(tmp_path: Path):
    bundle_dir = tmp_path / "b"
    _write(bundle_dir, [_nadir(), _fit()])
    bundle = load_bundle(bundle_dir)
    assert bundle.dem.shape == (4, 4)
    assert bundle.size_m == 3
    assert bundle.poses_trusted is True
    assert bundle.images["fit_0"].shape == (8, 8, 3)
    assert [cam.role for cam in bundle.cameras] == ["nadir", "fit"]


def test_requires_one_fit_camera(tmp_path: Path):
    bundle_dir = tmp_path / "b"
    _write(bundle_dir, [_nadir()])
    with pytest.raises(BubbleError, match="fit"):
        load_bundle(bundle_dir)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `pytest tests/bubble/test_params.py tests/bubble/test_bundle.py -v`

Expected: FAIL, `ModuleNotFoundError` for `bubble.params` or `bubble.bundle`

- [ ] **Step 3: Write the modules**

`pipeline/bubble/params.py`

```python
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import json
import math

from bubble.errors import BubbleError


@dataclass(frozen=True)
class RefineParams:
    opacity: float = 0.0
    fade_start_m: float = 4.0
    fade_end_m: float = 18.0
    angle_power: float = 0.0
    luminance_gain: float = 1.0
    luminance_bias: float = 0.0
    enabled_frames: tuple[str, ...] = ()


def _finite(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise BubbleError("value must be finite")
    return number


def apply_operator(params: RefineParams, name: str, value: str, cameras) -> RefineParams:
    if name == "set_opacity":
        number = _finite(value)
        if not 0 <= number <= 1:
            raise BubbleError("opacity out of range")
        return replace(params, opacity=number)
    if name == "set_fade_start_m":
        number = _finite(value)
        if number < 0 or number >= params.fade_end_m:
            raise BubbleError("fade_start_m out of range")
        return replace(params, fade_start_m=number)
    if name == "set_fade_end_m":
        number = _finite(value)
        if number <= params.fade_start_m or number > 500:
            raise BubbleError("fade_end_m out of range")
        return replace(params, fade_end_m=number)
    if name == "set_angle_power":
        number = _finite(value)
        if not 0 <= number <= 4:
            raise BubbleError("angle_power out of range")
        return replace(params, angle_power=number)
    if name == "set_luminance_gain":
        number = _finite(value)
        if not 0 <= number <= 4:
            raise BubbleError("luminance_gain out of range")
        return replace(params, luminance_gain=number)
    if name == "set_luminance_bias":
        number = _finite(value)
        if not -255 <= number <= 255:
            raise BubbleError("luminance_bias out of range")
        return replace(params, luminance_bias=number)
    if name == "enable_frame":
        match = [cam for cam in cameras if cam.name == value]
        if not match:
            raise BubbleError(f"unknown camera '{value}'")
        if match[0].role != "fit":
            raise BubbleError(f"cannot enable {match[0].role} camera '{value}'")
        if value in params.enabled_frames:
            return params
        return replace(params, enabled_frames=params.enabled_frames + (value,))
    if name == "disable_frame":
        return replace(
            params,
            enabled_frames=tuple(item for item in params.enabled_frames if item != value),
        )
    raise BubbleError(f"unknown operator '{name}'")


def save_params(path: Path, params: RefineParams) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "opacity": params.opacity,
        "fade_start_m": params.fade_start_m,
        "fade_end_m": params.fade_end_m,
        "angle_power": params.angle_power,
        "luminance_gain": params.luminance_gain,
        "luminance_bias": params.luminance_bias,
        "enabled_frames": list(params.enabled_frames),
    }, indent=2))


def load_params(path: Path) -> RefineParams:
    raw = json.loads(path.read_text())
    return RefineParams(
        opacity=raw["opacity"],
        fade_start_m=raw["fade_start_m"],
        fade_end_m=raw["fade_end_m"],
        angle_power=raw["angle_power"],
        luminance_gain=raw["luminance_gain"],
        luminance_bias=raw["luminance_bias"],
        enabled_frames=tuple(raw["enabled_frames"]),
    )
```

`pipeline/bubble/bundle.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.errors import BubbleError
from bubble.geom import Camera, Grid

ROLES = {"nadir", "fit", "holdout"}


@dataclass(frozen=True)
class Bundle:
    root: Path
    dem: np.ndarray
    ortho: np.ndarray
    grid: Grid
    cameras: tuple[Camera, ...]
    images: dict
    poses_trusted: bool
    requires_human: bool
    registration_rms_m: float | None
    bubble_id: str
    size_m: float


def _rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def load_bundle(path: Path) -> Bundle:
    path = path.resolve()
    dem = np.load(path / "dem.npy", allow_pickle=False)
    if dem.ndim != 2:
        raise BubbleError("dem.npy must be a 2D array")
    dem = dem.astype(np.float32)
    ortho = _rgb(path / "ortho.png")
    if ortho.shape[:2] != dem.shape:
        raise BubbleError("ortho.png shape does not match dem.npy")
    grid_raw = json.loads((path / "grid.json").read_text())
    grid = Grid(
        x0=float(grid_raw["x0"]),
        y0=float(grid_raw["y0"]),
        res_m=float(grid_raw["res_m"]),
        center_x=float(grid_raw.get("center_x", 0.0)),
        center_y=float(grid_raw.get("center_y", 0.0)),
    )
    if grid.res_m <= 0:
        raise BubbleError("res_m must be positive")
    raw = json.loads((path / "viewpoints.json").read_text())
    cameras = []
    images = {}
    for item in raw["cameras"]:
        if item["role"] not in ROLES:
            raise BubbleError(f"unknown role '{item['role']}'")
        image_name = item["image"]
        cam = Camera(
            name=item["name"],
            role=item["role"],
            xyz=(float(item["xyz"][0]), float(item["xyz"][1]), float(item["xyz"][2])),
            heading_deg=float(item["heading_deg"]),
            pitch_deg=float(item["pitch_deg"]),
            fov_deg=float(item["fov_deg"]),
            width=int(item["width"]),
            height=int(item["height"]),
            image=image_name,
        )
        if cam.fov_deg <= 0 or cam.width < 2 or cam.height < 2:
            raise BubbleError(f"bad camera '{cam.name}'")
        cameras.append(cam)
        if image_name is not None:
            image = _rgb(path / image_name)
            if image.shape[1] != cam.width or image.shape[0] != cam.height:
                raise BubbleError(
                    f"{cam.name} image is {image.shape[1]}x{image.shape[0]}, "
                    f"camera says {cam.width}x{cam.height}"
                )
            images[cam.name] = image
        elif cam.role != "nadir":
            raise BubbleError(f"{cam.role} camera '{cam.name}' needs an image")
    if not any(cam.role == "nadir" for cam in cameras):
        raise BubbleError("bundle needs one nadir camera")
    if not any(cam.role == "fit" for cam in cameras):
        raise BubbleError("bundle needs a fit camera")
    height, width = dem.shape
    size_m = float(max((height - 1) * grid.res_m, (width - 1) * grid.res_m))
    rms = raw["registration_rms_m"]
    return Bundle(
        root=path,
        dem=dem,
        ortho=ortho,
        grid=grid,
        cameras=tuple(cameras),
        images=images,
        poses_trusted=bool(raw["poses_trusted"]),
        requires_human=bool(raw["requires_human"]),
        registration_rms_m=None if rms is None else float(rms),
        bubble_id=str(raw["bubble_id"]),
        size_m=size_m,
    )
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/bubble/test_params.py tests/bubble/test_bundle.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/bubble/params.py pipeline/bubble/bundle.py tests/bubble/test_params.py tests/bubble/test_bundle.py
git commit -m "$(cat <<'EOF'
Load a photo bundle and apply one refine parameter per call.

Holdout cameras can be scored and cannot be projected.

EOF
)"
```

---

### Task 6: Synthetic mound fixture

**Files:**
- Create: `pipeline/bubble/fixture.py`
- Test: `tests/bubble/test_fixture.py`

**Interfaces:**
- Consumes: `render_mesh`, `Camera`, `Grid`, `project_points`
- Produces:
  - `PEAK_ROW = 14`, `PEAK_COL = 20`
  - `make_fixture(out: Path) -> Path`
  - Writes `dem.npy`, `ortho.png`, `albedo.npy`, `grid.json`, `viewpoints.json`, `refs/fit_0.png`, `refs/holdout_0.png`
  - Viewpoint flags: `poses_trusted` true, `requires_human` false, `registration_rms_m` 0, `bubble_id` `fixture_mound`

Albedo is a 2 m checker, `(220, 180, 80)` and `(40, 50, 70)`. Ortho is that checker after 6 box blurs of radius 3. Reference photos are `render_mesh` of the albedo from `fit_0` and `holdout_0`. DEM is `0.15 * (col - width/2) / width` plus `1.2 * exp(-((col-20)^2 + (row-14)^2) / 18)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bubble/test_fixture.py
from pathlib import Path

import numpy as np
import pytest

from bubble.bundle import load_bundle
from bubble.fixture import PEAK_COL, PEAK_ROW, make_fixture
from bubble.geom import project_points


def test_mound_cameras_see_the_peak(tmp_path: Path):
    out = make_fixture(tmp_path / "bundle")
    bundle = load_bundle(out)
    assert bundle.bubble_id == "fixture_mound"
    assert bundle.requires_human is False
    assert bundle.poses_trusted is True
    assert (out / "albedo.npy").exists()
    peak_z = float(bundle.dem[PEAK_ROW, PEAK_COL])
    assert peak_z == pytest.approx(1.2159, abs=1e-3)
    x = bundle.grid.x0 + PEAK_COL * bundle.grid.res_m
    y = bundle.grid.y0 - PEAK_ROW * bundle.grid.res_m
    for cam in bundle.cameras:
        if cam.role == "nadir":
            continue
        u, v, _depth, valid = project_points(cam, x, y, peak_z)
        assert bool(valid)
        assert 0 <= float(u) < cam.width
        assert 0 <= float(v) < cam.height
    ortho = bundle.ortho.astype(float)
    albedo = np.load(out / "albedo.npy")
    # The blurred ortho is far from the checker at the peak.
    assert np.linalg.norm(ortho[PEAK_ROW, PEAK_COL] - albedo[PEAK_ROW, PEAK_COL]) > 80
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `pytest tests/bubble/test_fixture.py::test_mound_cameras_see_the_peak -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'bubble.fixture'`

- [ ] **Step 3: Write `pipeline/bubble/fixture.py`**

```python
from __future__ import annotations

from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.geom import Camera, Grid
from bubble.render import render_mesh

PEAK_ROW = 14
PEAK_COL = 20


def box_blur(image: np.ndarray, radius: int) -> np.ndarray:
    k = 2 * radius + 1
    pad = np.pad(image.astype(np.float64), ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    cumulative = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    cumulative = np.pad(cumulative, ((1, 0), (1, 0), (0, 0)))
    window = (
        cumulative[k:, k:, :]
        - cumulative[:-k, k:, :]
        - cumulative[k:, :-k, :]
        + cumulative[:-k, :-k, :]
    )
    return window / (k * k)


def _save_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB").save(path)


def make_fixture(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    height = width = 33
    yy, xx = np.mgrid[0:height, 0:width]
    dem = (0.15 * (xx - width / 2) / width).astype(np.float64)
    dem += 1.2 * np.exp(-((xx - PEAK_COL) ** 2 + (yy - PEAK_ROW) ** 2) / 18.0)
    checks = ((yy // 2 + xx // 2) % 2).astype(bool)
    albedo = np.zeros((height, width, 3), np.uint8)
    albedo[checks] = (220, 180, 80)
    albedo[~checks] = (40, 50, 70)
    ortho_f = albedo.astype(np.float64)
    for _ in range(6):
        ortho_f = box_blur(ortho_f, 3)
    ortho = np.clip(np.rint(ortho_f), 0, 255).astype(np.uint8)
    grid = Grid(x0=-16, y0=16, res_m=1, center_x=0, center_y=0)
    fit = Camera("fit_0", "fit", (1.0, -1.0, 2.4), 45, -20, 60, 96, 72, "refs/fit_0.png")
    hold = Camera("holdout_0", "holdout", (2.5, -2.5, 2.6), 18, -22, 60, 96, 72, "refs/holdout_0.png")
    nadir = Camera("nadir", "nadir", (0.0, 0.0, 40.0), 0, -90, 50, 64, 64, None)
    np.save(out / "dem.npy", dem.astype(np.float32))
    np.save(out / "albedo.npy", albedo)
    _save_rgb(out / "ortho.png", ortho)
    _save_rgb(out / "refs" / "fit_0.png", render_mesh(dem, albedo, fit, grid))
    _save_rgb(out / "refs" / "holdout_0.png", render_mesh(dem, albedo, hold, grid))
    (out / "grid.json").write_text(json.dumps({
        "x0": grid.x0, "y0": grid.y0, "res_m": grid.res_m,
        "center_x": grid.center_x, "center_y": grid.center_y,
    }, indent=2))

    def dump(cam: Camera) -> dict:
        return {
            "name": cam.name,
            "role": cam.role,
            "xyz": list(cam.xyz),
            "heading_deg": cam.heading_deg,
            "pitch_deg": cam.pitch_deg,
            "fov_deg": cam.fov_deg,
            "width": cam.width,
            "height": cam.height,
            "image": cam.image,
        }

    (out / "viewpoints.json").write_text(json.dumps({
        "frame": "site_enu",
        "poses_trusted": True,
        "requires_human": False,
        "registration_rms_m": 0.0,
        "bubble_id": "fixture_mound",
        "cameras": [dump(nadir), dump(fit), dump(hold)],
    }, indent=2))
    return out
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/bubble/test_fixture.py -v`

Expected: PASS. Runtime is about a second because it renders two photos.

- [ ] **Step 5: Commit**

```bash
git add pipeline/bubble/fixture.py tests/bubble/test_fixture.py
git commit -m "$(cat <<'EOF'
Add a synthetic mound whose photos are renders of a known checker.

The fixture is the closed-loop stand-in for a posed Navcam frame.

EOF
)"
```

---

### Task 7: One tick, revert, and the state log

**Files:**
- Create: `pipeline/bubble/tick.py`
- Test: `tests/bubble/test_tick.py`

**Interfaces:**
- Consumes: every symbol from Tasks 1–6 listed in those Produces blocks
- Produces:
  - `ensure_initialized(bundle, work_dir) -> dict`
  - `run_tick(bundle_dir: Path, work_dir: Path, operator: str, value: str) -> dict`
  - `format_tick(tick, operator, value, before: Scores, after: Scores, result, reverted) -> str`
  - Work files: `params.json`, `metrics.json`, `bubble_block.json`, `LOOP_STATE.md`, `baseline/mesh.glb`, `baseline/texture.png`, `refined/mesh.glb`, `refined/texture.png`, `cache/<camera>.png`, `compare/<camera>.png`

`metrics.json` keys: `threshold_ssim`, `ssim`, `lpips`, `pass`, `numeric_pass`, `active_recommendation`, `geometry_delta_m_max`, `registration_rms_m`, `holdout_ssim`, `fit_ssim`, `baseline_fit_ssim`, `baseline_holdout_ssim`, `human_verdict`, `awaiting_human`, `outside_unchanged`, `iterations`, `bubble_id`, `requires_human`, `size_m`, `center_x`, `center_y`.

`lpips` is null. `threshold_ssim` is 0.75. A worse tick sets that iteration's `reverted` true and leaves `params.json` and `refined/mesh.glb` on the previous values. The sixth tick raises `BubbleError` matching `tick cap`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/bubble/test_tick.py
from pathlib import Path
import json

import pytest

from bubble.errors import BubbleError
from bubble.fixture import make_fixture
from bubble.params import load_params
from bubble.tick import format_tick, run_tick
from bubble.gates import Scores


def test_format_tick_names_the_edit():
    text = format_tick(2, "enable_frame", "fit_0", Scores(0.1, 0.2), Scores(0.8, 0.4), "better", False)
    assert "Tick 2" in text
    assert "enable_frame fit_0" in text
    assert "better" in text


def test_enable_improves_and_black_gain_reverts(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    work = tmp_path / "work"
    run_tick(bundle, work, "set_opacity", "1")
    metrics = run_tick(bundle, work, "enable_frame", "fit_0")
    assert metrics["iterations"][1]["result"] == "better"
    assert metrics["iterations"][1]["reverted"] is False
    assert metrics["fit_ssim"] > metrics["baseline_fit_ssim"] + 0.05
    kept = metrics["fit_ssim"]
    metrics = run_tick(bundle, work, "set_luminance_gain", "0")
    assert metrics["iterations"][-1]["reverted"] is True
    assert metrics["iterations"][-1]["result"] == "worse"
    assert load_params(work / "params.json").luminance_gain == 1.0
    assert metrics["fit_ssim"] == pytest.approx(kept)
    assert "reverted" in (work / "LOOP_STATE.md").read_text()


def test_sixth_tick_is_refused(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    work = tmp_path / "work"
    run_tick(bundle, work, "set_opacity", "1")
    metrics = json.loads((work / "metrics.json").read_text())
    metrics["iterations"] = [{"tick": i, "operator": "set_opacity", "value": "1",
                              "fit_ssim": 0, "holdout_ssim": 1, "result": "same", "reverted": False}
                             for i in range(1, 6)]
    (work / "metrics.json").write_text(json.dumps(metrics))
    with pytest.raises(BubbleError, match="tick cap"):
        run_tick(bundle, work, "set_opacity", "1")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `pytest tests/bubble/test_tick.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'bubble.tick'`

- [ ] **Step 3: Write `pipeline/bubble/tick.py`**

```python
from __future__ import annotations

from pathlib import Path
import json

import numpy as np
from PIL import Image

from bubble.bundle import Bundle, load_bundle
from bubble.compose import as_rgb, bake_texture
from bubble.errors import BubbleError
from bubble.gates import MAX_TICKS, THRESHOLD_SSIM_RECORDED, Scores, decide_pass, verdict
from bubble.gltf import position_delta, write_glb
from bubble.params import RefineParams, apply_operator, load_params, save_params
from bubble.render import render_mesh
from bubble.score import score_views, ssim_masked, write_compare


def format_tick(tick, operator, value, before: Scores, after: Scores, result: str, reverted: bool) -> str:
    revert_note = "; reverted" if reverted else ""
    return "\n".join([
        f"## Tick {tick} | operator {operator} {value}",
        "### Gaps vs ref",
        f"- fit SSIM {before.fit_ssim:.4f}; holdout SSIM {before.holdout_ssim:.4f}",
        "- compare PNGs are the visual judge; these numbers are the revert gate",
        "### Edit made",
        f"- {operator} {value}",
        "### Render result",
        f"- {result} on fit_ssim ({before.fit_ssim:.4f} -> {after.fit_ssim:.4f}); "
        f"holdout {before.holdout_ssim:.4f} -> {after.holdout_ssim:.4f}{revert_note}",
        "### Next",
        "- stay" if reverted else "- continue",
        "",
    ])


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _save_rgb(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(image, dtype=np.uint8), mode="RGB").save(path)


def _load_metrics(work_dir: Path) -> dict:
    return json.loads((work_dir / "metrics.json").read_text())


def render_all(bundle: Bundle, texture: np.ndarray) -> dict[str, np.ndarray]:
    return {cam.name: render_mesh(bundle.dem, texture, cam, bundle.grid) for cam in bundle.cameras}


def score_images(bundle: Bundle, rendered: dict[str, np.ndarray]) -> Scores:
    per = {}
    for cam in bundle.cameras:
        if cam.role == "nadir" or cam.image is None:
            continue
        per[cam.name] = ssim_masked(rendered[cam.name], bundle.images[cam.name])
    return score_views(per, bundle.cameras)


def write_views(bundle: Bundle, work_dir: Path, refined_images: dict[str, np.ndarray]) -> None:
    for cam in bundle.cameras:
        base = np.asarray(Image.open(work_dir / "cache" / f"{cam.name}.png").convert("RGB"))
        if cam.image is None:
            ref = base
        else:
            ref = bundle.images[cam.name]
        write_compare(work_dir / "compare" / f"{cam.name}.png", ref, base, refined_images[cam.name])


def fresh_metrics(bundle: Bundle, baseline: Scores) -> dict:
    return {
        "threshold_ssim": THRESHOLD_SSIM_RECORDED,
        "ssim": baseline.fit_ssim,
        "lpips": None,
        "pass": False,
        "numeric_pass": False,
        "active_recommendation": "baseline",
        "geometry_delta_m_max": 0.0,
        "registration_rms_m": bundle.registration_rms_m,
        "holdout_ssim": baseline.holdout_ssim,
        "fit_ssim": baseline.fit_ssim,
        "baseline_fit_ssim": baseline.fit_ssim,
        "baseline_holdout_ssim": baseline.holdout_ssim,
        "human_verdict": "pending",
        "awaiting_human": bundle.requires_human,
        "outside_unchanged": True,
        "iterations": [],
        "bubble_id": bundle.bubble_id,
        "requires_human": bundle.requires_human,
        "size_m": bundle.size_m,
        "center_x": bundle.grid.center_x,
        "center_y": bundle.grid.center_y,
    }


def write_block(work_dir: Path, metrics: dict) -> None:
    block = {
        "id": metrics["bubble_id"],
        "size_m": metrics["size_m"],
        "center_x": metrics["center_x"],
        "center_y": metrics["center_y"],
        "baseline_mesh": "/bubble/baseline/mesh.glb",
        "refined_mesh": "/bubble/refined/mesh.glb",
        "active": metrics["active_recommendation"],
        "metrics": metrics,
    }
    _dump(work_dir / "bubble_block.json", block)


def update_gates(bundle: Bundle, work_dir: Path, metrics: dict) -> dict:
    params = load_params(work_dir / "params.json")
    texture, touched = bake_texture(
        bundle.dem, bundle.ortho, bundle.grid, bundle.cameras, bundle.images,
        params, bundle.poses_trusted,
    )
    ortho = as_rgb(bundle.ortho)
    outside = True if not np.any(~touched) else bool(np.array_equal(texture[~touched], ortho[~touched]))
    delta = position_delta(work_dir / "baseline" / "mesh.glb", work_dir / "refined" / "mesh.glb")
    metrics["outside_unchanged"] = outside
    metrics["geometry_delta_m_max"] = delta
    metrics["ssim"] = metrics["fit_ssim"]
    numeric = decide_pass(
        Scores(metrics["baseline_fit_ssim"], metrics["baseline_holdout_ssim"]),
        Scores(metrics["fit_ssim"], metrics["holdout_ssim"]),
        delta,
        outside,
    )
    metrics["numeric_pass"] = numeric
    verdict_name = metrics.get("human_verdict")
    if verdict_name == "accept":
        metrics["pass"] = True
        metrics["active_recommendation"] = "refined"
        metrics["awaiting_human"] = False
    elif verdict_name == "reject":
        metrics["pass"] = False
        metrics["active_recommendation"] = "baseline"
        metrics["awaiting_human"] = False
    elif bundle.requires_human:
        metrics["human_verdict"] = "pending"
        metrics["awaiting_human"] = True
        metrics["pass"] = False
        metrics["active_recommendation"] = "baseline"
    else:
        metrics["human_verdict"] = "auto_gate"
        metrics["awaiting_human"] = False
        metrics["pass"] = numeric
        metrics["active_recommendation"] = "refined" if numeric else "baseline"
    _dump(work_dir / "metrics.json", metrics)
    write_block(work_dir, metrics)
    return metrics


def _write_pair(bundle: Bundle, work_dir: Path, texture: np.ndarray, kind: str) -> None:
    _save_rgb(work_dir / kind / "texture.png", texture)
    write_glb(work_dir / kind / "mesh.glb", bundle.dem, texture, bundle.grid)


def ensure_initialized(bundle: Bundle, work_dir: Path) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = work_dir / "metrics.json"
    if metrics_path.exists():
        return _load_metrics(work_dir)
    params = RefineParams()
    texture, _touched = bake_texture(
        bundle.dem, bundle.ortho, bundle.grid, bundle.cameras, bundle.images, params, bundle.poses_trusted
    )
    images = render_all(bundle, texture)
    for name, image in images.items():
        _save_rgb(work_dir / "cache" / f"{name}.png", image)
    _write_pair(bundle, work_dir, texture, "baseline")
    _write_pair(bundle, work_dir, texture, "refined")
    write_views(bundle, work_dir, images)
    save_params(work_dir / "params.json", params)
    scores = score_images(bundle, images)
    metrics = fresh_metrics(bundle, scores)
    header = (
        "# Bubble refine loop\n\n"
        "Photos are judged from `compare/`. Fit SSIM checks that a projected photo "
        "reappears from its own camera. It does not prove the pose is located on Mars. "
        "`human_verdict` is that check.\n\n"
    )
    (work_dir / "LOOP_STATE.md").write_text(header)
    return update_gates(bundle, work_dir, metrics)


def run_tick(bundle_dir: Path, work_dir: Path, operator: str, value: str) -> dict:
    bundle = load_bundle(bundle_dir)
    metrics = ensure_initialized(bundle, work_dir)
    if len(metrics["iterations"]) >= MAX_TICKS:
        raise BubbleError("tick cap 5 reached")
    params = load_params(work_dir / "params.json")
    new_params = apply_operator(params, operator, value, bundle.cameras)
    if new_params.enabled_frames and not bundle.poses_trusted:
        raise BubbleError("poses_trusted is false; refusing to project")
    before = Scores(metrics["fit_ssim"], metrics["holdout_ssim"])
    texture, _touched = bake_texture(
        bundle.dem, bundle.ortho, bundle.grid, bundle.cameras, bundle.images,
        new_params, bundle.poses_trusted,
    )
    images = render_all(bundle, texture)
    after = score_images(bundle, images)
    result = verdict(before, after)
    reverted = result == "worse"
    if not reverted:
        save_params(work_dir / "params.json", new_params)
        _write_pair(bundle, work_dir, texture, "refined")
        write_views(bundle, work_dir, images)
        metrics["fit_ssim"] = after.fit_ssim
        metrics["holdout_ssim"] = after.holdout_ssim
    entry = {
        "tick": len(metrics["iterations"]) + 1,
        "operator": operator,
        "value": value,
        "fit_ssim": after.fit_ssim,
        "holdout_ssim": after.holdout_ssim,
        "result": result,
        "reverted": reverted,
    }
    metrics["iterations"].append(entry)
    with (work_dir / "LOOP_STATE.md").open("a") as handle:
        handle.write(format_tick(entry["tick"], operator, value, before, after, result, reverted))
    return update_gates(bundle, work_dir, metrics)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/bubble/test_tick.py -v`

Expected: all PASS. The enable/revert test renders the mound several times and can take well under a minute.

- [ ] **Step 5: Commit**

```bash
git add pipeline/bubble/tick.py tests/bubble/test_tick.py
git commit -m "$(cat <<'EOF'
Run one bubble refine tick and revert it when the scores drop.

The tick log records the operator, both scores, and whether the edit was kept.

EOF
)"
```

---

### Task 8: Scripted run, human judge, committed fixture

**Files:**
- Create: `pipeline/bubble/scripted.py`
- Create: `pipeline/bubble/__main__.py`
- Test: `tests/bubble/test_scripted.py`
- Create: `contracts/fixtures/bubble_source/` by running the fixture command
- Create: `contracts/fixtures/bubble/` by running the scripted command

**Interfaces:**
- Consumes: `run_tick`, `load_bundle`, `make_fixture`, `BubbleError`
- Produces:
  - `assert_separated(bundle_dir, work_dir) -> None`
  - `run_scripted(bundle_dir, work_dir) -> dict`
  - `apply_judge(work_dir, verdict: str) -> dict` with `verdict` in `accept`, `reject`
  - CLI: `python -m bubble fixture --out PATH`, `python -m bubble tick --bundle PATH --work PATH --operator NAME --value TEXT`, `python -m bubble scripted --bundle PATH --work PATH`, `python -m bubble judge --work PATH --verdict accept|reject`

`run_scripted` checks directory separation, deletes the work directory, initializes, and if `poses_trusted` is false appends a refusal and returns without enabling a frame. Otherwise it runs the three scripted operators. `apply_judge` refuses `accept` when `geometry_delta_m_max != 0` or `outside_unchanged` is false.

- [ ] **Step 1: Write the failing test**

```python
# tests/bubble/test_scripted.py
from pathlib import Path
import json

import numpy as np
import pytest
from PIL import Image

from bubble.errors import BubbleError
from bubble.fixture import PEAK_COL, PEAK_ROW, make_fixture
from bubble.gltf import position_delta
from bubble.scripted import apply_judge, run_scripted


def test_nested_work_dir_is_refused(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(BubbleError, match="separate"):
        run_scripted(bundle, bundle / "out")
    assert bundle.is_dir()


def test_scripted_fixture_beats_the_ortho(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    work = tmp_path / "work"
    metrics = run_scripted(bundle, work)
    assert metrics["numeric_pass"] is True
    assert metrics["pass"] is True
    assert metrics["active_recommendation"] == "refined"
    assert metrics["human_verdict"] == "auto_gate"
    assert metrics["geometry_delta_m_max"] == 0.0
    assert metrics["outside_unchanged"] is True
    assert metrics["lpips"] is None
    assert metrics["threshold_ssim"] == 0.75
    assert len(metrics["iterations"]) == 3
    assert metrics["iterations"][1]["operator"] == "enable_frame"
    assert metrics["iterations"][1]["result"] == "better"
    assert position_delta(work / "baseline" / "mesh.glb", work / "refined" / "mesh.glb") == 0.0
    albedo = np.load(bundle / "albedo.npy")
    ortho = np.asarray(Image.open(bundle / "ortho.png").convert("RGB"))
    refined = np.asarray(Image.open(work / "refined" / "texture.png").convert("RGB"))
    peak = (PEAK_ROW, PEAK_COL)
    d_ref = np.linalg.norm(refined[peak].astype(float) - albedo[peak].astype(float))
    d_ort = np.linalg.norm(ortho[peak].astype(float) - albedo[peak].astype(float))
    assert d_ref + 40 <= d_ort
    assert np.array_equal(refined[0, 0], ortho[0, 0])
    block = json.loads((work / "bubble_block.json").read_text())
    assert block["active"] == "refined"
    assert block["baseline_mesh"] == "/bubble/baseline/mesh.glb"
    assert "enable_frame" in (work / "LOOP_STATE.md").read_text()
    for name in ("nadir", "fit_0", "holdout_0"):
        assert (work / "compare" / f"{name}.png").exists()


def test_human_gate_and_untrusted_poses(tmp_path: Path):
    bundle = make_fixture(tmp_path / "bundle")
    raw = json.loads((bundle / "viewpoints.json").read_text())
    raw["requires_human"] = True
    (bundle / "viewpoints.json").write_text(json.dumps(raw))
    work = tmp_path / "work"
    metrics = run_scripted(bundle, work)
    assert metrics["numeric_pass"] is True
    assert metrics["pass"] is False
    assert metrics["active_recommendation"] == "baseline"
    assert metrics["awaiting_human"] is True
    accepted = apply_judge(work, "accept")
    assert accepted["pass"] is True
    assert accepted["active_recommendation"] == "refined"
    assert json.loads((work / "bubble_block.json").read_text())["active"] == "refined"
    rejected = apply_judge(work, "reject")
    assert rejected["active_recommendation"] == "baseline"
    assert rejected["pass"] is False

    raw["poses_trusted"] = False
    raw["requires_human"] = False
    (bundle / "viewpoints.json").write_text(json.dumps(raw))
    work2 = tmp_path / "work2"
    metrics = run_scripted(bundle, work2)
    assert metrics["iterations"] == []
    assert metrics["pass"] is False
    assert "refusing to project" in (work2 / "LOOP_STATE.md").read_text()
    assert (work2 / "compare" / "fit_0.png").exists()
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `pytest tests/bubble/test_scripted.py -v`

Expected: FAIL, `ModuleNotFoundError: No module named 'bubble.scripted'`

- [ ] **Step 3: Write the driver and CLI**

`pipeline/bubble/scripted.py`

```python
from __future__ import annotations

from pathlib import Path
import json
import shutil

from bubble.bundle import load_bundle
from bubble.errors import BubbleError
from bubble.tick import ensure_initialized, run_tick


def assert_separated(bundle_dir: Path, work_dir: Path) -> None:
    bundle_dir = bundle_dir.resolve()
    work_dir = work_dir.resolve()
    if bundle_dir == work_dir or bundle_dir in work_dir.parents or work_dir in bundle_dir.parents:
        raise BubbleError("bundle and work directories must be separate")


def run_scripted(bundle_dir: Path, work_dir: Path) -> dict:
    assert_separated(bundle_dir, work_dir)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    bundle = load_bundle(bundle_dir)
    metrics = ensure_initialized(bundle, work_dir)
    if not bundle.poses_trusted:
        with (work_dir / "LOOP_STATE.md").open("a") as handle:
            handle.write("## Refusing to project\nposes_trusted is false. Shipping the ortho drape.\n")
        return metrics
    fit = [cam.name for cam in bundle.cameras if cam.role == "fit"]
    steps = [("set_opacity", "1"), ("enable_frame", fit[0]), ("set_fade_end_m", "20")]
    for operator, value in steps:
        metrics = run_tick(bundle_dir, work_dir, operator, value)
    return metrics


def apply_judge(work_dir: Path, verdict: str) -> dict:
    if verdict not in ("accept", "reject"):
        raise BubbleError("verdict must be accept or reject")
    metrics_path = work_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    if verdict == "accept":
        if metrics["geometry_delta_m_max"] != 0.0 or not metrics["outside_unchanged"]:
            raise BubbleError("refusing accept; geometry or footprint gate failed")
        metrics["human_verdict"] = "accept"
        metrics["awaiting_human"] = False
        metrics["pass"] = True
        metrics["active_recommendation"] = "refined"
    else:
        metrics["human_verdict"] = "reject"
        metrics["awaiting_human"] = False
        metrics["pass"] = False
        metrics["active_recommendation"] = "baseline"
    metrics_path.write_text(json.dumps(metrics, indent=2))
    block_path = work_dir / "bubble_block.json"
    block = json.loads(block_path.read_text())
    block["active"] = metrics["active_recommendation"]
    block["metrics"] = metrics
    block_path.write_text(json.dumps(block, indent=2))
    return metrics
```

`apply_judge` writes the JSON itself. It does not rebake or re-render.

`pipeline/bubble/__main__.py`

```python
from __future__ import annotations

from pathlib import Path
import argparse

from bubble.fixture import make_fixture
from bubble.scripted import apply_judge, run_scripted
from bubble.tick import run_tick


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bubble")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fixture = sub.add_parser("fixture")
    fixture.add_argument("--out", type=Path, required=True)

    tick = sub.add_parser("tick")
    tick.add_argument("--bundle", type=Path, required=True)
    tick.add_argument("--work", type=Path, required=True)
    tick.add_argument("--operator", required=True)
    tick.add_argument("--value", required=True)

    scripted = sub.add_parser("scripted")
    scripted.add_argument("--bundle", type=Path, required=True)
    scripted.add_argument("--work", type=Path, required=True)

    judge = sub.add_parser("judge")
    judge.add_argument("--work", type=Path, required=True)
    judge.add_argument("--verdict", required=True, choices=["accept", "reject"])

    args = parser.parse_args(argv)
    if args.cmd == "fixture":
        make_fixture(args.out)
    elif args.cmd == "tick":
        run_tick(args.bundle, args.work, args.operator, args.value)
    elif args.cmd == "scripted":
        run_scripted(args.bundle, args.work)
    else:
        apply_judge(args.work, args.verdict)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/bubble -v`

Expected: all PASS

- [ ] **Step 5: Write the committed fixture the other streams can load**

```bash
python -m bubble fixture --out contracts/fixtures/bubble_source
python -m bubble scripted --bundle contracts/fixtures/bubble_source --work contracts/fixtures/bubble
```

Expected: `contracts/fixtures/bubble/bubble_block.json` has `"active": "refined"`, both `mesh.glb` files exist, and `compare/fit_0.png` exists.

- [ ] **Step 6: Commit**

```bash
git add pipeline/bubble/scripted.py pipeline/bubble/__main__.py tests/bubble/test_scripted.py contracts/fixtures/bubble_source contracts/fixtures/bubble
git commit -m "$(cat <<'EOF'
Ship the scripted photo drape and a committed synthetic bubble.

A real bundle stays on the baseline mesh until a person accepts the compare images.

EOF
)"
```

---

## Real bundle procedure

Product pages and file URLs are in `docs/bubble-imagery.md`. Nothing from those URLs is downloaded by this plan. When a HiRISE chip and one posed frame exist, build a sibling of the fixture. Do not add rasterio to the project for this.

1. Warp the chip into the site frame yourself. Save one float32 `dem.npy` of shape `(H, W)` and an `ortho.png` of the same shape, row 0 = north. `grid.json` uses the northwest sample's site `x0`, `y0`, the sample spacing `res_m`, and the bubble-center `center_x`, `center_y`.
2. Pick one fit frame whose site-frame camera position, heading, and horizontal FOV you are willing to defend, plus one holdout frame you will not project. Match `width` and `height` to the PNG. Keep a nadir camera with `image: null`.
3. Set `poses_trusted` false and `requires_human` true. Run `python -m bubble scripted`. The log says `refusing to project`. Open `compare/fit_0.png` (photo, ortho render, ortho render).
4. If the photo's horizon and one landmark do not sit on the ortho render, stop. Leave `active` on `baseline`. That is a pose failure, and painting will not fix it.
5. If they do, set `poses_trusted` true, rerun scripted, and look at `compare/fit_0.png` again (photo, ortho, refined). Accept only if the refined panel shows that photo on the ground and the nadir strip is not repainted outside the footprint. Then `python -m bubble judge --work <work> --verdict accept`.
6. `registration_rms_m` is a note. The code does not read it to pass the gate.

## Merge contract

Teammates read files only.

```
contracts/fixtures/bubble/baseline/mesh.glb
contracts/fixtures/bubble/refined/mesh.glb
contracts/fixtures/bubble/compare/*.png
contracts/fixtures/bubble/metrics.json
contracts/fixtures/bubble/bubble_block.json
```

- Both GLBs are standard glTF 2.0, Y-up, embedded PNG, `metallicFactor` 0, `roughnessFactor` 1, `doubleSided` true. Vertex positions match. Do not sample this mesh for slope or rover pitch.
- `bubble_block.json` is the PRD `bubble` object: `id`, `size_m`, `center_x`, `center_y`, `baseline_mesh`, `refined_mesh`, `active`, `metrics`. Extra metric keys (`numeric_pass`, `human_verdict`, `holdout_ssim`, `geometry_delta_m_max`, `outside_unchanged`, `awaiting_human`) are safe to ignore.
- `active` is `refined` only after the numeric gate on a fixture, or after `judge --verdict accept` on a real bundle.
- Viewer FOV for a fair compare is the `fov_deg` in `viewpoints.json`. This loop does not chase Cesium lighting.
