# Ares Atlas stress test: are the answers physically sensible and robust?

*Generated from `validation/robustness.py` (full run 585 s on a loaded 10-core Mac, seed 20261115, 20 Monte Carlo runs per noise model). Raw numbers: `validation/results.json`. pytest wrapper: `validation/test_robustness.py` (`-m "not slow"` for the fast subset). Engine under test: `atlas_core/` on the real Jezero site (`data/build/site/jezero`, 4096 x 4096 cells at 2 m, 8.19 km square). Nothing in `atlas_core/` was modified. One package was installed into `.venv`: `pyproj 3.8.0` (numpy, scipy, scikit-image, rasterio were already present).*

## Verdict first

| Claim we make | Verdict | Evidence |
|---|---|---|
| Slope and aspect are computed correctly and do not depend on grid orientation | **Robust** | Rotating/transposing the real DEM changes slope by exactly 0.0 deg, aspect by < 4e-5 deg; Horn is exact on planes (0.0 deg error) and paraboloids (2e-6 deg) (section 1) |
| Walking time/distance is a well-behaved metric (symmetric, triangle inequality, nearest = minimum) | **Robust** | 91 pairs: max asymmetry 4e-13 min; 2,184 triples: 0 triangle violations; `nearest` never beaten by brute force (section 1) |
| `find_locations` candidate sets respond monotonically to thresholds, conditions, `within` | **Robust** | 6 series, all candidate-cell counts monotone; greedy spot counts were monotone too, but that is not guaranteed by the algorithm (section 1) |
| Line of sight is reciprocal at equal heights | **Robust** (by construction) | 429/429 line-of-sight pairs and 173/173 viewshed pairs agree (section 1) |
| Site-frame lat/lon conversion matches the team CRS | **Robust** | 0.3 mm max vs pyproj on a 41 x 41 grid; the 0.3 mm is the `y_0` rounding in `site_cfg.CRS` (section 2) |
| Every layer / tool output stays inside physical ranges | **Robust** | 34 checks, 0 failures (section 6) |
| Aggregate terrain statistics (share of site steeper than X, route length) | **Robust** | Route length moves < 1.6 % under +/-0.25 m noise; class change 1.4-3.3 % of cells (section 4) |
| Walking-time / envelope numbers | **Sensitive** | Time shifts +3 to +17 % and the 2-h envelope shrinks 13-31 % under added noise, because noise raises slope, which slows the placeholder speed curve (section 4) |
| Slope class of an individual cell / feature | **Sensitive to baseline** | Up to 14 % of cells in steep terrain change rover class between 1 m and 4 m baselines; expected (section 3) |
| EVA envelope vs. the assumed max slope (20/25/30 deg) | **Robust** for 60-120 min budgets (< 0.3 % change); **uninformative** for 8 h (saturates the site) | section 5 |
| "Rover avoids steep ground 12-20x more than a random path" (`science-validity.md`) | **Sensitive / does not reproduce exactly** | Current traverse file: 9x (> 15 deg) and 13x (> 20 deg), 95 % CI on the 20 deg share spans 6x to ~100x (section 5) |
| Specific top-N landing pad locations | **Fragile** | Top-10 pad overlap with baseline 0-5 % under correlated noise; 0 of 5 top pads survive a 75 m footprint (sections 4, 5) |
| Existence of any 50 m / 10 deg pad | **Fragile to white noise, robust to correlated** | White 0.25 m noise at 2 m posts: pads vanish in 18/20 runs; correlated 0.25 m noise: 900 pads remain, baseline 1,699 (section 4) |

## Method in one paragraph

Section 1-2 and 6 test properties that must hold exactly. Sections 3-5 perturb either the data (resolution, noise) or the assumed thresholds and measure how the engine's answers move. Perturbed sites are built by copying the `Site` dataclass and replacing `elevation`, `slope_2m` (recomputed with `terrain.slope_deg`) and `rough_5x5` (recomputed with a box-filter version verified equal to `terrain.roughness` to 1e-7 m); `quality_flag` and all mineral layers are left alone. Tools are called through their public Python functions (`search.find_locations`, `travel.nearest`, `route.plan_route`, `envelope.eva_envelope`, `visibility.*`), except the symmetry/triangle tests, which use the planning grid (`travel._Plan`) directly so 14 Dijkstra fields can be reused for 91 pairs; 6 pairs are also checked through the public `nearest`.

---

## 1. Invariance and metamorphic tests

| Test | Result |
|---|---|
| Analytic planes (4 slopes x 5 azimuths): max slope / aspect error | 0.0 deg / 0.0 deg |
| Paraboloid z = a r^2: max slope error / aspect error | 2e-6 deg / 1.5e-5 deg |
| DEM rotated 90/180/270 and transposed: max slope difference, 1/50/90/99th percentile differences | 0.0 deg, 0.0 deg (bitwise equal) |
| Same, aspect after applying the expected rotation (b - 90k; transpose: 270 - b) | max 3e-5 deg, 0 cells > 0.01 deg, 0 mismatched defined/undefined cells |
| walk_time(A->B) vs walk_time(B->A), 91 pairs of walkable cells, typical time 102 min | max asymmetry 4e-13 min (relative 3e-15); walk_m the same; public `nearest` 6 pairs: identical to 3 decimals |
| Triangle inequality, 2,184 triples | 0 violations (tolerance would have been one diagonal planning step, 0.18 min) |
| `nearest` never farther than any other pair | straight_m: 0 violations; walk_time: 0 violations (max excess 0.0003 min), 6 from x 6 to |
| Line-of-sight reciprocity, 429 random pairs 80-2500 m, eye heights alternating 1.7 m and 0.2 m | 100 % agreement in every distance bin (0-300, 300-800, 800-1500, 1500-2600 m); clearance difference between the two directions 0.0 m (median and p95) |
| Viewshed grid sweep, 173 pairs (A sees B iff B sees A, equal heights, 1.5 km radius) | 100 % agreement; and 100 % agreement between the grid sweep and the exact line of sight |

**find_locations monotonicity** (profile `landing_pad`, candidate cells = footprint-valid centres from the engine diagnostics):

| Series | Candidate cells (in order of the variants) | Monotone? |
|---|---|---|
| Relax slope 6, 8, 10, 12, 15 deg | 0.41 M, 1.06 M, 1.73 M, 2.30 M, 2.48 M | yes (increasing) |
| Relax roughness 0.3, 0.5, 0.8, 1.2 m | 1.08 M, 1.73 M, 1.73 M, 1.73 M | yes (saturates: quality flag binds) |
| Footprint 100, 75, 50, 25, 10 m | 0.11 M, 0.23 M, 1.73 M, 4.97 M, 9.21 M | yes |
| Add 0, 1, 2, 3 extra conditions | 1.73 M, 1.44 M, 1.36 M, 247 | yes (decreasing) |
| Relax `within` base walk 20, 40, 80, 160 min | 37 k, 265 k, 1.31 M, 1.73 M | yes |
| Add `within` 60 min | 1.73 M -> 0.80 M | yes |

**What this means.** The numerical kernels are exactly invariant and the search/travel layers behave like the mathematical objects their docs describe. Reciprocity of line of sight is exact because `_sight_profile` places samples at d_i = D i / n (mirror-symmetric when the ends swap) and the curvature bulge d (D - d) / 2R is symmetric; so the "why not exactly 100 %" question has the answer "it is 100 %, by construction". Real asymmetry would only appear with unequal heights (not reciprocal physically) or with a non-symmetric sampling scheme. Two caveats: (a) the **greedy spot count** after spacing is not guaranteed monotone in a threshold (a looser threshold can change which spot is picked first); it was monotone in all six series here, but `find_locations` promises only the candidate set; (b) the 8-connected grid path is direction-biased (next table).

**Grid-path direction bias** (synthetic flat site, 500 m legs, `walk_m` / straight distance): bearing 0 deg 1.000, 10 deg 1.054, 22.5 deg 1.076, 30 deg 1.081, 45 deg 1.007, 60 deg 1.081, 67.5 deg 1.076, 90 deg 1.000. The theoretical maximum for an 8-connected path is 1.0824, matched. Walk distances and times are therefore overestimated by up to 8 % depending on bearing. The engine already labels them approximate; this puts a number on it.

---

## 2. Coordinates

Grid of 41 x 41 points over the full +/-4096 m square.

| Check | Max error |
|---|---|
| `xy_to_latlon` then `latlon_to_xy` round trip | 3.5e-10 m |
| `xy_to_latlon` vs pyproj inverse of `site_cfg.CRS` | 0.0003 m |
| `latlon_to_xy` vs pyproj forward | 0.0003 m |
| pyproj forward of our lat/lon vs the original xy | 0.0003 m |
| Site origin through pyproj | (0, -0.0003) m |
| Cell centre (row, col) vs GeoTIFF affine transform; `xy_to_rc` round trip | 0.0 m; 0.0 cell |
| Flat site frame vs great-circle (haversine) distance over random pairs | 0.038 % relative |

**What this means.** The engine's simple eqc formulas are the same projection as the team CRS to 0.3 mm. The 0.3 mm is `y_0` being rounded to 3 decimals in `pipeline/site_cfg.py` (`{Y0:.3f}`), not an engine error. Over an 8 km site the flat frame differs from a true sphere by 0.04 % (about 3 m over 8 km) because lines of constant latitude are not great circles; negligible against the 2 m cell.

---

## 3. Resolution consistency (1 m vs 2 m vs 4 m)

The raw HiRISE DTM (`data/raw/hirise/window_18.452_77.418_4096.tif`, 1 m) was reprojected into the site frame at 1 m for four 1,024 m windows (landing area, steepest, median and flattest 1 km blocks of the site), then 2x2 and 4x4 block-averaged to 2 m and 4 m, and slope recomputed with the engine's Horn function at the matching spacing. Reproduction check: my 2 m elevation matches the shipped `elevation` layer to 0.4-2 mm RMS and slope to 0.005-0.03 deg RMS, so this is the same product.

Slope percentiles and share above thresholds (rows are the steepest block; others in `results.json`):

| Window | Baseline | p50 | p95 | p99 | > 10 deg | > 15 deg | > 20 deg | > 25 deg |
|---|---|---|---|---|---|---|---|---|
| Steepest block | 1 m | 10.5 | 22.0 | 30.6 | 53.5 % | 21.6 % | 7.5 % | 2.9 % |
| | 2 m | 10.1 | 19.7 | 26.1 | 50.6 % | 17.3 % | 4.6 % | 1.3 % |
| | 4 m | 9.3 | 16.8 | 20.9 | 43.8 % | 9.8 % | 1.4 % | 0.2 % |
| Landing area | 1 m | 3.0 | 8.9 | 13.2 | 3.3 % | 0.50 % | 0.06 % | 0.01 % |
| | 2 m | 2.8 | 8.5 | 12.4 | 2.7 % | 0.29 % | 0.03 % | 0.00 % |
| | 4 m | 2.5 | 7.6 | 11.1 | 1.7 % | 0.09 % | 0.00 % | 0.00 % |

Per-cell comparison (finer slope averaged to the coarser grid vs the native coarser slope; rover classes safe < 15, caution 15-20, no-go > 20):

| Window | Pair | mean diff | RMSE | corr | cells changing class | of which to a safer class |
|---|---|---|---|---|---|---|
| Steepest | 1 m vs 2 m | +0.67 deg | 1.59 deg | 0.969 | 7.1 % | 6.6 % |
| | 2 m vs 4 m | +1.06 | 2.36 | 0.895 | 9.1 % | 8.4 % |
| | 1 m vs 4 m | +1.73 | 3.55 | 0.796 | 14.0 % | 13.0 % |
| Landing area | 1 m vs 2 m | +0.19 | 0.39 | 0.992 | 0.2 % | 0.2 % |
| | 2 m vs 4 m | +0.39 | 0.72 | 0.970 | 0.2 % | 0.2 % |
| Median | 1 m vs 4 m | +0.63 | 1.14 | 0.950 | 0.5 % | 0.5 % |
| Flattest | 1 m vs 4 m | +0.42 | 0.68 | 0.925 | 0.05 % | 0.04 % |

Share of cells eligible for a pad on slope alone (<= 10 deg): steepest block 46.6 / 49.6 / 56.5 % at 1 / 2 / 4 m; landing area 96.6 / 97.3 / 98.3 %.

**What this means.** Slope is baseline dependent: a longer baseline smooths, so every statistic moves monotonically toward flatter (p99 slope falls from 30.6 to 20.9 deg in the steepest block; a cell steeper than 25 deg at 1 m is twelve times rarer at 4 m). Almost every class change is toward "safer" with the coarser baseline (92-93 % of changes). This is expected and physical (a rock step is steep at 1 m and invisible at 4 m); it is not an error, but it means **our 2 m slope map sits between the 1 m reality a rover wheel sees and the 4 m planning grid**, and the cited rover/pad thresholds were not defined for any of them. In benign terrain (landing area, flattest block) < 0.3 % of cells change class, so pad and route conclusions there are baseline-insensitive; the effect is concentrated on scarps and rims, where the thresholds matter most.

---

## 4. DEM noise robustness (Monte Carlo)

**Noise amplitude.** The page https://www.uahirise.org/dtm/about.php (opened during this work) states: "the post spacing is 1-2 m with vertical precision in the tens of centimeters." The HiRISE instrument paper (McEwen et al. 2007) set a goal of better than 0.25 m vertical precision (this figure was read in a search summary, **not** verified in the paper). The Kirk et al. 2008 JGR abstract (opened via the USGS record; full text blocked, HTTP 403) reports slopes of 1.8-2.8 deg at 1 m baseline and gives no explicit vertical error. So sigma_z = 0.25 m is an **assumed** value inside the sourced range; `docs/science-validation.md` already notes that no vertical accuracy has been cited. Three noise models:

| Model | Meaning |
|---|---|
| `white_0.25` | Independent 0.25 m error at every 2 m post. Pessimistic: assumes no averaging gain from 1 m to 2 m posts and no spatial correlation |
| `white_0.125` | 0.25 m independent errors at 1 m posts averaged 2 x 2 to a 2 m post |
| `corr_0.25` | Gaussian-correlated noise (smoothing sigma 4 m, assumed correlation scale), total sd 0.25 m. Closest to stereo-matching errors, which are correlated over the matching window |

Note the baseline already contains the real DTM error, so adding independent noise is an upper-bound perturbation (variance doubles). Because noise adds slope, all runs are **biased** toward steeper results; the Monte Carlo spread (across seeds) is tiny, and the shift from the baseline is what matters. 20 runs per model, each re-running `find_locations` (landing_pad), 5 rover routes, one 120-min EVA envelope.

| Quantity (mean over 20 runs) | white 0.25 | white 0.125 | corr 0.25 |
|---|---|---|---|
| Mean slope bias | +1.61 deg | +0.50 deg | +1.00 deg |
| Cells changing rover class (15/20 deg) | 3.3 % | 1.4 % | 2.3 % |
| Cells changing EVA class (15/25 deg) | 2.8 % | 1.2 % | 1.9 % |
| Baseline no-go cells still no-go (rover) | 86 % | 92 % | 89 % |
| No-go area vs baseline (rover) | x1.18 | x1.04 | x1.09 |
| Cells flipping across 10 deg | 8.2 % | 3.0 % | 5.1 % |
| Landing pads found (baseline 1,699) | 0 in 18/20 runs (max 1) | 1,259 +/- 8 | 902 +/- 14 |
| Pad candidate cells (baseline 1.73 M) | ~0 | 1.08 M | 0.50 M |
| Baseline top-10 pads still valid under the noisy slope | 0 % | 100 % | 88 % (min 50 %) |
| Jaccard of top-10 with baseline (centres within 25 m) | 0.00 | 0.34 (0.18-0.54) | 0.05 (0.00-0.11) |
| Jaccard of top-10 with baseline (exact cell) | 0.00 | 0.01 | 0.00 |
| Mean pairwise Jaccard between noisy runs | 0.00 | 0.23 | 0.02 |
| Route distance change (5 pairs, mean) | -0.1..+1.6 % | 0.0..+0.4 % | -0.6..0.9 % |
| Route time change (5 pairs) | +10.6..16.5 % | +3.0..6.0 % | +3.7..7.8 % |
| Route sd across runs (distance / time) | <= 0.6 % / <= 0.7 % | <= 0.6 % / <= 0.4 % | <= 0.7 % / <= 0.5 % |
| 120-min EVA envelope vs baseline (13.51 km^2) | -31 % | -13 % | -14 % |
| Envelope sd across runs | 0.15 % | 0.07 % | 0.19 % |
| Route infeasible runs | 0 / 100 | 0 / 100 | 0 / 100 |

White-noise sweep (slope only, 3 seeds): sigma 0.05 m changes 0.6 % of rover classes and flips 1.1 % of cells across 10 deg; 0.10 m: 1.1 % / 2.4 %; 0.25 m: 3.3 % / 8.2 %; 0.50 m: 12.7 % / 32.2 % (slope bias +4.5 deg).

**What this means.**

*Robust:* route **length** (< 1.6 % in the worst model), which route is feasible (100 % of runs), and the per-run spread of everything (noise realisation hardly matters, the amplitude does). Class changes are a few percent of cells and the no-go area changes by 4-18 %.

*Sensitive:* walking/driving **time** and the **EVA envelope**. They move systematically (+3 to +17 % time, -13 to -31 % envelope area) because slope noise is one-signed: it raises |slope| and the placeholder speed exp(-3.5 tan s) punishes it. That is a bias, not a random error, and it depends entirely on how large the real DTM noise is.

*Fragile:* the **identity of the best landing pads** and, in the worst case, their **existence**. The pad rule demands that every one of the ~7,800 cells in a 50 m footprint be <= 10 deg. That is an extreme-value statistic: tiny per-cell noise creates a > 10 deg outlier somewhere in almost every footprint. At white 0.25 m the 50 m pad disappears everywhere; at 0.125 m a third of the candidate area is lost; the specific top-10 centres barely persist even when the pad *category* does (Jaccard 0.05-0.34), because the "flattest" ranking among ~1,700 near-equal pads reshuffles. The fact that the real DEM yields 1.7 M candidate cells shows the true 2 m noise is well below white 0.25 m, which is evidence for the correlated model, under which answers at the area level (about 900 pads remain; 88 % of baseline top-10 still valid) survive and rankings do not. Practical reading: say "pad-quality ground exists in these areas", not "pad 3.1 is the best".

---

## 5. Threshold sensitivity

### 5a. Landing pad: slope limit x footprint radius (profile `landing_pad`, other conditions fixed)

| Slope limit | R = 25 m | R = 50 m | R = 75 m |
|---|---|---|---|
| 8 deg | 2,779 pads (21.8 % cells) | 1,242 (6.3 %) | 49 (0.4 %) |
| 10 deg (baseline) | 3,288 (29.6 %) | **1,699 (10.3 %)** | 94 (1.4 %) |
| 12 deg | 3,542 (35.5 %) | 2,069 (13.7 %) | 138 (2.4 %) |

Stability of the baseline top-5 (centres matched within 25 m): top-5 at R = 50 m is **identical for 8, 10 and 12 deg** (5/5 overlap, mean slope 0.76 deg), because the objective is "flattest" and the slope limit only decides eligibility. Changing the footprint changes the answer completely: 2/5 of the baseline top-5 are in the R = 25 m top-5 (3/5 in its top-20), and 0/5 in the R = 75 m top-5 or top-20.

### 5b. EVA envelope vs max slope (km^2, base at the landing site)

| Max slope | 60 min | 120 min | 480 min (8 h) | Walkable share of 4 m planning grid |
|---|---|---|---|---|
| 20 deg | 3.312 | 13.482 | 65.19 | 97.2 % |
| 25 deg (baseline) | 3.316 | 13.506 | 66.31 | 98.8 % |
| 30 deg | 3.317 | 13.515 | 66.81 | 99.6 % |

### 5c. Rover no-go threshold vs the real Perseverance traverse

Traverse in `traverse.geojson` (276 drive segments, sols 14-1007; 38.5 km at 2 m spacing, 19,261 samples, join segments longer than 25 m excluded):

| Slope threshold | Share of real traverse above it | Share of whole site | Site / traverse | Traverse share 95 % CI (block bootstrap) |
|---|---|---|---|---|
| > 10 deg | 4.5 % | 12.0 % | 2.7x | n/a |
| > 15 deg (caution) | 0.53 % | 4.79 % | 9.0x | 0.20-0.95 % |
| > 20 deg (rover no-go) | 0.15 % (29 samples) | 1.96 % | 13.0x | 0.02-0.32 % |
| > 25 deg | 0 % | 0.76 % | n/a | n/a |

**What this means.** Pad *area* conclusions (how much pad-quality ground, and where) scale smoothly with the assumed slope limit; the *top* of the ranking is insensitive to the slope limit and highly sensitive to the footprint, which is a design choice, not a measurement; it should be stated as such. The EVA envelope is **robust to the max-slope assumption**: for 1-2 h EVAs, 20 vs 30 deg changes area by 0.3 %; for the 8 h limit the walk reaches the site edge (66.3 of 67.1 km^2, flagged `touches_site_edge`), so envelope area at 8 h measures the site size, not the astronaut. The rover result is the qualitative claim "the real rover avoided steep ground" and it holds at every threshold, but the multiplier quoted in `docs/science-validity.md` (about 12x and 20x on a 24.9 km traverse with 0.41 % / 0.10 %) does **not reproduce** on the traverse file now in the site folder (38.5 km; 9x and 13x; the > 20 deg share rests on 29 samples and its interval allows 6x to ~100x). The shipped doc number should be refreshed or given an interval.

---

## 6. Sanity bounds

34 checks, all passed (`results.json -> section6.checks`). Highlights:

| Quantity | Observed | Physical limit |
|---|---|---|
| `slope_2m` | 0.001 .. 69.3 deg (160 cells > 60 deg, 0.014 % > 45 deg) | 0..90 |
| `aspect_deg` | 0 .. 359.998 deg (NaN on 0.10 % flat/border cells) | [0, 360) |
| `rough_5x5`, `tri`, `local_relief_*`, curvature | all >= 0 (curvature in +/-0.13 /m); relief_200 >= relief_50 everywhere | >= 0 |
| Evidence tiers; geologic and quality codes | all within the declared classes | -1..4 |
| `slope_2m` vs Horn(`elevation`) | bit-identical | exact |
| POI heights vs bilinear DEM | max 0.00 m | < 1.5 m |
| Elevation range | -2638.5 .. -2293.6 m (345 m relief); route and `terrain_z` samples inside it | site range |
| Every returned landing pad (1,699) | clear radius 50..196 m, max slope in footprint <= 9.998 deg, mean <= max, inside the site | profile |
| `find_regions` areas | each <= 29.6 km^2 and all together 42.7 km^2 | <= 67.1 km^2 |
| 8 h EVA envelope | 66.31 km^2, equals reachable cells x 4 m^2 | <= 67.1 km^2 |
| Walking speed (eva_walkable) | 0.90 m/s flat, 0.66 (5 deg), 0.49 (10), 0.35 (15), 0.25 (20), 0.18 (25), strictly decreasing, > 0 | within human range (<= 1.8 m/s) |
| Sun elevation | -89.3 .. +89.4 deg over a Mars year (noon and midnight samples) | -90..90 |
| Noon elevation vs 90 - abs(lat - declination) | agrees to 2e-13 deg for 28 sols across a Mars year (46..89 deg); midnight sun below the horizon (max -46 deg) | at 18.45 N the noon sun ranges 46-89 deg, consistent |

Caveats on this section. The sun check is a **consistency** test (the engine's noon elevation equals the closed-form value from the same ephemeris's declination); it does not validate the ephemeris against an independent source. The walking-speed range check shows that the curve is sensible, not that it matches suit data: `profiles.yaml` states "speed = flat*exp(-k*|tan(slope)+c|)" while the code (`route.speed_mps`, `travel._Plan.costs`) uses exp(-3.5 tan(slope)), i.e. c = 0. That is a documentation mismatch only (Tobler's original has c = 0.05, a peak at a slight downhill); the speeds are labelled placeholder everywhere.

---

## Bugs and discrepancies found

No functional bug in `atlas_core/` was found by these tests; every invariant that should hold held. Discrepancies, none fixed (read-only brief):

1. **Doc number not reproducible.** `docs/science-validity.md` quotes 0.41 % / 0.10 % of the real traverse above 15 / 20 deg (24.9 km, 12,306 samples). The current `data/build/site/jezero/traverse.geojson` (sols 14-1007) gives 0.53 % / 0.15 % on 38.5 km. Repro: `.venv/bin/python validation/robustness.py --sections 5`, key `rover_traverse`.
2. **Doc/code mismatch in the speed curve** (`profiles.yaml` note vs `route.speed_mps`): see section 6. Repro: `route.speed_mps(params, 0)` vs the formula in the profile note with k = 3.5, c = 0.05 (peak moves to -2.9 deg).
3. **0.3 mm frame offset**: `Y0` in `pipeline/site_cfg.py` is formatted with 3 decimals in the CRS string, so the origin maps to (0, -0.0003) m. Immaterial.
4. **Design sensitivity, not a bug:** the pad rule uses the worst cell in a 50 m footprint, so ranking and even existence are noise-fragile (section 4). A robust alternative would be a percentile (e.g. 99th) of slope in the footprint, or a margin on the threshold; not implemented.

## Limits of this test

- The noise models are assumptions around a sourced range (sigma 0.1-0.5 m); the real error structure of the Mars 2020 mosaic (seams, CTX-fill boundaries, mosaic blending) is not modelled, and the site already flags those (`quality_flag`).
- Noise was added to the DEM only; mineral/evidence layers were not perturbed (their uncertainty is expressed by the evidence tiers).
- Sections 1 and 4 use random samples (429 line-of-sight pairs, 91 walk pairs, 14 fields for triangles, 6 `nearest` queries, 5 routes, 20 noise runs per model). "0 violations" means none in these samples.
- Runtime: the full script took 585 s on a machine shared with other jobs (section 1: 206 s, section 4: 329 s with 6 worker processes, section 5: 33 s, section 6: 12 s). `--runs N` and `--quick` reduce it.

## Reproduce

```
.venv/bin/python validation/robustness.py --runs 20           # ~10 min, writes validation/results.json
.venv/bin/python -m pytest validation -m "not slow" -q        # fast invariants
.venv/bin/python -m pytest validation -q                      # everything
```
