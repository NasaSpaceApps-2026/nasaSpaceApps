# What the MCP server needs from the REST API (Sameer → Yousef)

Status: proposal, Nov 14. Builds on `docs/scope-c-d.md` "Interface between Yousef and Sameer".
The MCP server is a stateless wrapper over the REST API (scope doc). This file lists every
endpoint it calls, so Yousef's stub and real API can match.

Until Yousef's `api/` exists, `devapi/` is an in-repo stand-in that implements all of these on
top of `atlas_core` (the tested query engine). It is also a reference implementation Yousef can
adopt or replace. Switch with `ATLAS_API_URL` (default `http://localhost:8000`).

## Endpoints from the scope doc (unchanged)

`GET /site`, `GET /terrain/sample`, `POST /terrain/profile`, `GET /pois`, `POST /route`,
`POST /route/science`, `GET /rover/state`, `POST /rover/command`, `GET /fsd/status`,
`POST /view`, `POST /view/snapshot`, `GET /validation`, `WS /ws`, `POST /markers`,
`GET /markers`, `DELETE /markers/{id}`, `POST /search/sites`, `GET /search/targets`, `GET /health`.

## Additions (proposed): spatial query engine

These expose `atlas_core` (contracts/mcp-tools.md). Request bodies are the keyword arguments of
the matching `atlas_core` function, as JSON; responses are its return dict. Errors: HTTP 422 with
`{"error": str, "hint": str | null}` for AtlasError.

| Endpoint | atlas_core function |
|---|---|
| `GET /layers?kind=&query=` | `catalog.list_layers` |
| `GET /layers/{id}` | `catalog.describe_layer` |
| `GET /overview` | `catalog.site_overview` (plus markers and search targets, per scope doc) |
| `POST /describe` | `describe.describe_location` body `{at, radius_m?, layers?}` |
| `POST /search/locations` | `search.find_locations` |
| `POST /search/regions` | `search.find_regions` |
| `POST /search/nearest` | `travel.nearest` body `{from, to, metric?, profile?, limit?}` |
| `GET /sets/{id}?offset=&limit=&sort=&descending=` | `SetStore.page` |
| `POST /events/agent` | broadcast an `agent.event` on WS (`{tool, args_summary, result_summary}`) |

Location references everywhere also accept `"marker:<id>"` (scope doc).

`POST /search/sites` (scope doc) is implemented as `find_regions` over the target's evidence layer
plus reachability, mapped to the scope doc's response fields. Proposed change: replace the 0–1
`score` with the evidence tier (`best_tier`, `best_tier_name`), because CRISM products give
detections and band depths, not abundances.

## Route and rover semantics the MCP tools rely on

- `POST /route` → `{route_id, points[{x,y,z}], distance_m, duration_min, ascent_m, max_slope_deg,
  hazard_cells, consumables{used, margin}, stops[], warnings[], feasible, reason?, nearest_reachable?}`
- `POST /rover/command` with `{cmd: drive|turn|goto|follow_route|stop|teleport|fsd_enable|fsd_disable, ...}`
  Blocking commands return `{state, stop_reason}` with stop_reason one of
  `arrived, blocked_slope, blocked_hazard, replanned, stopped_by_user, timeout`.
- `POST /view` with `{mode?: globe|orbit|chase|ground, target?: <location ref>, layers?: {id: visible},
  highlights?: [ids]}`.
