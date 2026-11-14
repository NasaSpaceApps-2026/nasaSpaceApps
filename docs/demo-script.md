# Demo scripts

Built on what runs today: MCP tools, the agent backend, the chat panel with tool cards, the `devapi` stand-in server. Beats marked **[viewer]** need the teammates' 3D viewer; if it is not ready, use the chat-dev page (`chat-dev/`) and skip or fake nothing: say what is on screen. Captions carry the message, because judges may watch without sound.

Prompts below are what to type. Confirm the exact tool calls and numbers on the day; do not quote numbers from this script. `TODO: record the replay of a good run (agent/replays) as a fallback for the live agent.`

## 2-minute script

| # | Time | On screen | Prompt typed | Caption |
|---|---|---|---|---|
| 1 | 0:00-0:15 | Chat panel open on Jezero. Title card first if the video allows. **[viewer]** whole-planet globe, fly into Jezero. | none | "Ares Atlas: ask Claude about Mars terrain, get answers from NASA data." |
| 2 | 0:15-0:35 | Chat panel. `get_site_overview` tool card, then reply naming the data (HiRISE and CTX DTMs, 2 m grid). | `What data do you have for this site, and what can't it tell me?` | "Claude reads the layer catalogue: what each layer measures and does not." |
| 3 | 0:35-1:05 | Tool card for `find_locations` showing the readable summary (slope, roughness, "within 30 min walk of base"). Result list with scores and thresholds labelled assumed. **[viewer]** pins the spots. | `Find landing pads within a 30 minute walk of base.` | "Compound query, searched exhaustively. Thresholds are labelled: assumed." |
| 4 | 1:05-1:25 | Tool card with empty-result diagnostics (percent of cells passing each condition), Claude suggests which limit to relax. | `Find a landing pad with zero slope within 5 minutes of base.` | "When nothing fits, Claude shows which condition failed." |
| 5 | 1:25-1:45 | Reply quoting CTX vs HiRISE slope; image `docs/compare_ctx_vs_hirise_2000m.png` shown beside the chat. | `Why not use the coarser CTX data for slope?` | "CTX 20 m max slope 11.7 deg; HiRISE 2 m, 33.4 deg, same 2 km window." (Numbers recorded in `docs/compare_ctx_vs_hirise_stats.txt`. Note: the agent can only cite this if a tool or the prompt provides it; otherwise present the figure as the narrator's point, not Claude's.) |
| 6 | 1:45-2:00 | **[viewer]** rover driving to a chosen spot after a `teleport_rover` or `goto_location` card; otherwise the MCP server in Claude Desktop listing tools. | `Take the rover to the best pad.` | "Same server works from any MCP client. Open source: `TODO: repo URL`." |

## 30-second script

| # | Time | On screen | Prompt typed | Caption |
|---|---|---|---|---|
| 1 | 0:00-0:05 | Chat panel, **[viewer]** Jezero behind it | none | "Ask Claude about Mars." |
| 2 | 0:05-0:18 | `find_locations` tool card with readable conditions, then the ranked pads (prompt pre-typed, pressed live) | `Find landing pads within 30 minutes walk of base.` | "30 min walk, flat, clean HiRISE data. Searched exhaustively." |
| 3 | 0:18-0:25 | Empty-result diagnostics card | `...and zero slope?` | "No fit? It says why." |
| 4 | 0:25-0:30 | Slope comparison figure | none | "CTX hides 33 deg slopes behind 12. We use HiRISE." |

## Pre-flight checklist

1. `.venv/bin/python -m devapi --port 8000` with `ATLAS_SITE_DIR=data/build/site/jezero` (otherwise the synthetic site is served and every answer is prefixed `[SYNTHETIC TEST SITE - not Mars data]`; do not demo that).
2. MCP server, agent (needs `ANTHROPIC_API_KEY`), chat-dev; see the root README.
3. Run each prompt once beforehand; keep the replay if it is good.
