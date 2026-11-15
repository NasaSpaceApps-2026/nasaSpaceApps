# Ares Atlas MCP server

Spatial queries and rover operation for the Jezero crater site, for Claude. Spec: `contracts/mcp-tools.md`.
The server is a **stateless wrapper over the REST API** (`ATLAS_API_URL`, default `http://localhost:8000`).
Until `api/` (Yousef) exists, run the in-repo stand-in `devapi` (`devapi/README.md`).

Tools:
- Site and data: `get_site_overview`, `list_layers`, `describe_layer`, `describe_location` (alias `sample_terrain`, adds hazard class), `get_elevation_profile`, `search_pois`.
- Search: `find_locations`, `find_regions`, `find_sites` (minerals and geologic units with evidence), `nearest`, `get_set`.
- Routes: `plan_route`, `assess_route`, `plan_science_traverse`.
- Rover: `get_rover_state`, `drive_rover`, `goto_location`, `follow_route`, `teleport_rover`, `stop_rover`, `fsd_enable`, `fsd_disable`, `fsd_status`.
- Viewer and markers: `set_view`, `fly_camera`, `list_markers`, `place_marker`.
- Resource `atlas://site/overview`.

Every tool returns a compact text summary plus `structuredContent` (full JSON). User-correctable problems (unknown
layer, bad location, API unreachable) come back as normal tool results with `isError: true`, a message and a
hint, never a stack trace. Infeasible routes and refused drives are answers (`feasible: false`), not errors.
Every call also POSTs `/events/agent` (fire and forget; failures never affect the tool) so the viewer can show it.

## Run

From the repo root, with the project venv:

```bash
# 1. the API (synthetic site by default; ATLAS_SITE_DIR=/path/to/site for real data)
.venv/bin/python -m devapi --port 8000

# 2a. MCP over stdio (default)
.venv/bin/python mcp/server.py

# 2b. MCP over streamable HTTP on http://127.0.0.1:8765/mcp
.venv/bin/python mcp/server.py --http [--host 127.0.0.1] [--port 8765] [--api-url http://localhost:8000]
```

| Variable | Meaning |
|---|---|
| `ATLAS_API_URL` | Query-engine API base URL (default `http://localhost:8000`): layers, describe, find_*, sets, sun, lake, EVA. With no `ATLAS_ROVER_API_URL` it also serves rover, routes, markers and view. |
| `ATLAS_ROVER_API_URL` | Optional: Yousef's API (`api/`, ares_api). When set, rover state/commands, routes, markers, view, `find_sites`, POIs and terrain go there, translated by `mcp/backends.py`. Also `--rover-api-url`. |
| `ATLAS_SITE_DIR` | Read by `devapi`, not by the MCP server: site directory written by `pipeline/`. |
| `ATLAS_SITE=synthetic` | Read by `devapi`: force the synthetic site. |

If the API is down every tool says `API not reachable at ...; start it with python -m devapi`. With the synthetic
site every text result starts with `[SYNTHETIC TEST SITE - not Mars data]` (structured results carry
`"synthetic": true`). The `find_sites` description lists the API's search targets if the API is up when the
server starts.

## Quick start: everything at once

`stack/up.sh` starts Yousef's API (:8000, our real site exported), our `devapi` (:8001), this server over HTTP (:8765),
the agent (:8766) and the chat page with a live minimap (:5199); `stack/down.sh` stops them. See `stack/README.md`. Which
site frame each teammate component uses, and which combinations the chatbot can control consistently: `docs/frames.md`.

## Against Yousef's real API (split backends)

`devapi` stays the query engine on `ATLAS_API_URL`; Yousef's `ares_api` drives the rover. Both must load the same site,
or coordinates from `find_*` would send the rover to the wrong place. His API reads a different data layout, so export
our site once (symlinks for the big rasters), then start both:

```bash
python3.11 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements.txt     # once
.venv/bin/python -m devapi.export_ares_dir data/build/site/jezero ~/.cache/ares_jezero # once; test mapping, see its docstring
ARES_DATA_DIR=~/.cache/ares_jezero api/.venv/bin/uvicorn ares_api.main:app --app-dir api --port 8000 &
ATLAS_SITE_DIR=data/build/site/jezero .venv/bin/python -m devapi --port 8001 &
ATLAS_API_URL=http://localhost:8001 ATLAS_ROVER_API_URL=http://localhost:8000 .venv/bin/python mcp/server.py --http
```

At start the server compares `GET /site` of both backends and prints a loud stderr banner on a mismatch; while they
differ every tool result begins with `[FRAME MISMATCH: ...]` (origin more than 1 m apart) or `[EXTENT DIFFERS: ...]`.
Field-by-field differences, what is adapted and what his API lacks: `docs/api-compat.md`; a recorded live session:
`docs/rover-connection-test.md`. The agent backend reads rover pose and markers from `ATLAS_ROVER_API_URL` too.
Differences you will notice in tool output: `find_sites` ranks by his 0-1 score (not evidence tiers) and does not store
a set (use ids such as `site4`); `animate=false` drives about 400 m/s of display speed instead of instantly.

## Claude Code

```bash
claude mcp add ares-atlas -e ATLAS_API_URL=http://localhost:8000 -- /abs/path/to/repo/.venv/bin/python /abs/path/to/repo/mcp/server.py
# HTTP instead (start the server with --http first):
claude mcp add --transport http ares-atlas http://127.0.0.1:8765/mcp
```

## Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ares-atlas": {
      "command": "/abs/path/to/repo/.venv/bin/python",
      "args": ["/abs/path/to/repo/mcp/server.py"],
      "env": {"ATLAS_API_URL": "http://localhost:8000"}
    }
  }
}
```

## Notes

- Requires the `mcp` 2.x SDK (`MCPServer`, not the 1.x `FastMCP`).
- `mcp/` has no `__init__.py` on purpose: it must not shadow the installed `mcp` package.
- The server holds almost no state; location sets, markers, routes and the rover live in the API process(es). In split
  mode it only remembers the last planned route id and the `find_sites` site ids of this session (his API has no
  "latest route" default and no stored sets).
- Blocking rover tools (`drive_rover`, `goto_location`, `follow_route`) compute the motion instantly by default;
  `animate=true` plays it live in the viewer (real seconds, scaled by `speed_factor`).
- Tests: `.venv/bin/python -m pytest tests/test_server.py tests/test_devapi.py tests/test_backends.py` (MCP tools run
  against devapi in-process via httpx ASGI; the backend adapters and the frame check against canned ares_api responses;
  no network).
