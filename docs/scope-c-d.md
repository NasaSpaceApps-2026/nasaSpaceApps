# Scope: Streams C + D (Yousef + Sameer)

Nov 14, 2026 · decided by Yousef and Sameer · supersedes PRD where they conflict

Hand this file plus `PRD.md` to your coding agent. `PRD.md` is the background; this file is the plan for our half.

## Who owns what

| Person | Owns | Folders |
|---|---|---|
| Caden | Data pipeline, bubble refine loop (Stream A) | `pipeline/`, `data/`, `contracts/fixtures/` |
| Rithvik | Viewer: globe, terrain rendering, cameras, layers UI (Stream B) | `web/` except the folders below |
| **Yousef** | Backend logic + REST/WS API, route planning, rover sim, manual rover driving in the browser | `api/`, `contracts/api.openapi.yaml`, `web/src/rover/` |
| **Sameer** | MCP server, agent backend, system prompt, chat panel, agent quality, demo/submission | `mcp/`, `agent/`, `web/src/panels/chat/`, `docs/` |

Split rule: **Yousef owns everything below the REST line; Sameer owns everything above it.** Sameer never reads rasters; Yousef never writes prompts.

```
 user ──► chat panel ──► agent backend ──► MCP server ──►│ REST + WS API ──► terrain / routes / rover sim / markers
          (Sameer)        (Sameer)          (Sameer)     │ (Yousef)              (Yousef)
                                                         │
 user ──► viewer: terrain (Rithvik) + rover driving (Yousef) ◄── WS events ──┘
```

## Product decisions (made, don't relitigate)

- **Rover, not a ship.** The user drives a rover over the real terrain. No flight mode.
- **Getting around fast = ask the agent.** "Take me to the delta front" → agent teleports the rover and/or flies the camera there.
- **Physics v1 = terrain-following.** Sample height under 4 wheels each frame, set pitch/roll from them, enforce slope limits. No suspension or soil sim. Make it more realistic later, step by step, only after v1 works end to end.
- **Same rules in both modes.** Manual driving runs in the browser; auto driving (agent/FSD) runs in the API sim. Both read slope limits and speeds from `api/profiles.yaml` (served via `GET /site`), so they behave the same.
- **Markers.** Clicking the ground drops a marker. The agent can see markers and use them: "plan a route between my two markers."
- **Capable agent.** Must handle questions like "where are the best places to find carbonates?" and "plan a route between these two markers", always answering from tool results, never from memory.

## Interface between Yousef and Sameer

This is the contract. Freeze it at kickoff; change only if both agree. Yousef ships a **stub server returning canned JSON for every endpoint first**, so Sameer is never blocked.

Base URL `http://localhost:8000`. All positions are site-frame metres (PRD Contract 1). Anywhere a position is accepted, also accept a POI id (`"poi:<id>"`) or marker id (`"marker:<id>"`).

### REST endpoints

From PRD Contract 4 (unchanged): `GET /site`, `GET /terrain/sample`, `POST /terrain/profile`, `GET /pois`, `POST /route`, `POST /route/science`, `GET /rover/state`, `POST /rover/command`, `GET /fsd/status`, `GET /bubble/status`, `POST /view`, `POST /view/snapshot`, `GET /validation`, `WS /ws`.

New:

| Endpoint | Purpose |
|---|---|
| `POST /markers` | `{x, y, label?}` → marker `{id, x, y, z, lat, lon, label, created_by: "user"\|"agent"}` |
| `GET /markers` | All markers |
| `DELETE /markers/{id}` | Remove one |
| `POST /search/sites` | `{target, near?, radius_m?, max_slope_deg?, top_k?}` → ranked candidate sites (see below) |
| `GET /search/targets` | List of searchable targets (minerals, geology units) with source layer + mission |
| `GET /health` | Liveness |

`POST /search/sites` returns, per site: `id, x, y, z, lat, lon, score (0–1), area_m2, mean_slope_deg, distance_m from near, reachable (bool), evidence: [{layer, mission, instrument, value}]`. Evidence is required: the agent cites it.

### WebSocket events (additions to PRD)

| Direction | Type | Payload |
|---|---|---|
| Server → viewer | `marker.add` / `marker.remove` | Marker / id |
| Server → viewer | `search.results` | Site list, so the viewer can pin them |
| Viewer → server | `marker.click` | `{x, y}` from a ground click (server creates the marker and broadcasts `marker.add`) |

### MCP tools (Sameer builds; each maps to the endpoints above)

PRD Contract 5 tools, plus:

| Tool | Calls |
|---|---|
| `list_markers` | `GET /markers` |
| `place_marker` | `POST /markers` (agent-placed) |
| `find_sites` | `POST /search/sites`; description lists valid targets from `GET /search/targets` |
| `teleport_rover` | `POST /rover/command` `{cmd: "teleport", ...}` |
| `fly_camera` | `POST /view` (fly to a point/POI/marker) |

`get_site_overview` must include markers and available search targets.

## Yousef: backend + rover driving

**Stack:** Python 3.11, FastAPI, uvicorn, numpy, rasterio, scikit-image, shapely, pytest. Browser side: TypeScript + three.js inside Rithvik's app.

### Tasks in order

1. **Stub server (hour 0–2).** `contracts/api.openapi.yaml` + FastAPI app that returns canned JSON for every endpoint above; save responses in `contracts/fixtures/api/`. Push immediately and tell Sameer.
2. **Terrain service.** Load `contracts/fixtures/analysis/*.tif` once at startup (path from env `ARES_DATA_DIR`). Implement `/site`, `/terrain/sample`, `/terrain/profile`, `/pois`, `/health`. Serve `/tiles` and `/data` statically; CORS on.
3. **Markers.** In-memory store, endpoints, WS broadcast, `marker:<id>` resolution everywhere positions are accepted.
4. **Route planner.** PRD "Route planner" section: `skimage.graph.MCP_Geometric` on a 4 m grid, slope-dependent speed, no-go cells impassable, via points leg by leg, `feasible: false` with a reason + nearest reachable point.
5. **Site search.** `/search/targets` + `/search/sites`: threshold the target layer, find connected regions, score by layer value × area, penalise slope, check reachability with the planner, return top_k with evidence. Until Caden ships mineral layers, generate synthetic `layer_min_<name>.tif` rasters in `api/tests/fixtures/` (and ask Caden to add them to `make_fixture.py`).
6. **Rover sim + WebSocket hub.** PRD "Rover simulator": 10 Hz loop, `/rover/command` (drive, turn, goto, follow_route, stop, teleport, fsd_enable/disable), `rover.state` broadcasts, blocking commands return a stop reason.
7. **FSD policy.** Slope no-go, caution slowdown, hazard stop, one replan on block.
8. **Rover driving in the browser** (`web/src/rover/`). Rover mesh, W/A/S/D, terrain-following from 4 wheel samples, slope limit, chase camera, sends `rover.pose` at 5 Hz in manual mode, renders API poses in auto mode, any key press takes back control. Get a `sampleHeight(x, y)` function from Rithvik's terrain module; until it exists, build a standalone sandbox page that decodes the fixture height tiles itself (decode formula in PRD Contract 2).
9. **Science traverse + validation.** `/route/science`, `/validation` (PRD sections).
10. **Hardening.** Input validation, error messages an agent can act on ("goal is on a 31° slope; nearest reachable point is …").
11. **Then, more realistic physics,** one step at a time: inertia/acceleration → per-wheel contact → simple suspension. Each step must keep 30 fps.

### Acceptance tests

- Every endpoint answers with the real implementation on fixture data; `pytest` passes with no real data present.
- `/route` across the fixture site returns in < 1 s; across 3 km of real data in < 3 s.
- No returned route crosses a no-go cell (test 50 random pairs).
- A route between two markers works by marker id.
- `/search/sites` on a synthetic mineral layer returns the planted hotspots ranked first, each with evidence.
- `follow_route` ends within 3 m of the goal; a blocked goal returns `feasible: false` with a usable message.
- In the browser, the rover never floats or sinks more than 0.2 m and tilts on slopes.

## Sameer: MCP, agent, chat

**Stack:** Python MCP SDK, FastAPI agent backend (port 8766), Claude API with tool use (latest Claude model), React + TypeScript for the chat panel.

### Tasks in order

1. **Tool descriptions (hour 0–2).** `contracts/mcp-tools.md`: every tool with purpose, when to use it, args with units, example call, example result. Draft the demo prompts (below).
2. **MCP server** (`mcp/`, port 8765). Stateless wrapper over Yousef's REST API, built against his stub. stdio + streamable HTTP transports. Results: compact text, key numbers first, < ~300 tokens, next-step hint, errors as readable results. Every call also posts an `agent.event` so the viewer shows it.
3. **Test from Claude Desktop / Claude Code** as an external MCP client. Tighten descriptions until the model picks the right tool unaided.
4. **Agent backend** (`agent/`, port 8766). `POST /chat` runs the tool-calling loop against the MCP server and streams text + tool events. 12-call cap. Context injected each turn: rover pose, camera position, markers.
5. **System prompt.** PRD "Agent system prompt outline", plus: cite layer + mission for every science claim; resolve "here", "my marker", "these two markers" from injected context; for "take me to X", use `teleport_rover` / `fly_camera`.
6. **Chat panel** (`web/src/panels/chat/`, 380 px right slot in Rithvik's app). Streaming messages, tool-call cards (name, args summary, result summary), suggested prompts.
7. **Agent evals.** A script that runs the demo prompts against the real API and checks tool choice + that every number in the reply appears in a tool result. Run it after every prompt or tool-description change.
8. **Replay file.** Record a good session; replay it through the same panel with no API key.
9. **Docs and submission.** Project page, AI-use disclosure, demo video (PRD "Demo script and submission checklist").

### Demo prompts that must work

| Prompt | Expected tool path |
|---|---|
| "Where are the best places to find carbonates near base?" | `find_sites` → `set_view` highlights → answer citing CRISM evidence |
| "Plan a route between my two markers under 15°." | `list_markers` → `plan_route` → `assess_route` |
| "Take me to the delta front." | `search_pois` → `teleport_rover` + `fly_camera` |
| "Plan a 4-hour Marswalk to the two best science targets." | `plan_science_traverse` → `assess_route` |
| "Scout that route; stop if unsafe and tell me why." | `fsd_enable` → `follow_route` → hazard report |
| "What's worth sampling near me?" | `search_pois` / `find_sites` near rover pose |

### Acceptance tests

- Claude Desktop lists all tools and completes every demo prompt with no hand-holding.
- Every number in an agent reply appears in a tool result from the same session (eval script checks).
- Each tool call shows in the chat panel within 1 s of starting.
- Replay works with no API key.

## Dependencies on other streams

| Need | From | By | Fallback |
|---|---|---|---|
| Mineral layers (`layer_min_<name>.tif`, 0–1 abundance, Contract 3 grid) | Caden | Integration 1 | Synthetic layers in `api/tests/fixtures/` |
| `web/` scaffold, WS client, shared store, 380 px chat slot | Rithvik | Hour ~4 | Yousef: standalone rover sandbox page; Sameer: standalone chat page |
| `sampleHeight(x, y)` from the terrain module | Rithvik | When terrain renders | Decode fixture height tiles directly |
| Ground-click → `marker.click` event | Rithvik (or Yousef in `web/src/rover/`) | Integration 2 | Agent-placed markers only |

## Open questions

- Mineral layer naming and value range: confirm `layer_min_<name>.tif`, 0–1, with Caden.
- Who holds the Claude API key for the agent backend.
- Whether auto driving should eventually move into the browser so manual and auto share one physics implementation.
