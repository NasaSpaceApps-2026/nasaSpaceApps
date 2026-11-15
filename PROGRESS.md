# Progress

State of the repo on **Nov 15, 2026** (submission day), branch `sameer/mcp`. Per stream: done and remaining. Sources for each line are the files named; test counts were run on Nov 15 (511 Python tests, 44 chat-panel tests, all passing). Update after each meaningful change. Submission status: [docs/submission-checklist.md](docs/submission-checklist.md).

## Stream A: data pipeline (Caden)

Done
- Tile pipeline and fixtures: `pipeline/` (HiRISE DTM and 25 cm ortho windows, CTX context block, vectors, names, rover-world exports), `data/SOURCES.md`.
- Independent validation against Perseverance data: `pipeline/09_validate.py` -> [docs/science-validation.md](docs/science-validation.md) (rover tilt vs DEM r = 0.84; CTX vs HiRISE agreement).
- Site frame source of truth: `pipeline/site_cfg.py` (read by `sitebuild/`).

Remaining
- The official science layers (minerals, geology, traverse) have not replaced our interim copies built by `sitebuild/science.py` (see `data/build/site/jezero/SOURCES_site.md`). Caden to confirm.
- Bubble refine loop and vision loop (`pipeline/bubble`, `pipeline/vision_loop`): status not recorded here. Caden to confirm.
- Fix the stale `lat0/lon0` in the `crater`/`olympus` rover-world manifests (`docs/frames.md`).

## Stream B: viewer (Rithvik)

Done
- Cesium planet globe with Jezero fan and Olympus Mons scarp patches, street view with the pegman, site/orbit/ground/rover cameras (`usage.md`).
- Chat dock for the copilot mounted in the viewer (`web/src/panels/chat/AtlasDock.tsx`, `web/src/App.tsx`): tool results drawn on the viewer, camera follows the agent. Integration notes: `web/INTEGRATION.md` (owned by another builder; check it exists).

Remaining
- The viewer's default site is the synthetic fixture frame (`VITE_SITE_DIR=../contracts/fixtures`), not the copilot's real 8 km site; they line up only with `stack/up.sh --frame crater --with-viewer` (`docs/frames.md`). Rithvik and Sameer to confirm what the final demo shows.
- Re-check `cd web && npm run build` passes on the final commit.

## Stream C: backend and rover (Yousef)

Done
- REST/WS API (`api/ares_api`): terrain, markers, route planner, site search, rover simulator with FSD, science traverse, `/validation`; OpenAPI in `contracts/api.openapi.yaml`. `api/.venv/bin/python -m pytest api -q`.
- Browser rover driving (`web/src/rover/`): v3 rigid-body vehicle, crew buggy with boost, detailed buggy model, sandbox worlds (`cd web/src/rover/sandbox && npm run dev`; `npm run check` runs the physics checks). The API sim drives `crew_buggy` by default with the same power/grip balance (`api/ares_api/vehicle.py`).
- Playground test site (`api/scripts/make_playground.py`), persistence of markers/routes, readable 422 errors.
- Real-Jezero validation: hazard agreement 0.999 over 23.9 km of traverse; route realism median 1.00 on 8 waypoint pairs (`SCIENCE_VALIDITY.md`, `api/tests/test_real_data.py`).

Remaining
- Dev mineral layers are synthetic by default (`api/scripts/make_dev_layers.py`); under `stack/up.sh` the API reads an export of our site with real CRISM tiers divided by 4 (a test mapping, `devapi/export_ares_dir.py`).
- API auto-drive uses the steady-state version of the browser force model, not the full suspension dynamics (`SCIENCE_VALIDITY.md`, Known gaps).
- Crater wall and scarp in Caden's fixture are about 30 degrees all round, so the fixture's crater floor is unreachable at the 25 degree limit; real Jezero data does not have this problem.

## Stream D: MCP, agent, chat, docs (Sameer)

Done
- Query engine `atlas_core/` and its REST server `devapi/` (:8001): condition-language search, walking-time fields, evidence tiers, routes, line of sight, sun and shadow, paleolake flooding, EVA envelope, terrain layers. Real Jezero site built by `sitebuild/` (HiRISE 2 m, 7 CRISM mineral layers, USGS geologic units, 23 sample POIs, Perseverance traverse).
- MCP server `mcp/`: 39 tools listed from the running server on Nov 15, including `select_vehicle` and `drive_to`; split-backend mode with a startup frame check (`docs/api-compat.md`, `docs/rover-connection-test.md`).
- Agent backend `agent/` (:8766): tool loop, SSE, DeepSeek and Anthropic providers, record and replay. Recordings in `agent/replays/`.
- Chat panel `web/src/panels/chat/` and standalone page `chat-dev/` (:5199) with minimap; one-command launcher `stack/up.sh`.
- Evals: `agent/evals/questions.yaml` (27 cases; 24 were run live). DeepSeek: 19 of 24 on full runs with both `deepseek-chat` and `deepseek-v4-pro`; 6 of 7 on the rerun of failures; no ungrounded numbers in those 14 sessions ([docs/eval-run-deepseek.md](docs/eval-run-deepseek.md)).
- Honesty fixes from the blind tool review ([docs/tool-review.md](docs/tool-review.md)): 17 pass / 4 hesitate / 0 fail of 21 cases.
- Docs: README, science summary, project page, demo script, submission checklist, LICENSE, `.env.example`.

Remaining
- The 3 buggy-driving eval cases (`buggy_*`, `scout_to_marker`) have not been run live; no replay of buggy driving exists.
- Score the evals with a Claude model (only DeepSeek was run).
- Thresholds in `data/build/site/jezero/profiles.yaml` are all assumed; candidate citations in `docs/threshold-sources.md` are not applied.
- Record the demo video and fill the `TEAM TO FILL` fields in `docs/project-page.md`.
- `anthropic` is imported by the agent but missing from `pyproject.toml` dependencies; `pyproject.toml` is not edited here (README install line includes it).
- Not tested from a clean clone; the data build needs network.

## Known issues

- `docs/tool-review.md` is a dated working log; its early sections describe the earlier 28-tool, terrain-only state.
- `docs/science-validation.md` is auto-generated by `pipeline/09_validate.py`; the cross-link header added on Nov 15 is lost if it is regenerated.
