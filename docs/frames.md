# Site frames in play, and what the copilot can control consistently

Site frame (PRD contract 1): metres, x east, y north, origin at the site's `lat0`/`lon0`, equirectangular with Mars radius 3 396 190 m.
Two backends agree on a coordinate only if they load the same frame. This page lists every frame in the repo, which
combinations work with the chatbot (MCP + agent), and the bridge we added on our side. All numbers below were
computed or measured on 2026-11-14; "verified" means run, not inferred.

## The frames

Offsets are the frame origin expressed in OUR real-Jezero frame (`R cos(18.452) dlon`, `R dlat`).

| # | Frame | lat0, lon0 | Size | Data | Offset of origin in our frame |
|---|---|---|---|---|---|
| A | **Our query-engine site** `data/build/site/jezero` (`pipeline/site_cfg.py`) | 18.452, 77.418 | 8192 m, 2 m cells | Real: HiRISE DTM (CTX fill), slope/roughness, CRISM mineral tiers, geologic units, M20 traverse, POIs | (0, 0) |
| B | `contracts/fixtures` (what the viewer serves with `VITE_SITE_DIR=../contracts/fixtures`, and Yousef's API default `ARES_DATA_DIR`) | 18.46, 77.41 | 1024 m | **Synthetic, "not Mars"** (`fixture: true`) | (-450, +474) m |
| C | Rover world `crater` (`contracts/fixtures/rover_worlds/crater`, sandbox `?world=crater`, bubble "Jezero fan") | manifest says 18.44463, 77.45089 (stale, see below); real centre = `center_x/center_y` | 1800 m | Real HiRISE heights resampled from frame A + coloured ortho | **centre (+1000.85, +1766.39) m**; local (0,0) is the centre; covers x 101..1901, y 866..2666 of frame A |
| D | Rover world `olympus` | 18.44463, 77.45089 (stale) | 2236 m | Real HiRISE of the Olympus Mons scarp, lon 222.3 E | none: a different place on Mars, 144 degrees of longitude away |
| E | Rover worlds `projection`, `vision` (80 m, sol 9 Navcam) | 18.44463, 77.45089 | 80 m | Real HiRISE heights + Navcam drape | centre (+1849.08, -437.02) m = the Octavia E. Butler landing site |
| F | Yousef's API with an exported copy of frame A (`stack/cache/ares_jezero`) | 18.452, 77.418 | 8192 m | Our rasters through `devapi/export_ares_dir.py` (test mapping) | (0, 0) |
| G | Yousef's API with a crop of frame A (`stack/cache/ares_crater`) | 18.48179, 77.43578 | 1800 m | Our rasters, re-centred (`devapi/crop_site.py`) | (+1000, +1766) m, snapped to the 2 m grid |

Two facts worth knowing:

- The `lat0/lon0` in the `crater` and `olympus` world manifests are the old landing-site origin (the same values as
  `projection`/`vision`), not the world centre. For `crater` the world centre in our frame is `center_x/center_y`
  (checked: bubble centre 18.4818 N, 77.4358 E maps to exactly (1000.85, 1766.39) in frame A). The sandbox ignores
  `lat0/lon0` and draws API poses as world-local metres, so nothing breaks today; any tool that reads these manifests for
  geo-referencing would be off by about 2.4 km for `crater` and is meaningless for `olympus`.
- The sandbox applies **no frame transform** to the API rover pose. It adopts the API's `x, y` as its own world-local
  metres (`web/src/rover/link.ts`, `README.md`: a non-auto state more than 25 m away is adopted as a teleport). So the API's
  frame must BE the sandbox world's frame.

## What each API serves by default

- `uvicorn ares_api.main:app` with no env: `ARES_DATA_DIR=contracts/fixtures` = frame B (1 km synthetic).
- `ARES_DATA_DIR=api/fixtures/playground`: a richer 2 km synthetic site (gitignored, generated; not present here).
- `ARES_DATA_DIR=data/build`: real Jezero is possible only with Yousef's own pipeline commands in `CLAUDE.md`; our
  `devapi/export_ares_dir.py` writes the layout his API reads from our site (frame F).
- The rover sandbox's own default (no `?world=`): the playground if present, else `contracts/fixtures` (frame B).

## Combinations

The chatbot has two coordinate consumers: the query engine (`devapi`/`atlas_core`, frame A or a crop of it) answers
"where"; Yousef's API moves the rover. MCP passes `{x, y}` straight between them, so they must share a frame.
MCP checks `GET /site` of both at start and puts `[FRAME MISMATCH ...]` on every result if they differ.

| Combination | Consistent? | Notes |
|---|---|---|
| MCP + devapi (A) + API on exported A (F) + our chat page/minimap | **Yes, default of `stack/up.sh`** | Full real 8 km site; find_* coordinates, teleport/goto/routes agree. Verified live (agent run, rover moved in `:8000`). No teammate viewer shows it. |
| MCP + devapi on crop of A (G) + API on crop (G) + sandbox `?world=crater` | **Yes, `stack/up.sh --frame crater --with-viewer`** | Verified: teleporting the API rover to three points, the sandbox's own terrain elevation matched the API `z` to 1 cm (sandbox vs API: (100,50) -2479.96 vs -2479.59 on a 25 deg slope; (-400,300) -2476.63 vs -2476.63; (500,-600) -2554.48 vs -2554.47) and slopes matched. Coordinates seen by the model are world-local metres (x -900..900), so they are NOT frame-A coordinates; the minimap shows the same crop. Queries only cover the 1800 m crop (the mission base POI is outside the crop, so "base" is not available). |
| MCP + devapi (A) + API default (B) | No | 450 m / 474 m origin offset and a different (synthetic) place; MCP prints the banner. |
| MCP + devapi (A) + API (F) + sandbox `?world=crater` | No | Rover pose is drawn as world-local metres; a frame-A point such as (1844,-433) is far outside the +-900 m world. Teleporting to a frame-A coordinate in the crater area (e.g. 1000,1766) would draw 1.0 km east, 1.8 km north of the world centre. |
| Any of the above + sandbox `?world=olympus` | No | Different location; our data does not cover it. The API would need Olympus rasters; that is Caden/Yousef data work. |
| `projection` / `vision` worlds | Not useful | Bridgeable the same way (`crop_site.py --world projection`, 80 m) but an 80 m square is too small for the demo prompts. |
| Planet viewer (`:5173`) with `VITE_SITE_DIR=../contracts/fixtures` | Not with real data | It serves frame B (synthetic). The street-view iframe is the sandbox, so it follows the sandbox rows above. |

## The bridge (our side only)

The "crater" world lies completely inside our 8 km site, so no transform inside MCP is needed. Instead of translating
every coordinate in 900 lines of tool code, the data is re-centred once:

    local = frame_A - (1000.85, 1766.39)      # world-local metres; inverse adds the centre

`devapi/crop_site.py --world crater` slices frame A (no resampling) into an 1800 m square whose origin is the world
centre (snapped to the 2 m grid, 0.93 m from the exact centre), shifts POIs and the traverse, and writes `site.yaml` with
the new `lat0/lon0`. Run `devapi.export_ares_dir` on it for Yousef's API and point devapi at it
(`stack/up.sh --frame crater` does all of that). The MCP frame check then passes (both backends report the same
centre), so every tool, the agent and the chat page work unchanged. Heights in the crop come from the same HiRISE grid as
the sandbox world (elevation range -2564.01..-2455.70 here vs -2564.08..-2455.68 in the world manifest).

Residual differences, none fixable on our side: the sandbox world's ortho is a coloured fixture, not our hillshade;
the sandbox `crater` physics profile and Yousef's `crew_buggy` are separate implementations; the API's markers and
routes persist in `api/.state` keyed by data dir, so switching frames shows different saved markers.

## Alternatives considered

- **MCP offset for a selected world** (`ATLAS_WORLD_OFFSET`): translate every x/y in tool arguments and results at the
  rover-API boundary. Correct but touches most of `mcp/backends.py` and `format.py`; the crop gives the same result with
  no per-tool code, at the price of a smaller query area.
- **Ask Yousef's API to serve frame A** (`ARES_DATA_DIR` = export of A) and make the sandbox read an offset: needs a
  sandbox change (`web/src/rover/`), i.e. Yousef.
- **Ask Caden** to correct `lat0/lon0` in the `crater`/`olympus` world manifests (or document `center_x/center_y` as
  the geo-reference) so tools can use them.

## Commands

    stack/up.sh                              # real Jezero everywhere, chat page + minimap on :5199
    stack/up.sh --frame crater --with-viewer # crater crop; also starts the teammates' viewer/sandbox if :5173/:5174 are free
    stack/down.sh
