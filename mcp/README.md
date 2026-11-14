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
| `ATLAS_API_URL` | REST API base URL (default `http://localhost:8000`). |
| `ATLAS_SITE_DIR` | Read by `devapi`, not by the MCP server: site directory written by `pipeline/`. |
| `ATLAS_SITE=synthetic` | Read by `devapi`: force the synthetic site. |

If the API is down every tool says `API not reachable at ...; start it with python -m devapi`. With the synthetic
site every text result starts with `[SYNTHETIC TEST SITE - not Mars data]` (structured results carry
`"synthetic": true`). The `find_sites` description lists the API's search targets if the API is up when the
server starts.

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
- The server holds no state; location sets, markers, routes and the rover live in the API process.
- Blocking rover tools (`drive_rover`, `goto_location`, `follow_route`) compute the motion instantly by default;
  `animate=true` plays it live in the viewer (real seconds, scaled by `speed_factor`).
- Tests: `.venv/bin/python -m pytest tests/test_server.py tests/test_devapi.py` (MCP tools run against devapi
  in-process via httpx ASGI; no network).
