# Demo scripts (2-minute and 30-second)

Updated Nov 15 against what runs now: the full stack from `stack/up.sh` (agent :8766, MCP :8765 with 39 tools, rover API :8000, query engine :8001, chat page and minimap :5199), the planet viewer with street view (`usage.md`), and recorded sessions in `agent/replays/`. Captions carry the message, because judges may watch without sound.

Rules: say what is on screen, fake nothing. Prompts are what to type. Quote only numbers that appear in a tool card or in a document named in the beat, and re-check them on the day.

## Setup (do this before recording)

1. `stack/up.sh` (add `--with-viewer` for the planet viewer and sandbox). Check `http://localhost:8766/health` shows the provider and `mcp_connected`. The default frame is the real 8 km Jezero site; the planet viewer and sandbox worlds are different frames (`docs/frames.md`), so do not claim the copilot drives the sandbox rover unless you started `stack/up.sh --frame crater --with-viewer`.
2. Run each prompt once beforehand. A live answer from DeepSeek passes our evals in 19 of 24 cases (`docs/eval-run-deepseek.md`), so expect an occasional miss and use the replay fallback.
3. Fallback for any beat: restart the agent on a recording, no key or MCP needed (one file per agent start; every chat then streams that file at original timing, `agent/README.md`):

```bash
stack/down.sh
ATLAS_REPLAY=agent/replays/<file>.jsonl ATLAS_REPLAY_SPEED=1 .venv/bin/python -m agent
```

Add `ATLAS_REPLAY_REPLAY_TOOLS=1` (with MCP and APIs up) to re-trigger the viewer side effects (teleport, camera) during replay.

| Recording | Prompt it holds | Tools it shows |
|---|---|---|
| `agent/replays/20261114T215405.jsonl` | Find 3 flat landing pads within a 30 minute walk of base | get_site_overview, find_locations, set_view |
| `agent/replays/20261114T215418.jsonl` | Take me to the delta front | search_pois, teleport_rover, fly_camera |
| `agent/replays/run3-full/evals/20261114T190541.jsonl` | Where are the best places to find carbonates near base? | get_site_overview, find_sites |
| `agent/replays/run3-full/evals/20261114T190814.jsonl` | Where has carbonate been confirmed by the rover? | get_site_overview, find_sites |
| `agent/replays/run3-full/evals/20261114T190715.jsonl` | Find a pad with zero slope anywhere. | get_site_overview, find_locations |
| `agent/replays/run3-full/evals/20261114T190938.jsonl` | How far from base can the crew walk in an 8 hour EVA and still get back with a 20% reserve? | get_site_overview, eva_envelope |
| `agent/replays/run3-full/evals/20261114T190945.jsonl` | Plan a walk from base to the Swift Run sample site and tell me whether the crew can always get back within a 4 hour EVA with 20% reserve. | search_pois, plan_route, assess_route, check_eva_return |
| `agent/replays/run3-full/evals/20261114T190625.jsonl` | Scout that route; stop if unsafe and tell me why. (needs a planned route; the eval planned one first) | fsd_enable, follow_route |
| `agent/replays/run3-full/evals/20261114T190923.jsonl` | Would the landing site have been underwater at the highest lake level recorded by the delta, and how deep? | flood_at_level |

Recorded with `deepseek-chat` on the real Jezero site. Replays do not include the buggy tools (`drive_to`, `select_vehicle`); for that beat run it live or show it in the viewer.

## 2-minute script

| # | Time | Screen | Prompt typed | Caption | Fallback |
|---|---|---|---|---|---|
| 1 | 0:00-0:15 | Planet viewer (`http://localhost:5173/?view=planet`): Mars globe, Jezero fan label, press Drive to drop into street view. Title card first if the video allows. | none | "Ares Atlas: plan a Mars EVA with an AI copilot, from NASA data." | Screen-record the viewer once in advance; no agent needed. |
| 2 | 0:15-0:45 | Chat page or dock. Tool card for `find_locations` opens (readable conditions: slope, roughness, within 30 min walk of base); ranked pads and the assumed-threshold labels. | `Find 3 flat landing pads within a 30 minute walk of base.` | "Compound query, searched exhaustively. Every threshold says cited or assumed." | Replay `agent/replays/20261114T215405.jsonl` |
| 3 | 0:45-1:10 | Same chat. Tool card for `find_sites`; reply separates rover-confirmed samples from orbital detections and says what is not covered. | `Where has carbonate been confirmed by the rover?` | "Evidence tiers, not percentages: rover samples vs orbit (CRISM). No coverage is not 'none'." | Replay `.../run3-full/evals/20261114T190814.jsonl` |
| 4 | 1:10-1:30 | `check_eva_return` card (live runs add a Totals line; the run-3 recording predates it); reply states the usable budget after the 20% reserve and the point of no return. | `Plan a walk from base to the Swift Run sample site and tell me whether the crew can always get back within a 4 hour EVA with 20% reserve.` | "Out-and-back time over slope-limited terrain: can the crew get home?" | Replay `.../run3-full/evals/20261114T190945.jsonl` |
| 5 | 1:30-1:45 | Chat, then rover on the minimap (or viewer): agent teleports the rover and flies the camera. | `Take me to the delta front.` | "Same server drives the rover: search, teleport, fly." | Replay `agent/replays/20261114T215418.jsonl` (add `ATLAS_REPLAY_REPLAY_TOOLS=1` to move the rover) |
| 6 | 1:45-2:00 | Slide: `docs/compare_ctx_vs_hirise_2000m.png` and the validation line from `docs/science-validity.md`. Then Claude Desktop or Claude Code listing the MCP tools. | none | "Coarse data hides 33 deg slopes behind 12 (CTX vs HiRISE, same 2 km window). Open MCP server: any AI client. Code: github.com/NasaSpaceApps-2026/nasaSpaceApps" | Static slide; MCP listing screenshot. |

Narrator-only (the agent cannot cite these unless a tool returns them): CTX 11.7 deg vs HiRISE 33.4 deg (`docs/compare_ctx_vs_hirise_stats.txt`); rover drove 0.10% of its traverse on our no-go ground vs 1.96% site-wide (`docs/science-validity.md`).

Optional buggy beat (swap for beat 5 if the viewer path is up and tested): `Drive the buggy to the highest point within 1 km.` The `drive_to` tool exists in the MCP server; this prompt is in the eval file but has no recorded live run, so rehearse it first and do not promise it.

## 30-second script

| # | Time | Screen | Prompt typed | Caption | Fallback |
|---|---|---|---|---|---|
| 1 | 0:00-0:05 | Planet viewer, globe to Jezero (or chat page if the viewer is not up) | none | "Ask an AI about Mars terrain." | Pre-recorded viewer clip |
| 2 | 0:05-0:18 | `find_locations` tool card with readable conditions, then ranked pads (prompt pre-typed, pressed live) | `Find 3 flat landing pads within a 30 minute walk of base.` | "30 min walk, flat, clean HiRISE data. Every tool call visible." | Replay `agent/replays/20261114T215405.jsonl` |
| 3 | 0:18-0:25 | `find_sites` card, rover-confirmed vs orbital | `Where has carbonate been confirmed by the rover?` | "Rover-confirmed vs orbital evidence, never mixed." | Replay `.../run3-full/evals/20261114T190814.jsonl` |
| 4 | 0:25-0:30 | Slope comparison figure | none | "CTX hides 33 deg slopes behind 12. We use HiRISE. Open MCP server." | Static image |

## Pre-flight checklist

1. `stack/up.sh`; health at `http://localhost:8766/health`; key present in `.env` (never show the file on screen).
2. Confirm the site banner: answers must not start with `[SYNTHETIC TEST SITE - not Mars data]` or `[FRAME MISMATCH`; if they do, stop and fix the stack before recording.
3. Run every prompt once; keep the good run's file from `agent/replays/` as the fallback.
4. Close other projects holding ports 5173/5174 (`stack/README.md`, "Ports").
5. No NASA logos, no API keys, no personal paths on screen.
