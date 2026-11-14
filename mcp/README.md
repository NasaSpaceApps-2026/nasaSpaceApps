# Ares Atlas MCP server

Spatial queries over the Jezero crater site for Claude. Spec: `contracts/mcp-tools.md`.
Tools: `get_site_overview`, `list_layers`, `describe_layer`, `describe_location` (alias `sample_terrain`),
`find_locations`, `find_regions`, `nearest`, `get_set`; resource `atlas://site/overview`.
Every tool returns a compact text summary plus `structuredContent` (full dict). User-correctable
problems (unknown layer, bad location) come back as normal tool results with `isError: true`, a message
and a hint, never a stack trace.

## Run

From the repo root, with the project venv:

```bash
# stdio (default)
ATLAS_SITE_DIR=/path/to/site .venv/bin/python mcp/server.py

# streamable HTTP on http://127.0.0.1:8765/mcp
ATLAS_SITE_DIR=/path/to/site .venv/bin/python mcp/server.py --http [--host 127.0.0.1] [--port 8765]
```

## Environment

| Variable | Meaning |
|---|---|
| `ATLAS_SITE_DIR` | Site directory written by `pipeline/` (`site.yaml`, rasters, `profiles.yaml`, `pois.geojson`). |
| `ATLAS_SITE=synthetic` | Force the synthetic test site even if `ATLAS_SITE_DIR` is set. |

With neither set, the server serves the synthetic test site and every text result starts with
`[SYNTHETIC TEST SITE — not Mars data]` (structured results carry `"synthetic": true`).

## Claude Code

```bash
claude mcp add ares-atlas -e ATLAS_SITE_DIR=/abs/path/to/site -- /abs/path/to/repo/.venv/bin/python /abs/path/to/repo/mcp/server.py
# HTTP instead:
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
      "env": {"ATLAS_SITE_DIR": "/abs/path/to/site"}
    }
  }
}
```

## Notes

- Requires the `mcp` 2.x SDK (`MCPServer`, not the 1.x `FastMCP`).
- `mcp/` has no `__init__.py` on purpose: it must not shadow the installed `mcp` package.
- Location sets live for the server process only (one `SetStore` per process).
- Tests: `.venv/bin/python -m pytest tests/test_server.py tests/test_catalog_describe.py`.
