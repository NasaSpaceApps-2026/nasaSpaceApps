# Science validity log

What the simulation claims, what each claim rests on, and how sure we are. Feeds the
submission's Validity section. Add an entry whenever a number or model changes.

Status key: **cited** (published NASA/peer value) · **derived** (computed from cited values) ·
**assumed** (reasonable placeholder, labelled in the app) · **synthetic** (test data, not Mars) ·
**to verify** (believed correct, source not yet checked).

## Constants and data

| Item | Value | Status | Basis |
|---|---|---|---|
| Mars surface gravity | 3.71 m/s² | cited | NASA Mars fact sheet |
| Mars radius (site frame) | 3,396,190 m | cited | IAU/USGS Mars sphere, PRD Contract 1 |
| Terrain elevation | HiRISE DTM, 1 m posts (CTX 20 m fallback) | cited | USGS Mars 2020 TRN products (Stream A) |
| Imagery | HiRISE ortho, 25 cm, greyscale | cited | USGS; colour shown only as labelled false colour |
| Slope, roughness | Derived from the DTM (gradient; 5×5 std dev) | derived | Standard DEM analysis |
| Mineral layers | CRISM-derived abundance, 0–1 | **synthetic** for now | Placeholder rasters labelled "not Mars data" until Stream A ships CRISM products |
| Test sites (fixture, playground) | Analytic / seeded-noise terrain | **synthetic** | Used only for development; manifest says so |

## Vehicles

| Item | Value | Status | Basis |
|---|---|---|---|
| Scout rover mass | 1,025 kg | cited | Perseverance (NASA) |
| Scout rover top speed | 0.04 m/s | cited | Perseverance ~4.2 cm/s (NASA) |
| Scout drive power | 40 W | assumed | Chosen so the cap is reached on flat ground |
| Buggy top speed | 3.6 m/s (13 km/h) | cited | Apollo LRV nominal top speed |
| Buggy boost speed | 5.0 m/s (18 km/h) | cited | LRV record speed, Apollo 17 |
| Buggy slope rating | 25° | cited | LRV design slope capability |
| Buggy turn radius | 3.1 m | cited | LRV, four-wheel steering (~3 m) |
| Buggy mass (loaded) | 700 kg | derived | LRV 210 kg + two suited crew + equipment |
| Buggy motor power | 1,500 W | assumed | LRV had ~750 W (4 × 0.25 hp) for lunar gravity; doubled for Mars g |
| Wheel–regolith grip μ | 0.6 | assumed | Gives a ~27° climb limit, consistent with the 25° LRV rating |
| Rolling resistance | 0.08 | assumed | Loose regolith; to verify against rover mobility literature |
| Boost duration | 4 s, 8 s recharge | assumed | Viewer feature; does not affect planning |

## Models

| Model | What it does | Status | Notes / limits |
|---|---|---|---|
| Rover force model (browser) | Gravity along slope, motor force limited by power (P/v) and grip (μN), rolling resistance, brakes up to grip | derived | Rigid body following the terrain under four wheel contacts; no suspension dynamics, wheel sinkage, or slip-ratio soil model. Climbs, stalls and slides emerge from forces, no hard slope walls |
| Route planner speed | speed = flat · exp(−k·\|tan slope\|) | assumed | Earth hiking-function shape (Tobler-style). Placeholder for time estimates only |
| Route planner safety | Cells over the profile's no-go slope (plus a 4 m buffer) are impassable | assumed limits | Policy, not physics: astronaut 25°, scout 20°, buggy 25° (LRV-cited) |
| Least-cost path | skimage `MCP_Geometric` on a 4 m grid | derived | Standard cost-distance; path resampled to 5 m |
| Consumables | (drive + stop time) / EVA limit, minus reserve | assumed | Time-based only; no O₂/power model yet |
| Site search | Threshold layer → connected regions → score by value, area, slope | derived | Evidence (layer, instrument, values) returned with every site |

## Query engine and science tools (Sameer: `atlas_core/`, `sitebuild/`, MCP)

Narrative summary for judges: `docs/science-validity.md`. These rows cover the real Jezero site our
MCP query engine uses (`data/build/site/jezero`, built by `sitebuild/`; interim science layers until
Stream A's official ones land).

| Item | Value | Status | Basis |
|---|---|---|---|
| Mineral evidence (query engine) | Ordinal tiers (rover-confirmed / strong orbital / weak orbital / not detected / no coverage), 7 minerals | cited | Parente et al. CRISM map of Jezero (Zenodo, CC BY 4.0), 36 m; black = no coverage. Not abundances: no source gives 0–1 abundance at this scale |
| Rover-confirmed minerals | Olivine + carbonate at samples 4–7; olivine at 14–15 | cited | Farley et al. 2022 (Science); Williams et al. 2023 and Hurowitz et al. 2023. Positions derived from waypoints (10–50 m) |
| Geologic units | USGS SIM 3464, shifted +25.8 m E / −98.1 m N to our terrain | cited (shift derived) | Shift measured against HiRISE hillshade; 16 tiles agree within 25 m |
| Sun position | Mars24 algorithm | cited | Allison & McEwen 2000; NASA GISS algorithm page. Reproduces its Jan 6 2000 example (Ls 277.18758, EOT −5.18774) and Perseverance landing LMST 15:53 (JPL) |
| Paleolake levels | −2410 m uppermost foresets; breach ca. −2400 m; others in `docs/lake-levels.md` | cited (abstracts) | Mangold et al. LPSC 2024; Villette et al. 2025 (modelled). Datum not stated in sources; floods today's topography |
| Terrain layers | Aspect, curvature, TPI, TRI, local relief, exposure index | derived | Horn 1981; Zevenbergen & Thorne 1987; Weiss 2001; Riley et al. 1999; Evans 1972. `outcrop_exposure` is our heuristic and is inflated in the noisy-DTM zone |
| Line of sight | Radial sweep with curvature correction | derived | Franklin & Ray 1994; R = 3,396,190 m; no refraction, Fresnel zone or antenna model |
| EVA return time | 2 × one-way walking time | assumed | Isotropic placeholder speed curve; reserve 20 % from profile (assumed) |
| DTM quality flags | Seams 7.4 %, noisy zones 8.2 % of site | derived | Our detectors (`sitebuild/seams.py`, `noise.py`); flags only, elevations never altered |

## Validation done

| Check | Result | Where |
|---|---|---|
| Physics unit checks (flat, tilt, climb speed = P / (m g (sin θ + Crr cos θ)), slip at grip limit, brake hold vs slide, dip in/out, momentum stall) | 12 / 12 pass | `web/src/rover/sandbox/physics.check.ts` |
| Height-tile decode vs source terrain | < 0.08 m error (1 m posts) | same |
| Planner: no route crosses a no-go cell (50 random pairs); flat-terrain duration = distance / speed within 2 % | Pass | `api/tests/test_api.py` |
| Query-engine slope map vs real Perseverance traverse (24.9 km, MMGIS) | Traverse spends 0.41 % of its length on > 15° vs 4.79 % of site (≈ 12× less), 0.10 % on > 20° vs 1.96 % (≈ 20× less). The "99.9 % safe/caution" headline alone is weak because 98 % of the site is safe | `devapi` `GET /validation`, real site |
| CTX 20 m vs HiRISE 2 m slope, 2 km around landing site | Max 11.7° vs 33.4°; > 15°: 0 % vs 0.81 % | `docs/compare_ctx_vs_hirise_stats.txt` |
| Query engine search / travel / visibility vs independent slow oracles | Agree (all cases tested) | `tests/oracle.py`, `tests/oracle_travel.py`, `tests/test_visibility.py` |
| Site frame (query engine) vs `pipeline/site_cfg.py` | 0.3 mm max | `tests/test_site_build.py` |
| Hazard map vs real Perseverance traverse | **Pending real data** (synthetic traverse: 100 % safe/caution) | `GET /validation` |
| Planned vs driven leg length | **Pending real data** (synthetic: ratios 0.98–1.05) | `GET /validation` |

## Known gaps

- Mineral layers and test terrains are synthetic; real CRISM and HiRISE data replace them via Stream A.
- Grip, rolling resistance and buggy power are assumptions; worth one literature check each.
- The browser (force model) and API auto-drive (speed curve + slope stop) use different vehicle models;
  the API's is the conservative planning estimate.
- No thermal, power or life-support model; consumables are time only.

## Change log

- 2026-11-14: Force-based rover physics replaces hard slope blocking; buggy profile from Apollo LRV specs.
- 2026-11-14: Planner, site search, validation endpoint; slope limits as safety policy.
- 2026-11-14: Query engine science rows (real CRISM tiers, rover-confirmed samples, Mars24 sun, paleolake levels, terrain layers, line of sight) and real-traverse validation added (Sameer).
