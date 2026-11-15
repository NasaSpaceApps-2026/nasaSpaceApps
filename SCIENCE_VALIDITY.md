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
| Buggy concept | Performance crew buggy, faster than the LRV | assumed | Geometry, slope rating and turn radius from the Apollo LRV; speed and power are a concept, not a flown vehicle |
| Buggy top speed | 12 m/s (43 km/h) | assumed | Concept. Reference: LRV 3.6 m/s (13 km/h) nominal (cited) |
| Buggy boost speed | 20 m/s cap (72 km/h); ~17 m/s reached in a 6 s boost | assumed | Concept. Reference: LRV record 5 m/s (18 km/h), Apollo 17 (cited) |
| Buggy motor power | 10 kW (16 kW boost) | assumed | Concept. Reference: LRV ~750 W (4 × 0.25 hp) (cited) |
| Buggy slope rating | 25° | cited | LRV design slope capability (advisory limit in the viewer) |
| Buggy turn radius | 3.1 m | cited | LRV, four-wheel steering (~3 m) |
| Buggy mass (loaded) | 700 kg | derived | LRV 210 kg + two suited crew + equipment |
| Wheel–regolith grip μ | 0.6 | assumed | Gives a ~27° climb limit, consistent with the 25° LRV rating |
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
| Tyre model | Friction circle: √(F_long² + F_lat²) ≤ μ F_z per wheel; lateral force opposes slip velocity (stiff limit); drive torque beyond μ F_z spins the wheel; rolling resistance C_rr F_z; locked-wheel brakes | derived | Gives climb limit atan(μ − C_rr) ≈ 27.5°, brake hold atan μ ≈ 31°, cornering v²/R ≤ μ g. No slip-ratio curve or soil sinkage |
| Coasting | Buggy coasts with no throttle (rolls back on slopes > ~4.6° = atan(Crr)); Space brakes | derived | Scout's drive motors hold it (auto_brake) |

## Models

| Model | What it does | Status | Notes / limits |
|---|---|---|---|
| Rover rigid-body model (browser) | 6-DOF chassis (quaternion attitude, box inertia) on four spring-damper wheels; tyre forces from per-wheel load inside μF_z; motor limited by power (P/v) and grip; chassis corners collide with the ground | derived | Sequential-impulse solver with an implicit spring-damper, 1/240 s sub-steps (slow rovers: up to 0.1 s, ≤ 5 cm per step; ≤ 400 per frame), so it is stable at rest, at 20 m/s and at 3200× time-lapse. Massless wheels, rigid DEM ground (no sinkage), no slip-ratio soil model. Climbs, stalls, slides, jumps and roll-overs emerge from forces |
| Route planner speed | speed = flat · exp(−k·\|tan slope\|) | assumed | Earth hiking-function shape (Tobler-style). Placeholder for time estimates only |
| Route planner safety | Cells over the profile's no-go slope (plus a 4 m buffer) are impassable | assumed limits | Policy, not physics: astronaut 25°, scout 20°, buggy 25° (LRV-cited) |
| Least-cost path | skimage `MCP_Geometric` on a 4 m grid | derived | Standard cost-distance; path resampled to 5 m |
| Consumables | (drive + stop time) / EVA limit, minus reserve | assumed | Time-based only; no O₂/power model yet |
| Site search | Threshold layer → connected regions → score by value, area, slope | derived | Evidence (layer, instrument, values) returned with every site |

## Validation done

| Check | Result | Where |
|---|---|---|
| Physics checks (static sag = m g/4k with no jitter; climb speed on 20° = P / (m g (sin θ + C_rr cos θ)) within 0.3 %; scout slips on 30°; brakes hold 20° with no creep, slide on 35°/33°; coasting rollback = g (sin θ − C_rr cos θ); braking time v/(μ g); dip in/out; jump: ≥ 0.3 s air, per-frame Δz/Δpitch bounded, settles in 2 s; cornering radius at 12 m/s ≥ v²/(μ g) (68 m vs 59 m) and 3.1 m at 2 m/s; boost never airborne on flat; scout stable at 16× time-lapse) | 19 / 19 pass | `web/src/rover/sandbox/physics.check.ts` |
| Height-tile decode vs source terrain | < 0.08 m error (1 m posts) | same |
| Planner: no route crosses a no-go cell (50 random pairs); flat-terrain duration = distance / speed within 2 % | Pass | `api/tests/test_api.py` |
| Hazard map vs real Perseverance traverse | **Pending real data** (synthetic traverse: 100 % safe/caution) | `GET /validation` |
| Planned vs driven leg length | **Pending real data** (synthetic: ratios 0.98–1.05) | `GET /validation` |

## Known gaps

- Mineral layers and test terrains are synthetic; real CRISM and HiRISE data replace them via Stream A.
- Grip, rolling resistance and buggy power are assumptions; worth one literature check each.
- The browser (force model) and API auto-drive (speed curve + slope stop) use different vehicle models;
  the API's is the conservative planning estimate.
- No thermal, power or life-support model; consumables are time only.
- Suspension tune, CG height and the scout's spring stand-in for the rocker-bogie are assumptions; the
  buggy nose-dives off sharp crests and can roll over (both physical for this geometry, but untested
  against a real vehicle).
- Tyre lateral force is the stiff (slip-velocity cancelling) limit of a tyre, saturating at μF_z; no
  cornering-stiffness / slip-angle curve, so the transition into a slide is abrupt.
- Wheels are massless (no unsprung mass, no wheel-hop dynamics) and the ground is the 1 m bilinear DEM:
  at 15+ m/s the post-to-post slope kinks make the wheels chatter.

## Change log

- 2026-11-14: Browser rover physics v3: rigid-body chassis with four spring-damper wheels, friction-circle
  tyres and chassis ground contact replace the kinematic terrain-following body (no more snapping on
  landing; suspension now real). Cornering is grip-limited (known gap closed). New profile fields
  suspension_hz, damping_ratio, suspension_travel_m.

- 2026-11-14: Ballistic airborne phase (jumps over crests at speed); check: 12 m/s over a 1.5 m bump gives 1.7 s air, matching the hand calculation.

- 2026-11-14: Buggy raised to a concept performance vehicle (12 m/s, 10 kW; LRV values kept as reference); coasting and rollback on slopes, Space brake.

- 2026-11-14: Force-based rover physics replaces hard slope blocking; buggy profile from Apollo LRV specs.
- 2026-11-14: Planner, site search, validation endpoint; slope limits as safety policy.
