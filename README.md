# Ares Atlas

NASA Space Apps 2026, challenge "Interplanetary Survival Guide: Martian Map". Ares Atlas lets Claude answer spatial questions about Jezero crater (find landing pads within a walking time of base, search for mineral evidence once those layers exist, plan routes) and operate a rover, through an MCP server over HiRISE and CTX terrain data. A chat panel shows Claude's answers and every tool call.

## Folder map

Ownership follows `docs/scope-c-d.md` (and `PRD.md` for streams A and B). The scope doc names `api/` for Yousef; this repo currently has the stand-in `devapi/` instead.

| Folder | What | Owner |
|---|---|---|
| `pipeline/`, `data/`, `contracts/fixtures/` | Data pipeline, tiles, raw and built data | Caden |
| `web/` (except below) | 3D viewer: globe, terrain, cameras, layers UI | Rithvik |
| `web/src/rover/`, API (`devapi/` stand-in) | REST/WS API, routes, rover sim, manual driving | Yousef |
| `mcp/`, `agent/`, `web/src/panels/chat/`, `docs/` | MCP server, agent backend, system prompt, chat panel, demo and submission | Sameer |
| `chat-dev/` | Standalone dev page for the chat panel | Sameer |
| `atlas_core/`, `sitebuild/`, `tests/` | Query engine library, site builder (HiRISE/CTX to analysis grid), tests | shared / see `sitebuild/README.md` |
| `contracts/` | Tool and API contracts | shared |

## Quick start

Python deps are in `pyproject.toml`; use the project venv (`.venv/bin/python`).

```bash
# 1. REST API stand-in on :8000 (real site data; omit ATLAS_SITE_DIR for a synthetic test site)
ATLAS_SITE_DIR=data/build/site/jezero .venv/bin/python -m devapi --port 8000

# 2. MCP server over HTTP on :8765/mcp (or stdio: .venv/bin/python mcp/server.py)
.venv/bin/python mcp/server.py --http

# 3. Agent backend on :8766 (needs a key; or replay a recording, see agent/README.md)
export ANTHROPIC_API_KEY=...
.venv/bin/python -m agent

# 4. Chat panel dev page on http://localhost:5199
cd chat-dev && npm install && npm run dev
# no key? `npm run mock` starts a scripted fake agent on :8766 instead of step 3
```

Tests: `.venv/bin/python -m pytest -q` and `cd chat-dev && npx vitest run && npm run build`.

To use the MCP server from Claude Code or Claude Desktop, see `mcp/README.md`.

## Docs

- `PRD.md`: product requirements and contracts
- `docs/scope-c-d.md`: scope for the backend/MCP/agent half
- `contracts/mcp-tools.md`: MCP tool and condition-language spec
- `mcp/README.md`, `agent/README.md`, `devapi/README.md`, `sitebuild/README.md`, `web/src/panels/chat/README.md`
- `data/SOURCES.md`: data sources and citations
- `docs/project-page.md`: draft submission text; `docs/demo-script.md`: demo scripts

Bubble imagery is NASA Mars 2020 data via USGS and the PDS. Links, file sizes, and the coordinate mismatch between the HiRISE GeoTIFFs and the rover waypoints are in [docs/bubble-imagery.md](docs/bubble-imagery.md). The rasters and rover frames are not in git.
