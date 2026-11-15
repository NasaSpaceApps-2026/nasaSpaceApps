# Handoff for the next agent (Nov 15, Sameer's stream)

Ares Atlas, NASA Space Apps 2026 ("Interplanetary Survival Guide: Martian Map"). Submission due tonight.
Read first: `README.md`, `CLAUDE.md` (team conventions + ownership), `docs/science-validity.md`.

## Where things are
- **Branch `sameer/mcp`**, PR **#4** to main: https://github.com/NasaSpaceApps-2026/nasaSpaceApps/pull/4
  Last pushed commit: `afd3714` (interactive viewer). Committed locally since, NOT pushed: overlays backend,
  Explore panel, snap-back fix, friendlier copilot, etc. (`git log origin/sameer/mcp..HEAD`).
- **Uncommitted in the working tree (two finished/finishing builders, overlapping files):**
  1. *Conversation quality* (done): `agent/conversation.py` (server-side memory incl. tool results),
     `agent/{app,config,context,loop,model,session}.py`, `agent/system_prompt.md` (rewritten),
     `agent/evals/{run_evals.py,scoring.py,conversation_questions.yaml}`, `web/src/panels/chat/{session.ts,
     session.test.ts,useChat.ts,state.ts,README.md}`, `chat-dev/mock-agent.mjs`, `tests/test_conversation.py`,
     `docs/eval-run-conversation.md`, `agent/replays/conversation/`. Live Flash: 8/15 -> 13/15 conversations
     (clean full run; some checks were loosened, listed in the doc).
  2. *Mid-mission astronaut tools* (may still be running): `atlas_core/mission.py`, `devapi/routes_mission.py`
     (+ registration in `devapi/app.py`), `mcp/tools_mission.py` (+ `mcp/server.py`), `tests/test_mission.py`,
     `agent/evals/mission_questions.yaml`, `agent/replays/mission/`, `docs/mission-tools.md`.
     Tools: eva_start/end, eva_status, return_route, nearest_haven, check_path_ahead, comm_check,
     log_sample/list_samples, bearing_to, rendezvous, daylight_check. Expected report: docs/eval-run-mission.md.
  - Known: `tests/test_server.py::test_lists_tools_and_descriptions` fails until its expected tool list
    includes the mission tools. Fix that, run the full suite, then commit both together and push.
  - `docs/threshold-sources.md` is unreviewed research (cut off by a usage limit) — verify or drop.

## Next steps (in order)
1. Finish/verify the two items above; `.venv/bin/python -m pytest -q -p no:cacheprovider tests`,
   `cd web && npm run build`, `cd chat-dev && npx vitest run`; scan for the key (never in files); commit; push.
2. **Restart the user's stack** so it picks up all backend changes (overlays, places, memory, mission tools,
   API snap-back fix): `stack/down.sh && stack/up.sh --with-viewer` (ask the user first; it interrupts them).
   Viewer prints its URL (5175 here: ports 5173/5174 are held by another project). Use Chrome/Brave.
3. Ask the user to **rotate the DeepSeek key** (a builder printed it once into its own session output; it is
   not in any project file).
4. Remaining polish (from reports): tier names like `strong_orbital` still leak into some replies; Flash
   sometimes skips the short acknowledgement; viewer fly-out after a drive not visually verified; stray orange
   dot on the globe once; rover on the Jezero map is a plain box model; faint street-view tile seam.
5. Team items (not ours): TEAM TO FILL lines in `docs/project-page.md`, demo video, NASA-logo check,
   clean-clone run, merging PR #4.

## How to run
`stack/up.sh --with-viewer` (API :8000 Yousef, devapi :8001 ours, MCP :8765, agent :8766 DeepSeek from `.env`,
chat :5199, viewer + sandbox). `stack/down.sh` stops. Logs in `stack/logs/`.

## Rules that matter (from the user)
- Ownership: Sameer owns mcp/, agent/, devapi/, atlas_core/, sitebuild/, chat-dev/, stack/, web/src/panels/chat/,
  docs/, validation/. Final-pass approval covers minimal, logged edits in web/ (`web/INTEGRATION.md`) and api/
  (`api/CHANGES-final-pass.md`). Never edit pipeline/ or data/ (Caden). Don't touch Caden's model.
- Code with Sonnet subagents; review their output (screenshots, tests) before committing. Commit to the branch,
  push only when the user says so (they approved pushing `sameer/mcp`).
- Science honesty: every number from a tool or a cited source; evidence tiers, never probabilities; label
  assumed thresholds; keep `docs/science-validity.md` updated with evidence for each new feature.
- Never print or commit `DEEPSEEK_API_KEY` (in git-ignored `.env`). Never launch Firefox/Safari from the
  terminal (it triggered the user's Firefox updater password prompt once); headless checks use Brave via
  puppeteer-core in the session scratchpad.
- The user wants: visible on-screen drives, plain-language friendly replies, loose place resolution
  ("Mount Sons" -> Olympus Mons, "the crater" -> nearest crater), a demo-ready UI.
