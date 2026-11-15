# Ares Atlas

**NASA Space Apps 2026, challenge "Interplanetary Survival Guide: Martian Map".**

Ares Atlas is a mission-planning prototype: decision support for a Mars EVA at Jezero crater, built on real NASA and USGS data. You fly from a whole-planet globe down to street level, drive a rover or crew buggy over HiRISE terrain, and ask an AI copilot spatial questions ("find a landing pad within a 30 minute walk of base", "where has olivine been detected, and how sure are we?"). The copilot answers by calling our open MCP server, so every number comes from a computed tool result and every tool call is visible in the chat. It is a research and planning prototype, not operational software: thresholds marked "assumed" are team assumptions, and synthetic test terrain is always labelled "not Mars data".

Judges: start with [docs/science-validity.md](docs/science-validity.md) (evidence), [docs/demo-script.md](docs/demo-script.md) (what to try), and [docs/project-page.md](docs/project-page.md) (submission text).

## What it does

- **Globe and street view.** Whole-planet Mars globe with Jezero fan and Olympus Mons scarp patches; press Drive to drop into street view in the same window (`usage.md`).
- **Rover and buggy driving.** Browser rigid-body vehicle physics (suspension, friction-circle tyres, Mars gravity) for a crewed buggy concept; an API rover simulator with a slope-stop safety policy (FSD) for scouting. Vehicle numbers are labelled cited or assumed in [SCIENCE_VALIDITY.md](SCIENCE_VALIDITY.md).
- **AI copilot via MCP, with visible tool calls.** Chat panel shows the answer and a collapsible card per tool call. The same MCP server works from Claude Desktop or Claude Code (`mcp/README.md`).
- **Science tools** (served by `atlas_core`, exposed through MCP): landing-pad search with walk-time limits, mineral evidence tiers (7 CRISM minerals plus rover-confirmed samples), route planning and assessment, EVA return-time envelope and point of no return, radio line of sight and vantage points, sun position and terrain shadow, paleolake flooding at published levels, terrain layers (aspect, curvature, relief, outcrop exposure).
- **Honest answers.** Results carry thresholds with their source (cited or assumed), data quality flags, and diagnostics for empty results. Evidence is an ordinal tier, never a made-up percentage.

## Architecture

```
 viewer (web/, :5173)  --\
 chat dock / chat-dev    +--> agent (:8766) --> MCP server (:8765/mcp) --+--> rover API (:8000)  Yousef's ares_api:
 (:5199)               --/    LLM tool loop      39 tools                |      rover sim, FSD, routes, markers, WS
                              SSE: text +                               |
                              tool calls                                +--> query engine devapi (:8001)
                                                                               atlas_core: search, evidence, LOS, sun, lake, EVA
                                              both read the same site data: data/build/site/jezero (HiRISE DTM, CRISM tiers, ...)
```

The MCP server checks at startup that both backends load the same site frame and prefixes every result with a banner if they differ ([docs/frames.md](docs/frames.md)). Contracts: [contracts/mcp-tools.md](contracts/mcp-tools.md), [contracts/api.openapi.yaml](contracts/api.openapi.yaml).

## Run it (one path)

Prerequisites (once):

```bash
python3.11 -m venv .venv && .venv/bin/pip install numpy scipy scikit-image rasterio pyyaml "mcp[cli]" fastapi uvicorn httpx websockets pyogrio shapely sse-starlette anthropic
python3.11 -m venv api/.venv && api/.venv/bin/pip install -r api/requirements.txt    # Yousef's API
(cd chat-dev && npm install)                                                         # add web/ and web/src/rover/sandbox for --with-viewer
cp .env.example .env    # then set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY
.venv/bin/python -m sitebuild.build_site    # builds data/build/site/jezero (gitignored); needs network, downloads HiRISE/CTX windows and science sources
```

Then:

```bash
stack/up.sh                 # API :8000, query engine :8001, MCP :8765, agent :8766, chat page + minimap :5199
stack/up.sh --with-viewer   # also the planet viewer (:5173) and rover sandbox (:5174)
stack/down.sh               # stop what up.sh started
```

Open `http://localhost:5199/` and ask "Find 3 flat landing pads within a 30 minute walk of base." Details, ports and the `--frame crater` option: [stack/README.md](stack/README.md). Viewer-only and sandbox worlds (no keys, no backend): [usage.md](usage.md). Without an LLM key: `cd chat-dev && npm run mock` (scripted fake agent), or replay a recorded session ([agent/README.md](agent/README.md), recordings in `agent/replays/`).

Tests: `.venv/bin/python -m pytest -q` (511 passed on Nov 15) and `cd chat-dev && npx vitest run` (44 passed).

## Folder map

| Folder | What | Owner |
|---|---|---|
| `pipeline/`, `data/`, `contracts/fixtures/` | Tile data pipeline, raw and built data, fixtures | Caden |
| `web/` (except below) | 3D viewer: globe, terrain, cameras, layers UI | Rithvik |
| `api/`, `web/src/rover/` | Rover/route REST + WS API, rover sim, browser driving | Yousef |
| `mcp/`, `agent/`, `web/src/panels/chat/`, `chat-dev/`, `stack/`, `docs/` | MCP server, agent backend, chat panel, launcher, docs | Sameer |
| `atlas_core/`, `devapi/`, `sitebuild/`, `tests/` | Query engine, its REST server, site builder, tests | Sameer |
| `contracts/` | Tool and API contracts | shared |

More: [PRD.md](PRD.md), [docs/scope-c-d.md](docs/scope-c-d.md), [PROGRESS.md](PROGRESS.md), [CLAUDE.md](CLAUDE.md).

## Evidence, credits, AI use, licence

- **Science validity:** [docs/science-validity.md](docs/science-validity.md) (summary) -> [SCIENCE_VALIDITY.md](SCIENCE_VALIDITY.md) (detailed log) -> [docs/science-validation.md](docs/science-validation.md) (auto-generated checks).
- **Data credits:** [data/SOURCES.md](data/SOURCES.md); the science layers' sources are in `data/build/site/jezero/SOURCES_site.md` (written by `sitebuild`, not in git).
- **AI use:** AI coding assistants (Claude Code, Cursor) were used to write much of the code; the in-app copilot uses an LLM (DeepSeek via API in the current build; Claude-compatible) through our MCP server. Full statement: [docs/project-page.md](docs/project-page.md).
- **Licence:** code is MIT, see [LICENSE](LICENSE).

### Data licences

- **NASA/USGS data** (HiRISE and CTX DTMs and orthomosaics, USGS Mars 2020 TRN products): public domain / CC0 as stated on the USGS product pages ([data/SOURCES.md](data/SOURCES.md) says to verify the exact text on each page before redistribution). Credit USGS Astrogeology Science Center and NASA/JPL/MRO. The IAU/USGS gazetteer names are public domain per the source.
- **Perseverance traverse, waypoints and sample list** (NASA/JPL-Caltech Mars 2020 MMGIS and NASA pages): no licence text on the endpoints; NASA content is generally not copyrighted; credit NASA/JPL-Caltech.
- **CRISM mineral map** (Parente et al., Zenodo, doi:10.5281/zenodo.5575824): **CC BY 4.0, attribution required.** We use it as ordinal evidence tiers, not as the authors' product.
- **USGS geologic map SIM 3464:** USGS; public-domain status not verified (see `SOURCES_site.md`).
- **Viking MDIM 2.1 colour** (via NASA Mars Trek): NASA/JPL/USGS, used only as low-frequency false colour.
- Published values from papers (Farley et al. 2022; Williams et al. 2023; Hurowitz et al. 2023; Mangold et al. 2024; Villette et al. 2025) are cited, not redistributed.
- Data is not in git (`data/raw/`, `data/build/` are ignored); `sitebuild` and `pipeline` fetch it. No NASA logos are used.

## Known limitations

- Landing, walking and rover thresholds are team assumptions (labelled `assumed`); the walking-speed curve is an Earth hiking-function placeholder. Candidate citations are in [docs/threshold-sources.md](docs/threshold-sources.md) and are not applied yet.
- One site: an 8 km square at Jezero, 2 m grid. 36% of the site has no CRISM coverage; the tools say "no coverage", never "not detected". Sample-site positions are derived from rover waypoints (10-50 m uncertainty).
- The copilot's live pass rate is 19 of 24 eval cases (DeepSeek); not yet scored with Claude. A language model can still err despite the grounding checks ([docs/eval-run-deepseek.md](docs/eval-run-deepseek.md)).
- The planet viewer and sandbox worlds serve different (partly synthetic) frames from the copilot's real 8 km site. They line up only in the `--frame crater` setup ([docs/frames.md](docs/frames.md)).
- Vehicle dynamics and the buggy concept are models with assumed grip, power and suspension; there is no thermal, power or life-support model.
- Not tested from a clean machine; first-time data build needs network and several hundred MB of downloads.
