# MCP tools spec: spatial query layer (v0.1 draft)

Owner: Stream D. Status: draft for team review. Scope: the query tools Claude uses to answer
complex spatial questions about the Jezero site. Rover driving, routing and view tools stay as
listed in PRD Contract 5 and are out of scope here, except where they consume location sets.

## 1. Goal

Claude should answer compound questions such as:

- "Find places with good landing conditions."
- "Find carbonate outcrops the rover can reach in 2 hours, and the nearest safe landing pad to each."
- "What is at this spot, and how sure are we?"

We do this with a few general tools that compose, not one tool per question. Claude does the
reasoning. The tools return exact computations over NASA data, with provenance and confidence,
and never claim more certainty than the data supports.

## 2. Design principles

1. **Exhaustive over heuristic.** The site grid is small (4,096 x 4,096 at 2 m). Score every cell
   with numpy instead of using heuristic search, so the best answer is never missed.
2. **Graph search only for travel.** Dijkstra is used when a question involves getting
   somewhere (walking time or distance), never for static filtering.
3. **Evidence, not verdicts.** Every value carries its source, resolution and confidence tier.
   No invented probabilities.
4. **"No coverage" is not "no".** Missing data, filled cells and seams are reported as unknown,
   never as safe or absent.
5. **Handles, not coordinate dumps.** Search results are stored server-side as location sets.
   Tools return a `set_id` and a short summary; later tools take the `set_id` as input.
6. **Empty results explain themselves.** When nothing matches, report how many cells pass each
   condition alone, so Claude knows which condition to relax.
7. **Short results.** Text summary under about 300 tokens, key numbers first, then a next-step
   hint. Full data in MCP `structuredContent`.

## 3. Core concepts

### 3.1 Coordinates and location references

Site frame per PRD Contract 1: metres, x east, y north, origin at site centre. Every tool that
takes a location accepts a **location reference**:

| Form | Example | Meaning |
|---|---|---|
| Point | `{"x": 120, "y": -340}` | A site-frame point |
| Lat/lon | `{"lat": 18.4447, "lon": 77.4508}` | Converted to the site frame |
| POI id | `"poi:octavia_e_butler_landing"` | A named point of interest |
| Keyword | `"rover"`, `"base"` | Current rover position, mission base |
| Set | `"set:a1b2"` | Every member of a location set |

### 3.2 Layers and the layer catalogue

Every dataset is a **layer**, described by a catalogue entry. Claude reads it to know what a
layer can and cannot answer.

```yaml
id: slope_2m
title: Slope over 2 m baseline
kind: continuous            # continuous | categorical | evidence | points
units: degrees
resolution_m: 2
measures: Surface slope from the HiRISE DTM, Horn method, 2 m baseline.
does_not_measure: Slopes of features smaller than about 3 m (individual rocks, small ripples).
quality: Seam rows in the HiRISE mosaic are masked as unknown. CTX-filled cells are flagged.
coverage: site                  # site | partial (with a mask layer) | global
source:
  mission: MRO
  instrument: HiRISE
  product: Mars 2020 TRN HiRISE DTM Mosaic (JEZ_hirise_soc_006, 1 m)
  citation: Fergason et al. 2020, USGS, doi:10.5066/P9REJ9JN
  url: https://astrogeology.usgs.gov/search/map/mars_2020_terrain_relative_navigation_hirise_dtm_mosaic
```

Categorical layers (geologic units, hazard classes) list their classes with descriptions.
Mineral layers are added later through the same schema, without changing any tool.

### 3.3 Evidence tiers

For anything detected rather than measured (minerals, geologic interpretations), values are an
ordinal tier, not a probability:

| Tier | Name | Meaning |
|---|---|---|
| 4 | `confirmed_in_situ` | Measured by a rover instrument at or near this location |
| 3 | `strong_orbital` | Clear orbital spectral detection above the source's strong threshold |
| 2 | `weak_orbital` | Orbital detection near threshold, or partly obscured by dust |
| 1 | `inferred_from_unit` | The mapped geologic unit is known to contain it; no direct detection here |
| 0 | `not_detected` | Covered by data, nothing detected (absence of evidence, not evidence of absence) |
| null | `no_coverage` | No data covers this location |

An **evidence layer** stores the tier per cell plus the contributing source ids. Evidence layers
are built by the pipeline from mineral and geology sources. Tier definitions per source live in
the catalogue, with citations.

### 3.4 Profiles

A profile is a named, cited bundle of conditions and parameters, stored in `profiles.yaml`
(shared with Stream C). Tools accept `profile` as shorthand; explicit conditions override it.

```yaml
landing_pad:
  description: Touchdown pad for a crewed lander. Pad scale, not a full landing ellipse.
  footprint_radius_m: {value: 50, source: assumed}
  conditions:
    - {layer: slope_2m, op: "<=", value: 10, source: "TBD: cite"}
    - {layer: rough_5x5, op: "<=", value: 0.5, source: "TBD: cite"}
    - {layer: hazard_class, op: not_in, value: [dune, no_go, unknown], source: "TBD: cite"}
eva_walkable:
  travel: {flat_speed_mps: 0.9, max_slope_deg: 25, speed_curve: tobler_placeholder}
  conditions:
    - {layer: slope_2m, op: "<=", value: 25, source: "TBD: cite"}
rover_scout:
  travel: {flat_speed_mps: 0.04, max_slope_deg: 20}
```

Every threshold is either cited or labelled `assumed`; tools pass these labels through to their
output.

### 3.5 Location sets (handles)

A location set is a server-side result of a search, referenced by `set_id`.

```json
{
  "set_id": "set:a1b2",
  "kind": "spots",                 // spots (footprint-sized points) | regions (connected areas)
  "created_by": "find_locations",
  "query": { "...": "the full normalised query, for reproducibility" },
  "count": 37,
  "members": [
    {"id": "a1b2.1", "x": 812, "y": -1220, "lat": 18.43, "lon": 77.47,
     "score": 2.1, "clear_radius_m": 64, "area_m2": null,
     "attributes": {"slope_2m_mean": 2.1, "slope_2m_max": 6.4, "walk_time_min": 22}}
  ]
}
```

Sets live for the server session. Members are sorted by score. Sets are immutable; operations
create new sets.

## 4. Tools (v1)

### 4.1 `get_site_overview()`

The first call in every session. Returns: site bounds and coordinate frame in one sentence; layer
ids grouped by kind, with one-line `measures`; profile names; key POIs (about 10); rover state;
pointers to `describe_layer` and the evidence tier scale. Also exposed as MCP resource
`atlas://site/overview`. Under 500 tokens.

### 4.2 `list_layers(kind?, query?)` and `describe_layer(id)`

`list_layers` returns ids, titles and one-line `measures`. `describe_layer` returns the full
catalogue entry, including `does_not_measure`, quality notes, classes and citation. The tool
description tells Claude to call `describe_layer` before relying on an unfamiliar layer.

### 4.3 `describe_location(at, radius_m = 0, layers?)`

Everything known at one place.

- `radius_m = 0`: point values. Elevation bilinear; categorical and evidence layers nearest-cell.
- `radius_m > 0`: footprint statistics (mean, max, share of each class, best evidence tier).
- Always includes data quality at the point (HiRISE, CTX fill, seam, no data) and the nearest
  rover ground truth (sample site or in-situ measurement) with its distance.
- `at` may be a set; then it returns a compact table for up to 10 members.

Replaces PRD `sample_terrain` (kept as an alias).

### 4.4 `find_locations(conditions?, profile?, footprint_radius_m?, within?, objective?, spacing_m?, limit = 5)`

Find footprint-sized spots that satisfy all conditions.

**Conditions** (all ANDed; OR is expressed as `any_of`):

```json
{"layer": "slope_2m", "op": "<=", "value": 10}
{"layer": "geologic_unit", "op": "in", "value": ["Seitah", "Maaz"]}
{"evidence": "carbonate", "min_tier": "strong_orbital"}
{"any_of": [ {"evidence": "carbonate", "min_tier": "weak_orbital"},
             {"evidence": "olivine", "min_tier": "weak_orbital"} ]}
```

**Within** (optional reachability or distance limit):

```json
{"from": "base", "max": 120, "metric": "walk_time_min", "profile": "eva_walkable"}
{"from": "set:a1b2", "max": 500, "metric": "straight_m"}
```

Metrics: `straight_m`, `walk_m`, `walk_time_min`. With several `from` locations, the limit means
"within reach of the nearest one".

**Objective** (one primary objective; ties broken by distance to the first `from`, else by
id): `flattest`, `largest_clear_area`, `closest`, `highest`, `lowest`, `best_evidence:<name>`.

**Algorithm:**

1. Build a boolean mask per condition on the 2 m analysis grid. Unknown cells fail every
   condition.
2. AND the masks; then compute the Euclidean distance transform of the failing cells. A cell is a
   valid centre if its distance to the nearest failing cell is at least `footprint_radius_m`. The
   distance value is reported as `clear_radius_m`.
3. If `within` is set: compute travel cost from all `from` locations at once (multi-source
   Dijkstra, `skimage.graph.MCP_Geometric` on the 4 m planning grid, cells above the profile's
   max slope impassable) and mask by the limit. Straight-line distance uses a distance transform
   instead.
4. Score valid centres by the objective.
5. Spread: pick the best centre, exclude everything within `spacing_m` (default
   `max(2 * footprint_radius_m, 100)`), repeat until `limit` or no centres remain. The full
   ranked list is stored in the set; only the top `limit` appear in the text result.
6. Return the set id, the top results with attributes, the profile thresholds used with their
   sources, and diagnostics.

**Diagnostics** (always included; essential when empty):

```
No spots found. Cells passing each condition alone:
  slope_2m <= 10 ............. 71%
  rough_5x5 <= 0.5 ........... 64%
  hazard_class not dune ...... 88%
  all three .................. 41%
  + footprint radius 50 m .... 0.3%
  + within 60 min walk ....... 0%
Hint: relax the time limit or the footprint radius.
```

### 4.5 `find_regions(conditions, min_area_m2 = 400, within?, objective?, limit = 5)`

Same condition language as `find_locations`, but returns connected areas (for example carbonate
outcrops or a geologic unit), not footprint-sized spots. Algorithm: mask, then connected
components (8-connectivity), drop those below `min_area_m2`, then rank. Each member reports area,
centroid, bounding box, a representative interior point (the cell farthest from the region edge)
and per-region statistics. Same diagnostics.

### 4.6 `nearest(from, to, metric = "walk_time_min", profile = "eva_walkable", limit = 5)`

For each member of `from`, find the nearest member of `to`, then rank the pairs by cost.

- "Nearest landing pad to each carbonate outcrop": `from = carbonate_set`, `to = pad_set`.
- Implementation: for up to 25 `from` members, run one Dijkstra per member and read off the
  cost to every `to` member. Above 25, run one multi-source Dijkstra seeded from all `to` members
  and record which seed reaches each cell first.
- Travel is direction-independent in this solver (uphill and downhill cost the same), so times
  are labelled approximate. A path for any pair can be produced with `plan_route`.
- Returns a new set of pairs: `{from_id, to_id, cost, straight_m}`.

### 4.7 `get_set(set_id, offset = 0, limit = 10, sort?)`

Page through a stored set, or re-sort it by any attribute.

## 5. Result format

Every tool returns:

- **Text content**: one-line headline with key numbers, up to about 10 lines of results, the
  assumptions used (thresholds with `cited` or `assumed`), and a next-step hint.
- **`structuredContent`**: the full result as JSON, following the schemas above.

Errors are tool results, not exceptions, and say what to try instead, for example:
`Unknown layer "slope". Available slope layers: slope_2m, slope_4m. Call list_layers(kind="continuous").`

## 6. Accuracy and verification

- **Reference implementation**: every search tool has a slow, simple, independent version
  (loops, no clever indexing) in `mcp/tests/oracle.py`. Tests check the fast tools against it on
  the fixture terrain and on random windows of the real data.
- **Property tests**: no returned spot has any cell inside its footprint that fails a condition;
  no reachable spot is beyond the limit by the oracle's Dijkstra; unknown cells never pass.
- **Ground-truth checks**: slope at five hand-picked places matches a manual calculation; the
  landing site POI lies within 10 m of the published landing coordinates.
- **Question set**: `mcp/evals/questions.yaml`, each with an expected behaviour, run through
  Claude over MCP and scored on correct locations, correct confidence, cited sources and no
  numbers absent from tool results. Starter questions:

| # | Question | Expected behaviour |
|---|---|---|
| 1 | What is the terrain like at the Perseverance landing site? | describe_location; slope with baseline; source HiRISE |
| 2 | Find 3 flat spots big enough for a 50 m pad within a 1 hour walk of the landing site | find_locations with footprint and walk_time; spaced results; thresholds labelled |
| 3 | Find good landing spots | find_locations with profile landing_pad; says pad scale, not landing ellipse |
| 4 | Find a pad with zero slope anywhere | empty result; diagnostics; suggests a realistic threshold |
| 5 | Is this crater rim safe to walk? | describe_location with radius; reports max slope, not only mean |
| 6 | What can the slope layer not tell me? | describe_layer; quotes does_not_measure |
| 7 | Find spots outside the HiRISE coverage | reports coverage limits; does not treat no-data as flat |
| 8 | Find carbonate outcrops reachable in 2 hours and the nearest landing pad to each | find_regions(evidence) then find_locations(profile) then nearest; tiers stated (needs mineral data) |
| 9 | Where has carbonate been confirmed by the rover? | only tier 4 evidence; cites sample sites (needs mineral data) |
| 10 | Is there olivine at the delta front? | answers with tier and source; separates orbital from in-situ (needs mineral data) |

## 7. Module layout

```
atlas_core/        shared library, imported by mcp/ and api/
  catalog.py       layer catalogue loading and validation
  grid.py          analysis grid, coordinate conversion, location reference parsing
  conditions.py    condition language to masks
  search.py        find_locations, find_regions, spreading, diagnostics
  travel.py        cost surfaces, Dijkstra wrappers, nearest
  sets.py          location set store
mcp/
  server.py        MCP tool definitions, text formatting, structuredContent
  tests/           unit, property and oracle tests
  evals/           question set and runner
```

The MCP server calls `atlas_core` directly, so it does not wait for Stream C's REST API. The API
imports the same library, so there is one implementation.

## 8. Open decisions

- Landing pad thresholds: find citable values; check whether the Mars 2020 TRN hazard map is
  public and use it to validate the `landing_pad` profile.
- Slope definition: 2 m baseline as the primary value, plus footprint maximum (proposed).
- Default metric for "near": `walk_time_min` (proposed); `straight_m` always reported alongside.
- Set lifetime: session only (proposed); persist to disk only if the replay file needs it.
