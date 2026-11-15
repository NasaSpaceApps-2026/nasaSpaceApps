# Ares Atlas: science validity summary

*Living document. Last updated Nov 14, 2026, evening (branch `sameer/mcp`). Every number below comes from a
script or test in this repo; the "Reproduce" column says which.*

## What we claim

Ares Atlas lets an AI copilot answer spatial questions about Jezero crater, such as "find a flat
landing pad within a 30-minute walk of base" or "where has olivine been detected, and how sure are
we?", by computing answers from NASA orbital and rover data rather than from the model's memory.
We claim three things, and test each:

1. **The terrain is real and correctly placed.** Elevation, slope and roughness come from the
   published USGS Mars 2020 HiRISE elevation model, in one coordinate frame shared with the team's 3D
   viewer.
2. **The answers are computed correctly.** Searches and walking-time calculations return exactly what
   their definitions say, checked against independent slow reference implementations.
3. **The answers are honest about evidence.** Every result says which dataset it came from, how sure
   that dataset is, and what it cannot tell you.

## Data (all public NASA/USGS products)

| Data | Mission / instrument | Resolution | Used for |
|---|---|---|---|
| Mars 2020 TRN HiRISE DTM mosaic (Fergason et al. 2020, USGS) | MRO / HiRISE | 1 m, averaged to 2 m | Elevation, slope, roughness |
| Mars 2020 TRN CTX DTM mosaic (USGS) | MRO / CTX | 20 m | Gap fill (not needed: HiRISE covers 100%) and comparison |
| CRISM mineral map of Jezero (Parente et al., Zenodo, CC BY 4.0) | MRO / CRISM | 36 m | Mineral evidence tiers (7 minerals) |
| Geologic map of Jezero, USGS SIM 3464 | Mapped from MRO data | Vector | Geologic units |
| Perseverance traverse and waypoints (NASA MMGIS) | Mars 2020 | Per sol | Validation, sample-site positions |
| Perseverance sample list (NASA) + Farley et al. 2022, Williams et al. 2023, Hurowitz et al. 2023 | Mars 2020 | Per sample | Rover-confirmed mineral evidence |

Full citations: `data/build/site/jezero/SOURCES_site.md`, `docs/data-inventory-minerals.md`.
Mineral, geology and traverse layers are interim copies for our query engine until the team's
official pipeline layers land.

## Evidence that it works

| # | Check | Result | Reproduce |
|---|---|---|---|
| 1 | Our site frame equals the team frame (`pipeline/site_cfg.py`) | Max disagreement 0.3 mm over 7 test points | `tests/test_site_build.py::test_site_frame_is_site_cfg` |
| 2 | Elevation matches the USGS source | Landing-site elevation within 1 m of a direct read of the HiRISE file | `tests/test_site_build.py` |
| 3 | Perseverance's first waypoint vs the published landing site | 6.5 m apart | `sitebuild/science.py` build log |
| 4 | Searches return exactly what they define | Fast search agrees with a plain-loop reference on every cell tested; no returned spot has a failing cell in its footprint | `tests/oracle.py`, `tests/test_search.py` |
| 5 | Walking time and distance | Dijkstra matches an independent reference implementation everywhere (rtol 1e-5); flat-ground time equals distance / speed within 1% | `tests/oracle_travel.py`, `tests/test_travel.py` |
| 6 | Whole suite | 478 Python tests + 30 chat-panel tests pass | `.venv/bin/python -m pytest -q` |

### Validation against Perseverance's real drives

We compared our slope map with the 24.9 km the rover actually drove inside our site (12,306 samples
at 2 m spacing). Reproduce: `GET /validation` on `devapi`.

| Slope class (rover profile) | Share of the whole site | Share of the real traverse |
|---|---|---|
| Steeper than 15° (caution) | 4.79% | 0.41% (about 12× less) |
| Steeper than 20° (no-go) | 1.96% | 0.10% (about 20× less) |

The rover spent 12 to 20 times less of its drive on ground our map marks as steep than a random
path would. Our slope map identifies terrain the rover's planners avoided.

**Independent check from the team pipeline** (`docs/science-validation.md`, `pipeline/09_validate.py`):
Perseverance's own tilt sensor at 347 drive stops correlates with slope from the same HiRISE terrain
at r = 0.84 (RMSE 2.27°, bias +0.02°; max rover tilt 20.3°). This is the strongest evidence we have,
because the rover's inclinometer does not depend on the orbital terrain model. (The pipeline's
elevation comparison, 0.05 m spread, is not independent: rover elevations are tied to that same
model.) The pipeline's traverse statistics differ slightly from ours (23.9 km and 0.3% over 15°
against our 24.9 km and 0.41%), most likely because it uses a two-point slope formula and different
clipping at the site edge; both show the same pattern.

**Read this honestly:**
- The headline "99.9% of the traverse is on safe or caution ground" is weak on its own, because
  98% of the site is safe or caution. The enrichment ratios above are the meaningful result.
- Rover drivers avoid ground for reasons besides slope (rocks, sand, science priorities), so this
  supports our slope map without proving it.
- **Route realism** (planned route vs the path actually driven, 3 waypoint pairs near the landing
  site): planned lengths 110 m, 110 m, 150 m against driven 319 m, 131 m, 148 m. The first pair
  differs because the real drive wandered between waypoints. All three pairs are close to
  the landing site, so this check is still thin.

### Why high-resolution data matters (finding)

In a 2 km window around the landing site, the steepest slope is **11.7°** in the 20 m CTX terrain
(the resolution Cesium Mars uses for Jezero) and **33.4°** in our 2 m HiRISE terrain. CTX shows
**0%** of that window over 15°; HiRISE shows 0.81%. Coarse terrain hides exactly the hazards an EVA
planner needs to see. Reproduce: `sitebuild/compare_ctx_hirise.py`, recorded in
`docs/compare_ctx_vs_hirise_stats.txt`.

### Science tools beyond minerals

Each tool is checked against a known answer, not just "it runs".

| Capability | Example question | Method (source) | Validation |
|---|---|---|---|
| Sun and shadow | "When is this outcrop lit for imaging?" | Mars24 algorithm (Allison & McEwen 2000, NASA GISS) + terrain shadow casting | Reproduces the GISS worked example (Ls 277.18758°, equation of time −5.18774°) and Perseverance's landing local mean solar time of 15:53 (JPL) |
| Line of sight | "Is base in radio line of sight from here?" | Radial sweep with Mars curvature (Franklin & Ray 1994) | Matches a brute-force per-cell reference; flat-ground horizon matches √(2Rh) (≈3.4 km at 1.7 m eye height) |
| Landform layers | "North-facing slopes", "most exposed rock section" | Horn 1981, Zevenbergen & Thorne 1987, Weiss 2001, Riley et al. 1999, Evans 1972 | Exact values on analytic surfaces (planes, bowls, saddles, steps); the real delta-front scarps score highest for relief |
| Ancient lake | "What was underwater at the delta-top level?" | Flood today's terrain to a published level | Levels only from opened sources (Mangold et al. 2024: −2410 m foresets; Villette et al. 2025, modelled). Depth at the landing site equals level minus elevation (159.8 m at −2410 m) |
| EVA safety | "Where can I go and still get back with 20% reserve?" | Out-and-back walking time over the slope-limited terrain | On flat ground the envelope radius equals speed × usable time ÷ 2; route check finds the exact point of no return |

## How the answers stay honest

- **Evidence tiers, not probabilities.** Mineral evidence is one of: rover-confirmed > strong
  orbital > weak orbital > inferred from geologic unit > not detected, plus a separate "no
  coverage". We never turn a tier into a percentage, because no source supports one.
- **Rover vs orbit is never mixed.** Rover-confirmed evidence names the sample and paper (e.g.
  "Sample 5: Coulettes, Farley et al. 2022"). Orbital evidence names MRO/CRISM. Regions report area
  per tier, so a large orbital patch with one confirmed sample is not presented as "confirmed".
- **No data is not "no".** 36% of the site has no CRISM coverage. Tools report "no coverage: cannot
  answer" there, never "not detected".
- **Data-quality flags.** HiRISE stitching seams (7.4% of the site) and noisy terrain zones (8.2%)
  are detected and flagged, never smoothed away. Landing pads avoid them.
- **Registration caveats.** We measured the geologic map's offset from our terrain (+25.8 m east,
  −98.1 m north; tiles agree within 25 m) and corrected it. Results near a unit or mineral boundary
  carry a caveat.
- **Every threshold is labelled.** Each limit is shown as cited or assumed, and every number the
  agent gives must appear in a tool result. The eval scorer checks this automatically; it will run
  on live answers once we have an API key.
- **Blind review.** A reviewer saw only what the AI sees and ran 21 realistic questions live. The
  results went from 6 pass / 6 hesitate / 4 fail to 17 pass / 4 hesitate / 0 fail, mostly through
  honesty fixes (`docs/tool-review.md`).

## Known limitations

- Landing, walking and rover thresholds are **assumed** pending citations. The walking-speed curve
  is an Earth hiking function used as a placeholder.
- Walking times use a 4 m grid and treat uphill and downhill equally, so they are approximate.
- Sample-site positions are derived from rover waypoints (10–50 m uncertainty). Named regions such
  as Séítah and the delta front are points, not mapped areas.
- The CRISM map's registration to our terrain is unmeasured (36 m pixels).
- Live agent (DeepSeek, real Jezero site): 19 of 24 cases passed in full runs with both
  `deepseek-chat` and `deepseek-v4-pro`. After we made tools state every number users ask for
  (round-trip times, distances), a rerun of the failing cases passed 6 of 7 on each model with **zero**
  untraceable numbers in 14 sessions; the remaining failures differ between models (likely run-to-run
  variation). Results: `docs/eval-run-deepseek.md`. Not yet scored with Claude.
- The lake tool floods today's topography, not the ancient lake floor, and the sources do not state
  their elevation datum. The site is only 8 km across, so lake areas at high levels are lower bounds.
- `outcrop_exposure` is our own heuristic; it cannot tell rock from sand and is inflated where the
  terrain model is noisy.
- Shadows ignore atmospheric scattering and terrain beyond the site edge.

## Change log

- **Nov 14:** first version.
- **Nov 14 (later):** added sun/shadow, line of sight, landform layers, ancient lake and EVA safety tools with their validation; suite at 386 tests.
- **Nov 14 (evening):** cited the team pipeline's independent rover-tilt check (r = 0.84) and noted the small traverse-statistics difference.
- **Nov 14 (night):** first live agent scores (DeepSeek 19/24); suite at 473 tests.
