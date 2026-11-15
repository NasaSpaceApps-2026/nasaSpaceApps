# Science validity log

*This is the detailed log. Judges: read the summary [docs/science-validity.md](docs/science-validity.md) first. The auto-generated checks (rover tilt vs DEM, CTX vs HiRISE, assumptions register) are in [docs/science-validation.md](docs/science-validation.md). Last reviewed Nov 15, 2026.*

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
| Mineral layers (query engine, used by the copilot) | Ordinal evidence tiers, 7 minerals; not abundances | cited | Real CRISM map (Parente et al.), see "Query engine and science tools" below |
| Mineral layers (Yousef's API dev layers) | `layer_min_*` 0–1 | **synthetic** by default | `api/scripts/make_dev_layers.py` rasters labelled "not Mars data" (`api/fixtures/analysis`). Under `stack/up.sh` the API instead loads an export of our site, where `layer_min_*` = real CRISM tier / 4 (test mapping, tier indices not abundances; `devapi/export_ares_dir.py`) |
| Test sites (fixture, playground) | Analytic / seeded-noise terrain | **synthetic** | Used only for development; manifest says so |

## Vehicles

| Item | Value | Status | Basis |
|---|---|---|---|
| Scout rover mass | 1,025 kg | cited | Perseverance (NASA) |
| Scout rover top speed | 0.04 m/s | cited | Perseverance ~4.2 cm/s (NASA) |
| Scout drive power | 40 W | assumed | Chosen so the cap is reached on flat ground |
| Buggy concept | Performance crew buggy, faster than the LRV | assumed | Geometry, slope rating and turn radius from the Apollo LRV; speed and power are a concept, not a flown vehicle |
| Buggy top speed | 24 m/s (86 km/h) | assumed | Concept. Reference: LRV 3.6 m/s (13 km/h) nominal (cited) |
| Buggy boost speed | 40 m/s cap (144 km/h); ~33 m/s reached in a 6 s boost from cruise | assumed | Concept. Reference: LRV record 5 m/s (18 km/h), Apollo 17 (cited) |
| Buggy motor power | 25 kW (40 kW boost) | assumed | Concept. Reference: LRV ~750 W (4 × 0.25 hp) (cited). Acceleration is grip-limited at low speed (~(μ − Crr)·g ≈ 1.9 m/s², 0–86 km/h in ~13 s) |
| Buggy slope rating | 25° | cited | LRV design slope capability (advisory limit in the viewer) |
| Buggy turn radius | ~3.1 m at the rear axle (~3.8 m at the body centre) | derived | Front-wheel steering only (40° max lock). The LRV reached ~3 m with four-wheel steering (cited) |
| Buggy drivetrain | Rear-wheel drive; centre of mass 0.25 m behind the wheelbase midpoint (~61 % rear weight) | assumed | Rear-engine dune-buggy layout. Consequence: only rear-axle grip pushes, so climbs depend on rear-axle load. With μ 1.2 rear grip would allow ~47°, but wheelie control (motor keeps ≥ 15 % of the weight on the front wheels, as EV traction control does) caps steady climbs at ~36.5°: cos θ·a_r − sin θ·h ≥ 0.15 cos θ·L |
| Buggy mass (loaded) | 700 kg | derived | LRV 210 kg + two suited crew + equipment |
| Wheel–regolith grip μ | 2.4 (buggy), 0.6 (scout) | assumed | Buggy: concept paddle tyres, 4× the 0.6 baseline on request (well above loose regolith; grip now exceeds the roll-over and nose-over limits, so stability control below sets the real limits; 0.6 gave a ~27° all-wheel-drive limit, consistent with the 25° LRV rating). Scout unchanged |
| Rolling resistance | 0.08 | assumed | Loose regolith; to verify against rover mobility literature |
| Boost duration | 6 s, 10 s recharge | assumed | Viewer feature; does not affect planning |
| Jumps, landings, roll-overs | Emerge from the rigid-body model (no special modes): wheels leave the ground when the springs fully extend; flight under Mars g with free rotation; landing absorbed by springs, dampers and bump stops | derived | Check: 12 m/s over a 1.5 m / 16 m bump gives 1.75 s air, lands at 3.6 m/s, settles to ride height within 2 s |
| Buggy suspension tune | 1.15 Hz sprung frequency, damping ratio 0.38, 0.25 m travel | assumed | Soft long-travel off-road buggy tune (typical 1–1.5 Hz, ζ 0.3–0.5); not an LRV value |
| Buggy spring / damper per wheel | k = (m/4)(2πf)² = 9.1 kN/m; c = 2ζ√(k m/4) = 0.96 kN·s/m | derived | From mass and the tune above |
| Buggy static sag | m g / (4k) = g / (2πf)² = 7.1 cm (28 % of travel) | derived | Mars g makes the same spring sag 2.6× less than on Earth |
| Buggy inertia | Box 3.3 × 2.17 × 1.2 m, 700 kg: pitch 719, roll 359, yaw 910 kg·m² | derived | Uniform-box approximation; real mass distribution not modelled |
| Buggy CG height / wheel radius | 0.75 m above ground; 0.42 m wheels; wheelbase 2.3 m, track 1.8 m | derived / assumed | CG from the 50° static tip angle (atan(0.9/0.75)); geometry matches the 3D model (LRV-like) |
| Buggy steering | Ackermann wheel angles for curvature steer/3.1 m; rear wheels counter-steer below 3 m/s, front-only above 8 m/s; full lock in 0.4 s | assumed | LRV had 4-wheel steering (cited); the speed blend is a stability choice for this concept |
| Scout suspension | 1.0 Hz, ζ 0.6, 0.3 m travel; 0.2625 m wheels; CG 1.15 m; box 3.0 × 2.7 × 2.2 m | assumed (wheel size, dimensions cited) | Rocker-bogie approximated by four soft corner springs. Perseverance wheel 52.5 cm, body ~3 × 2.7 × 2.2 m (NASA) |
| Scout steering | Corner wheels point along the turn; yaw rate commanded (40°/s on screen), not produced by wheel forces | assumed | Perseverance turns in place by steering its corner wheels |
| Tyre model | Friction circle: √(F_long² + F_lat²) ≤ μ F_z per wheel; lateral force opposes slip velocity (stiff limit); drive torque beyond μ F_z spins the wheel; rolling resistance C_rr F_z; locked-wheel brakes | derived | Buggy (μ 2.4): parked brakes hold past its ~50° static tip angle; cornering and braking set by stability control (below); scout (μ 0.6): climb limit atan(μ − C_rr) ≈ 27.5°. No slip-ratio curve or soil sinkage |
| Coasting | Buggy coasts with no throttle (rolls back on slopes > ~4.6° = atan(Crr)); Space brakes | derived | Scout's drive motors hold it (auto_brake) |

## Models

| Model | What it does | Status | Notes / limits |
|---|---|---|---|
| Rover rigid-body model (browser) | 6-DOF chassis (quaternion attitude, box inertia) on four spring-damper wheels; tyre forces from per-wheel load inside μF_z; motor limited by power (P/v) and grip; chassis corners collide with the ground | derived | Sequential-impulse solver with an implicit spring-damper, 1/240 s sub-steps (slow rovers: up to 0.1 s, ≤ 5 cm per step; ≤ 400 per frame), so it is stable at rest, at 20 m/s and at 3200× time-lapse. Massless wheels, rigid DEM ground (no sinkage), no slip-ratio soil model. Climbs, stalls, slides, jumps and roll-overs emerge from forces |
| Route planner speed | speed = flat · exp(−k·\|tan slope\|) | assumed | Earth hiking-function shape (Tobler-style). Placeholder for time estimates only |
| Route planner safety | Cells over the profile's no-go slope (plus a 4 m buffer) are impassable | assumed limits | Policy, not physics: astronaut 25°, scout 20°, buggy 25° (LRV-cited) |
| Interpolated (filled) DEM cells | Planner: passable, cost x(1 + 4.0), reported in route warnings (default policy `caution`; `block` = impassable, `allow` = no penalty). FSD `caution`: speed x0.5 and an event on entry; `block` stops with `blocked_hazard`. No-data cells are always impassable | assumed | Policy, not physics: the 4.0 penalty and 0.5 speed factor are placeholders (api/profiles.yaml, rover.py); elevation on filled cells is estimated, so slope there is less certain. Changed 2026-11-15 from FSD always stopping |
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
| Physics checks (static sag = m g/4k with no jitter; climb speed on 20° = P / (m g (sin θ + C_rr cos θ)) within 0.3 %; scout slips on 30°; brakes hold 20° with no creep, slide on 35°/33°; coasting rollback = g (sin θ − C_rr cos θ); braking time v/(μ g); dip in/out; jump: ≥ 0.3 s air, per-frame Δz/Δpitch bounded, settles in 2 s; cornering radius at 12 m/s ≥ v²/(μ g) (68 m vs 59 m) and 3.1 m at 2 m/s; boost never airborne on flat; scout stable at 16× time-lapse) | 19 / 19 pass | `web/src/rover/sandbox/physics.check.ts` |
| Height-tile decode vs source terrain | < 0.08 m error (1 m posts) | same |
| Planner: no route crosses a no-go cell (50 random pairs); flat-terrain duration = distance / speed within 2 % | Pass | `api/tests/test_api.py` |
| Query-engine slope map vs real Perseverance traverse (24.9 km, MMGIS) | Traverse spends 0.41 % of its length on > 15° vs 4.79 % of site (≈ 12× less), 0.10 % on > 20° vs 1.96 % (≈ 20× less). The "99.9 % safe/caution" headline alone is weak because 98 % of the site is safe | `devapi` `GET /validation`, real site |
| CTX 20 m vs HiRISE 2 m slope, 2 km around landing site | Max 11.7° vs 33.4°; > 15°: 0 % vs 0.81 % | `docs/compare_ctx_vs_hirise_stats.txt` |
| Query engine search / travel / visibility vs independent slow oracles | Agree (all cases tested) | `tests/oracle.py`, `tests/oracle_travel.py`, `tests/test_visibility.py` |
| Site frame (query engine) vs `pipeline/site_cfg.py` | 0.3 mm max | `tests/test_site_build.py` |
| Hazard map vs real Perseverance traverse (Mars 2020 MMGIS traverse, 279 drives, 23.9 km inside the site, sols 13–1019; slope from the HiRISE DTM doi:10.5066/P9REJ9JN on the 2 m grid; rover_scout classes: caution ≥ 15°, no-go ≥ 20°) | **derived**, 2026-11-14: hazard_agreement **0.999** (99.6 % safe, 0.3 % caution, 0.1 % no-go by length). Baseline over the whole 8 km site: 94.9 % safe, 3.0 % caution, 2.1 % no-go, so the rover drove on our no-go ground ~20× less often than chance. Map slope along the drives: median 3.1°, p95 9.7°, p99 12.7°, max 23.0°. Limit: most of the site is flat, so agreement alone is weak; the baseline comparison is the stronger signal | `GET /validation`, `api/tests/test_real_data.py` |
| Planned vs driven leg length (8 consecutive real waypoint pairs with 100–1500 m driven, spread over sols 129–1001; actual = rover odometry `dist_total_m`; planner profile rover_scout) | **derived**, 2026-11-14: route_realism planned/driven median **1.00**; 6 of 8 pairs 0.88–1.03; 0.77 (sol 713→714) and 0.14 (sol 1000→1001: 136 m driven between points 19 m apart, an out-and-back) where the rover did not drive point to point. All 8 planned within the 20° limit (max 8.7°). Limit: these drives are near-straight on flat floor, so this checks plausibility, not detour choice | `GET /validation`, `api/tests/test_real_data.py` |

## Known gaps

- Test terrains (fixture, playground) are synthetic, and so are Yousef's API dev mineral layers when it runs on its defaults. The copilot's data (query engine) is real HiRISE and CRISM; the official pipeline layers (Stream A) have not replaced our interim copies.
- Grip, rolling resistance and buggy power are assumptions; worth one literature check each.
- API auto-drive uses the steady-state version of the browser force model (power/grip balance on the signed
  grade, `api/ares_api/vehicle.py`), not the full rigid-body/suspension dynamics; the route planner still
  uses the speed-curve estimate for time.
- No thermal, power or life-support model; consumables are time only.
- Suspension tune, CG height and the scout's spring stand-in for the rocker-bogie are assumptions; the
  buggy nose-dives off sharp crests and can roll over (both physical for this geometry, but untested
  against a real vehicle).
- Tyre lateral force is the stiff (slip-velocity cancelling) limit of a tyre, saturating at μF_z; no
  cornering-stiffness / slip-angle curve, so the transition into a slide is abrupt.
- Wheels are massless (no unsprung mass, no wheel-hop dynamics) and the ground is the 1 m bilinear DEM:
  at 15+ m/s the post-to-post slope kinks make the wheels chatter.

## Change log

- 2026-11-15: Cross-linked the three science documents; corrected the mineral-layer rows (query engine uses real CRISM tiers; the API's dev layers are synthetic only on its defaults).

- 2026-11-14: First run on the real Jezero build (Caden's pipeline: HiRISE DTM 2 m grid, Mars 2020 traverse). `/validation` now uses every traverse drive (not just the first), length-weighted, with a site-wide baseline, and re-plans 8 odometry-measured waypoint pairs; results in the table above.

- 2026-11-14: Buggy grip doubled again (μ 1.2 → 2.4). Grip now exceeds the tip limits, so added stability control: steering curvature capped at v²κ ≤ 0.85 g (track/2)/h ≈ 3.8 m/s² (roll-over threshold 1.2 g; R ≈ 38 m at 12 m/s, vs ~37 m at μ 1.2), and ABS caps braking at 0.6 × a_f/h ≈ 1.12 g (nose-over at 1.87 g; 60 % margin for suspension dive overshoot): 24 m/s → 0 in 5.8 s. Climbs stay wheelie-limited at ~36°.

- 2026-11-14: Buggy grip doubled (μ 0.6 → 1.2) with motor wheelie control; climbs to ~36°, brakes hold to ~50°, turn radius at speed halves. API `grade_speed` now uses axle load transfer + the same wheelie limit.

- 2026-11-14: API simulator drives the crew buggy by default with the same power/grip force balance as the viewer (stalls where driven-wheel grip runs out); vehicle switchable (`POST /rover/vehicle`).

- 2026-11-14: Buggy switched to rear-wheel drive with front-only steering and a rear-biased centre of mass; climb limit ~20°, body squats ~0.8° at rest.

- 2026-11-14: Buggy concept doubled: 24 m/s top, 40 m/s boost cap, 25 kW. At 24 m/s the grip-limited turn radius on Mars is ~260 m (v² / μg).

- 2026-11-14: Browser rover physics v3: rigid-body chassis with four spring-damper wheels, friction-circle
  tyres and chassis ground contact replace the kinematic terrain-following body (no more snapping on
  landing; suspension now real). Cornering is grip-limited (known gap closed). New profile fields
  suspension_hz, damping_ratio, suspension_travel_m.

- 2026-11-14: Ballistic airborne phase (jumps over crests at speed); check: 12 m/s over a 1.5 m bump gives 1.7 s air, matching the hand calculation.

- 2026-11-14: Buggy raised to a concept performance vehicle (12 m/s, 10 kW; LRV values kept as reference); coasting and rollback on slopes, Space brake.

- 2026-11-14: Force-based rover physics replaces hard slope blocking; buggy profile from Apollo LRV specs.
- 2026-11-14: Planner, site search, validation endpoint; slope limits as safety policy.
- 2026-11-14: Query engine science rows (real CRISM tiers, rover-confirmed samples, Mars24 sun, paleolake levels, terrain layers, line of sight) and real-traverse validation added (Sameer).
