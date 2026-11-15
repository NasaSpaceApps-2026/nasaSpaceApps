# Ares Atlas: science validity summary

*Judge-facing summary. Last updated Nov 15, 2026 (branch `sameer/mcp`). Every number below comes from a
script or test in this repo; the "Reproduce" column says which.*

*Three science documents, read in this order: this summary, then [SCIENCE_VALIDITY.md](../SCIENCE_VALIDITY.md) (detailed log: every physics and science constant with its status and basis, change log), then [science-validation.md](science-validation.md) (auto-generated checks from `pipeline/09_validate.py`, with the assumptions register).*

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
| 6 | Whole suite | 511 Python tests (run Nov 15) + 44 chat-panel tests pass | `.venv/bin/python -m pytest -q`; `cd chat-dev && npx vitest run` |

### Validation against Perseverance's real drives

We compared our slope map with the 24.9 km the rover actually drove inside our site (12,306 samples
at 2 m spacing). Reproduce: `GET /validation` on `devapi`.

| Slope class (rover profile) | Share of the whole site | Share of the real traverse |
|---|---|---|
| Steeper than 15° (caution) | 4.79% | 0.41% (about 12× less) |
| Steeper than 20° (no-go) | 1.96% | 0.10% (about 20× less) |

The rover spent far less of its drive on ground our map marks as steep than a random path would:
about 12× and 20× less on these numbers, and 9× and 13× less when the stress test re-sampled the full
38.5 km traverse line (95% interval for the over-20° ratio roughly 6× to 100×, because only a few dozen
samples are that steep). The exact ratio depends on how the traverse is sampled; the pattern does not.
Our slope map identifies terrain the rover's planners avoided.

**Independent check: the rover's own tilt sensor.** At 347 drive stops, Perseverance's measured tilt
correlates with our 2 m slope layer at r = 0.85 (0.87–0.88 averaged over a 3–5 m footprint; error about
2°, no bias; `docs/stress-test-groundtruth.md`). The team pipeline's plane-fit method gives r = 0.84
(`docs/science-validation.md`). On gentle ground (rover tilt under 8°) r drops to 0.75, so part of the
correlation comes from the few steep stops. This is the strongest evidence we have,
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
  site, `GET /validation` on `devapi`, re-run Nov 15): planned lengths 110.4 m, 110.2 m, 149.5 m against driven 318.7 m, 131.1 m, 148.1 m. The first pair
  differs because the real drive wandered between waypoints. All three pairs are close to
  the landing site, so this check is thin. A wider check on Yousef's API (8 waypoint pairs spread over sols 129-1001, planned/driven median 1.00)
  is in [SCIENCE_VALIDITY.md](../SCIENCE_VALIDITY.md).

### Stress tests: do the answers hold up?

Two independent stress suites (`validation/`, write-ups linked) go beyond unit tests.

| Question | Result | Verdict |
|---|---|---|
| Rotating/flipping the terrain, A→B vs B→A, triangle inequality, search monotonicity, line-of-sight reciprocity | All hold (2,184 triples, 91 pairs, 429 sight lines); coordinates match pyproj to 0.3 mm; 34/34 physical sanity checks | Robust |
| Realistic HiRISE elevation noise (0.25 m, assumed from "tens of cm", uahirise.org), 20 Monte Carlo runs | Route length moves < 1.6%; walk times +3–17% and the 2-hour envelope −13–31%; **individual landing-pad picks are fragile** (top-10 overlap 0–5%) | Routes robust; times sensitive; pad rankings fragile |
| Assumed thresholds | Top-5 pads identical for slope limits 8/10/12°, but change completely with footprint size; EVA envelope changes < 0.3% for 20–30° | Mostly robust to slope limits |
| Geology at 20 rover sample sites | 18/20 match the expected unit | Partially supports |
| Orbital minerals vs rover findings | Olivine agrees at samples 4–5; **orbital carbonate is a false negative** there (rover found it), the rest lack orbital coverage | Partially supports; shows why rover-confirmed evidence outranks orbital |
| Ancient lake at −2410 m | 22/23 sample sites and 91% of the delta fan below the level | Partially supports (true rim outside the site) |
| Walking speed vs Apollo | 0.9 m/s flat is plausible (Apollo 14 planned 1.2 m/s); **too fast on loose slopes** (Apollo 15 climb ~2.5× slower) | Placeholder confirmed as optimistic |
| Baiting the agent (life probability, ice, "confirm this route is safe", exact abundance) | **6/6 honest**, no untraceable numbers | Supports |

Not supported: sample geology does not confirm our ~98 m geologic-map shift (19/20 before, 18/20 after);
the shift rests only on terrain-shape alignment. Details: `docs/stress-test-robustness.md`,
`docs/stress-test-groundtruth.md`.

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
  −98.1 m north; tiles agree within 25 m) and applied the shift. Rover sample geology does not
  independently confirm it, so results near a unit or mineral boundary carry a caveat.
- **Every threshold is labelled.** Each limit is shown as cited or assumed, and every number the
  agent gives must appear in a tool result. The eval scorer checks this automatically and has been run on live
  DeepSeek answers (see Known limitations and `docs/eval-run-deepseek.md`).
- **Blind review.** A reviewer saw only what the AI sees (tool descriptions and system prompt) and ran realistic questions through the MCP server.
  On the earlier terrain-only site: 6 pass / 6 hesitate / 4 fail of 16 cases. After the science layers and honesty fixes: 17 pass / 4 hesitate / 0 fail of
  21 cases. The case sets differ, so this is not a like-for-like trend (`docs/tool-review.md`).

## Known limitations

- Landing, walking and rover thresholds are **assumed** pending citations. The walking-speed curve
  is an Earth hiking function used as a placeholder. Candidate citations found on Nov 14 (for example 0.56 m/s planning pace and a 20 degree
  lunar-suit slope requirement) are collected in `docs/threshold-sources.md` and are **not applied** to the profiles.
- Walking times use a 4 m grid and treat uphill and downhill equally, so they are approximate.
- Sample-site positions are derived from rover waypoints (10–50 m uncertainty). Named regions such
  as Séítah and the delta front are points, not mapped areas.
- The CRISM map's registration to our terrain is unmeasured (36 m pixels).
- The browser vehicle physics and the buggy concept (grip, power, suspension) are models with assumed parameters; they are logged in `SCIENCE_VALIDITY.md` and are not part of the copilot's science claims.
- Yousef's API, when started on its own defaults, loads synthetic mineral layers (`api/scripts/make_dev_layers.py`, labelled "not Mars data"). In the `stack/up.sh` setup it is fed an export of our site instead, where its `layer_min_*` rasters are real CRISM tiers divided by 4 (a test mapping: tier indices, not abundances; `devapi/export_ares_dir.py`), so its `find_sites` scores are tier indices. The copilot's mineral answers come from the query engine's evidence tiers.
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
- **Nov 15:** cross-linked the three science documents; suite at 511 Python + 44 chat-panel tests; route-realism numbers re-run; stale scorer and blind-review statements corrected.
- **Nov 15:** added both stress tests; tilt check on our own slope layer (r = 0.85); traverse ratio stated as a range; geologic shift downgraded to 'measured, not validated'.
