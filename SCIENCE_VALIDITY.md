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

## Validation done

| Check | Result | Where |
|---|---|---|
| Physics unit checks (flat, tilt, climb speed = P / (m g (sin θ + Crr cos θ)), slip at grip limit, brake hold vs slide, dip in/out, momentum stall) | 12 / 12 pass | `web/src/rover/sandbox/physics.check.ts` |
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

## Change log

- 2026-11-14: Force-based rover physics replaces hard slope blocking; buggy profile from Apollo LRV specs.
- 2026-11-14: Planner, site search, validation endpoint; slope limits as safety policy.
