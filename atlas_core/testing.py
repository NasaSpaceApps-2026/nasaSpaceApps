"""Synthetic test site with KNOWN ground truth. Every test suite builds on this so that all
modules agree on the same world. Clearly synthetic: never shown to users as Mars data.

Layout (256 x 256 cells at 2 m = 512 m square, origin at centre, row 0 = north):

    - Plain: gentle tilt, 1 degree rising to the east.
    - Crater: bowl centred at (x=-120, y=120), radius 60 m, 25 m deep; flat-ish floor, inner
      walls steepening to about 55-60 degrees at the rim; a 3 m raised rim out to r = 70 m.
    - Scarp ridge: a north-south ridge at x = 100..112 m (peak 10 m at x = 106, sides about 59
      degrees), from the south edge up to y = 150. It can only be passed around its north end,
      so walking distance >> straight distance for points on opposite sides near the south.
    - No-data hole: a 20 x 20 m square of NaN centred at (x=0, y=-150).
    - geologic_unit (categorical): 0 "plains" everywhere, 1 "crater_floor" inside the crater,
      2 "scarp_block" for x > 106 (east of the scarp). -1 under the no-data hole.
    - carbonate (evidence): tier 3 strong_orbital in a 40 x 40 m patch centred (x=-150, y=-150);
      tier 2 in a 20 m ring around it; tier 4 confirmed_in_situ at one cell centred (x=-151, y=-151);
      tier 1 inferred_from_unit across the whole crater_floor; tier 0 elsewhere;
      -1 no_coverage for y > 200 (northern strip) and under the hole.
    - Cell centres fall on odd coordinates (..., -3, -1, 1, 3, ...).
    - POIs: base at (0, 0); landing at (50, -50); outcrop_a at (-151, -151); east_point at (200, -200).
"""
from __future__ import annotations

import numpy as np

from .site import Layer, Site
from .terrain import roughness, slope_deg

RES = 2.0
N = 256


def _grid():
    half = N * RES / 2
    c = np.arange(N)
    x = -half + (c + 0.5) * RES
    y = half - (c + 0.5) * RES
    X, Y = np.meshgrid(x, y)          # X[r, c], Y[r, c]
    return X, Y


def make_dem() -> np.ndarray:
    """Synthetic DEM (float32, metres): 1 deg tilt, crater, scarp ridge and a NaN hole; layout in the module docstring."""
    X, Y = _grid()
    z = -2500.0 + np.tan(np.radians(1.0)) * X           # 1 degree tilt rising east
    # crater: parabolic-ish bowl, steep walls near the rim
    r = np.hypot(X + 120, Y - 120)
    bowl = np.where(r < 60, -25.0 * (1 - (r / 60) ** 4), 0.0)
    rim = np.where((r >= 60) & (r < 70), 3.0 * (1 - (r - 60) / 10), 0.0)
    z = z + bowl + rim
    # scarp ridge: x = 100..112, peak 10 m at x = 106 (about 59 degree sides), south of y = 150.
    # North of y = 150 there is no ridge, so that is the way around.
    ridge = np.clip(10.0 - np.abs(X - 106) * (10.0 / 6.0), 0, None)
    z = z + np.where(Y <= 150, ridge, 0.0)
    hole = (np.abs(X - 0) <= 10) & (np.abs(Y + 150) <= 10)
    z[hole] = np.nan
    return z.astype(np.float32)


def make_site(with_pois: bool = True) -> Site:
    """Build the synthetic test Site (256 x 256 cells at 2 m) with its layers, profiles and POIs. Not Mars data."""
    X, Y = _grid()
    dem = make_dem()
    hole = ~np.isfinite(dem)
    slope = slope_deg(dem, RES)
    rough = roughness(dem)

    unit = np.zeros((N, N), np.int16)
    unit[np.hypot(X + 120, Y - 120) < 60] = 1
    unit[X > 106] = 2
    unit[hole] = -1

    carb = np.zeros((N, N), np.int8)
    carb[unit == 1] = 1
    dx, dy = np.abs(X + 150), np.abs(Y + 150)
    carb[(dx <= 40) & (dy <= 40)] = 2
    carb[(dx <= 20) & (dy <= 20)] = 3
    r0, c0 = np.unravel_index(np.argmin(np.hypot(X + 151, Y + 151)), X.shape)
    carb[r0, c0] = 4
    carb[Y > 200] = -1
    carb[hole] = -1

    src = {"mission": "SYNTHETIC", "instrument": "test fixture", "product": "atlas_core.testing",
           "citation": "synthetic test data, not Mars", "url": None}

    def meta(title, units, measures, dnm, **kw):
        m = {"title": title, "units": units, "resolution_m": RES, "measures": measures,
             "does_not_measure": dnm, "quality": "synthetic", "coverage": "site", "source": src}
        m.update(kw)
        return m

    layers = {
        "elevation": Layer("elevation", "continuous", dem,
                           meta("Elevation", "m", "Synthetic elevation.", "Anything below 2 m.")),
        "slope_2m": Layer("slope_2m", "continuous", slope,
                          meta("Slope over 2 m grid (Horn)", "degrees", "Surface slope.", "Rocks under 3 m.")),
        "rough_5x5": Layer("rough_5x5", "continuous", rough,
                           meta("Roughness 5x5", "m", "Std of elevation in 10 m window.", "Grain-scale texture.")),
        "geologic_unit": Layer("geologic_unit", "categorical", unit,
                               meta("Geologic unit", None, "Mapped unit.", "Composition directly."),
                               classes={0: "plains", 1: "crater_floor", 2: "scarp_block"}),
        "carbonate": Layer("carbonate", "evidence", carb,
                           meta("Carbonate evidence", "tier", "Carbonate detections by tier.",
                                "Carbonate buried below the surface or under dust.")),
    }
    profiles = {
        "landing_pad": {
            "description": "Synthetic test profile.",
            "footprint_radius_m": {"value": 10, "source": "assumed"},
            "conditions": [
                {"layer": "slope_2m", "op": "<=", "value": 5, "source": "assumed"},
                {"layer": "rough_5x5", "op": "<=", "value": 0.5, "source": "assumed"},
            ],
        },
        "eva_walkable": {
            "description": "Synthetic walking profile.",
            "travel": {"flat_speed_mps": 1.0, "max_slope_deg": 25, "speed_curve": "tobler_placeholder"},
            "conditions": [{"layer": "slope_2m", "op": "<=", "value": 25, "source": "assumed"}],
        },
        "rover_scout": {
            "description": "Synthetic rover profile.",
            "travel": {"flat_speed_mps": 0.04, "max_slope_deg": 20, "speed_curve": "tobler_placeholder"},
        },
    }
    pois = []
    if with_pois:
        pois = [
            {"id": "base", "name": "Test base", "type": "base", "x": 0.0, "y": 0.0},
            {"id": "landing", "name": "Test landing", "type": "landmark", "x": 50.0, "y": -50.0},
            {"id": "outcrop_a", "name": "Outcrop A", "type": "science_target", "x": -151.0, "y": -151.0},
            {"id": "east_point", "name": "East point", "type": "landmark", "x": 200.0, "y": -200.0},
        ]
    return Site(name="synthetic", lat0=18.46, lon0=77.41, res_m=RES, size_px=N,
                layers=layers, profiles=profiles, pois=pois)
