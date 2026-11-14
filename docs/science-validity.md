# Ares Atlas: science validity summary

*Living document. Last updated Nov 14, 2026 (branch `sameer/mcp`). Every number below comes from a
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
| 6 | Whole suite | 272 Python tests + 30 chat-panel tests pass | `.venv/bin/python -m pytest -q` |

### Validation against Perseverance's real drives

We compared our slope map with the 24.9 km the rover actually drove inside our site (12,306 samples
at 2 m spacing). Reproduce: `GET /validation` on `devapi`.

| Slope class (rover profile) | Share of the whole site | Share of the real traverse |
|---|---|---|
| Steeper than 15° (caution) | 4.79% | 0.41% (about 12× less) |
| Steeper than 20° (no-go) | 1.96% | 0.10% (about 20× less) |

The rover spent 12 to 20 times less of its drive on ground our map marks as steep than a random
path would. Our slope map identifies terrain the rover's planners avoided.

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
- The live AI agent has not yet been scored end to end (no API key yet). Tool-choice quality so far
  comes from the blind review.

## Change log

- **Nov 14:** first version.
