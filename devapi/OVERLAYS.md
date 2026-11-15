# Image overlays for the Jezero viewer

Implemented in `devapi/routes_overlay.py` (raster helpers in `atlas_core/overlay.py`). CORS allows any origin, so any localhost viewer port works. Errors are HTTP 422 `{"error", "hint"}` (unknown kind, unknown or out-of-range parameter).

## Endpoints

- `GET /overlay/{kind}.png?<params>`: `image/png` RGBA covering exactly the site square (row 0 = north edge, col 0 = west edge, extent +-`half_m` in both axes), at most 2048 px per side (the 4096 grid is reduced 2x: nearest for classes and tiers, mean for continuous fields and masks). Transparent where there is no data or nothing to show. Results are cached (LRU, 48 entries) by kind and parameters, and the `.png` and `.json` of one parameter set share one computation.
- `GET /overlay/{kind}.json?<same params>`: `{"title", "legend": [{"color": "#rrggbb", "label"}], "source", "caveat", "stats": {...}, "bounds": {"half_m", "lat0", "lon0", "px", "res_m"}, "kind"}`.
- `GET /overlay/kinds`: `{"kinds": [{"kind", "title", "params": [{"name", "type": "number|integer|enum", "default", "min", "max", "unit" | "options"}]}], "image": {...}}`. The `lake` entry also carries `named_levels` (`id`, `level_m`, `label`, `kind`, `citation`) for slider tick marks.
- `GET /traverse.geojson`: GeoJSON FeatureCollection, one LineString (the Perseverance traverse, with `properties.x`/`y` per vertex in site metres) plus Point waypoints sorted by sol. Each waypoint has `properties.sol` and `properties.vertex_index`, so for a sol slider draw `line.coordinates[:vertex_index + 1]` of the last waypoint with `sol <= slider`. `metadata.sol_first` and `sol_last` give the slider range.

Pixel to site metres: `x = -half_m + (col + 0.5) * res_m`, `y = half_m - (row + 0.5) * res_m`, where `res_m = 2 * half_m / px`.

## Kinds and parameters

| kind | params (default, range) | stats |
|---|---|---|
| `evidence` | `layer` = olivine (default), carbonate, fe_mg_smectite, kaolinite, high_ca_pyroxene, low_ca_pyroxene, mg_smectite_2 | `tiers_km2`, `detected_km2`, `no_coverage_fraction` |
| `geology` | none | `units_km2` |
| `slope` | `profile` = eva_walkable (default), rover_scout, crew_buggy (and any profile with a travel block) | `caution_slope_deg`, `max_slope_deg`, `limits_source` ("assumed"), `safe_fraction`, `caution_fraction`, `no_go_fraction` |
| `lake` | `level` m (default -2410; site elevation min..max) | `area_km2`, `max_depth_m`, `mean_depth_m`, `named_levels`, `touches_site_edge` |
| `sun` | `ltst` hours 0..24 (12), `sol` (current mission sol) | `sun_elevation_deg`, `sun_azimuth_deg`, `sunrise_ltst`, `sunset_ltst`, `lit_fraction`, `cast_shadow_fraction`, `self_shadow_fraction`, `sun_up`, `utc` |
| `viewshed` | `x`, `y` site metres (0, 0), `h` eye height m (1.7), `radius` m (3000, max 8000) | `visible_km2`, `visible_fraction`, `farthest_visible_m` |
| `envelope` | `x`, `y` (0, 0), `budget_min` (240), `reserve` 0..0.99 (0.2), `profile` (eva_walkable) | `area_km2`, `max_radius_m`, `one_way_limit_min`, `max_one_way_min`, `usable_min` |

Styling: evidence tiers are yellow (confirmed on the ground), cyan (strong orbital), violet (weak orbital); not-detected is transparent and no-coverage is a faint grey hatch. Geology uses an Okabe-Ito palette. Slope is green / orange / red. Lake is a light-to-dark blue depth ramp. Sun and shadow is a dark translucent tint on shadowed cells (the whole site at night). Viewshed is an amber tint. Envelope is a yellow to purple ramp by one-way walking time.

The envelope uses the isotropic travel model: reachable if out plus back time (twice the one-way time) is within `budget_min * (1 - reserve)`. Walking speeds and slope limits are team assumptions and are labelled as such in `caveat`.

## Examples

```
GET /overlay/evidence.png?layer=carbonate
GET /overlay/lake.json?level=-2450
GET /overlay/sun.png?ltst=16.5&sol=1000
GET /overlay/viewshed.png?x=500&y=-300&h=1.7&radius=3000
GET /overlay/envelope.json?x=0&y=0&budget_min=240&reserve=0.2&profile=eva_walkable
GET /overlay/kinds
GET /traverse.geojson
```

`GET /overlay/lake.json?level=-2450` returns, abridged:

```json
{"title": "Ancient lake at -2450 m", "legend": [{"color": "#a6dbff", "label": "Shallow water"}],
 "source": "MRO / HiRISE (stereo DTM, MOLA-registered); ...", "caveat": "...",
 "stats": {"level_m": -2450.0, "area_km2": 0.0, "max_depth_m": 0.0, "named_levels": [...]},
 "bounds": {"half_m": 4096.0, "lat0": 18.452, "lon0": 77.418, "px": 2048, "res_m": 4.0}, "kind": "lake"}
```

## Timings (Jezero, 4096 px site, one process, cold / cached)

evidence, geology, slope about 0.18 s / 1 ms; lake about 0.6 s / 1 ms; viewshed about 1.0 s / 1 ms (0.7 s at radius 5000 after warm-up); sun about 1.3 s (night 0.2 s) / 1 ms; envelope about 1.6 s / 1 ms (the walking-time search in `atlas_core.travel` takes 1.1 s of that).
